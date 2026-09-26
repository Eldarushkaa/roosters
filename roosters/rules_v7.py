"""Pure, deterministic game rules for Rooster Arena.

This module has no database, HTTP, clock, or random-number dependencies. The
application service validates ownership and funds, freezes a battle snapshot,
draws its random value once, and commits rewards atomically. Functions here only
calculate values. Changing battle math requires a new rules version and a
settlement path for already persisted versions; see docs/GAME_DESIGN.md.
"""

from copy import deepcopy
from fractions import Fraction
from math import isqrt
from typing import Dict, Mapping, Optional


RULES_VERSION = "v7"
COIN_SCALE = 100
BATTLE_DURATION = 10
ROULETTE_DURATION = 2
STAKES_MINOR = (1000, 2500, 5000, 10000)
MIN_STAKE_MINOR = 1000
STAKE_STEP_MINOR = 1
# Presets above are shortcuts, not the accepted stake domain. Even the largest
# bot prize (2.16 times the stake) must remain an exact JSON/JavaScript integer.
MAX_SAFE_INTEGER = 2**53 - 1
MAX_STAKE_MINOR = MAX_SAFE_INTEGER * 25 // 54
WELCOME_MINOR = 42000
DAILY_MINOR = 17000
FREE_REWARD_MINOR = 1500
REFERRAL_MINOR = 15000
PASSIVE_PER_MINUTE_MINOR = 200
PASSIVE_CAP_SECONDS = 8 * 3600
MAX_GEAR_LEVEL = 10
TAP_CAP = 90
TAP_BONUS_CAP = 0.2
BOT_TAPS = 0
BOT_RTP_MIN = Fraction(9, 10)
BOT_RTP_MAX = Fraction(6, 5)
PVP_WIN_MULTIPLIER = Fraction(19, 10)

BREEDS = [
    {
        "id": "yard",
        "name": "Дворовый",
        "description": "Начинающий боец с характером. Большая карьера начинается во дворе.",
        "price_minor": 0,
        "power_multiplier": 1.0,
        "power_multiplier_percent": 100,
        "color": "#f3ad55",
    },
    {
        "id": "copper",
        "name": "Медный",
        "description": "Крепкий, упрямый и всегда готов защищать свой двор.",
        "price_minor": 50000,
        "power_multiplier": 1.2,
        "power_multiplier_percent": 120,
        "color": "#d98754",
    },
    {
        "id": "storm",
        "name": "Грозовой",
        "description": "Взъерошенный чемпион. На арене после него летят перья.",
        "price_minor": 180000,
        "power_multiplier": 1.5,
        "power_multiplier_percent": 150,
        "color": "#77adf0",
    },
    {
        "id": "ember",
        "name": "Огненный",
        "description": "Редкая порода с огненным гребнем и несгибаемым характером.",
        "price_minor": 600000,
        "power_multiplier": 1.9,
        "power_multiplier_percent": 190,
        "color": "#fb7664",
    },
]

SLOTS = [
    {"id": "helmet", "name": "Шлем", "power_per_level": 6},
    {"id": "armor", "name": "Броня", "power_per_level": 8},
    {"id": "sword", "name": "Меч", "power_per_level": 10},
]

_BREED_MULTIPLIER = {breed["id"]: breed["power_multiplier_percent"] for breed in BREEDS}
_SLOT_POWER = {slot["id"]: slot["power_per_level"] for slot in SLOTS}


def _integer(value: int, name: str, minimum: int = 0) -> int:
    """Reject bools and fractional values instead of silently changing rules."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("{} must be an integer >= {}".format(name, minimum))
    return value


def catalog() -> dict:
    """Return detached JSON-ready content; monetary values use hundredths."""
    return {
        "coin_scale": COIN_SCALE,
        "breeds": deepcopy(BREEDS),
        "slots": deepcopy(SLOTS),
        "battle": {
            "duration": BATTLE_DURATION,
            "roulette_duration": ROULETTE_DURATION,
            "tap_cap": TAP_CAP,
            "tap_bonus_cap": TAP_BONUS_CAP,
            "stakes_minor": list(STAKES_MINOR),
            "bot_power_min_ratio": 0.7,
            "bot_power_max_ratio": 1.4,
            "bot_rtp_min": float(BOT_RTP_MIN),
            "bot_rtp_max": float(BOT_RTP_MAX),
            "pvp_win_multiplier": float(PVP_WIN_MULTIPLIER),
            "free_reward_minor": FREE_REWARD_MINOR,
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
    if breed_id not in _BREED_MULTIPLIER:
        raise ValueError("Unknown breed")
    if set(gear) - set(_SLOT_POWER):
        raise ValueError("Unknown gear slot")
    equipment_power = 0
    for slot, gain in _SLOT_POWER.items():
        level = _integer(gear.get(slot, 0), "gear level")
        if level > MAX_GEAR_LEVEL:
            raise ValueError("Gear level exceeds maximum")
        equipment_power += gain * level
    base = 100 + 5 * (progression(xp)["level"] - 1) + equipment_power
    return base * _BREED_MULTIPLIER[breed_id] // 100


def upgrade_cost(level: int) -> Optional[int]:
    """Price in hundredths of a coin for the next equipment level."""
    _integer(level, "gear level")
    if level > MAX_GEAR_LEVEL:
        raise ValueError("Gear level exceeds maximum")
    return None if level == MAX_GEAR_LEVEL else 80 * COIN_SCALE * (level + 1) ** 2


def bot_power_bounds(player_power: int) -> tuple:
    """Inclusive integer powers whose ratios stay within 70%..140%."""
    _integer(player_power, "player_power", 1)
    return (7 * player_power + 9) // 10, 14 * player_power // 10


def bot_probability(player_power: int, bot_power: int, taps: int) -> Fraction:
    """Exact bot win chance, increasing by one third after ninety taps.

    The zero-tap probability is P / (P + B). Each accepted tap adds 1/270 of
    that baseline, capped at ninety taps. With a fixed pre-fight prize this
    raises theoretical gross RTP from 90% to 120%. This is a probability bonus,
    separate from the effective-power bonus used in PvP. Bot powers outside
    the roulette bounds are rejected rather than silently changing that curve.
    """
    lower, upper = bot_power_bounds(player_power)
    _integer(bot_power, "bot_power", 1)
    _integer(taps, "taps")
    if not lower <= bot_power <= upper:
        raise ValueError("Bot power is outside the roulette bounds")
    base = Fraction(player_power, player_power + bot_power)
    chance = base * Fraction(3 * TAP_CAP + min(taps, TAP_CAP), 3 * TAP_CAP)
    if not 0 < chance < 1:
        raise ValueError("Bot probability must stay strictly between zero and one")
    return chance


def _stake(stake_minor: int) -> int:
    _integer(stake_minor, "stake_minor", MIN_STAKE_MINOR)
    if stake_minor > MAX_STAKE_MINOR:
        raise ValueError("Stake exceeds the exact monetary range")
    return stake_minor


def bot_payout(stake_minor: int, player_power: int, bot_power: int) -> int:
    """Fixed gross winning prize, including the stake, in coin hundredths.

    Quote once before the roulette, from frozen powers and zero-tap chance.
    Tap count never changes this quote. Flooring only once to a whole minor
    unit makes actual RTP slightly lower than 90%..120%; the difference is
    less than one minor unit times the win chance, divided by the stake.
    A loss pays zero. The application charges entry and persists this quote.
    """
    stake_minor = _stake(stake_minor)
    quote = stake_minor * BOT_RTP_MIN / bot_probability(player_power, bot_power, 0)
    return quote.numerator // quote.denominator


def online_payout(stake_minor: int) -> int:
    """Winner receives 1.9 times their own stake; the loser receives zero.

    The opponent's stake never enters this quote. Before minor-unit rounding,
    expected return is 1.9 times win probability, with no pooled-bank guarantee.
    Floor once to a whole coin hundredth, including for custom stakes.
    """
    quote = _stake(stake_minor) * PVP_WIN_MULTIPLIER
    return quote.numerator // quote.denominator


def win_probability(
    power_a: int,
    power_b: int,
    taps_a: int,
    taps_b: int,
) -> Fraction:
    """Exact PvP chance from power and capped taps, symmetric across sides.

    Ninety taps provide at most 20% extra effective power. There is no tap-rate
    limit. A 10%..90% probability clamp keeps either side able to win. The
    integer weights below are the common denominator of 1 + taps / 450.
    """
    _integer(power_a, "power_a", 1)
    _integer(power_b, "power_b", 1)
    _integer(taps_a, "taps_a")
    _integer(taps_b, "taps_b")
    effective_a = power_a * (5 * TAP_CAP + min(taps_a, TAP_CAP))
    effective_b = power_b * (5 * TAP_CAP + min(taps_b, TAP_CAP))
    chance = Fraction(effective_a, effective_a + effective_b)
    return min(Fraction(9, 10), max(Fraction(1, 10), chance))


def battle_probability(
    snapshot_a: Mapping[str, object],
    snapshot_b: Mapping[str, object],
    taps_a: int,
    taps_b: int,
) -> Fraction:
    """Compute PvP odds from frozen powers; bots use ``bot_probability``."""
    return win_probability(snapshot_a["power"], snapshot_b["power"], taps_a, taps_b)


def rewards(mode: str, won: bool) -> Dict[str, int]:
    """XP only; money comes from the persisted prize or a one-time free grant.

    The service owns free-battle eligibility and pays FREE_REWARD_MINOR once
    regardless of its outcome. That onboarding grant is excluded from RTP.
    """
    if not isinstance(won, bool):
        raise ValueError("won must be a boolean")
    if mode in ("free", "practice"):
        xp = 10 if won else 5
    elif mode in ("bot", "online", "ranked"):
        xp = 25 if won else 12
    else:
        raise ValueError("Unknown battle mode")
    return {"xp": xp}
