"""Game integration: temporary databases, deterministic time, actual transactions.

Retries, concurrent tabs, partial failures and historical settlement are tested
without a live wallet, Telegram account, network request or wall-clock sleep.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from unittest.mock import patch
from uuid import uuid4

import pytest

from roosters import rules, rules_v1, rules_v2, rules_v4, rules_v5, rules_v6, rules_v7, wallet
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
    assert battle["tap_cap"] == 90 and battle["rules_version"] == "v8"
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
    assert battle["wager"] == {"stake_minor": 2500, "win_payout_minor": 6000}
    assert arena.player()["balance_minor"] == arena.player("tg:b")["balance_minor"] == 39500
    arena.cmd("queue/leave")  # Cancel after the match cannot refund a committed stake.
    assert arena.player()["balance_minor"] == 39500
    a = arena.finish(battle)
    b = arena.state("tg:b")["battle"]
    assert a["result"]["won"] != b["result"]["won"]
    assert a["result"]["payout_minor"]+b["result"]["payout_minor"] == 6000
    assert arena.player()["balance_minor"]+arena.player("tg:b")["balance_minor"] == 85000
    assert arena.player()["pvp_wins"]+arena.player("tg:b")["pvp_wins"] == 1
    assert arena.service.leaderboard("tg:a")["pvp_wins"][0]["id"] == "tg:a"


def test_dev_and_telegram_accounts_never_match(arena):
    arena.join(stake=1000)
    arena.service.register("dev:1", "Dev", True)
    arena.join("dev:1", 2500)
    for pid in ("tg:a", "dev:1"):
        assert arena.state(pid)["battle"] is None
        assert arena.state(pid)["queue"] is not None


@pytest.mark.parametrize("stake_a", rules.STAKES_MINOR)
@pytest.mark.parametrize("stake_b", rules.STAKES_MINOR)
@pytest.mark.parametrize("draw,winner", [(0.05, "tg:a"), (0.95, "tg:b")])
def test_online_any_power_and_stakes_keep_personal_quotes_across_restart_and_replay(arena, stake_a, stake_b, draw, winner):
    # The power gap is well outside the old 35% matchmaking range.
    with arena.db.transaction() as db:
        db.execute("UPDATE players SET breed_id='ember',xp=4500,gear=? WHERE id='tg:b'",
                   ('{"helmet":10,"armor":10,"sword":10}',))
    arena.service = arena.restart(draw)
    before = {pid: arena.player(pid)["balance_minor"] for pid in ("tg:a", "tg:b")}
    assert arena.join(stake=stake_a)["result"] == {"matched": False}
    key = str(uuid4())
    matched = arena.cmd("queue/join", {"stake_minor": stake_b}, "tg:b", key)
    assert matched["result"]["matched"]
    replay = arena.cmd("queue/join", {"stake_minor": stake_b}, "tg:b", key, service=arena.restart())
    assert replay["result"] == matched["result"]
    battle = arena.state()["battle"]
    assert battle["you"]["power"] == 100 and battle["opponent"]["power"] == 731
    assert battle["current_win_probability"] == float(rules.win_probability(100, 731, 0, 0))
    for pid, stake in (("tg:a", stake_a), ("tg:b", stake_b)):
        restored = arena.restart().state(pid)
        assert restored["queue"] is None
        assert restored["player"]["balance_minor"] == before[pid] - stake
        assert restored["battle"]["wager"] == {"stake_minor": stake, "win_payout_minor": rules.online_payout(stake, rules.win_probability(
            restored["battle"]["you"]["power"], restored["battle"]["opponent"]["power"], 0, 0))}
    arena.finish(battle)
    arena.restart().tick()
    for pid, stake in (("tg:a", stake_a), ("tg:b", stake_b)):
        state = arena.restart().state(pid)
        result = state["battle"]["result"]
        payout = rules.online_payout(stake, rules.win_probability(
            state["battle"]["you"]["power"], state["battle"]["opponent"]["power"], 0, 0)) if pid == winner else 0
        assert result["won"] == (pid == winner)
        assert result["payout_minor"] == payout and result["net_minor"] == payout - stake
        assert state["player"]["balance_minor"] == before[pid] - stake + payout
        assert state["history"][0]["wager"] == state["battle"]["wager"]
        assert state["player"]["battles"] == 1
    with arena.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason='battle_entry'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason='battle_reward'").fetchone()[0] == 2


def test_online_quotes_are_server_generated_for_every_allowed_stake(arena):
    assert arena.state()["economy"]["online_quotes"] == [
        {"stake_minor": stake, "win_payout_minor": 12 * stake // 5} for stake in rules.STAKES_MINOR]


@pytest.mark.parametrize("queued_stake,balance,joining_stake,matched", [
    (1000, 1000, 10000, True), (10000, 2500, 1000, False),
])
def test_candidate_affordability_uses_candidate_own_stake(arena, queued_stake, balance, joining_stake, matched):
    arena.join(stake=queued_stake)
    with arena.db.transaction() as db:
        wallet.change(db, "tg:a", balance - 42000, "test_spend", "affordability", arena.now)
    assert arena.join("tg:b", joining_stake)["result"]["matched"] is matched
    if not matched:
        assert arena.player()["balance_minor"] == balance
        assert arena.player("tg:b")["balance_minor"] == 42000


def test_online_second_debit_failure_rolls_back_both_stakes_and_preserves_queue(arena):
    arena.join(stake=1000)
    original = wallet.change

    def fail_second(db, pid, delta, reason, reference, now):
        if pid == "tg:b" and reason == "battle_entry":
            raise RuntimeError("simulated second wallet failure")
        return original(db, pid, delta, reason, reference, now)

    key = str(uuid4())
    with patch("roosters.service.wallet.change", side_effect=fail_second), pytest.raises(RuntimeError):
        arena.cmd("queue/join", {"stake_minor": 10000}, "tg:b", key)
    assert arena.state()["queue"]["stake_minor"] == 1000
    for pid in ("tg:a", "tg:b"):
        assert arena.player(pid)["balance_minor"] == 42000
        assert arena.state(pid)["battle"] is None
    assert arena.cmd("queue/join", {"stake_minor": 10000}, "tg:b", key)["result"]["matched"]


def test_online_selects_oldest_eligible_player_without_stake_or_power_preference(arena):
    arena.join(stake=1000)
    arena.now += 46  # Still queued, but temporarily absent from live presence.
    arena.cmd("presence", pid="tg:b")
    assert not arena.join("tg:b", 10000)["result"]["matched"]
    arena.cmd("presence")
    arena.service.register("tg:c", "Third", False)
    with arena.db.transaction() as db:
        db.execute("UPDATE players SET breed_id='ember' WHERE id='tg:c'")
    matched = arena.join("tg:c", 10000)
    assert matched["result"]["matched"]
    assert matched["state"]["battle"]["opponent"]["name"] == "tg:a"
    assert arena.state("tg:b")["queue"] is not None


def test_competing_joins_can_claim_a_waiting_player_only_once(arena):
    arena.service.register("tg:c", "Third", False)
    arena.join(stake=1000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(arena.cmd, "queue/join", {"stake_minor": stake}, pid,
                               service=arena.restart()) for pid, stake in (("tg:b", 2500), ("tg:c", 10000))]
        results = [future.result() for future in futures]
    assert sorted(result["result"]["matched"] for result in results) == [False, True]
    for pid, stake in (("tg:a", 1000), ("tg:b", 2500), ("tg:c", 10000)):
        state = arena.state(pid)
        assert (state["battle"] is None) != (state["queue"] is None)
        assert state["player"]["balance_minor"] == 42000 - (stake if state["battle"] else 0)
    with arena.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM battles").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason='battle_entry'").fetchone()[0] == 2


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
    invalid = [("battle/start", dict(arena.payload(), stake_minor=v)) for v in (True, -1, 1000.0, 999, 10000000)]
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


def test_referral_signup_concurrent_registration_and_relogin_credit_inviter_once(arena):
    code = arena.player()["referral_code"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        registered = list(pool.map(
            lambda service: service.register("tg:invited", "Invited", False, referral=code),
            [arena.restart(), arena.restart()]))
    assert all(state["player"]["balance_minor"] == 42000 for state in registered)
    arena.restart().register("tg:invited", "Renamed", False, referral=arena.player("tg:b")["referral_code"])
    assert arena.player()["balance_minor"] == 72000
    assert arena.player("tg:b")["balance_minor"] == 42000
    assert arena.player("tg:invited")["balance_minor"] == 42000
    assert registered[0]["catalog"]["referral"] == {
        "signup_reward_minor": 30000, "battle_reward_minor": 30000, "battles_required": 3}
    with closing(arena.db.connect()) as db:
        assert db.execute("SELECT inviter_id FROM players WHERE id='tg:invited'").fetchone()[0] == "tg:a"
        receipts = db.execute("SELECT player_id,delta_minor,reason,reference FROM coin_ledger WHERE reason LIKE 'referral%'").fetchall()
        assert [tuple(row) for row in receipts] == [("tg:a", 30000, "referral_signup", "tg:invited")]


@pytest.mark.parametrize("draw", [0.0, .99])
def test_referral_third_completed_battle_counts_free_bot_and_pvp_once_on_win_or_loss(arena, draw):
    arena.service = arena.restart(draw)
    arena.service.register("tg:invited", "Invited", False, referral=arena.player()["referral_code"])
    for stake in (0, 1000):
        battle = arena.start(stake, "tg:invited")
        before = arena.player("tg:invited")["balance_minor"]
        result = arena.finish(battle, "tg:invited")["result"]
        assert result["won"] == (draw == 0)
        assert arena.player("tg:invited")["balance_minor"] == before + result["payout_minor"]
        assert arena.player()["balance_minor"] == 72000

    arena.join("tg:invited")
    arena.join("tg:b")
    battle = arena.state("tg:invited")["battle"]
    before = arena.player("tg:invited")["balance_minor"]
    arena.now = battle["ends_at"] - .01
    assert arena.player()["balance_minor"] == 72000
    arena.now = battle["ends_at"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda service: service.tick(), [arena.restart(), arena.restart()]))
    result = arena.state("tg:invited")["battle"]["result"]
    assert result["won"] == (draw == 0)
    assert arena.player("tg:invited")["balance_minor"] == before + result["payout_minor"]
    assert arena.player("tg:invited")["battles"] == 3
    assert arena.player()["balance_minor"] == 102000
    arena.finish(arena.start(pid="tg:invited"), "tg:invited")
    arena.restart().tick()
    assert arena.player()["balance_minor"] == 102000
    with closing(arena.db.connect()) as db:
        assert db.execute("SELECT referral_paid FROM players WHERE id='tg:invited'").fetchone()[0] == 1
        receipts = db.execute("SELECT player_id,delta_minor,reason FROM coin_ledger WHERE reason LIKE 'referral%' ORDER BY reason").fetchall()
        assert [tuple(row) for row in receipts] == [("tg:a", 30000, "referral"), ("tg:a", 30000, "referral_signup")]


@pytest.mark.parametrize("case", ["dev_invitee", "dev_inviter", "invalid", "self", "existing"])
def test_referral_rejects_ineligible_invites_without_any_rewards(arena, case):
    arena.service.register("dev:inviter", "Dev", True)
    pid = "tg:a" if case == "self" else "tg:b" if case == "existing" else "dev:invited" if case == "dev_invitee" else "tg:invited"
    code = "ref_unknown" if case == "invalid" else arena.player("dev:inviter" if case == "dev_inviter" else "tg:a")["referral_code"]
    arena.service.register(pid, "Invited", case == "dev_invitee", referral=code)
    for _ in range(3):
        arena.finish(arena.start(pid=pid), pid)
    with closing(arena.db.connect()) as db:
        row = db.execute("SELECT inviter_id,referral_paid FROM players WHERE id=?", (pid,)).fetchone()
        assert tuple(row) == (None, 0)
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason LIKE 'referral%'").fetchone()[0] == 0


@pytest.mark.parametrize("already_paid", [False, True])
def test_referral_existing_account_gets_no_signup_backfill_and_preserves_paid_history(arena, already_paid):
    arena.service.register("tg:invited", "Invited", False)
    with arena.db.transaction() as db:
        db.execute("UPDATE players SET inviter_id='tg:a',battles=?,ranked_battles=?,referral_paid=? WHERE id='tg:invited'",
                   (3 if already_paid else 2, 3 if already_paid else 2, int(already_paid)))
        if already_paid:
            for pid in ("tg:a", "tg:invited"):
                wallet.change(db, pid, 15000, "referral", "tg:invited", arena.now)
    arena.restart().register("tg:invited", "Returning", False, referral=arena.player()["referral_code"])
    assert arena.player()["balance_minor"] == (57000 if already_paid else 42000)
    battle = arena.start(pid="tg:invited")
    before = arena.player("tg:invited")["balance_minor"]
    result = arena.finish(battle, "tg:invited")["result"]
    assert arena.player("tg:invited")["balance_minor"] == before + result["payout_minor"]
    assert arena.player()["balance_minor"] == (57000 if already_paid else 72000)
    if already_paid:
        historical = next(event for event in arena.state("tg:invited")["reward_events"] if event["reason"] == "referral")
        assert historical["amount_minor"] == 15000 and historical["friend_name"] is None
    with closing(arena.db.connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason='referral_signup'").fetchone()[0] == 0
        assert db.execute("SELECT delta_minor FROM coin_ledger WHERE player_id='tg:a' AND reason='referral'").fetchone()[0] == (15000 if already_paid else 30000)


def test_referral_signup_credit_failure_rolls_back_registration_and_can_retry(arena):
    change = wallet.change
    code = arena.player()["referral_code"]

    def fail_after_credit(db, pid, amount, reason, reference, now):
        change(db, pid, amount, reason, reference, now)
        if reason == "referral_signup":
            raise RuntimeError("injected signup failure")

    with patch("roosters.service.wallet.change", side_effect=fail_after_credit), pytest.raises(RuntimeError, match="injected signup failure"):
        arena.service.register("tg:invited", "Invited", False, referral=code)
    assert arena.player()["balance_minor"] == 42000
    with closing(arena.db.connect()) as db:
        assert db.execute("SELECT 1 FROM players WHERE id='tg:invited'").fetchone() is None
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reference='tg:invited'").fetchone()[0] == 0
    arena.restart().register("tg:invited", "Invited", False, referral=code)
    assert arena.player()["balance_minor"] == 72000
    assert arena.player("tg:invited")["balance_minor"] == 42000


def test_referral_milestone_credit_failure_rolls_back_entire_settlement(arena):
    arena.service.register("tg:invited", "Invited", False, referral=arena.player()["referral_code"])
    for _ in range(2):
        arena.finish(arena.start(pid="tg:invited"), "tg:invited")
    battle = arena.start(pid="tg:invited")
    before = arena.player("tg:invited")["balance_minor"]
    change = wallet.change

    def fail_after_credit(db, pid, amount, reason, reference, now):
        change(db, pid, amount, reason, reference, now)
        if reason == "referral":
            raise RuntimeError("injected milestone failure")

    arena.now = battle["ends_at"]
    with patch("roosters.service.wallet.change", side_effect=fail_after_credit), pytest.raises(RuntimeError, match="injected milestone failure"):
        arena.service.tick()
    # Read directly: state() would retry the due settlement before inspecting rollback.
    with closing(arena.db.connect()) as db:
        row = db.execute("SELECT balance_minor,battles,referral_paid FROM players WHERE id='tg:invited'").fetchone()
        assert tuple(row) == (before, 2, 0)
        assert db.execute("SELECT balance_minor FROM players WHERE id='tg:a'").fetchone()[0] == 72000
        assert db.execute("SELECT status FROM battles WHERE id=?", (battle["id"],)).fetchone()[0] == "active"
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason='referral' OR (reason='battle_reward' AND reference=?)", (battle["id"],)).fetchone()[0] == 0
    arena.restart().tick()
    assert arena.player()["balance_minor"] == 102000
    assert arena.player("tg:invited")["battles"] == 3


def test_reward_events_show_committed_payouts_for_current_player_without_replay_duplicates(arena):
    assert arena.state()["reward_events"] == []
    arena.cmd("claim/daily")
    assert arena.state()["reward_events"] == []
    arena.service.register("tg:invited", "Аня <friend>", False, referral=arena.player()["referral_code"])
    signup_at = arena.now
    key, payload = str(uuid4()), arena.payload()
    battle = arena.cmd("battle/start", payload, key=key)["state"]["battle"]
    assert [event["reason"] for event in arena.state()["reward_events"]] == ["referral_signup"]
    result = arena.finish(battle)["result"]
    assert result["payout_minor"] > 0
    battle_at = arena.now
    for _ in range(3):
        arena.finish(arena.start(pid="tg:invited", service=arena.restart(.99)), "tg:invited")
    events = arena.state()["reward_events"]
    assert [{key: value for key, value in event.items() if key != "id"} for event in events] == [
        {"reason": "referral", "amount_minor": 30000, "created_at": arena.now, "friend_name": "Аня <friend>"},
        {"reason": "battle_reward", "amount_minor": result["payout_minor"], "created_at": battle_at, "friend_name": None},
        {"reason": "referral_signup", "amount_minor": 30000, "created_at": signup_at, "friend_name": "Аня <friend>"},
    ]
    assert [event["id"] for event in events] == sorted({event["id"] for event in events}, reverse=True)
    # The journal reports gross credited payout, while net also subtracts the entry stake.
    assert events[1]["amount_minor"] != result["net_minor"]
    invited_events = arena.state("tg:invited")["reward_events"]
    assert len(invited_events) == 3
    assert all(event["reason"] == "battle_reward" and event["amount_minor"] == 0
               and event["friend_name"] is None for event in invited_events)
    assert arena.state("tg:b")["reward_events"] == []
    arena.restart().tick()
    replay = arena.cmd("battle/start", payload, key=key, service=arena.restart())
    assert replay["state"]["reward_events"] == events


def test_reward_events_limit_applies_to_current_players_latest_twenty_rewards(arena):
    # Equal timestamps exercise stable newest-first ordering by journal receipt.
    with arena.db.transaction() as db:
        for number in range(25):
            reference = "historical-battle-" + str(number)
            wallet.change(db, "tg:a", 10000 + number, "battle_reward", reference, arena.now)
            wallet.change(db, "tg:b", 90000 + number, "battle_reward", reference, arena.now)
            wallet.change(db, "tg:a", 100, "daily", "historical-day-" + str(number), arena.now)
    before = arena.player()["balance_minor"]
    events = arena.state()["reward_events"]
    assert len(events) == 20
    assert [event["amount_minor"] for event in events] == list(range(10024, 10004, -1))
    assert all(event["reason"] == "battle_reward" for event in events)
    assert len({event["id"] for event in events}) == 20
    assert arena.player()["balance_minor"] == before


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


def test_v3_battle_survives_current_release_with_original_power_taps_prize_and_odds(arena):
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
    assert arena.start()['rules_version'] == 'v8'


@pytest.mark.parametrize("stake", [1000, 1001, 12345, 42000])
def test_custom_bot_stakes_include_full_balance_and_replay_exactly(arena, stake):
    quote = arena.service.battle_quote("tg:a", stake)
    key = str(uuid4())
    body = {"mode": "bot", "stake_minor": stake, "expected_power": quote["power"]}
    first = arena.cmd("battle/start", body, key=key)
    replay = arena.cmd("battle/start", body, key=key, service=arena.restart())
    assert replay["result"] == first["result"]
    assert replay["state"]["player"]["balance_minor"] == 42000 - stake
    battle = first["state"]["battle"]
    prize = stake * 9 * (100 + battle["opponent"]["power"]) // 1000
    assert battle["wager"] == {"stake_minor": stake, "win_payout_minor": prize}
    assert quote["bot"]["min_payout_minor"] <= prize <= quote["bot"]["max_payout_minor"]
    finished = arena.finish(battle)
    assert finished["result"]["payout_minor"] == prize
    assert arena.player()["balance_minor"] == 42000 - stake + prize


def test_online_custom_cent_stakes_keep_personal_rounded_prizes(arena):
    arena.join(stake=1001)
    arena.join("tg:b", 12345)
    a, b = arena.state()["battle"], arena.state("tg:b")["battle"]
    assert a["wager"] == {"stake_minor": 1001, "win_payout_minor": 2402}
    assert b["wager"] == {"stake_minor": 12345, "win_payout_minor": 29628}
    assert arena.finish(a)["result"]["net_minor"] == 1401
    assert arena.state("tg:b")["battle"]["result"]["net_minor"] == -12345


def test_stake_limits_follow_balance_including_less_than_minimum(arena):
    assert arena.state()["economy"]["stake_limits"] == {"min_minor": 1000, "max_minor": 42000, "step_minor": 1}
    with arena.db.transaction() as db:
        wallet.change(db, "tg:a", 999 - 42000, "test_spend", "below_minimum", arena.now)
    assert arena.state()["economy"]["stake_limits"] == {"min_minor": 1000, "max_minor": 999, "step_minor": 1}
    with pytest.raises(GameError, match="Недостаточно"):
        arena.service.battle_quote("tg:a", 1000)
    assert arena.start(0)["wager"]["stake_minor"] == 0


def test_technical_stake_limit_keeps_quotes_exact_and_respects_sqlite_headroom(arena):
    with arena.db.transaction() as db:
        wallet.change(db, "tg:a", rules.MAX_STAKE_MINOR + 1000 - 42000, "test_grant", "large", arena.now)
    assert arena.state()["economy"]["stake_limits"]["max_minor"] == rules.MAX_STAKE_MINOR
    quote = arena.service.battle_quote("tg:a", rules.MAX_STAKE_MINOR)
    assert quote["bot"]["max_payout_minor"] <= 2**53 - 1
    for op in (lambda: arena.start(rules.MAX_STAKE_MINOR + 1),
               lambda: arena.service.battle_quote("tg:a", rules.MAX_STAKE_MINOR + 1)):
        with pytest.raises(GameError) as error:
            op()
        assert error.value.code == "invalid_stake"
    with arena.db.transaction() as db:
        current = db.execute("SELECT balance_minor FROM players WHERE id='tg:a'").fetchone()[0]
        wallet.change(db, "tg:a", wallet.MAX_MINOR_UNITS - 11000 - current, "test_grant", "headroom", arena.now)
    assert arena.state()["economy"]["stake_limits"]["max_minor"] == 1000
    with pytest.raises(GameError) as error:
        arena.service.battle_quote("tg:a", 1001)
    assert error.value.code == "invalid_stake"


def test_quote_does_not_run_maintenance_change_free_eligibility_or_write_any_table(arena):
    def snapshot():
        with closing(arena.db.connect()) as db:
            return list(db.iterdump())

    before = snapshot()
    quote = arena.service.battle_quote("tg:a", 1001)
    assert snapshot() == before
    assert quote == {"rules_version": "v8", "power": 100, "stake_minor": 1001,
                     "bot": {"min_payout_minor": 1531, "max_payout_minor": 2162},
                     "online": {"win_payout_minor": 2402}}
    battle = arena.start()
    arena.now = battle["ends_at"] + 1
    before = snapshot()
    arena.service.battle_quote("tg:a", 1001)
    assert snapshot() == before  # Due battle remains un-settled by quotation.


@pytest.mark.parametrize("stake,code", [(999, "invalid_stake"), (0, "invalid_stake"),
                                       (True, "invalid_stake"), (1001.0, "invalid_stake"),
                                       ("1001", "invalid_stake"), (42001, "insufficient_funds")])
def test_quote_rejects_invalid_or_unfunded_custom_stakes_without_mutation(arena, stake, code):
    before = arena.player()
    with pytest.raises(GameError) as error:
        arena.service.battle_quote("tg:a", stake)
    assert error.value.code == code
    assert arena.player() == before


def test_custom_quote_does_not_reserve_funds_or_authorize_stale_power(arena):
    quote = arena.service.battle_quote("tg:a", 42000)
    arena.cmd("gear/upgrade", {"slot": "sword"})
    with pytest.raises(GameError) as stale:
        arena.cmd("battle/start", {"mode": "bot", "stake_minor": 42000, "expected_power": quote["power"]})
    assert stale.value.code == "power_changed"
    for op in (lambda: arena.start(42000), lambda: arena.join(stake=42000)):
        with pytest.raises(GameError) as funds:
            op()
        assert funds.value.code == "insufficient_funds"


def test_concurrent_custom_all_in_requests_charge_only_one_stake(arena):
    body = arena.payload(42000)

    def submit():
        try:
            return arena.cmd("battle/start", body, service=arena.restart())
        except GameError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(submit), pool.submit(submit)]]
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, GameError) for result in results) == 1
    assert arena.player()["balance_minor"] == 0
    with arena.db.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM coin_ledger WHERE reason='battle_entry'").fetchone()[0] == 1


@pytest.mark.parametrize("engine, stake", [(rules_v5, 1000), (rules_v6, 1001), (rules_v7, 1001)])
def test_historical_battle_survives_release_with_saved_rules(arena, engine, stake):
    with patch("roosters.service.rules", engine):
        battle = arena.start(stake=stake)
    arena.service = arena.restart()
    assert arena.state()["battle"] == battle
    arena.now = battle["starts_at"]
    assert arena.tap(battle, 90) == 90
    finished = arena.finish(battle)
    assert finished["rules_version"] == engine.RULES_VERSION
    assert finished["wager"] == battle["wager"]
    assert finished["win_probability"] == float(engine.bot_probability(100, battle["opponent"]["power"], 90))


@pytest.mark.parametrize("engine, won", [(rules_v6, False), (rules, True)])
def test_training_settlement_uses_saved_rtp_after_restart(arena, engine, won):
    # Equal powers and a draw between the old 61.11% and new 66.67% odds.
    draws = iter((30 / 71, 0.64))
    arena.service.random_float = lambda: next(draws)
    with patch("roosters.service.rules", engine):
        battle = arena.start()
    assert battle["opponent"]["power"] == 100
    arena.service = arena.restart()
    arena.now = battle["starts_at"]
    assert arena.tap(battle, 90) == 90
    assert arena.tap(battle, 1) == 0
    expected = float(engine.bot_probability(100, 100, 90))
    assert arena.state()["battle"]["current_win_probability"] == expected
    finished = arena.finish(battle)
    assert finished["win_probability"] == expected
    assert finished["result"]["won"] is won
    assert finished["result"]["payout_minor"] == (1800 if won else 0)
    assert arena.player()["balance_minor"] == (42800 if won else 41000)
    arena.restart().tick()
    assert arena.state()["battle"] == finished


def test_v4_bot_battle_keeps_original_odds_prize_and_cap_after_restart(arena):
    with patch('roosters.service.rules', rules_v4):
        battle = arena.start()
    assert battle['rules_version'] == 'v4'
    arena.service = arena.restart()
    assert arena.state()['battle']['wager'] == battle['wager']
    arena.now = battle['starts_at']
    assert arena.tap(battle, 90) == 90
    assert arena.tap(battle, 1) == 0
    old_probability = float(rules_v4.bot_probability(battle['you']['power'], battle['opponent']['power'], 90))
    assert arena.state()['battle']['current_win_probability'] == old_probability
    finished = arena.finish(battle)
    assert finished['win_probability'] == old_probability
    assert finished['result']['payout_minor'] == battle['wager']['win_payout_minor']
    assert old_probability < float(rules.bot_probability(battle['you']['power'], battle['opponent']['power'], 90))


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


@pytest.mark.parametrize("engine", [rules_v6, rules_v7])
def test_previous_online_battles_keep_fixed_payouts_and_gain_tap_analysis(arena, engine):
    with patch("roosters.service.rules", engine):
        arena.join(stake=1001)
        arena.join("tg:b", 2500)
    battle = arena.state()["battle"]
    arena.service = arena.restart()
    arena.now = battle["starts_at"]
    arena.tap(battle, 90)
    finished = arena.finish(battle)
    assert finished["rules_version"] == engine.RULES_VERSION
    assert not finished["dynamic_payout"]
    assert finished["wager"]["win_payout_minor"] == 1901
    assert finished["result"]["payout_minor"] == 1901
    assert finished["tap_analysis"]["you"]["taps"] == 90
    assert finished["tap_analysis"]["you"]["initial_probability"] == 0.5
    assert finished["tap_analysis"]["you"]["final_probability"] == finished["win_probability"]


@pytest.mark.parametrize("taps_a,taps_b,prize_a,prize_b", [
    (0, 0, 2400, 6000), (90, 0, 2200, 6600), (0, 90, 2640, 5500), (90, 90, 2400, 6000),
])
def test_online_dynamic_payout_analysis_and_history_survive_restart(arena, taps_a, taps_b, prize_a, prize_b):
    arena.join(stake=1000)
    arena.join("tg:b", 2500)
    battle = arena.state()["battle"]
    assert battle["dynamic_payout"] and battle["tap_analysis"] is None
    arena.now = battle["starts_at"]
    if taps_a:
        key = str(uuid4())
        assert arena.tap(battle, taps_a, key=key) == taps_a
        assert arena.tap(battle, taps_a, key=key) == taps_a
    if taps_b:
        arena.tap(battle, taps_b, "tg:b")
    assert arena.state()["battle"]["wager"]["win_payout_minor"] == prize_a
    assert arena.state("tg:b")["battle"]["wager"]["win_payout_minor"] == prize_b
    arena.service = arena.restart()
    a = arena.finish(battle)
    b = arena.state("tg:b")["battle"]
    assert a["result"]["payout_minor"] == prize_a
    assert b["result"]["payout_minor"] == 0
    assert arena.player()["balance_minor"] == 41000 + prize_a
    assert arena.player("tg:b")["balance_minor"] == 39500
    assert a["tap_analysis"]["you"] == b["tap_analysis"]["opponent"]
    assert a["tap_analysis"]["opponent"] == b["tap_analysis"]["you"]
    assert a["tap_analysis"]["you"]["initial_probability"] == 0.5
    assert a["tap_analysis"]["you"]["taps"] == taps_a
    assert a["tap_analysis"]["opponent"]["taps"] == taps_b
    assert a["tap_analysis"]["you"]["change"] == pytest.approx(a["win_probability"] - 0.5)
    arena.restart().tick()
    arena.cmd("gear/upgrade", {"slot": "helmet"})
    assert arena.state()["history"][0] == a
    assert arena.restart().state("tg:b")["history"][0] == b


def test_old_queue_stake_exceeding_new_payout_bound_is_not_matched(arena):
    with arena.db.transaction() as db:
        db.execute("UPDATE players SET balance_minor=? WHERE id='tg:a'", (rules_v7.MAX_STAKE_MINOR,))
        db.execute("INSERT INTO queue VALUES (?,?,?,?,?,?)",
                   ("tg:a", 100, arena.now, arena.now + 45, arena.now + 120, rules_v7.MAX_STAKE_MINOR))
    assert not arena.join("tg:b")["result"]["matched"]
