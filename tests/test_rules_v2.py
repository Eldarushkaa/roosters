"""Frozen v2 battle rules and whole-coin rewards for historical settlement."""

import itertools
import unittest

from roosters import rules_v2 as rules


class ProgressionTests(unittest.TestCase):
    def test_exact_level_boundaries(self):
        for level in (1, 2, 3, 10, 100, 1_000_000):
            threshold = 50 * level * (level - 1)
            with self.subTest(level=level):
                self.assertEqual(rules.progression(threshold), {
                    "level": level, "xp_in_level": 0, "xp_to_next": 100 * level,
                })
                if level > 1:
                    before = rules.progression(threshold - 1)
                    self.assertEqual(before["level"], level - 1)
                    self.assertEqual(before["xp_in_level"], before["xp_to_next"] - 1)

    def test_progress_is_conserved(self):
        for xp in range(0, 10_000, 17):
            result = rules.progression(xp)
            level = result["level"]
            self.assertEqual(xp, 50 * level * (level - 1) + result["xp_in_level"])
            self.assertTrue(0 <= result["xp_in_level"] < result["xp_to_next"])

    def test_invalid_experience_is_rejected(self):
        for xp in (-1, 1.5, True, "100", None):
            with self.subTest(xp=xp), self.assertRaises(ValueError):
                rules.progression(xp)


class EquipmentTests(unittest.TestCase):
    def test_power_combines_breed_levels_and_equipment(self):
        self.assertEqual(rules.power("yard", {}, 0), 100)
        self.assertEqual(rules.power("copper", {"helmet": 1, "armor": 2, "sword": 3}, 300), 182)
        self.assertEqual(rules.power("ember", {"helmet": 10, "armor": 10, "sword": 10}, 4500), 475)

    def test_each_upgrade_strictly_increases_power(self):
        for breed in rules.BREEDS:
            for slot in rules.SLOTS:
                for level in range(rules.MAX_GEAR_LEVEL):
                    before = rules.power(breed["id"], {slot["id"]: level}, 1234)
                    after = rules.power(breed["id"], {slot["id"]: level + 1}, 1234)
                    self.assertEqual(after - before, slot["power_per_level"])

    def test_cost_curve_and_terminal_level(self):
        self.assertEqual([rules.upgrade_cost(level) for level in range(4)], [80, 320, 720, 1280])
        self.assertEqual(sum(rules.upgrade_cost(level) for level in range(10)), 30_800)
        self.assertIsNone(rules.upgrade_cost(10))
        for level in (-1, 11, 1.5, True):
            with self.subTest(level=level), self.assertRaises(ValueError):
                rules.upgrade_cost(level)

    def test_stale_or_invalid_equipment_cannot_add_power(self):
        for gear in ({"wings": 5}, {"sword": -1}, {"armor": 11}, {"helmet": True}):
            with self.subTest(gear=gear), self.assertRaises(ValueError):
                rules.power("yard", gear, 0)
        with self.assertRaises(ValueError):
            rules.power("unknown", {}, 0)

    def test_catalog_is_a_deep_copy(self):
        one = rules.catalog()
        one["breeds"][0]["power"] = 99999
        one["battle"]["duration"] = 999
        one["slots"].clear()
        two = rules.catalog()
        self.assertEqual(two["breeds"][0]["power"], 100)
        self.assertEqual(two["battle"]["duration"], 10)
        self.assertNotIn("stances", two)
        self.assertEqual(len(two["slots"]), 3)


class BattleProbabilityTests(unittest.TestCase):
    def test_short_battles_keep_the_original_tap_goal_and_bonus(self):
        self.assertEqual(rules.RULES_VERSION, "v2")
        self.assertEqual(rules.BATTLE_DURATION, 10)
        self.assertEqual(rules.TAP_CAP, 60)
        self.assertEqual(rules.TAP_RATE, 6)
        self.assertEqual(rules.TAP_BURST, 12)
        self.assertEqual(rules.TAP_INITIAL, 6)
        self.assertEqual(rules.TAP_BONUS_CAP, 0.2)
        self.assertEqual(rules.BOT_TAPS, 30)

    def test_all_matchups_are_symmetric_and_bounded(self):
        powers = (1, 100, 190, 100_000)
        taps = (0, 30, 60, 600)
        for pa, pb, ta, tb in itertools.product(powers, powers, taps, taps):
            first = rules.win_probability(pa, pb, ta, tb)
            second = rules.win_probability(pb, pa, tb, ta)
            self.assertGreaterEqual(first, 0.1)
            self.assertLessEqual(first, 0.9)
            self.assertAlmostEqual(first + second, 1.0, places=14)

    def test_identical_sides_always_have_equal_chance(self):
        for taps in (0, 30, 60):
            self.assertEqual(rules.win_probability(123, 123, taps, taps), 0.5)

    def test_power_monotonically_increases_win_chance(self):
        previous = 0
        for power in range(1, 1500):
            chance = rules.win_probability(power, 150, 30, 60)
            self.assertGreaterEqual(chance, previous)
            previous = chance

    def test_taps_are_monotone_and_capped(self):
        previous = 0
        for taps in range(200):
            chance = rules.win_probability(100, 100, taps, 0)
            self.assertGreaterEqual(chance, previous)
            previous = chance
        capped = rules.win_probability(100, 100, 60, 0)
        self.assertAlmostEqual(capped, 120 / 220)
        self.assertEqual(capped, rules.win_probability(100, 100, 60_000, 0))

    def test_extreme_power_still_leaves_a_chance_for_either_side(self):
        self.assertEqual(rules.win_probability(1, 100000, 60, 0), .1)
        self.assertEqual(rules.win_probability(100000, 1, 0, 60), .9)

    def test_snapshot_probability_uses_power_and_taps_without_stances(self):
        expected = rules.win_probability(100, 150, 60, 30)
        self.assertEqual(rules.battle_probability({"power": 100}, {"power": 150}, 60, 30), expected)
        self.assertEqual(rules.battle_probability(
            {"power": 100, "stance": "rush"}, {"power": 150, "stance": "feint"}, 60, 30,
        ), expected)

    def test_invalid_battle_inputs_are_rejected(self):
        valid = [100, 100, 0, 0]
        for position, invalid in ((0, 0), (1, -1), (0, True), (2, -1), (2, True), (3, 2.5)):
            values = valid[:]
            values[position] = invalid
            with self.subTest(position=position, invalid=invalid), self.assertRaises(ValueError):
                rules.win_probability(*values)


class RewardTests(unittest.TestCase):
    def test_ranked_modes_have_identical_gross_rewards(self):
        for mode in ("online", "bot", "ranked"):
            self.assertEqual(rules.rewards(mode, True), {"coins": 35, "medals": 5, "xp": 25})
            self.assertEqual(rules.rewards(mode, False), {"coins": 15, "medals": 1, "xp": 12})

    def test_equal_skill_ranked_play_has_zero_expected_medal_drain(self):
        win = rules.rewards("online", True)["medals"] - rules.ENTRY_FEE
        loss = rules.rewards("online", False)["medals"] - rules.ENTRY_FEE
        self.assertEqual((win + loss) / 2, 0)
        self.assertEqual((win, loss), (2, -2))

    def test_practice_grants_progress_but_no_entry_currency(self):
        self.assertEqual(rules.rewards("practice", True), {"coins": 15, "medals": 0, "xp": 10})
        self.assertEqual(rules.rewards("practice", False), {"coins": 8, "medals": 0, "xp": 5})

    def test_reward_results_do_not_share_mutable_state(self):
        result = rules.rewards("bot", True)
        result["medals"] = 1000
        self.assertEqual(rules.rewards("bot", True)["medals"], 5)

    def test_invalid_reward_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            rules.rewards("unknown", True)
        with self.assertRaises(ValueError):
            rules.rewards("bot", 1)


if __name__ == "__main__":
    unittest.main()
