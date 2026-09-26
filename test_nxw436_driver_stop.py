from __future__ import annotations

import unittest

from nxw436_driver import NXW436


class RecordingSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        pass


class NXW436DriverStopTests(unittest.TestCase):
    def test_stop_uses_exact_same_prefix_twice(self) -> None:
        driver = NXW436("TEST")
        serial = RecordingSerial()
        driver.serial = serial  # type: ignore[assignment]
        driver.stop("az", "-")
        self.assertEqual(serial.writes, [bytes.fromhex("07000000"), bytes.fromhex("07000000")])

    def test_stop_never_sends_both_axis_directions(self) -> None:
        driver = NXW436("TEST")
        serial = RecordingSerial()
        driver.serial = serial  # type: ignore[assignment]
        driver.stop("alt", "+")
        self.assertEqual(serial.writes, [bytes.fromhex("1A000000"), bytes.fromhex("1A000000")])
        self.assertNotIn(bytes.fromhex("1B000000"), serial.writes)


if __name__ == "__main__":
    unittest.main()
