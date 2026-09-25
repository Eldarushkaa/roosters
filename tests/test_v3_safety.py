"""Transaction failures and competing commands against the real v3 service.

Fixtures own their SQLite files. Failure injection is deliberately placed after
an earlier write, proving rollback across the wallet and game boundaries.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from roosters import rules, wallet
from roosters.errors import GameError
from roosters.service import GameService
from roosters.storage import Database


@pytest.fixture
def game(tmp_path):
    database = Database(tmp_path / "v3-safety.sqlite3")
    database.migrate()
    clock = {"now": 1_800_000_000.0}
    draw = Mock(return_value=0.25)
    service = GameService(database, clock=lambda: clock["now"], random_float=draw)
    service.register("tg:101", "First", False)
    service.register("tg:202", "Second", False)
    return SimpleNamespace(database=database, service=service, clock=clock, draw=draw,
                           a="tg:101", b="tg:202")


def command(game, player, operation, body=None, key=None):
    return game.service.command(player, key or str(uuid4()), operation, body or {})


def rows(game, table):
    with closing(game.database.connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")]


def snapshot(game):
    return {name: rows(game, name) for name in ("players", "coin_ledger", "battles", "queue", "commands", "ledger")}


def bot_request(game, player, stake):
    power = game.service.state(player)["player"]["power"]
    return {"mode": "bot", "stake_minor": stake, "expected_power": power}


def test_insufficient_bot_stake_does_not_use_free_battle_or_persist_an_opponent(game):
    with game.database.transaction() as db:
        wallet.change(db, game.a, -rules.WELCOME_MINOR, "fixture", "empty-wallet", game.clock["now"])
    body = bot_request(game, game.a, rules.STAKES_MINOR[0])
    before = snapshot(game)
    key = str(uuid4())
    with pytest.raises(GameError) as caught:
        command(game, game.a, "battle/start", body, key)
    assert caught.value.code == "insufficient_funds"
    assert snapshot(game) == before
    assert rows(game, "battles") == []  # No draw/opponent to recover or reroll.
    assert game.service.state(game.a)["economy"]["first_free_battle_available"]

    free = command(game, game.a, "battle/start", bot_request(game, game.a, 0))
    assert free["state"]["battle"]["wager"]["stake_minor"] == 0
    assert free["state"]["player"]["balance_minor"] == 0
    assert not free["state"]["economy"]["first_free_battle_available"]


def test_second_pvp_debit_failure_rolls_back_first_debit_and_retry_creates_one_match(game, monkeypatch):
    stake = rules.STAKES_MINOR[0]
    command(game, game.a, "queue/join", {"stake_minor": stake})
    before = snapshot(game)
    original_change = wallet.change
    calls = []

    def fail_second_debit(db, player, delta_minor, reason, reference, now):
        if reason == "battle_entry":
            calls.append(player)
            if player == game.b:
                raise GameError("insufficient_funds", "Injected second debit failure", 409)
        return original_change(db, player, delta_minor, reason, reference, now)

    key = str(uuid4())
    with monkeypatch.context() as patcher:
        patcher.setattr(wallet, "change", fail_second_debit)
        with pytest.raises(GameError) as caught:
            command(game, game.b, "queue/join", {"stake_minor": stake}, key)
    assert caught.value.code == "insufficient_funds"
    assert calls == [game.a, game.b]
    assert snapshot(game) == before

    created = command(game, game.b, "queue/join", {"stake_minor": stake}, key)
    committed = snapshot(game)
    replay = command(game, game.b, "queue/join", {"stake_minor": stake}, key)
    assert replay["result"] == created["result"]
    assert snapshot(game) == committed
    assert len(rows(game, "battles")) == 1
    assert rows(game, "queue") == []
    entries = [r for r in rows(game, "coin_ledger") if r["reason"] == "battle_entry"]
    assert len(entries) == 2
    assert {r["reference"] for r in entries} == {created["result"]["battle_id"]}
    assert all(r["delta_minor"] == -stake for r in entries)
    assert all(p["balance_minor"] == rules.WELCOME_MINOR - stake for p in rows(game, "players"))


def test_failure_after_both_pvp_debits_does_not_leave_charges_or_change_queue(game, monkeypatch):
    stake = rules.STAKES_MINOR[0]
    command(game, game.a, "queue/join", {"stake_minor": stake})
    before = snapshot(game)

    def fail_draw():
        raise RuntimeError("Injected failure after both debits")

    monkeypatch.setattr(game.service, "random_float", fail_draw)
    with pytest.raises(RuntimeError, match="after both debits"):
        command(game, game.b, "queue/join", {"stake_minor": stake})
    assert snapshot(game) == before


def test_settlement_failure_after_first_reward_rolls_back_both_players_and_retries_once(game, monkeypatch):
    stake = rules.STAKES_MINOR[0]
    command(game, game.a, "queue/join", {"stake_minor": stake})
    match = command(game, game.b, "queue/join", {"stake_minor": stake})["state"]["battle"]
    game.clock["now"] = match["ends_at"] + 1
    before = snapshot(game)
    original_change = wallet.change
    first_credit_completed = []

    def fail_second_reward(db, player, delta_minor, reason, reference, now):
        if reason == "battle_reward" and player == game.b:
            raise RuntimeError("Injected second reward failure")
        original_change(db, player, delta_minor, reason, reference, now)
        if reason == "battle_reward":
            first_credit_completed.append(player)

    with monkeypatch.context() as patcher:
        patcher.setattr(wallet, "change", fail_second_reward)
        with pytest.raises(RuntimeError, match="second reward"):
            game.service.tick()
    assert first_credit_completed == [game.a]
    assert snapshot(game) == before

    game.service.tick()
    committed = snapshot(game)
    game.service.tick()
    assert snapshot(game) == committed
    assert rows(game, "battles")[0]["status"] == "finished"
    assert sum(p["pvp_wins"] for p in rows(game, "players")) == 1
    assert len([r for r in rows(game, "coin_ledger") if r["reason"] == "battle_reward"]) == 2
    assert sum(p["balance_minor"] for p in rows(game, "players")) == 2 * rules.WELCOME_MINOR - 2 * stake + rules.online_payout(stake)


@pytest.mark.parametrize("first", ["match", "cancel"])
def test_cancel_racing_match_is_atomic_in_both_lock_orders(game, monkeypatch, first):
    stake = rules.STAKES_MINOR[0]
    command(game, game.a, "queue/join", {"stake_minor": stake})
    locked = Event()
    release = Event()
    second_started = Event()
    original_execute = game.service._execute
    first_op = "queue/join" if first == "match" else "queue/leave"

    def hold_first_transaction(db, pid, operation, body, key, now):
        result = original_execute(db, pid, operation, body, key, now)
        if operation == first_op:
            locked.set()
            assert release.wait(5), "Test did not release the first transaction"
        return result

    monkeypatch.setattr(game.service, "_execute", hold_first_transaction)
    match_key, cancel_key = str(uuid4()), str(uuid4())

    def match():
        return command(game, game.b, "queue/join", {"stake_minor": stake}, match_key)

    def cancel():
        return command(game, game.a, "queue/leave", {}, cancel_key)

    first_call, second_call = (match, cancel) if first == "match" else (cancel, match)

    def start_second():
        second_started.set()
        return second_call()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first_call)
        try:
            assert locked.wait(5)
            second_future = pool.submit(start_second)
            assert second_started.wait(5)
        finally:
            release.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    if first == "match":
        assert len(rows(game, "battles")) == 1
        assert rows(game, "queue") == []
        assert all(p["balance_minor"] == rules.WELCOME_MINOR - stake for p in rows(game, "players"))
        assert all(p["first_battle_used"] == 1 for p in rows(game, "players"))
        assert game.service.state(game.a)["battle"]["status"] == "active"
    else:
        assert rows(game, "battles") == []
        assert [q["player_id"] for q in rows(game, "queue")] == [game.b]
        assert all(p["balance_minor"] == rules.WELCOME_MINOR for p in rows(game, "players"))
        assert all(p["first_battle_used"] == 0 for p in rows(game, "players"))
    # Lost responses in either request do not change the chosen serialization.
    before = snapshot(game)
    match()
    cancel()
    assert snapshot(game) == before


def test_concurrent_free_starts_create_one_grant_and_no_second_draw(game):
    body = bot_request(game, game.a, 0)

    def start(_):
        try:
            return command(game, game.a, "battle/start", body)
        except GameError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, range(2)))
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, GameError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code == "player_busy"
    assert len(rows(game, "battles")) == 1
    assert game.draw.call_count == 2  # One opponent roll and one outcome roll.
    assert len([r for r in rows(game, "coin_ledger") if r["reason"] == "battle_entry"]) == 1
    battle = successes[0]["state"]["battle"]
    game.clock["now"] = battle["ends_at"] + 1
    game.service.tick()
    game.service.tick()
    assert game.service.state(game.a)["player"]["balance_minor"] == rules.WELCOME_MINOR + rules.FREE_REWARD_MINOR
    with pytest.raises(GameError) as caught:
        command(game, game.a, "battle/start", bot_request(game, game.a, 0))
    assert caught.value.code == "free_battle_used"


def test_insufficient_waiting_player_is_never_debited_or_silently_matched(game):
    stake = rules.STAKES_MINOR[0]
    command(game, game.a, "queue/join", {"stake_minor": stake})
    # Simulate an independent account debit before another player joins.
    with game.database.transaction() as db:
        wallet.change(db, game.a, -rules.WELCOME_MINOR, "fixture", "external-debit", game.clock["now"])
    joined = command(game, game.b, "queue/join", {"stake_minor": stake})
    assert joined["result"] == {"matched": False}
    assert rows(game, "battles") == []
    assert len(rows(game, "queue")) == 2
    assert not [r for r in rows(game, "coin_ledger") if r["reason"] == "battle_entry"]
