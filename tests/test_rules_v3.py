"""v3 remains frozen when new battles switch to multipliers and 90 taps."""
from fractions import Fraction
from roosters import rules_v3


def test_original_power_and_tap_curve():
    assert rules_v3.RULES_VERSION == 'v3'
    assert rules_v3.TAP_CAP == 80
    assert rules_v3.power('copper', {'helmet': 1, 'armor': 2, 'sword': 3}, 300) == 182
    assert rules_v3.power('ember', {'helmet': 10, 'armor': 10, 'sword': 10}, 4500) == 475
    assert rules_v3.bot_probability(100, 100, 80) == Fraction(7, 12)
    assert rules_v3.bot_probability(100, 100, 90) == Fraction(7, 12)
    assert rules_v3.bot_payout(1000, 100, 100) == 1800
