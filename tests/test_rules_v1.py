"""Historical snapshots must retain the v1 outcome and payout rules."""

import itertools
import unittest

from roosters import rules_v1, rules_v2 as rules


class HistoricalRuleTests(unittest.TestCase):
    def test_original_timing_and_tap_limits_are_frozen(self):
        self.assertEqual(rules_v1.RULES_VERSION, "v1")
        self.assertEqual(rules_v1.BATTLE_DURATION, 15)
        self.assertEqual(rules_v1.TAP_CAP, 60)
        self.assertEqual(rules_v1.TAP_RATE, 4)
        self.assertEqual(rules_v1.TAP_BURST, 8)
        self.assertEqual(rules_v1.TAP_INITIAL, 0)
        self.assertEqual(rules_v1.BOT_TAPS, 30)
        self.assertEqual(rules_v1.TAP_BONUS_CAP, 0.2)

    def test_stance_cycle_and_snapshot_odds_are_preserved(self):
        ids = [stance["id"] for stance in rules_v1.STANCES]
        for stance in rules_v1.STANCES:
            with self.subTest(stance=stance["id"]):
                chances = [rules_v1.win_probability(100, 100, stance["id"], rival, 0, 0)
                           for rival in ids]
                self.assertEqual(sum(chance > .5 for chance in chances), 1)
                self.assertEqual(sum(chance < .5 for chance in chances), 1)
                self.assertAlmostEqual(sum(chances) / len(chances), .5)
                self.assertAlmostEqual(rules_v1.battle_probability(
                    {"power": 100, "stance": stance["id"]},
                    {"power": 100, "stance": stance["beats"]}, 0, 0,
                ), 112 / 212)

    def test_taps_and_stance_still_multiply_on_old_battles(self):
        self.assertAlmostEqual(rules_v1.battle_probability(
            {"power": 100, "stance": "rush"},
            {"power": 100, "stance": "feint"}, 60, 0,
        ), 134.4 / 234.4)

    def test_old_and_new_power_progression_and_rewards_match(self):
        for breed, xp in itertools.product(rules.BREEDS, (0, 100, 300, 4500)):
            gear = {"helmet": 2, "armor": 3, "sword": 4}
            self.assertEqual(rules.power(breed["id"], gear, xp),
                             rules_v1.power(breed["id"], gear, xp))
        self.assertEqual(rules.catalog()["breeds"], rules_v1.catalog()["breeds"])
        self.assertEqual(rules.catalog()["slots"], rules_v1.catalog()["slots"])
        self.assertEqual(rules_v1.ENTRY_FEE, 3)
        for mode, won in itertools.product(("bot", "online", "ranked", "practice"), (True, False)):
            self.assertEqual(rules.rewards(mode, won), rules_v1.rewards(mode, won))


if __name__ == "__main__":
    unittest.main()
