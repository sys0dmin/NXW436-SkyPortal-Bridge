from __future__ import annotations

import unittest
import threading

from nxw436_driver import NXW436


class RecordingSerial:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        pass

    def read(self, count: int) -> bytes:
        return b"\x00" * count


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

    def test_query_transaction_and_move_write_do_not_interleave(self) -> None:
        class TransactionSerial(RecordingSerial):
            def __init__(self) -> None:
                super().__init__()
                self.events: list[str] = []

            def write(self, data: bytes) -> None:
                self.events.append(f"write:{data.hex()}")

            def read(self, count: int) -> bytes:
                self.events.append(f"read:{count}")
                return super().read(count)

        driver = NXW436("TEST")
        serial = TransactionSerial()
        driver.serial = serial  # type: ignore[assignment]
        barrier = threading.Barrier(2)

        def query() -> None:
            barrier.wait()
            driver.query_position_raw("az")

        def move() -> None:
            barrier.wait()
            driver.move("alt", "+", b"\x00\x00\xF5")

        first = threading.Thread(target=query)
        second = threading.Thread(target=move)
        first.start(); second.start(); first.join(); second.join()
        self.assertIn(serial.events, (
            ["write:01", "read:3", "write:1a0000f5"],
            ["write:1a0000f5", "write:01", "read:3"],
        ))


if __name__ == "__main__":
    unittest.main()
