from __future__ import annotations

import unittest

from mount_model import POSITION_MODULUS
from nxw436_speed_analysis import analyze_raw_speed


class NXW436SpeedAnalysisTests(unittest.TestCase):
    def test_counts_and_degrees_per_second(self) -> None:
        counts, degrees = analyze_raw_speed([(0.0, 100), (2.0, 140)])
        self.assertEqual(counts, 20.0)
        self.assertEqual(degrees, 20.0 * 360.0 / POSITION_MODULUS)

    def test_wrap_aware_positive_and_negative_deltas(self) -> None:
        counts, _ = analyze_raw_speed([(0.0, POSITION_MODULUS - 5), (1.0, 5)])
        self.assertEqual(counts, 10.0)
        counts, _ = analyze_raw_speed([(0.0, 5), (1.0, POSITION_MODULUS - 5)])
        self.assertEqual(counts, -10.0)

    def test_rejects_invalid_time_series(self) -> None:
        with self.assertRaises(ValueError):
            analyze_raw_speed([(0.0, 1)])
        with self.assertRaises(ValueError):
            analyze_raw_speed([(1.0, 1), (1.0, 2)])
