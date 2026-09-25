from __future__ import annotations

import unittest

from nxw436_az_motor_exercise import MAX_SECONDS_PER_DIRECTION, MEDIUM_PAYLOAD, output_path


class AzMotorExerciseTests(unittest.TestCase):
    def test_medium_payload_and_bounded_duration(self):
        self.assertEqual(MEDIUM_PAYLOAD.hex().upper(), "0072F1")
        self.assertEqual(MAX_SECONDS_PER_DIRECTION, 120.0)

    def test_unique_event_output_name(self):
        self.assertEqual(
            str(output_path("az-free-medium-r1")),
            "nxw436_az_motor_exercise_az-free-medium-r1_events.csv",
        )


if __name__ == "__main__":
    unittest.main()
