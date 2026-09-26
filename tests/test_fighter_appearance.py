"""Persisted fighter appearance follows equipment without changing combat."""

import json
from unittest.mock import Mock, patch

import pytest

from roosters import rules
from roosters.service import GameService, encode
from test_service import Arena


@pytest.fixture
def arena(tmp_path):
    return Arena(tmp_path / "appearance.sqlite3")


def equip(arena, pid, breed_id, gear):
    # Disposable fixture funds; progression itself uses the actual commands.
    with arena.db.transaction() as db:
        db.execute("UPDATE players SET balance_minor=? WHERE id=?", (100_000_000, pid))
    arena.cmd("breed/buy", {"breed_id": breed_id}, pid)
    for slot, level in gear.items():
        for _ in range(level):
            arena.cmd("gear/upgrade", {"slot": slot}, pid)


def appearance(fighter):
    return {key: fighter[key] for key in ("breed_id", "gear")}


def test_bot_appearance_is_persisted_without_consuming_more_draws(arena):
    gear = {"helmet": 2, "armor": 5, "sword": 7}
    equip(arena, "tg:a", "storm", gear)
    player_power = arena.player()["power"]
    lower, upper = rules.bot_power_bounds(player_power)
    draws = Mock(side_effect=[0.6, 0.83])
    arena.service.random_float = draws
    copper = next(breed for breed in rules.BREEDS if breed["id"] == "copper")
    with patch("roosters.service.secrets.choice", side_effect=["Test bot", copper]) as choices:
        battle = arena.start(2500)
    assert draws.call_count == 2  # Opponent power, then outcome, unchanged.
    assert choices.call_count == 2  # Existing name and breed selections only.
    bot_power = lower + int(0.6 * (upper - lower + 1))
    assert battle["opponent"]["power"] == bot_power
    assert battle["you"]["gear"] == gear
    assert battle["you"]["breed_id"] == "storm"
    assert battle["opponent"]["breed_id"] == "copper"
    assert set(battle["opponent"]["gear"]) == {"helmet", "armor", "sword"}
    assert all(0 < level <= rules.MAX_GEAR_LEVEL for level in battle["opponent"]["gear"].values())
    assert battle["wager"]["win_payout_minor"] == rules.bot_payout(2500, player_power, bot_power)
    assert battle["current_win_probability"] == float(rules.bot_probability(player_power, bot_power, 0))
    with arena.db.transaction() as db:
        saved = db.execute("SELECT * FROM battles WHERE id=?", (battle["id"],)).fetchone()
        assert saved["draw"] == 0.83
        assert appearance(json.loads(saved["snapshot_a"])) == appearance(battle["you"])
        assert appearance(json.loads(saved["snapshot_b"])) == appearance(battle["opponent"])
    with patch.object(GameService, "_bot_gear", side_effect=AssertionError("Must not regenerate appearance")):
        assert arena.restart().state("tg:a")["battle"] == battle
        finished = arena.finish(battle)
    assert finished["result"]["won"] is False
    assert finished["result"]["payout_minor"] == 0
    arena.cmd("gear/upgrade", {"slot": "sword"})
    arena.cmd("breed/buy", {"breed_id": "ember"})
    state = arena.restart().state("tg:a")
    assert state["player"]["gear"]["sword"] == 8
    assert state["player"]["breed_id"] == "ember"
    for saved_battle in (state["battle"], state["history"][0]):
        assert appearance(saved_battle["you"]) == appearance(battle["you"])
        assert appearance(saved_battle["opponent"]) == appearance(battle["opponent"])


def test_online_snapshots_keep_each_players_exact_appearance_from_both_views(arena):
    gear_a = {"helmet": 1, "armor": 3, "sword": 5}
    gear_b = {"helmet": 6, "armor": 2, "sword": 4}
    equip(arena, "tg:a", "copper", gear_a)
    equip(arena, "tg:b", "storm", gear_b)
    arena.join("tg:a")
    arena.join("tg:b")
    battle = arena.state()["battle"]
    expected_a = {"breed_id": "copper", "gear": gear_a}
    expected_b = {"breed_id": "storm", "gear": gear_b}
    assert appearance(battle["you"]) == expected_a
    assert appearance(battle["opponent"]) == expected_b
    reverse = arena.restart().state("tg:b")["battle"]
    assert appearance(reverse["you"]) == expected_b
    assert appearance(reverse["opponent"]) == expected_a
    arena.finish(battle)
    for pid in ("tg:a", "tg:b"):
        arena.cmd("breed/buy", {"breed_id": "ember"}, pid)
        arena.cmd("gear/upgrade", {"slot": "helmet"}, pid)
    for pid, you, opponent in (("tg:a", expected_a, expected_b), ("tg:b", expected_b, expected_a)):
        state = arena.restart().state(pid)
        assert state["player"]["breed_id"] == "ember"
        assert state["player"]["gear"]["helmet"] == you["gear"]["helmet"] + 1
        for saved_battle in (state["battle"], state["history"][0]):
            assert appearance(saved_battle["you"]) == you
            assert appearance(saved_battle["opponent"]) == opponent


@pytest.mark.parametrize("mode", ["bot", "online"])
def test_existing_snapshots_without_gear_still_recover_and_settle(arena, mode):
    if mode == "online":
        arena.join("tg:a")
        arena.join("tg:b")
        battle = arena.state()["battle"]
    else:
        battle = arena.start()
    with arena.db.transaction() as db:
        saved = db.execute("SELECT snapshot_a,snapshot_b FROM battles WHERE id=?", (battle["id"],)).fetchone()
        snapshots = [json.loads(saved[key]) for key in ("snapshot_a", "snapshot_b")]
        for snapshot in snapshots:
            snapshot.pop("gear")
        db.execute("UPDATE battles SET snapshot_a=?,snapshot_b=? WHERE id=?",
                   (encode(snapshots[0]), encode(snapshots[1]), battle["id"]))
    recovered = arena.restart().state("tg:a")["battle"]
    assert "gear" not in recovered["you"] and "gear" not in recovered["opponent"]
    assert recovered["wager"] == battle["wager"]
    assert recovered["current_win_probability"] == battle["current_win_probability"]
    finished = arena.finish(battle)
    assert finished["status"] == "finished"
    assert finished["win_probability"] == battle["current_win_probability"]
    history = arena.restart().state("tg:a")["history"][0]
    assert history["result"] == finished["result"]
    assert "gear" not in history["you"] and "gear" not in history["opponent"]
