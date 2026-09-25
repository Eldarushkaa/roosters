"""SQLite unit of work, one connection per command, single host/local disk.

BEGIN IMMEDIATE serializes competing commands before reading economic state.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SINGLE_CURRENCY_MIGRATION = "003_single_currency.sql"
LEGACY_MEDAL_COINS = 10
MINOR_UNITS_PER_COIN = 100


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 15000")
        return db

    def migrate(self):
        db = self.connect()
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY)")
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (path.name,)).fetchone():
                    continue
                statement = ""
                for line in path.read_text(encoding="utf-8").splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError("Incomplete SQL migration: " + path.name)
                self._after_migration(db, path.name)
                db.execute("INSERT INTO schema_migrations VALUES (?)", (path.name,))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _after_migration(self, db, version):
        """Complete data backfills before the transaction records the version.

        Python uses the same integer power formula as gameplay instead of an
        approximate SQLite square root. Any malformed legacy record fails the
        entire migration, including its schema changes and opening journal.
        """
        if version == "004_breed_multipliers.sql":
            from . import rules
            for player in db.execute("SELECT * FROM players ORDER BY id").fetchall():
                power = rules.power(player["breed_id"], json.loads(player["gear"]), player["xp"])
                db.execute("UPDATE players SET power_cache=? WHERE id=?", (power, player["id"]))
            # Queue entries are unpaid; discard stale matchmaking power quotes.
            db.execute("DELETE FROM queue")
            return
        if version != SINGLE_CURRENCY_MIGRATION:
            return
        from . import rules, wallet

        now = time.time()
        db.execute("""UPDATE players SET first_battle_used=EXISTS(
            SELECT 1 FROM battles WHERE player_a=players.id OR player_b=players.id
        )""")
        # Generic wins include bots/practice/dev opponents and cannot serve as
        # the source of truth for the real-player PvP board.
        db.execute("""WITH victories AS (
            SELECT b.winner_id AS player_id, COUNT(*) AS wins
            FROM battles b
            JOIN players a ON a.id=b.player_a AND a.is_dev=0
            JOIN players p ON p.id=b.player_b AND p.is_dev=0
            WHERE b.status='finished' AND b.mode='online'
              AND b.winner_id IN (b.player_a,b.player_b)
            GROUP BY b.winner_id
        )
        UPDATE players SET pvp_wins=COALESCE(
            (SELECT wins FROM victories WHERE victories.player_id=players.id),0
        )""")
        for player in db.execute("SELECT * FROM players ORDER BY id").fetchall():
            opening = (player["coins"] + player["medals"] * LEGACY_MEDAL_COINS) * MINOR_UNITS_PER_COIN
            wallet.change(db, player["id"], opening, "migration_opening", version, now)
            power = rules.power(player["breed_id"], json.loads(player["gear"]), player["xp"])
            db.execute("UPDATE players SET power_cache=? WHERE id=?", (power, player["id"]))

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
