"""Migrate disposable v1 databases without rewriting their economic history."""
import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from roosters import rules, rules_v4, wallet
from roosters.service import GameService, encode
from roosters.storage import Database, SINGLE_CURRENCY_MIGRATION


NOW = 1_800_000_000


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="roosters-migration-")
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name) / "legacy.sqlite3")
        initial = Path(__file__).parents[1] / "roosters/migrations/001_initial.sql"
        with closing(self.database.connect()) as db:
            db.executescript(initial.read_text())
            db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
            db.execute("INSERT INTO schema_migrations VALUES ('001_initial.sql')")

    def player(self, player_id, *, coins=123, medals=7, is_dev=False, xp=370):
        with self.database.transaction() as db:
            db.execute("""INSERT INTO players(
                id,name,is_dev,coins,medals,xp,breed_id,gear,owned_breeds,energy,
                energy_at,passive_at,daily_at,last_seen,created_at,referral_code,wins
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                player_id, player_id, int(is_dev), coins, medals, xp, "copper",
                '{"helmet":1,"armor":2,"sword":3}', '["yard","copper"]', 17,
                NOW - 20, NOW - 120, NOW - 300, NOW, NOW - 1000, "ref_" + player_id, 999,
            ))

    def battle(self, battle_id, player_a, player_b=None, *, mode="practice", winner=None, status="finished", version="v1"):
        snapshot = json.dumps({"name": player_a, "power": 182, "breed_id": "copper", "stance": "rush"})
        result = json.dumps({"coins": 35, "medals": 5, "xp": 25, "won": True})
        with self.database.transaction() as db:
            db.execute("""INSERT INTO battles(
                id,player_a,player_b,mode,rules_version,snapshot_a,snapshot_b,draw,
                starts_at,ends_at,tap_at_a,tap_at_b,status,probability,winner_id,result_a
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                battle_id, player_a, player_b, mode, version, snapshot, snapshot, 0.375,
                NOW - 20, NOW + 10 if status == "active" else NOW - 5,
                NOW - 20, NOW - 20, status, None if status == "active" else 0.625,
                winner, None if status == "active" else result,
            ))

    def rows(self, table, columns="*"):
        with closing(self.database.connect()) as db:
            return [dict(row) for row in db.execute("SELECT " + columns + " FROM " + table + " ORDER BY rowid")]

    def test_conversion_preserves_archives_and_progress_and_is_applied_once(self):
        self.player("tg:1")
        self.player("tg:2", coins=0, medals=0)
        self.battle("finished-v1", "tg:1")
        self.battle("active-v2", "tg:1", mode="bot", status="active", version="v2")
        with self.database.transaction() as db:
            db.execute("INSERT INTO queue VALUES (?,?,?,?,?,?)", ("tg:2", "guard", 182, NOW, NOW + 60, NOW + 120))
            db.execute("INSERT INTO ledger(player_id,currency,delta,balance_after,reason,reference,created_at) VALUES (?,?,?,?,?,?,?)",
                       ("tg:1", "coins", 123, 123, "welcome", "tg:1", NOW))
            db.execute("INSERT INTO commands VALUES (?,?,?,?,?)",
                       ("tg:1", "old-command", "original-fingerprint", '{"coins":35,"medals":5}', NOW))
        before = {table: self.rows(table) for table in ("players", "battles", "ledger", "commands")}

        self.database.migrate()
        self.database.migrate()

        for table, previous_rows in before.items():
            for previous, current in zip(previous_rows, self.rows(table)):
                self.assertEqual(previous, {key: current[key] for key in previous}, table)
            self.assertEqual(len(previous_rows), len(self.rows(table)), table)
        players = self.rows("players")
        self.assertEqual([p["balance_minor"] for p in players], [19300, 0])
        self.assertEqual([p["first_battle_used"] for p in players], [1, 0])
        self.assertEqual([p["power_cache"] for p in players], [194, 194])
        self.assertEqual(self.rows("queue"), [])
        journal = self.rows("coin_ledger")
        self.assertEqual([r["delta_minor"] for r in journal], [19300, 0])
        self.assertTrue(all(r["reason"] == "migration_opening" for r in journal))
        self.assertTrue(all(r["reference"] == SINGLE_CURRENCY_MIGRATION for r in journal))
        with self.database.transaction() as db:
            wallet.change(db, "tg:1", 125, "test_reward", "new-command", NOW)
        self.database.migrate()
        self.assertEqual(self.rows("players")[0]["balance_minor"], 19425)
        self.assertEqual(len(self.rows("coin_ledger")), 3)
        self.assertEqual(len(self.rows("schema_migrations")), 5)

    def test_active_battle_alone_uses_first_free_battle(self):
        self.player("tg:1")
        self.battle("active-v1", "tg:1", status="active")
        self.database.migrate()
        self.assertEqual(self.rows("players")[0]["first_battle_used"], 1)
        battle = self.rows("battles")[0]
        self.assertEqual((battle["status"], battle["draw"], battle["ends_at"]), ("active", 0.375, NOW + 10))
        self.assertEqual((battle["stake_minor"], battle["win_payout_minor"], battle["created_at"]), (0, 0, 0))

    def test_real_pvp_wins_ignore_generic_wins_bots_dev_and_unfinished_matches(self):
        for player_id in ("tg:1", "tg:2", "tg:3"):
            self.player(player_id)
        self.player("dev:1", is_dev=True)
        self.battle("pvp-win-a", "tg:1", "tg:2", mode="online", winner="tg:1")
        self.battle("pvp-win-b", "tg:1", "tg:2", mode="online", winner="tg:2", version="v2")
        self.battle("pvp-second-a", "tg:3", "tg:1", mode="online", winner="tg:1")
        self.battle("bot-win", "tg:1", mode="bot", winner="tg:1")
        self.battle("practice-win", "tg:1", winner="tg:1")
        self.battle("dev-loser", "tg:1", "dev:1", mode="online", winner="tg:1")
        self.battle("dev-winner", "dev:1", "tg:1", mode="online", winner="dev:1")
        self.battle("ongoing", "tg:1", "tg:2", mode="online", status="active", winner="tg:1")
        self.battle("not-a-participant", "tg:1", "tg:2", mode="online", winner="tg:3")
        self.database.migrate()
        self.assertEqual({p["id"]: p["pvp_wins"] for p in self.rows("players")},
                         {"tg:1": 2, "tg:2": 1, "tg:3": 0, "dev:1": 0})

    def test_backfill_failure_rolls_back_schema_journal_and_migration_marker(self):
        self.player("tg:1")
        self.player("tg:2")
        with self.database.transaction() as db:
            db.execute("INSERT INTO queue VALUES (?,?,?,?,?,?)", ("tg:1", "rush", 182, NOW, NOW + 60, NOW + 120))
        players_before, queue_before = self.rows("players"), self.rows("queue")
        with patch.object(rules, "power", side_effect=[182, ValueError("Invalid legacy power")]):
            with self.assertRaisesRegex(ValueError, "Invalid legacy power"):
                self.database.migrate()
        self.assertEqual(self.rows("players"), players_before)
        self.assertEqual(self.rows("queue"), queue_before)
        self.assertEqual(self.rows("schema_migrations"), [{"version": "001_initial.sql"}])
        with closing(self.database.connect()) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='coin_ledger'").fetchone())
            self.assertNotIn("balance_minor", [r["name"] for r in db.execute("PRAGMA table_info(players)")])
        self.database.migrate()
        self.assertEqual(len(self.rows("coin_ledger")), 2)

    def test_new_schema_keeps_queue_defaults_and_ranking_indexes(self):
        self.database.migrate()
        with closing(self.database.connect()) as db:
            columns = {r["name"]: r for r in db.execute("PRAGMA table_info(queue)")}
            self.assertNotIn("stance", columns)
            self.assertEqual(columns["stake_minor"]["dflt_value"], "1000")
            indexes = {r["name"] for r in db.execute("PRAGMA index_list(players)")}
            self.assertTrue({"players_power_ranking", "players_pvp_ranking"}.issubset(indexes))

    def test_v5_migration_preserves_v4_active_and_finished_personal_wagers(self):
        # Exercise the actual old schema, not a v5 row relabelled as v4.
        old_migrations = sorted((Path(__file__).parents[1] / "roosters/migrations").glob("00[1-4]_*.sql"))
        with patch("roosters.storage.Path.glob", return_value=old_migrations):
            self.database.migrate()
        for pid in ("tg:1", "tg:2", "tg:waiting"):
            self.player(pid)
        self.battle("active-v4", "tg:1", "tg:2", mode="online", status="active", version="v4")
        self.battle("finished-v4", "tg:1", "tg:2", mode="online", winner="tg:2", version="v4")
        with self.database.transaction() as db:
            db.execute("UPDATE battles SET stake_minor=2500,win_payout_minor=4750")
            db.execute("UPDATE battles SET result_a=?,result_b=? WHERE id='finished-v4'", (
                encode({"won": False, "xp": 12, "payout_minor": 0, "net_minor": -2500}),
                encode({"won": True, "xp": 25, "payout_minor": 4750, "net_minor": 2250})))
            db.execute("UPDATE players SET battle_id='active-v4',first_battle_used=1 WHERE id IN ('tg:1','tg:2')")
            db.execute("INSERT INTO queue(player_id,power,joined_at,expires_at,deadline,stake_minor) VALUES (?,?,?,?,?,?)",
                       ("tg:waiting", 194, NOW, NOW + 60, NOW + 120, 10000))
        before = {table: self.rows(table) for table in ("players", "battles", "queue", "coin_ledger", "commands")}
        self.database.migrate()
        self.database.migrate()
        for table, previous_rows in before.items():
            for previous, current in zip(previous_rows, self.rows(table)):
                self.assertEqual(previous, {key: current[key] for key in previous}, table)
            self.assertEqual(len(previous_rows), len(self.rows(table)), table)
        for battle in self.rows("battles"):
            self.assertEqual(battle["stake_b_minor"], 2500)
            self.assertEqual(battle["win_payout_b_minor"], 4750)
        service = GameService(self.database, clock=lambda: NOW)
        for pid in ("tg:1", "tg:2"):
            state = service.state(pid)
            self.assertEqual(state["battle"]["rules_version"], "v4")
            self.assertEqual(state["battle"]["tap_cap"], 90)
            self.assertEqual(state["battle"]["wager"], {"stake_minor": 2500, "win_payout_minor": 4750})
            self.assertEqual(state["history"][0]["wager"], state["battle"]["wager"])
            self.assertEqual(state["history"][0]["result"]["net_minor"], -2500 if pid == "tg:1" else 2250)
        service.clock = lambda: NOW + 10
        service.tick()
        for pid, won in (("tg:1", True), ("tg:2", False)):
            result = service.state(pid)["battle"]["result"]
            self.assertEqual(result["payout_minor"], 4750 if won else 0)
            self.assertEqual(result["net_minor"], 2250 if won else -2500)
            self.assertEqual(result["xp"], rules_v4.rewards("online", won)["xp"])
