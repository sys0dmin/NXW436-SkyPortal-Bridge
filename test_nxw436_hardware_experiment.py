from __future__ import annotations

import unittest

from celestron_aux.dispatcher import AUXCapabilities, AUXDispatcher, VirtualMountIdentity
from celestron_aux.messages import AUXFrame, MC_GET_MODEL, MC_GET_POSITION, MC_GET_VER, MC_MOVE_NEG, MC_MOVE_POS
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from mount_api import MountController
from mount_model import POSITION_MODULUS
from nxw436_hardware_experiment import build_hardware_profile, hardware_speed_policy, session_zero_adapter
from nxw436_mount_backend import NXW436MountBackend


class RecordingHardwareTransport:
    def __init__(self, az: int = 100, alt: int = 200) -> None:
        self.positions = {"az": az, "alt": alt}
        self.commands: list[tuple] = []
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def query_position_raw(self, axis: str) -> bytes:
        return self.positions[axis].to_bytes(3, "big")

    def move(self, axis: str, direction: str, payload: bytes) -> None:
        self.commands.append(("move", axis, direction, payload))

    def stop(self, axis: str, direction: str | None = None) -> None:
        self.commands.append(("stop", axis, direction))


class NXW436HardwareExperimentTests(unittest.TestCase):
    def make_dispatcher(self) -> tuple[AUXDispatcher, RecordingHardwareTransport]:
        transport = RecordingHardwareTransport()
        backend = NXW436MountBackend(transport)
        adapter = session_zero_adapter(backend, az_direction=1, alt_direction=1)
        dispatcher = AUXDispatcher(
            MountController(backend), AUXCapabilities(position_translation_enabled=True),
            coordinate_adapter=adapter, identity=VirtualMountIdentity((3, 8)),
            synthetic_profile=build_hardware_profile(),
            virtual_mcs=VirtualCelestronMotorControllers(manual_rate_to_speed=hardware_speed_policy),
        )
        return dispatcher, transport

    def test_explicit_rates_map_to_verified_payloads_for_both_axes_and_directions(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        cases = (
            (0x10, MC_MOVE_POS, 0x02, "az", "+", "0000F5"),
            (0x10, MC_MOVE_NEG, 0x05, "az", "-", "003978"),
            (0x11, MC_MOVE_POS, 0x07, "alt", "+", "0072F1"),
            (0x11, MC_MOVE_NEG, 0x05, "alt", "-", "003978"),
            (0x10, MC_MOVE_NEG, 0x09, "az", "-", "00E5E3"),
            (0x11, MC_MOVE_POS, 0x09, "alt", "+", "00E5E3"),
        )
        for destination, command, rate, axis, direction, payload in cases:
            result = dispatcher.dispatch(AUXFrame(0x20, destination, command, bytes((rate,))))
            self.assertEqual(result.status, "simulated_manual_motion_accepted")
            self.assertEqual(result.reply.payload, b"")
            self.assertEqual(transport.commands[-1], ("move", axis, direction, bytes.fromhex(payload)))
        count = len(transport.commands)
        for rate in (0x08, 0x0A):
            self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, bytes((rate,)))).reply)
        self.assertEqual(len(transport.commands), count)

    def test_negative_move_then_skyportal_pos_zero_stops_negative_direction(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_MOVE_NEG, b"\x02")).reply)
        stop = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_MOVE_POS, b"\x00"))
        self.assertEqual(stop.status, "simulated_manual_stop_accepted")
        self.assertEqual(transport.commands[-1], ("stop", "alt", "-"))

    def test_fresh_startup_stop_is_frontend_idle_ack_without_uart(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_VER)).reply)
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_MODEL)).reply)
        for destination in (0x10, 0x11):
            result = dispatcher.dispatch(AUXFrame(0x20, destination, MC_MOVE_POS, b"\x00"))
            self.assertEqual(result.status, "frontend_idle_stop_ack")
            self.assertEqual(result.reply.payload, b"")
        self.assertEqual(transport.commands, [])

    def test_positive_move_then_stop_uses_positive_direction(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x02")).reply)
        stop = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00"))
        self.assertEqual(stop.status, "simulated_manual_stop_accepted")
        self.assertEqual(transport.commands[-1], ("stop", "az", "+"))

    def test_failed_owned_stop_has_no_ack_and_keeps_owned_state(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_NEG, b"\x02")).reply)
        def fail_stop(_axis: str, _direction: str | None = None) -> None:
            raise RuntimeError("serial stop failure")
        transport.stop = fail_stop  # type: ignore[method-assign]
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00"))
        self.assertEqual(result.status, "simulated_manual_stop_rejected")
        self.assertIsNone(result.reply)
        repeat = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00"))
        self.assertEqual(repeat.status, "simulated_manual_stop_rejected")
        self.assertIsNone(repeat.reply)

    def test_negative_stop_ack_survives_post_stop_position_timeout(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_NEG, b"\x02")).reply)
        transport.query_position_raw = lambda _axis: None  # type: ignore[method-assign]
        stop = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00"))
        self.assertEqual(stop.status, "simulated_manual_stop_accepted")
        self.assertEqual(stop.reply.payload, b"")
        self.assertEqual(transport.commands[-1], ("stop", "az", "-"))
        # Ownership transitioned to idle despite best-effort telemetry failure.
        again = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00"))
        self.assertEqual(again.status, "frontend_idle_stop_ack")

    def test_positive_stop_ack_survives_post_stop_position_exception(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        self.assertIsNotNone(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_MOVE_POS, b"\x02")).reply)
        def fail_position(_axis: str) -> bytes:
            raise RuntimeError("position timeout")
        transport.query_position_raw = fail_position  # type: ignore[method-assign]
        stop = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_MOVE_POS, b"\x00"))
        self.assertEqual(stop.status, "simulated_manual_stop_accepted")
        self.assertEqual(stop.reply.payload, b"")
        self.assertEqual(transport.commands[-1], ("stop", "alt", "+"))

    def test_hardware_backend_failure_has_no_success_ack(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        def fail_move(_axis: str, _direction: str, _payload: bytes) -> None:
            raise RuntimeError("serial timeout")
        transport.move = fail_move  # type: ignore[method-assign]
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x02"))
        self.assertEqual(result.status, "simulated_manual_motion_rejected")
        self.assertIsNone(result.reply)

    def test_hardware_position_uses_session_zero_adapter(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        transport.positions["az"] = 101
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(result.status, "mount_query_reply")
        expected = ((1 << 24) + POSITION_MODULUS // 2) // POSITION_MODULUS
        self.assertEqual(result.reply.payload, expected.to_bytes(3, "big"))

    def test_raw_position_changes_and_telemetry_are_axis_independent(self) -> None:
        transport = RecordingHardwareTransport(az=POSITION_MODULUS - 2, alt=200)
        backend = NXW436MountBackend(transport)
        adapter = session_zero_adapter(backend, az_direction=1, alt_direction=1)
        events: list[tuple[int, int, int]] = []
        virtual = VirtualCelestronMotorControllers(position_observer=lambda destination, raw, aux: events.append((destination, raw, aux)))
        dispatcher = AUXDispatcher(
            MountController(backend), AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=build_hardware_profile(), virtual_mcs=virtual,
        )
        transport.positions["az"] = 1
        transport.positions["alt"] = 205
        az = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        alt = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_POSITION))
        self.assertNotEqual(az.reply.payload, b"\x00\x00\x00")
        self.assertNotEqual(alt.reply.payload, b"\x00\x00\x00")
        self.assertEqual(events[0][0:2], (0x10, 1))
        self.assertEqual(events[1][0:2], (0x11, 205))
        self.assertNotEqual(events[0][2], events[1][2])

    def test_startup_guide_rate_is_inert(self) -> None:
        dispatcher, transport = self.make_dispatcher()
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, 0x06, b"\x00\x00\x00"))
        self.assertEqual(result.status, "recognized_unsupported")
        self.assertIsNone(result.reply)
        self.assertEqual(transport.commands, [])


if __name__ == "__main__":
    unittest.main()
