from __future__ import annotations

import unittest
from unittest.mock import patch

from fake_mount_backend import FakeMountBackend
from mount_api import Axis, Direction, MountController, MountStateError, PositionFrameError, SpeedTier, POSITION_MODULUS
from mount_model import POSITION_MODULUS as CANONICAL_POSITION_MODULUS
from nxw436_driver import COUNTS_PER_REV, RAW_MODULO
from nxw436_mount_backend import NXW436MountBackend


class RecordingTransport:
    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def query_position_raw(self, axis: str) -> bytes:
        return (42 if axis == "az" else 99).to_bytes(3, "big")

    def move(self, axis: str, direction: str, payload: bytes) -> None:
        self.commands.append(("move", axis, direction, payload))

    def stop(self, axis: str, direction: str | None = None) -> None:
        self.commands.append(("stop", axis, direction))


class ControllerReturningUndershoot:
    def __init__(self, transport: RecordingTransport, axis: str, delta: int) -> None:
        self.transport = transport
        self.axis = axis
        self.delta = delta

    def run(self, **_: object) -> dict[str, object]:
        return {
            "final_raw": 95,
            "final_error_counts": 5,
            "settled_outcome": "undershoot",
        }


class MountApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeMountBackend(az_position=POSITION_MODULUS - 3, alt_position=12)
        self.mount = MountController(self.fake)

    def test_named_axis_operations_keep_protocol_out_of_frontend(self) -> None:
        self.mount.move_az(Direction.PLUS, SpeedTier.MEDIUM)
        self.fake.advance(Axis.AZ, 5)
        self.assertEqual(self.mount.get_az_position(), 2)
        self.assertTrue(self.mount.get_axis_status(Axis.AZ).motion_commanded)
        self.mount.stop_az()
        self.assertFalse(self.mount.get_axis_status(Axis.AZ).motion_commanded)
        self.assertEqual(self.fake.commands[0], ("move", Axis.AZ, Direction.PLUS, SpeedTier.MEDIUM))

    def test_position_modulus_has_one_neutral_canonical_owner(self) -> None:
        self.assertEqual(CANONICAL_POSITION_MODULUS, POSITION_MODULUS)
        self.assertEqual(RAW_MODULO, CANONICAL_POSITION_MODULUS)
        self.assertEqual(COUNTS_PER_REV, CANONICAL_POSITION_MODULUS)

    def test_minus_direction_wraps_in_the_other_direction(self) -> None:
        self.mount.move_alt(Direction.MINUS, SpeedTier.SLOW)
        self.fake.advance(Axis.ALT, 20)
        self.assertEqual(self.mount.get_alt_position(), POSITION_MODULUS - 8)
        self.mount.stop_alt()

    def test_fake_goto_is_absolute_and_uses_shortest_modular_delta(self) -> None:
        result = self.mount.goto_az(4)
        self.assertTrue(result.completed)
        self.assertTrue(result.target_acquired)
        self.assertEqual(result.final_error_counts, 0)
        self.assertEqual(result.requested_delta, 7)
        self.assertEqual(self.mount.get_az_position(), 4)
        reverse = self.mount.goto_az(POSITION_MODULUS - 2)
        self.assertEqual(reverse.requested_delta, -6)

    def test_stop_without_prior_motion_is_rejected(self) -> None:
        with self.assertRaises(MountStateError):
            self.mount.stop_az()

    def test_status_makes_unknown_direction_stop_limitation_explicit(self) -> None:
        status = self.mount.get_axis_status(Axis.AZ)
        self.assertFalse(status.safe_stop_available)
        self.assertEqual(status.safe_stop_unavailable_reason, "no direction is known in this backend process")

    def test_nxw436_adapter_maps_named_speed_to_preserved_profile(self) -> None:
        transport = RecordingTransport()
        backend = NXW436MountBackend(transport)
        backend.move(Axis.AZ, Direction.PLUS, SpeedTier.MEDIUM)
        backend.stop(Axis.AZ)
        self.assertEqual(transport.commands, [
            ("move", "az", "+", bytes.fromhex("0072F1")),
            ("stop", "az", "+"),
        ])
        # The actual NXW436 driver retains the experimentally required STOP x2;
        # this adapter must not substitute a different stop command or direction.

    def test_nxw436_adapter_rejects_short_position_frame(self) -> None:
        transport = RecordingTransport()
        transport.query_position_raw = lambda _axis: b"\x01\x02"  # type: ignore[method-assign]
        with self.assertRaises(PositionFrameError):
            NXW436MountBackend(transport).get_position(Axis.AZ)

    def test_nxw436_goto_completion_is_not_exact_target_acquisition(self) -> None:
        transport = RecordingTransport()
        backend = NXW436MountBackend(transport)
        with patch("nxw436_mount_backend.RelativePositionController", ControllerReturningUndershoot):
            result = backend.goto(Axis.AZ, 100)
        self.assertTrue(result.completed)
        self.assertFalse(result.target_acquired)
        self.assertEqual(result.final_position, 95)
        self.assertEqual(result.final_error_counts, 5)
        self.assertEqual(result.settled_outcome, "undershoot")


if __name__ == "__main__":
    unittest.main()
