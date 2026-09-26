from __future__ import annotations

import unittest
from unittest.mock import patch

from celestron_aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from celestron_aux.dispatcher import AUXCapabilities, AUXDispatcher, SyntheticAUXProfile
from celestron_aux.messages import AUXFrame, MC_GET_POSITION, MC_MOVE_NEG, MC_MOVE_POS
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from fake_mount_backend import FakeMountBackend
from mount_api import Axis, Direction, MountController, MountError, SpeedTier
from mount_model import POSITION_MODULUS


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeMountMotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.backend = FakeMountBackend(monotonic_clock=self.clock)

    def test_axes_directions_speeds_wrap_and_stop_are_deterministic(self) -> None:
        self.backend.move(Axis.AZ, Direction.PLUS, SpeedTier.FAST)
        self.clock.advance(1)
        self.assertEqual(self.backend.get_position(Axis.AZ), 1000)
        self.assertEqual(self.backend.get_position(Axis.ALT), 0)
        self.backend.move(Axis.AZ, Direction.MINUS, SpeedTier.MEDIUM)
        self.clock.advance(1)
        self.assertEqual(self.backend.get_position(Axis.AZ), 800)
        self.backend.move(Axis.ALT, Direction.PLUS, SpeedTier.SLOW)
        self.clock.advance(1)
        self.assertEqual(self.backend.get_position(Axis.ALT), 10)
        self.backend.move(Axis.ALT, Direction.MINUS, SpeedTier.FINE)
        self.clock.advance(1)
        self.assertEqual(self.backend.get_position(Axis.ALT), POSITION_MODULUS - 40)
        self.backend.stop(Axis.AZ)
        frozen = self.backend.get_position(Axis.AZ)
        self.clock.advance(5)
        self.assertEqual(self.backend.get_position(Axis.AZ), frozen)
        self.backend = FakeMountBackend(az_position=POSITION_MODULUS - 5, monotonic_clock=self.clock)
        self.backend.move(Axis.AZ, Direction.PLUS, SpeedTier.SLOW)
        self.clock.advance(1)
        self.assertEqual(self.backend.get_position(Axis.AZ), 5)

    def test_aux_manual_motion_updates_position_and_ack_only_after_backend_call(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
        )
        profile = SyntheticAUXProfile(
            frozenset({
                (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x10, MC_MOVE_NEG, 1),
                (0x20, 0x10, MC_GET_POSITION, 0),
            }),
            simulated_manual_motion_requests=frozenset({
                (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x10, MC_MOVE_NEG, 1),
            }),
        )
        dispatcher = AUXDispatcher(
            MountController(self.backend), AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=profile, virtual_mcs=VirtualCelestronMotorControllers(),
        )
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x09")).reply.payload, b"")
        self.clock.advance(1)
        position = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(position.reply.payload, bytes.fromhex("E80000"))
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_NEG, b"\x05")).reply.payload, b"")
        self.clock.advance(1)
        self.assertLess(self.backend.get_position(Axis.AZ), 1000)
        self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x0A")).reply)
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00")).reply.payload, b"")
        frozen = self.backend.get_position(Axis.AZ)
        self.clock.advance(2)
        self.assertEqual(self.backend.get_position(Axis.AZ), frozen)

    def test_manual_backend_failure_does_not_ack(self) -> None:
        profile = SyntheticAUXProfile(
            frozenset({(0x20, 0x10, MC_MOVE_POS, 1)}),
            simulated_manual_motion_requests=frozenset({(0x20, 0x10, MC_MOVE_POS, 1)}),
        )
        controller = MountController(self.backend)
        dispatcher = AUXDispatcher(
            controller, synthetic_profile=profile,
            virtual_mcs=VirtualCelestronMotorControllers(),
        )
        with patch.object(controller, "move_az", side_effect=MountError("backend failed")):
            result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x09"))
        self.assertEqual(result.status, "simulated_manual_motion_rejected")
        self.assertIsNone(result.reply)
        self.assertEqual(self.backend.commands, [])


if __name__ == "__main__":
    unittest.main()
