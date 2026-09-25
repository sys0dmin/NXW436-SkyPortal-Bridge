from __future__ import annotations

import unittest

from nxw436_az_start_sustain_characterize import (
    analyze_measurement_windows,
    classify_trial,
    valid_observation_seconds,
)


class StartSustainClassificationTests(unittest.TestCase):
    def test_reliable_requires_progress_stability_and_clean_rx(self):
        self.assertEqual(
            classify_trial(reliability_progress=21, stable=True, invalid_samples=0, min_progress_counts=20),
            (True, "reliable"),
        )
        self.assertEqual(
            classify_trial(reliability_progress=19, stable=True, invalid_samples=0, min_progress_counts=20)[1],
            "insufficient-commanded-progress",
        )
        self.assertEqual(
            classify_trial(reliability_progress=21, stable=False, invalid_samples=0, min_progress_counts=20)[1],
            "motion-not-stable",
        )
        self.assertEqual(
            classify_trial(reliability_progress=21, stable=True, invalid_samples=1, min_progress_counts=20)[1],
            "invalid-samples-observed",
        )

    def test_sustain_kick_progress_must_not_mask_insufficient_candidate_progress(self):
        # A high progress before the candidate is irrelevant here: the caller
        # supplies only post-warm-up candidate progress to this function.
        self.assertEqual(
            classify_trial(reliability_progress=7, stable=True, invalid_samples=0, min_progress_counts=20),
            (False, "insufficient-commanded-progress"),
        )

    def test_observation_window_accepts_bounded_long_diagnostic(self):
        self.assertTrue(valid_observation_seconds(35.0))
        self.assertTrue(valid_observation_seconds(300.0))
        self.assertTrue(valid_observation_seconds(360.0))
        self.assertFalse(valid_observation_seconds(360.1))

    def test_window_analysis_uses_valid_samples_without_interpolation(self):
        rows = []
        for relative_s in range(0, 91, 10):
            raw = 1_000 - relative_s * 10
            rows.append({
                "phase": "candidate-measurement",
                "elapsed_candidate_s": str(20 + relative_s),
                "valid": True,
                "raw_position": str(raw),
                "delta_counts": "-100" if relative_s else "0",
            })
        rows.append({
            "phase": "candidate-measurement",
            "elapsed_candidate_s": "65",
            "valid": False,
            "raw_position": "",
            "delta_counts": "",
        })
        windows, metrics = analyze_measurement_windows(rows, direction_sign=-1, target_cps=10.0)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[1]["invalid_samples"], 1)
        self.assertEqual(windows[0]["signed_delta_counts"], -300)
        self.assertAlmostEqual(windows[0]["commanded_cps"], 10.0)
        self.assertAlmostEqual(metrics["window_analysis_whole_cps"], 10.0)
        self.assertAlmostEqual(metrics["tracking_1m_error_counts"], 0.0)
        self.assertAlmostEqual(metrics["tracking_5m_sample_s"], 90.0)
        self.assertAlmostEqual(metrics["tracking_5m_error_counts"], 0.0)

    def test_window_analysis_counts_material_reverse_increment(self):
        rows = [
            {"phase": "candidate-measurement", "elapsed_candidate_s": "20", "valid": True, "raw_position": "100", "delta_counts": "0"},
            {"phase": "candidate-measurement", "elapsed_candidate_s": "30", "valid": True, "raw_position": "80", "delta_counts": "-20"},
            {"phase": "candidate-measurement", "elapsed_candidate_s": "40", "valid": True, "raw_position": "82", "delta_counts": "2"},
            {"phase": "candidate-measurement", "elapsed_candidate_s": "50", "valid": True, "raw_position": "60", "delta_counts": "-22"},
        ]
        windows, _ = analyze_measurement_windows(rows, direction_sign=-1, target_cps=None)
        self.assertEqual(windows[0]["reverse_increments"], 1)


if __name__ == "__main__":
    unittest.main()
