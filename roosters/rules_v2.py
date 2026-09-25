"""Pure, deterministic game rules for Rooster Arena.

This module has no database, HTTP, clock, or random-number dependencies. The
application service validates ownership and funds, freezes a battle snapshot,
draws its random value once, and commits rewards atomically. Functions here only
calculate values. Changing battle math requires a new rules version and a
settlement path for already persisted versions; see docs/GAME_DESIGN.md.
"""

from copy import deepcopy
from math import isqrt
from typing import Dict, Mapping, Optional


RULES_VERSION = "v2"
BATTLE_DURATION = 10
ENTRY_FEE = 3
WIN_MEDALS = 5
LOSE_MEDALS = 1
ENERGY_CAP = 120
ENERGY_REGEN_SECONDS = 30
PASSIVE_PER_MINUTE = 2
PASSIVE_CAP_SECONDS = 8 * 3600
DAILY_COINS = 120
DAILY_MEDALS = 5
MAX_GEAR_LEVEL = 10
TAP_CAP = 60
TAP_RATE = 6
TAP_BURST = 12
# One second of allowance leaves time for batched taps and network delivery.
TAP_INITIAL = 6
BOT_TAPS = 30
TAP_BONUS_CAP = 0.2

BREEDS = [
    {
        "id": "yard",
        "name": "Дворовый",
        "description": "Начинающий боец с характером. Большая карьера начинается во дворе.",
        "price": 0,
        "power": 100,
        "color": "#f3ad55",
    },
    {
        "id": "copper",
        "name": "Медный",
        "description": "Крепкий, упрямый и всегда готов защищать свой двор.",
        "price": 500,
        "power": 120,
        "color": "#d98754",
    },
    {
        "id": "storm",
        "name": "Грозовой",
        "description": "Взъерошенный чемпион. На арене после него летят перья.",
        "price": 1800,
        "power": 150,
        "color": "#77adf0",
    },
    {
        "id": "ember",
        "name": "Огненный",
        "description": "Редкая порода с огненным гребнем и несгибаемым характером.",
        "price": 6000,
        "power": 190,
        "color": "#fb7664",
    },
]

SLOTS = [
    {"id": "helmet", "name": "Шлем", "power_per_level": 6},
    {"id": "armor", "name": "Броня", "power_per_level": 8},
    {"id": "sword", "name": "Меч", "power_per_level": 10},
]

_BREED_POWER = {breed["id"]: breed["power"] for breed in BREEDS}
_SLOT_POWER = {slot["id"]: slot["power_per_level"] for slot in SLOTS}


def _integer(value: int, name: str, minimum: int = 0) -> int:
    """Reject bools and fractional values instead of silently changing rules."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("{} must be an integer >= {}".format(name, minimum))
    return value


def catalog() -> dict:
    """Return a detached API catalog; callers cannot mutate shared rule data."""
    return {
        "breeds": deepcopy(BREEDS),
        "slots": deepcopy(SLOTS),
        "battle": {
            "duration": BATTLE_DURATION,
            "entry_fee": ENTRY_FEE,
            "win_medals": WIN_MEDALS,
            "lose_medals": LOSE_MEDALS,
            "tap_bonus_cap": TAP_BONUS_CAP,
        },
    }


def progression(xp: int) -> Dict[str, int]:
    """Calculate level progress from lifetime XP, using exact integer arithmetic.

    Level 1 needs 100 XP, level 2 needs another 200, and so on. ``xp_to_next``
    is the whole threshold of the current level, suitable as a progress-bar
    maximum; the remaining XP is ``xp_to_next - xp_in_level``.
    """
    _integer(xp, "xp")
    # Reaching level L costs 100 * (1 + ... + L - 1) = 50 * L * (L - 1).
    level = (1 + isqrt(1 + 8 * (xp // 100))) // 2
    spent = 50 * level * (level - 1)
    return {"level": level, "xp_in_level": xp - spent, "xp_to_next": 100 * level}


def power(breed_id: str, gear: Mapping[str, int], xp: int) -> int:
    """Return base power; battle taps never alter this saved stat.

    Missing known slots mean level zero. Unknown slots, breeds, and levels are
    rejected so stale or malformed content cannot create invisible advantages.
    """
    if breed_id not in _BREED_POWER:
        raise ValueError("Unknown breed")
    if set(gear) - set(_SLOT_POWER):
        raise ValueError("Unknown gear slot")
    equipment_power = 0
    for slot, gain in _SLOT_POWER.items():
        level = _integer(gear.get(slot, 0), "gear level")
        if level > MAX_GEAR_LEVEL:
            raise ValueError("Gear level exceeds maximum")
        equipment_power += gain * level
    return _BREED_POWER[breed_id] + 5 * (progression(xp)["level"] - 1) + equipment_power


def upgrade_cost(level: int) -> Optional[int]:
    """Coin price to move from the current equipment level to the next one."""
    _integer(level, "gear level")
    if level > MAX_GEAR_LEVEL:
        raise ValueError("Gear level exceeds maximum")
    return None if level == MAX_GEAR_LEVEL else 80 * (level + 1) ** 2


def win_probability(
    power_a: int,
    power_b: int,
    taps_a: int,
    taps_b: int,
) -> float:
    """Compute A's chance from power and accepted taps, without a stance choice.

    Sixty accepted taps provide at most 20% extra effective power. The service
    applies tap-rate limits before passing accepted counts here. A 10%..90%
    clamp keeps every battle uncertain. Swapping sides gives the complement.
    """
    _integer(power_a, "power_a", 1)
    _integer(power_b, "power_b", 1)
    _integer(taps_a, "taps_a")
    _integer(taps_b, "taps_b")

    effective_a = power_a * (1 + TAP_BONUS_CAP * min(taps_a, TAP_CAP) / TAP_CAP)
    effective_b = power_b * (1 + TAP_BONUS_CAP * min(taps_b, TAP_CAP) / TAP_CAP)
    chance = effective_a / (effective_a + effective_b)
    return min(0.9, max(0.1, chance))


def battle_probability(
    snapshot_a: Mapping[str, object],
    snapshot_b: Mapping[str, object],
    taps_a: int,
    taps_b: int,
) -> float:
    """Read frozen power from snapshots; obsolete stance fields have no effect."""
    return win_probability(snapshot_a["power"], snapshot_b["power"], taps_a, taps_b)


def rewards(mode: str, won: bool) -> Dict[str, int]:
    """Gross battle rewards; the service charges entry separately and once.

    Bot and online ranked battles have identical payouts. ``ranked`` is a domain
    alias for simulations; the HTTP API uses ``bot`` or matchmaking for online.
    Practice is free, pays no medals, and remains available after a loss streak.
    """
    if not isinstance(won, bool):
        raise ValueError("won must be a boolean")
    if mode == "practice":
        coins, medals, xp = (15, 0, 10) if won else (8, 0, 5)
    elif mode in ("bot", "online", "ranked"):
        coins, medals, xp = (35, WIN_MEDALS, 25) if won else (15, LOSE_MEDALS, 12)
    else:
        raise ValueError("Unknown battle mode")
    return {"coins": coins, "medals": medals, "xp": xp}
