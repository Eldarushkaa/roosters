"""Balance invariants independent of storage, HTTP, time, and random seeds."""

from fractions import Fraction
import itertools
import json
import unittest

from roosters import rules, rules_v4, rules_v5


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
        self.assertEqual(rules.power("copper", {"helmet": 1, "armor": 2, "sword": 3}, 300), 194)
        self.assertEqual(rules.power("ember", {"helmet": 10, "armor": 10, "sword": 10}, 4500), 731)

    def test_each_upgrade_strictly_increases_power(self):
        for breed in rules.BREEDS:
            for slot in rules.SLOTS:
                for level in range(rules.MAX_GEAR_LEVEL):
                    before = rules.power(breed["id"], {slot["id"]: level}, 1234)
                    after = rules.power(breed["id"], {slot["id"]: level + 1}, 1234)
                    base_before = rules.power("yard", {slot["id"]: level}, 1234)
                    base_after = base_before + slot["power_per_level"]
                    percent = breed["power_multiplier_percent"]
                    self.assertEqual(before, base_before * percent // 100)
                    self.assertEqual(after, base_after * percent // 100)
                    self.assertGreater(after, before)

    def test_cost_curve_and_terminal_level(self):
        self.assertEqual([rules.upgrade_cost(level) for level in range(4)], [8000, 32000, 72000, 128000])
        self.assertEqual(sum(rules.upgrade_cost(level) for level in range(10)), 3_080_000)
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
        one["breeds"][0]["power_multiplier_percent"] = 99999
        one["battle"]["duration"] = 999
        one["slots"].clear()
        one["battle"]["stakes_minor"].clear()
        two = rules.catalog()
        self.assertEqual(two["breeds"][0]["power_multiplier_percent"], 100)
        self.assertEqual(two["battle"]["duration"], 10)
        self.assertNotIn("stances", two)
        self.assertEqual(len(two["slots"]), 3)
        self.assertEqual(two["battle"]["stakes_minor"], [1000, 2500, 5000, 10000])


class CatalogTests(unittest.TestCase):
    def test_catalog_exposes_explicit_money_units_and_actual_game_terms(self):
        catalog = rules.catalog()
        # Fractions remain internal; this is directly serializable by the API.
        self.assertEqual(json.loads(json.dumps(catalog)), catalog)
        self.assertEqual(catalog["coin_scale"], 100)
        self.assertEqual([breed["price_minor"] for breed in catalog["breeds"]], [0, 50000, 180000, 600000])
        self.assertTrue(all("price" not in breed for breed in catalog["breeds"]))
        self.assertEqual(catalog["battle"], {
            "duration": 10, "roulette_duration": 2, "tap_cap": 90,
            "tap_bonus_cap": 0.2, "stakes_minor": [1000, 2500, 5000, 10000],
            "bot_power_min_ratio": 0.7, "bot_power_max_ratio": 1.4,
            "bot_rtp_min": 0.9, "bot_rtp_max": 1.1, "pvp_win_multiplier": 1.9,
            "free_reward_minor": 1500,
        })


class BotProbabilityTests(unittest.TestCase):
    def test_roulette_bounds_use_integer_ceil_and_floor(self):
        for power in range(1, 1000):
            low, high = rules.bot_power_bounds(power)
            self.assertGreaterEqual(low, Fraction(7 * power, 10))
            self.assertLess(low - 1, Fraction(7 * power, 10))
            self.assertLessEqual(high, Fraction(14 * power, 10))
            self.assertGreater(high + 1, Fraction(14 * power, 10))
            self.assertLessEqual(low, high)
        self.assertEqual(rules.bot_power_bounds(1), (1, 1))
        self.assertEqual(rules.bot_power_bounds(101), (71, 141))
        self.assertEqual(rules.bot_power_bounds(10**30), (7 * 10**29, 14 * 10**29))

    def test_all_taps_raise_chance_uniformly_up_to_ninety(self):
        for power, opponent in ((100, 70), (100, 100), (100, 140), (101, 71), (101, 141)):
            base = Fraction(power, power + opponent)
            for taps in range(101):
                chance = rules.bot_probability(power, opponent, taps)
                self.assertEqual(chance, base * Fraction(405 + min(taps, 90), 405))
                self.assertGreater(chance, 0)
                self.assertLess(chance, 1)
            self.assertEqual(rules.bot_probability(power, opponent, 100000), base * Fraction(11, 9))

    def test_bot_quote_has_exact_endpoints_and_includes_the_stake(self):
        for stake in (1000, 2500, 5000, 10000):
            payout = rules.bot_payout(stake, 100, 100)
            self.assertEqual(payout, stake * 9 // 5)
            self.assertEqual(rules.bot_probability(100, 100, 0) * payout / stake, Fraction(9, 10))
            self.assertEqual(rules.bot_probability(100, 100, 90) * payout / stake, Fraction(11, 10))
        self.assertEqual(rules.bot_payout(1000, 100, 70), 1530)
        self.assertEqual(rules.bot_payout(1000, 100, 140), 2160)

    def test_every_integer_roulette_result_and_stake_obey_rounding_bounds(self):
        # Exhaust every attainable bot power for a range of starting/upgraded
        # players; larger powers exercise the same formula without float loss.
        powers = list(range(1, 151)) + [190, 475, 1000]
        for power in powers:
            low, high = rules.bot_power_bounds(power)
            for opponent in range(low, high + 1):
                p0 = rules.bot_probability(power, opponent, 0)
                p90 = rules.bot_probability(power, opponent, 90)
                for stake in rules.STAKES_MINOR:
                    payout = rules.bot_payout(stake, power, opponent)
                    self.assertIs(type(payout), int)
                    rtp0 = p0 * payout / stake
                    rtp90 = p90 * payout / stake
                    self.assertLessEqual(rtp0, Fraction(9, 10))
                    self.assertGreater(rtp0, Fraction(9, 10) - p0 / stake)
                    self.assertLessEqual(rtp90, Fraction(11, 10))
                    self.assertGreater(rtp90, Fraction(11, 10) - p90 / stake)
                    self.assertEqual(rtp90, rtp0 * Fraction(11, 9))

    def test_v4_bot_curve_remains_at_105_percent_and_v5_reaches_110(self):
        for engine, maximum in ((rules_v4, Fraction(21, 20)), (rules, Fraction(11, 10))):
            prize = engine.bot_payout(1000, 100, 100)
            self.assertEqual(prize, 1800)
            self.assertEqual(engine.bot_probability(100, 100, 0) * prize / 1000, Fraction(9, 10))
            self.assertEqual(engine.bot_probability(100, 100, 90) * prize / 1000, maximum)

    def test_quote_does_not_use_floating_point_even_for_huge_powers(self):
        power = 10**30 + 3
        opponent = rules.bot_power_bounds(power)[1]
        raw_quote = Fraction(1000 * 9 * (power + opponent), 10 * power)
        self.assertEqual(rules.bot_payout(1000, power, opponent), raw_quote.numerator // raw_quote.denominator)

    def test_invalid_inputs_cannot_enter_the_bot_probability_curve(self):
        for power in (0, -1, True, 100.0, "100", None):
            with self.subTest(power=power), self.assertRaises(ValueError):
                rules.bot_power_bounds(power)
        for values in ((100, 69, 0), (100, 141, 0), (100, 0, 0), (100, True, 0),
                       (100, 100.0, 0), (100, 100, -1), (100, 100, True), (100, 100, 0.5)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                rules.bot_probability(*values)
        with self.assertRaises(ValueError):
            rules.bot_payout(1000, 100, 200)


class PvPProbabilityTests(unittest.TestCase):
    def test_all_matchups_are_exactly_symmetric_and_bounded(self):
        powers = (1, 100, 190, 100_000)
        taps = (0, 40, 90, 600)
        for pa, pb, ta, tb in itertools.product(powers, powers, taps, taps):
            first = rules.win_probability(pa, pb, ta, tb)
            second = rules.win_probability(pb, pa, tb, ta)
            self.assertIsInstance(first, Fraction)
            self.assertGreaterEqual(first, Fraction(1, 10))
            self.assertLessEqual(first, Fraction(9, 10))
            self.assertEqual(first + second, 1)

    def test_identical_sides_always_have_equal_chance(self):
        for taps in (0, 40, 90):
            self.assertEqual(rules.win_probability(123, 123, taps, taps), Fraction(1, 2))

    def test_power_monotonically_increases_win_chance(self):
        previous = 0
        for power in range(1, 1500):
            chance = rules.win_probability(power, 150, 40, 90)
            self.assertGreaterEqual(chance, previous)
            previous = chance

    def test_taps_are_monotone_and_capped_at_twenty_percent_power(self):
        previous = 0
        for taps in range(200):
            chance = rules.win_probability(100, 100, taps, 0)
            self.assertGreaterEqual(chance, previous)
            previous = chance
        capped = rules.win_probability(100, 100, 90, 0)
        self.assertEqual(capped, Fraction(120, 220))
        self.assertEqual(capped, rules.win_probability(100, 100, 80_000, 0))

    def test_extreme_power_still_leaves_a_chance_for_either_side(self):
        self.assertEqual(rules.win_probability(1, 100000, 90, 0), Fraction(1, 10))
        self.assertEqual(rules.win_probability(100000, 1, 0, 90), Fraction(9, 10))

    def test_snapshot_probability_uses_power_and_taps_without_stances(self):
        expected = rules.win_probability(100, 150, 90, 40)
        self.assertEqual(rules.battle_probability({"power": 100}, {"power": 150}, 90, 40), expected)
        self.assertEqual(rules.battle_probability(
            {"power": 100, "stance": "rush"}, {"power": 150, "stance": "feint"}, 90, 40,
        ), expected)

    def test_invalid_battle_inputs_are_rejected(self):
        valid = [100, 100, 0, 0]
        for position, invalid in ((0, 0), (1, -1), (0, True), (2, -1), (2, True), (3, 2.5)):
            values = valid[:]
            values[position] = invalid
            with self.subTest(position=position, invalid=invalid), self.assertRaises(ValueError):
                rules.win_probability(*values)


class PayoutTests(unittest.TestCase):
    def test_pvp_pays_nineteen_tenths_of_own_stake(self):
        for stake, prize in ((1000, 1900), (2500, 4750), (5000, 9500), (10000, 19000)):
            self.assertEqual(rules.online_payout(stake), prize)
            self.assertEqual(Fraction(prize, stake), Fraction(19, 10))
            self.assertEqual(prize - stake, 9 * stake // 10)

    def test_equal_stakes_still_have_ninety_five_percent_aggregate_return(self):
        for stake, chance in itertools.product(rules.STAKES_MINOR,
                                              (Fraction(1, 10), Fraction(1, 2), Fraction(9, 10))):
            prize = rules.online_payout(stake)
            return_a = chance * prize
            return_b = (1 - chance) * prize
            self.assertEqual((return_a + return_b) / (2 * stake), Fraction(19, 20))

    def test_pvp_personal_expectation_scales_with_win_probability(self):
        for stake, chance in itertools.product(rules.STAKES_MINOR,
                                              (Fraction(1, 10), Fraction(1, 2), Fraction(9, 10))):
            expected_gross = chance * rules.online_payout(stake)
            self.assertEqual(expected_gross / stake, chance * Fraction(19, 10))
            self.assertEqual(expected_gross - stake, stake * (chance * Fraction(19, 10) - 1))

    def test_only_supported_integer_stakes_can_produce_prizes(self):
        for stake in (0, -1000, True, 1000.0, "1000", None, 999, rules.MAX_STAKE_MINOR + 1):
            with self.subTest(stake=stake), self.assertRaises(ValueError):
                rules.online_payout(stake)
            with self.subTest(stake=stake), self.assertRaises(ValueError):
                rules.bot_payout(stake, 100, 100)

    def test_custom_stakes_floor_exact_prizes_and_keep_json_integers_safe(self):
        for stake in (1001, 12345, 42000, 100000, rules.MAX_STAKE_MINOR):
            self.assertEqual(rules.online_payout(stake), 19 * stake // 10)
            for opponent in (70, 100, 140):
                prize = rules.bot_payout(stake, 100, opponent)
                self.assertEqual(prize, stake * 9 * (100 + opponent) // 1000)
                self.assertLessEqual(prize, rules.MAX_SAFE_INTEGER)
        with self.assertRaises(ValueError):
            rules_v5.online_payout(1001)


class RewardTests(unittest.TestCase):
    def test_battle_progress_rewards_do_not_mint_extra_currency(self):
        for mode in ("online", "bot", "ranked"):
            self.assertEqual(rules.rewards(mode, True), {"xp": 25})
            self.assertEqual(rules.rewards(mode, False), {"xp": 12})
        for mode in ("free", "practice"):
            self.assertEqual(rules.rewards(mode, True), {"xp": 10})
            self.assertEqual(rules.rewards(mode, False), {"xp": 5})
        self.assertEqual(rules.FREE_REWARD_MINOR, 1500)

    def test_reward_results_do_not_share_mutable_state(self):
        result = rules.rewards("bot", True)
        result["xp"] = 1000
        self.assertEqual(rules.rewards("bot", True)["xp"], 25)

    def test_invalid_reward_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            rules.rewards("unknown", True)
        with self.assertRaises(ValueError):
            rules.rewards("bot", 1)


if __name__ == "__main__":
    unittest.main()
