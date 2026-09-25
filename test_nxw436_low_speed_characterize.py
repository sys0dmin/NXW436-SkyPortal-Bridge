from __future__ import annotations

import unittest

from nxw436_low_speed_characterize import (
    is_material_reverse_increment, motion_stability_metrics, parse_payload, stable_motion,
)


class LowSpeedCharacterizeHelperTests(unittest.TestCase):
    def test_payload_range_excludes_zero_and_unproven_ff_payload(self):
        self.assertEqual(parse_payload("002000"), 0x002000)
        with self.assertRaises(Exception):
            parse_payload("000000")
        with self.assertRaises(Exception):
            parse_payload("FFFFFF")

    def test_stability_requires_commanded_progress_in_both_halves(self):
        stable, reason = stable_motion([(0.0, 0), (1.0, 3), (2.0, 6), (3.0, 9)], 1)
        self.assertTrue(stable, reason)
        stable, reason = stable_motion([(0.0, 0), (1.0, 3), (2.0, 3), (3.0, 3)], 1)
        self.assertFalse(stable)
        self.assertEqual(reason, "no-commanded-progress-in-both-halves")

    def test_negative_direction_uses_signed_encoder_positions(self):
        stable, reason = stable_motion([(0.0, 0), (1.0, -3), (2.0, -6), (3.0, -9)], -1)
        self.assertTrue(stable, reason)

    def test_one_count_reverse_jitter_is_not_classified_as_material_reverse_motion(self):
        self.assertFalse(is_material_reverse_increment(1, -1))
        self.assertTrue(is_material_reverse_increment(2, -1))

    def test_low_quantized_rate_uses_half_rates_not_median_instantaneous_steps(self):
        stable, reason, first, second, ratio = motion_stability_metrics(
            [(0.0, 0), (0.18, 0), (0.36, 1), (0.54, 1), (0.72, 2), (0.90, 2)], 1
        )
        self.assertTrue(stable, reason)
        self.assertGreater(first, 0)
        self.assertGreater(second, 0)
        self.assertGreaterEqual(ratio, 0.5)

    def test_large_half_rate_change_is_not_stable(self):
        stable, reason, _, _, ratio = motion_stability_metrics(
            [(0.0, 0), (1.0, 1), (2.0, 2), (3.0, 12), (4.0, 22), (5.0, 32)], 1
        )
        self.assertFalse(stable)
        self.assertEqual(reason, "half-rate-ratio-below-0.5")
        self.assertLess(ratio, 0.5)


if __name__ == "__main__":
    unittest.main()
