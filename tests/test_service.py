"""V3 integration: temporary databases, deterministic time, actual transactions.

Retries, concurrent tabs, partial failures and historical settlement are tested
without a live wallet, Telegram account, network request or wall-clock sleep.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from unittest.mock import patch
from uuid import uuid4

import pytest

from roosters import rules, rules_v1, rules_v2, wallet
from roosters.errors import GameError
from roosters.service import GameService, encode
from roosters.storage import Database


class Arena:
    def __init__(self, path):
        self.now = 1_800_000_000.0
        self.db = Database(path)
        self.db.migrate()
        self.service = self.restart()
        for pid in ("tg:a", "tg:b"):
            self.service.register(pid, pid, False)

    def restart(self, draw=.25):
        return GameService(self.db, clock=lambda: self.now, random_float=lambda: draw)

    def cmd(self, op, body=None, pid="tg:a", key=None, service=None):
        return (service or self.service).command(pid, key or str(uuid4()), op, body or {})

    def state(self, pid="tg:a"):
        return self.service.state(pid)

    def player(self, pid="tg:a"):
        return self.state(pid)["player"]

    def payload(self, stake=1000, pid="tg:a"):
        return {"mode": "bot", "stake_minor": stake, "expected_power": self.player(pid)["power"]}

    def start(self, stake=1000, pid="tg:a", service=None):
        return self.cmd("battle/start", self.payload(stake, pid), pid, service=service)["state"]["battle"]

    def finish(self, battle, pid="tg:a"):
        self.now = battle["ends_at"]
        self.service.tick()
        return self.state(pid)["battle"]

    def join(self, pid="tg:a", stake=1000):
        return self.cmd("queue/join", {"stake_minor": stake}, pid)

    def tap(self, battle, taps, pid="tg:a", key=None):
        return self.cmd("battle/tap", {"battle_id": battle["id"], "taps": taps}, pid, key)["result"]["accepted"]


@pytest.fixture
def arena(tmp_path):
    return Arena(tmp_path / "service.sqlite3")


def test_registration_preserves_progress_and_single_currency_contract(arena):
    assert arena.player()["balance_minor"] == 42000
    arena.cmd("gear/upgrade", {"slot": "helmet"})
    before = arena.player()
    arena.service.register("tg:a", "New name", False)
    restored = arena.restart().state("tg:a")["player"]
    for field in ("balance_minor", "xp", "gear", "owned_breeds"):
        assert before[field] == restored[field]
    assert not {"coins", "medals"} & set(restored)
    assert "energy" not in arena.state()["economy"]


def test_daily_replay_restart_and_payload_operation_conflicts(arena):
    key = str(uuid4())
    first = arena.cmd("claim/daily", key=key)
    replay = arena.cmd("claim/daily", key=key, service=arena.restart())
    assert first["result"] == replay["result"]
    assert arena.player()["balance_minor"] == 59000
    for op, body in (("presence", {}), ("claim/daily", {"payout_minor": 1})):
        with pytest.raises(GameError) as error:
            arena.cmd(op, body, key=key)
        assert error.value.code == "idempotency_conflict"
    with pytest.raises(GameError):
        arena.cmd("claim/daily")
    arena.now += 86400
    arena.cmd("claim/daily")
    assert arena.player()["balance_minor"] == 76000


def test_same_key_scoped_to_player(arena):
    key = str(uuid4())
    for pid in ("tg:a", "tg:b"):
        arena.cmd("claim/daily", pid=pid, key=key)
        assert arena.player(pid)["balance_minor"] == 59000


def test_passive_cap_and_fractional_minute(arena):
    arena.now += 90
    assert arena.cmd("claim/passive")["result"]["payout_minor"] == 200
    arena.now += 30
    assert arena.cmd("claim/passive")["result"]["payout_minor"] == 200
    arena.now += 10*3600
    assert arena.cmd("claim/passive")["result"]["payout_minor"] == 96000
    with pytest.raises(GameError):
        arena.cmd("claim/passive")


def test_failed_upgrade_atomic_then_same_key_can_retry_after_income(arena):
    for _ in range(2):
        arena.cmd("gear/upgrade", {"slot": "sword"})
    before, key = arena.player(), str(uuid4())
    with pytest.raises(GameError):
        arena.cmd("gear/upgrade", {"slot": "sword"}, key=key)
    assert arena.player() == before
    arena.now += 8*3600
    arena.cmd("claim/passive")
    arena.cmd("gear/upgrade", {"slot": "sword"}, key=key)
    assert arena.player()["gear"]["sword"] == 3
    assert arena.service.leaderboard("tg:a")["power"][0]["power"] == 130


def test_breed_failure_atomic_and_equipping_owned_is_free(arena):
    before = arena.player()
    with pytest.raises(GameError):
        arena.cmd("breed/buy", {"breed_id": "ember"})
    assert arena.player() == before
    arena.cmd("claim/daily")
    arena.cmd("breed/buy", {"breed_id": "copper"})
    arena.cmd("breed/buy", {"breed_id": "yard"})
    arena.cmd("breed/buy", {"breed_id": "copper"})
    assert arena.player()["balance_minor"] == 9000
    assert arena.service.leaderboard("tg:a")["power"][0]["power"] == 120


def test_quote_draw_and_debit_survive_lost_response_and_restart(arena):
    key, body = str(uuid4()), arena.payload(2500)
    first = arena.cmd("battle/start", body, key=key)
    battle = first["state"]["battle"]
    assert first["state"]["player"]["balance_minor"] == 39500
    assert battle["starts_at"]-battle["created_at"] == 2
    assert battle["ends_at"]-battle["starts_at"] == 10
    assert battle["tap_cap"] == 90 and battle["rules_version"] == "v4"
    assert battle["wager"]["win_payout_minor"] == rules.bot_payout(2500, 100, battle["opponent"]["power"])
    recovered = arena.cmd("battle/start", body, key=key, service=arena.restart(.99))
    assert recovered["state"]["battle"] == battle
    finished = arena.finish(battle)
    paid = arena.player()
    assert paid["balance_minor"] == 42000+finished["result"]["net_minor"]
    assert finished["result"]["payout_minor"] == battle["wager"]["win_payout_minor"]
    late = arena.cmd("battle/start", body, key=key, service=arena.restart())
    assert late["state"]["battle"]["id"] == battle["id"]
    assert arena.player() == paid


def test_paid_loss_zero_payout_and_no_second_debit(arena):
    battle = arena.start(10000, service=arena.restart(.99))
    result = arena.finish(battle)["result"]
    assert not result["won"] and result["payout_minor"] == 0
    assert result["net_minor"] == -10000
    arena.restart().tick()
    assert arena.player()["balance_minor"] == 32000
    assert arena.player()["battles"] == 1


def test_free_battle_fixed_reward_on_loss_consumed_on_start(arena):
    battle = arena.start(0, service=arena.restart(.99))
    assert not arena.state()["economy"]["first_free_battle_available"]
    assert arena.player()["balance_minor"] == 42000
    result = arena.finish(battle)["result"]
    assert not result["won"] and result["payout_minor"] == 1500
    with pytest.raises(GameError) as error:
        arena.start(0)
    assert error.value.code == "free_battle_used"
    assert arena.player()["balance_minor"] == 43500


def test_removed_training_practice_and_rps_rejected(arena):
    before = arena.player()
    for op, body in (("train", {"taps": 80}), ("battle/start", dict(arena.payload(), mode="practice")),
                     ("battle/start", dict(arena.payload(), stance="rush")), ("queue/join", {"stance": "rush"})):
        with pytest.raises(GameError):
            arena.cmd(op, body)
    assert arena.player() == before


def test_ninety_taps_same_instant_without_rate_limit_replay_is_safe(arena):
    battle = arena.start()
    assert arena.tap(battle, 90) == 0
    arena.now = battle["starts_at"]
    key = str(uuid4())
    assert arena.tap(battle, 55, key=key) == 55
    assert arena.tap(battle, 55, key=key) == 55  # Replayed result, not another increment.
    assert arena.tap(battle, 90) == 35
    assert arena.tap(battle, 90) == 0
    finished = arena.finish(battle)
    assert finished["you"]["taps"] == 90
    assert finished["win_probability"] == float(rules.bot_probability(100, battle["opponent"]["power"], 90))


def test_last_instant_burst_allowed_but_deadline_and_ownership_enforced(arena):
    battle = arena.start()
    arena.now = battle["ends_at"]-.001
    with pytest.raises(GameError):
        arena.tap(battle, 80, "tg:b")
    assert arena.tap(battle, 80) == 80
    arena.now = battle["ends_at"]
    with pytest.raises(GameError):
        arena.tap(battle, 1)
    assert arena.state()["battle"]["you"]["taps"] == 80


def test_partial_settlement_failure_rolls_back_then_recovers(arena):
    battle = arena.start()
    arena.now = battle["ends_at"]
    original = wallet.change

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after journal write")

    with patch("roosters.service.wallet.change", side_effect=crash), pytest.raises(RuntimeError):
        arena.service.tick()
    with arena.db.transaction() as db:
        assert db.execute("SELECT balance_minor FROM players WHERE id='tg:a'").fetchone()[0] == 41000
        assert db.execute("SELECT status FROM battles WHERE id=?", (battle["id"],)).fetchone()[0] == "active"
    recovered = arena.restart().state("tg:a")
    assert recovered["player"]["balance_minor"] == 41000+recovered["battle"]["result"]["payout_minor"]


def test_competing_tabs_only_start_one_free_battle(arena):
    body = arena.payload(0)

    def submit(service):
        try:
            return arena.cmd("battle/start", body, service=service)
        except GameError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [arena.restart(), arena.restart()]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, GameError) for r in results) == 1
    arena.finish(arena.state()["battle"])
    assert arena.player()["balance_minor"] == 43500


def test_duplicate_commands_and_concurrent_workers_credit_once(arena):
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda service: arena.cmd("claim/daily", key=key, service=service), [arena.restart(), arena.restart()]))
    assert results[0]["result"] == results[1]["result"]
    assert arena.player()["balance_minor"] == 59000
    battle = arena.start()
    arena.now = battle["ends_at"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda service: service.tick(), [arena.restart(), arena.restart()]))
    state = arena.state()
    assert state["player"]["balance_minor"] == 58000+state["battle"]["result"]["payout_minor"]
    assert state["player"]["battles"] == 1


def test_queue_cancel_expiry_and_equipment_lock(arena):
    before = arena.player()
    arena.join()
    for op, body in (("gear/upgrade", {"slot": "sword"}), ("breed/buy", {"breed_id": "yard"}), ("battle/start", arena.payload())):
        with pytest.raises(GameError):
            arena.cmd(op, body)
    arena.cmd("queue/leave")
    assert arena.player() == before
    arena.join()
    arena.now += 121
    state = arena.state()
    assert state["queue"] is None and state["battle"] is None
    assert arena.player() == before


def test_online_equal_stakes_exact_bank_and_real_win_ranking(arena):
    arena.join(stake=2500)
    arena.join("tg:b", 2500)
    battle = arena.state()["battle"]
    assert battle["wager"] == {"stake_minor": 2500, "win_payout_minor": 4750}
    assert arena.player()["balance_minor"] == arena.player("tg:b")["balance_minor"] == 39500
    arena.cmd("queue/leave")  # Cancel after the match cannot refund a committed stake.
    assert arena.player()["balance_minor"] == 39500
    a = arena.finish(battle)
    b = arena.state("tg:b")["battle"]
    assert a["result"]["won"] != b["result"]["won"]
    assert a["result"]["payout_minor"]+b["result"]["payout_minor"] == 4750
    assert arena.player()["balance_minor"]+arena.player("tg:b")["balance_minor"] == 83750
    assert arena.player()["pvp_wins"]+arena.player("tg:b")["pvp_wins"] == 1
    assert arena.service.leaderboard("tg:a")["pvp_wins"][0]["id"] == "tg:a"


def test_different_stakes_and_dev_accounts_never_match(arena):
    arena.join(stake=1000)
    arena.join("tg:b", 2500)
    arena.service.register("dev:1", "Dev", True)
    arena.join("dev:1")
    for pid in ("tg:a", "tg:b", "dev:1"):
        assert arena.state(pid)["battle"] is None
        assert arena.state(pid)["queue"] is not None


def test_bot_and_dev_wins_do_not_increment_real_pvp_score(arena):
    arena.finish(arena.start())
    assert arena.player()["pvp_wins"] == 0
    for pid in ("dev:1", "dev:2"):
        arena.service.register(pid, pid, True)
        arena.join(pid)
    arena.finish(arena.state("dev:1")["battle"], "dev:1")
    assert arena.player("dev:1")["pvp_wins"]+arena.player("dev:2")["pvp_wins"] == 0
    assert all(r["id"].startswith("dev:") for r in arena.service.leaderboard("dev:1")["power"])


def test_power_ranking_uses_equipment_not_xp_or_bot_wins(arena):
    arena.cmd("gear/upgrade", {"slot": "sword"}, "tg:b")
    arena.finish(arena.start())
    board = arena.service.leaderboard("tg:a")
    assert board["power"][0]["id"] == "tg:b" and board["power"][0]["power"] == 110
    assert all(r["pvp_wins"] == 0 for r in board["pvp_wins"])


def test_presence_excludes_dev_and_expires(arena):
    arena.service.register("dev:1", "Dev", True)
    assert arena.state()["presence"] == {"online": 2, "development_online": 1, "searching": 0}
    arena.now += 46
    assert arena.state()["presence"]["online"] == arena.state()["presence"]["development_online"] == 0


def test_economic_forgery_and_stale_power_atomic(arena):
    invalid = [("battle/start", dict(arena.payload(), stake_minor=v)) for v in (True, -1, 1000.0, 1001, 10000000)]
    invalid += [("battle/start", dict(arena.payload(), expected_power=101)), ("battle/start", dict(arena.payload(), won=True)),
                ("gear/upgrade", {"slot": ["sword"]}), ("gear/upgrade", {"slot": "sword", "cost_minor": 0}),
                ("breed/buy", {"breed_id": ["ember"]}), ("queue/join", {"stake_minor": 0}), ("presence", {"online": 1000})]
    before = arena.player()
    for op, body in invalid:
        with pytest.raises(GameError):
            arena.cmd(op, body)
        assert arena.player() == before
    battle = arena.start()
    for taps in (True, 0, -1, 1.5, 91):
        with pytest.raises(GameError):
            arena.tap(battle, taps)
    for key in (None, "", "not-a-uuid"):
        with pytest.raises(GameError):
            arena.service.command("tg:a", key, "presence", {})


@pytest.mark.parametrize("engine", [rules_v1, rules_v2])
def test_legacy_active_match_keeps_timing_odds_and_converts_rewards(arena, engine):
    battle = arena.start()
    a, b = dict(battle["you"], power=100, stance="rush"), dict(battle["opponent"], power=100, stance="feint")
    with arena.db.transaction() as db:
        db.execute("""UPDATE battles SET rules_version=?,snapshot_a=?,snapshot_b=?,ends_at=?,
            tap_budget_a=?,tap_at_a=? WHERE id=?""", (engine.RULES_VERSION, encode(a), encode(b),
            battle["starts_at"]+engine.BATTLE_DURATION, getattr(engine, "TAP_INITIAL", 0), battle["starts_at"], battle["id"]))
    arena.now = battle["starts_at"]+1
    accepted = arena.tap(battle, 20)
    assert accepted == (4 if engine is rules_v1 else 12)
    before = arena.player()
    restored = arena.restart().state("tg:a")["battle"]
    assert restored["ends_at"]-restored["starts_at"] == engine.BATTLE_DURATION
    finished = arena.finish(restored)
    assert finished["win_probability"] == engine.battle_probability(a, b, accepted, engine.BOT_TAPS)
    reward = engine.rewards("bot", finished["result"]["won"])
    assert arena.player()["balance_minor"] == before["balance_minor"]+(reward["coins"]+10*reward["medals"])*100
    assert finished["result"]["legacy"]
    assert arena.restart().state("tg:a")["battle"]["result"] == finished["result"]


def test_committed_removed_command_replays_without_reenabling_training(arena):
    key, payload, result = str(uuid4()), {"taps": 3}, {"accepted": 3, "coins": 3}
    fingerprint = hashlib.sha256(encode(["train", payload]).encode()).hexdigest()
    with arena.db.transaction() as db:
        db.execute("INSERT INTO commands VALUES (?,?,?,?,?)", ("tg:a", key, fingerprint, encode(result), arena.now))
    before = arena.player()
    assert arena.cmd("train", payload, key=key)["result"] == result
    assert arena.player() == before
    with pytest.raises(GameError):
        arena.cmd("train", payload)


def test_new_release_label_cannot_reinterpret_persisted_v3_as_medals(arena):
    battle = arena.start()
    # The saved engine stays registered as v3. A later release label alone
    # must not change its wallet units, tap handling or payout format.
    with patch.object(rules, "RULES_VERSION", "v4"):
        arena.now = battle["starts_at"]
        assert arena.tap(battle, 80) == 80
        result = arena.finish(battle)
        assert result["wager"] == battle["wager"]
        assert result["result"]["payout_minor"] == battle["wager"]["win_payout_minor"]
        assert "legacy" not in result["result"]


def test_referral_excludes_free_and_pays_after_three_paid_battles_once(arena):
    arena.service.register("tg:invited", "Invited", False, referral=arena.player()["referral_code"])
    arena.finish(arena.start(0, "tg:invited"), "tg:invited")
    assert arena.player()["balance_minor"] == 42000
    for number in range(4):
        battle = arena.start(pid="tg:invited")
        before = arena.player("tg:invited")["balance_minor"]
        result = arena.finish(battle, "tg:invited")["result"]
        assert arena.player("tg:invited")["balance_minor"] == before+result["payout_minor"]+(15000 if number == 2 else 0)
        assert arena.player()["balance_minor"] == (57000 if number >= 2 else 42000)


def test_live_bot_odds_follow_only_accepted_taps(arena):
    battle = arena.start()
    base = battle['current_win_probability']
    assert base == float(rules.bot_probability(battle['you']['power'], battle['opponent']['power'], 0))
    assert battle['win_probability'] is None
    assert arena.tap(battle, 90) == 0  # Preparation cannot improve the odds.
    assert arena.state()['battle']['current_win_probability'] == base
    arena.now = battle['starts_at']
    key = str(uuid4())
    assert arena.tap(battle, 45, key=key) == 45
    halfway = arena.state()['battle']['current_win_probability']
    assert halfway > base
    arena.tap(battle, 45, key=key)
    assert arena.state()['battle']['current_win_probability'] == halfway
    assert arena.tap(battle, 90) == 45
    current = arena.state()['battle']['current_win_probability']
    assert current == float(rules.bot_probability(battle['you']['power'], battle['opponent']['power'], 90))
    finished = arena.finish(battle)
    assert finished['win_probability'] == current == finished['current_win_probability']


def test_live_pvp_odds_are_complementary_and_respond_to_opponent(arena):
    arena.join('tg:a')
    arena.join('tg:b')
    battle = arena.state()['battle']
    arena.now = battle['starts_at']
    arena.tap(battle, 90)
    higher = arena.state()['battle']['current_win_probability']
    assert higher > .5
    assert higher + arena.state('tg:b')['battle']['current_win_probability'] == 1
    arena.tap(battle, 90, 'tg:b')
    assert arena.state()['battle']['current_win_probability'] == .5


def test_v3_battle_survives_v4_with_original_power_taps_prize_and_odds(arena):
    from roosters import rules_v3
    with patch('roosters.service.rules', rules_v3):
        arena.cmd('gear/upgrade', {'slot': 'helmet'})
        battle = arena.start()
    assert battle['rules_version'] == 'v3'
    restored = arena.restart().state('tg:a')['battle']
    assert restored['tap_cap'] == 80
    assert restored['you'] == battle['you']
    assert restored['wager'] == battle['wager']
    arena.now = battle['starts_at']
    assert arena.tap(battle, 90) == 80
    assert arena.tap(battle, 1) == 0
    finished = arena.finish(battle)
    expected = float(rules_v3.bot_probability(battle['you']['power'], battle['opponent']['power'], 80))
    assert finished['win_probability'] == expected
    assert finished['result']['payout_minor'] == battle['wager']['win_payout_minor']
    assert arena.start()['rules_version'] == 'v4'


def test_v4_migration_rebuilds_power_cache_without_changing_wallet_or_battle(arena):
    from roosters import rules_v3
    with arena.db.transaction() as db:
        db.execute("UPDATE players SET breed_id='copper',gear=? WHERE id='tg:a'", ('{"helmet":1,"armor":2,"sword":3}',))
    with patch('roosters.service.rules', rules_v3):
        battle = arena.start()
    before = arena.player()['balance_minor']
    with arena.db.transaction() as db:
        db.execute("DELETE FROM schema_migrations WHERE version='004_breed_multipliers.sql'")
        db.execute("UPDATE players SET power_cache=172 WHERE id='tg:a'")
    arena.db.migrate()
    with arena.db.transaction() as db:
        assert db.execute("SELECT power_cache FROM players WHERE id='tg:a'").fetchone()[0] == 182
    assert arena.player()['balance_minor'] == before
    assert arena.state()['battle'] == battle
    arena.db.migrate()
    assert arena.player()['balance_minor'] == before
