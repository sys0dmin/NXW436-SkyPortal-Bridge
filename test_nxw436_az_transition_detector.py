from __future__ import annotations

import csv
import unittest
from pathlib import Path

from nxw436_progress_detector import evaluate_no_progress


INTERVAL = 0.176


def first_detection(samples):
    valid_history = []
    for sample in samples:
        if sample is None:
            continue
        valid_history.append(sample)
        evidence = evaluate_no_progress(
            valid_history,
            window_s=1.5,
            min_progress_counts=20,
            nominal_sample_interval=0.15,
        )
        if evidence is not None and evidence.confirmed:
            return evidence
    return None


class NoProgressDetectorTests(unittest.TestCase):
    def test_continuous_motion_is_not_no_progress(self):
        samples = [(i * INTERVAL, i * 5) for i in range(30)]
        self.assertIsNone(first_detection(samples))

    def test_stationary_position_triggers_after_full_window(self):
        samples = [(i * INTERVAL, 100) for i in range(30)]
        evidence = first_detection(samples)
        self.assertIsNotNone(evidence)
        self.assertGreaterEqual(evidence.span_s, 1.5)
        self.assertEqual(evidence.commanded_progress, 0)

    def test_motion_then_plateau_triggers(self):
        samples = []
        position = 0
        for i in range(35):
            if i < 8:
                position += 10
            samples.append((i * INTERVAL, position))
        self.assertIsNotNone(first_detection(samples))

    def test_invalid_sample_does_not_break_valid_history(self):
        samples = []
        for i in range(30):
            samples.append(None if i == 6 else (i * INTERVAL, 100))
        evidence = first_detection(samples)
        self.assertIsNotNone(evidence)
        self.assertGreaterEqual(evidence.span_s, 1.5)

    def test_trigger_historical_span_is_at_least_window(self):
        samples = [(i * INTERVAL, 42) for i in range(30)]
        evidence = first_detection(samples)
        self.assertIsNotNone(evidence)
        self.assertGreaterEqual(evidence.span_s, 1.5)
        self.assertLessEqual(evidence.span_s, 1.5 + 2 * 0.15)

    def test_saved_cycle_6_replay_detects_plateau(self):
        csv_path = Path(__file__).with_name(
            "nxw436_az_transition_cycles_az-plus-resend-multicycle-01_raw.csv"
        )
        self.assertTrue(csv_path.exists(), "saved acceptance CSV is required")
        history = []
        detected = None
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row["cycle_index"] != "6" or row["phase"] != "MEDIUM":
                    continue
                if row["valid"].lower() != "true":
                    continue
                history.append((float(row["monotonic_s"]), int(row["unwrapped_position"])))
                evidence = evaluate_no_progress(
                    history,
                    window_s=1.5,
                    min_progress_counts=20,
                    nominal_sample_interval=0.15,
                )
                if evidence is not None and evidence.confirmed:
                    detected = evidence
                    break
        self.assertIsNotNone(detected)
        self.assertGreaterEqual(detected.span_s, 1.5)
        self.assertLess(detected.commanded_progress, 20)


if __name__ == "__main__":
    unittest.main()
