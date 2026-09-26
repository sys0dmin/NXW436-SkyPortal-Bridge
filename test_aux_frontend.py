from __future__ import annotations

import ast
import logging
import socket
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from celestron_aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from celestron_aux.dispatcher import AUXCapabilities, AUXDispatcher, SyntheticAUXProfile, VirtualMountIdentity
from celestron_aux.framing import deserialize, serialize
from celestron_aux.messages import AUXFrame, MC_GET_APPROACH, MC_GET_MAX_RATE, MC_GET_MAX_SLEW_RATE, MC_GET_MODEL, MC_GET_NEG_BACKLASH, MC_GET_POS_BACKLASH, MC_GET_POSITION, MC_GET_VER, MC_MOVE_POS, MC_SET_APPROACH
from celestron_aux.parser import AUXStreamParser
from celestron_aux.tcp_server import AUXTCPServer
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from celestron_aux.messages import MC_GET_AUTOGUIDE_RATE, MC_SET_AUTOGUIDE_RATE
from fake_mount_backend import FakeMountBackend
from mount_api import Axis, MountController, MountError


class AuxFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeMountBackend(az_position=12, alt_position=34)
        self.dispatcher = AUXDispatcher(MountController(self.fake))

    def test_serialize_parse_round_trip_and_checksum(self) -> None:
        frame = AUXFrame(0x20, 0x10, MC_GET_POSITION)
        wire = serialize(frame)
        self.assertEqual(wire, bytes.fromhex("3B03201001CC"))
        self.assertEqual(deserialize(wire), frame)
        self.assertEqual(sum(wire[1:]) & 0xFF, 0)

    def test_fragmented_coalesced_and_empty_payload_frames(self) -> None:
        first = serialize(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        second = serialize(AUXFrame(0x20, 0x11, MC_GET_POSITION))
        parser = AUXStreamParser()
        self.assertEqual(parser.feed(first[:3]), [])
        events = parser.feed(first[3:] + second)
        self.assertEqual([event.frame for event in events], [deserialize(first), deserialize(second)])

    def test_garbage_malformed_and_recovery(self) -> None:
        valid = serialize(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        parser = AUXStreamParser()
        self.assertEqual(parser.feed(b"garbage"), [])
        events = parser.feed(b"\x3B\x02" + valid[:-1] + b"\x00" + valid)
        self.assertTrue(any(event.error == "impossible_length" for event in events))
        self.assertTrue(any(event.error == "AUX checksum mismatch" for event in events))
        self.assertEqual(events[-1].frame, deserialize(valid))

    def test_incomplete_and_reset(self) -> None:
        frame = serialize(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        parser = AUXStreamParser()
        parser.feed(frame[:4])
        parser.reset()
        self.assertEqual(parser.feed(frame[4:]), [])
        events = parser.feed(frame)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].frame, deserialize(frame))

    def test_unknown_and_disabled_motion_do_not_change_fake_backend(self) -> None:
        before = list(self.fake.commands)
        unknown = self.dispatcher.dispatch(AUXFrame(0x20, 0x10, 0x99))
        motion = self.dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x09"))
        position = self.dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(unknown.status, "unsupported_unknown_command")
        self.assertEqual(motion.status, "recognized_motion_disabled")
        self.assertEqual(position.status, "recognized_synthetic_profile_unconfigured")
        self.assertEqual(self.fake.commands, before)
        self.assertEqual(self.fake.get_axis_status(Axis.AZ).position, 12)

    def test_test_only_identity_reply_swaps_addresses(self) -> None:
        dispatcher = AUXDispatcher(
            MountController(self.fake), identity=VirtualMountIdentity((7, 9)),
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_VER, 0)})),
        )
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_VER))
        self.assertEqual(result.status, "local_reply")
        self.assertEqual(result.reply, AUXFrame(0x10, 0x20, MC_GET_VER, b"\x07\x09"))
        self.assertEqual(self.fake.commands, [])

    def test_hbg3_model_reply_matches_observed_skyportal_tuple(self) -> None:
        dispatcher = AUXDispatcher(
            MountController(self.fake), identity=VirtualMountIdentity((3, 8)),
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_MODEL, 0)})),
        )
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_MODEL))
        self.assertEqual(result.reply, AUXFrame(0x10, 0x20, MC_GET_MODEL, b"\x11\x89"))
        self.assertEqual(serialize(result.reply), bytes.fromhex("3B0510200511892C"))
        for altered in (
            AUXFrame(0x21, 0x10, MC_GET_MODEL),
            AUXFrame(0x20, 0x11, MC_GET_MODEL),
            AUXFrame(0x20, 0x10, MC_GET_MODEL, b"\x00"),
        ):
            self.assertIsNone(dispatcher.dispatch(altered).reply)

    def test_unobserved_version_destinations_remain_no_reply(self) -> None:
        dispatcher = AUXDispatcher(
            MountController(self.fake), identity=VirtualMountIdentity((3, 8)),
            synthetic_profile=SyntheticAUXProfile(frozenset({
                (0x20, 0x10, MC_GET_VER, 0), (0x20, 0x11, MC_GET_VER, 0),
                (0x20, 0x10, MC_GET_MODEL, 0),
            })),
        )
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_VER)).reply,
                         AUXFrame(0x11, 0x20, MC_GET_VER, b"\x03\x08"))
        self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0xBD, MC_GET_VER)).reply)
        self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0xB9, MC_GET_VER)).reply)

    def test_hypothetical_zero_payload_ack_is_exact_and_has_no_backend_calls(self) -> None:
        profile = SyntheticAUXProfile(
            frozenset({(0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x11, MC_MOVE_POS, 1)}),
            hypothetical_zero_payload_ack_requests=frozenset({
                (0x20, 0x10, MC_MOVE_POS, b"\x00"),
                (0x20, 0x11, MC_MOVE_POS, b"\x00"),
            }),
        )
        dispatcher = AUXDispatcher(MountController(self.fake), synthetic_profile=profile)
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00"))
        self.assertEqual(result.status, "experimental_zero_payload_ack")
        self.assertEqual(serialize(result.reply), bytes.fromhex("3B03102024A9"))
        alt_result = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_MOVE_POS, b"\x00"))
        self.assertEqual(alt_result.status, "experimental_zero_payload_ack")
        self.assertEqual(serialize(alt_result.reply), bytes.fromhex("3B03112024A8"))
        for altered in (
            AUXFrame(0x21, 0x10, MC_MOVE_POS, b"\x00"),
            AUXFrame(0x20, 0x12, MC_MOVE_POS, b"\x00"),
            AUXFrame(0x20, 0x10, MC_GET_MODEL, b"\x00"),
            AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x01"),
            AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x00\x00"),
        ):
            self.assertIsNone(dispatcher.dispatch(altered).reply)
        self.assertEqual(self.fake.commands, [])

    def test_experimental_zero_backlash_is_exact_and_exclusive(self) -> None:
        profile = SyntheticAUXProfile(
            frozenset({(0x20, 0x10, MC_GET_POS_BACKLASH, 0), (0x20, 0x11, MC_GET_POS_BACKLASH, 0)}),
            experimental_zero_backlash_requests=frozenset({
                (0x20, 0x10, MC_GET_POS_BACKLASH, b""),
                (0x20, 0x11, MC_GET_POS_BACKLASH, b""),
            }),
        )
        dispatcher = AUXDispatcher(MountController(self.fake), synthetic_profile=profile)
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POS_BACKLASH))
        self.assertEqual(result.status, "experimental_zero_backlash")
        self.assertEqual(serialize(result.reply), bytes.fromhex("3B04102040008C"))
        alt_result = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_POS_BACKLASH))
        self.assertEqual(alt_result.status, "experimental_zero_backlash")
        self.assertEqual(serialize(AUXFrame(0x20, 0x11, MC_GET_POS_BACKLASH)), bytes.fromhex("3B032011408C"))
        self.assertEqual(serialize(alt_result.reply), bytes.fromhex("3B04112040008B"))
        for altered in (
            AUXFrame(0x21, 0x10, MC_GET_POS_BACKLASH),
            AUXFrame(0x20, 0x12, MC_GET_POS_BACKLASH),
            AUXFrame(0x20, 0x10, MC_GET_POS_BACKLASH, b"\x00"),
            AUXFrame(0x20, 0x10, MC_GET_NEG_BACKLASH),
        ):
            self.assertIsNone(dispatcher.dispatch(altered).reply)
        self.assertEqual(self.fake.commands, [])

    def test_experimental_opaque_approach_value_is_exact_and_exclusive(self) -> None:
        profile = SyntheticAUXProfile(
            frozenset({(0x20, 0x10, MC_GET_APPROACH, 0), (0x20, 0x11, MC_GET_APPROACH, 0)}),
            experimental_approach_value_00_requests=frozenset({
                (0x20, 0x10, MC_GET_APPROACH, b""),
                (0x20, 0x11, MC_GET_APPROACH, b""),
            }),
        )
        dispatcher = AUXDispatcher(MountController(self.fake), synthetic_profile=profile)
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_APPROACH))
        self.assertEqual(result.status, "experimental_approach_value_00")
        self.assertEqual(serialize(result.reply), bytes.fromhex("3B041020FC00D0"))
        alt_result = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_APPROACH))
        self.assertEqual(alt_result.status, "experimental_approach_value_00")
        self.assertEqual(serialize(AUXFrame(0x20, 0x11, MC_GET_APPROACH)), bytes.fromhex("3B032011FCD0"))
        self.assertEqual(serialize(alt_result.reply), bytes.fromhex("3B041120FC00CF"))
        for altered in (
            AUXFrame(0x20, 0x12, MC_GET_APPROACH),
            AUXFrame(0x20, 0x10, MC_GET_APPROACH, b"\x00"),
            AUXFrame(0x21, 0x10, MC_GET_APPROACH),
            AUXFrame(0x20, 0x10, MC_SET_APPROACH, b"\x00"),
        ):
            self.assertIsNone(dispatcher.dispatch(altered).reply)
        self.assertEqual(self.fake.commands, [])

    def test_experimental_hbg3_v38_max_slew_rate_is_exact_and_exclusive(self) -> None:
        profile = SyntheticAUXProfile(
            frozenset({(0x20, 0x10, MC_GET_MAX_SLEW_RATE, 0)}),
            experimental_hbg3_v38_max_slew_rate_requests=frozenset({(0x20, 0x10, MC_GET_MAX_SLEW_RATE, b"")}),
        )
        dispatcher = AUXDispatcher(MountController(self.fake), synthetic_profile=profile)
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_MAX_SLEW_RATE))
        self.assertEqual(result.status, "experimental_hbg3_v38_max_slew_rate")
        self.assertEqual(serialize(AUXFrame(0x20, 0x10, MC_GET_MAX_SLEW_RATE)), bytes.fromhex("3B03201021AC"))
        self.assertEqual(serialize(result.reply), bytes.fromhex("3B07102021A01194540F"))
        for altered in (
            AUXFrame(0x20, 0x11, MC_GET_MAX_SLEW_RATE),
            AUXFrame(0x21, 0x10, MC_GET_MAX_SLEW_RATE),
            AUXFrame(0x20, 0x10, MC_GET_MAX_SLEW_RATE, b"\x00"),
            AUXFrame(0x20, 0x10, MC_GET_MAX_RATE),
        ):
            self.assertIsNone(dispatcher.dispatch(altered).reply)
        self.assertEqual(self.fake.commands, [])

    def test_experimental_hbg3_v38_max_rate_is_exact_and_exclusive(self) -> None:
        profile = SyntheticAUXProfile(
            frozenset({(0x20, 0x10, MC_GET_MAX_RATE, 0)}),
            experimental_hbg3_v38_max_rate_requests=frozenset({(0x20, 0x10, MC_GET_MAX_RATE, b"")}),
        )
        dispatcher = AUXDispatcher(MountController(self.fake), synthetic_profile=profile)
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_MAX_RATE))
        self.assertEqual(result.status, "experimental_hbg3_v38_max_rate")
        self.assertEqual(serialize(AUXFrame(0x20, 0x10, MC_GET_MAX_RATE)), bytes.fromhex("3B03201023AA"))
        self.assertEqual(serialize(result.reply), bytes.fromhex("3B0410202300A9"))
        for altered in (
            AUXFrame(0x20, 0x11, MC_GET_MAX_RATE),
            AUXFrame(0x21, 0x10, MC_GET_MAX_RATE),
            AUXFrame(0x20, 0x10, MC_GET_MAX_RATE, b"\x00"),
        ):
            self.assertIsNone(dispatcher.dispatch(altered).reply)
        self.assertEqual(self.fake.commands, [])

    def test_virtual_autoguide_rate_is_stateful_and_axis_isolated(self) -> None:
        profile = SyntheticAUXProfile(frozenset({
            (0x20, 0x10, MC_GET_AUTOGUIDE_RATE, 0), (0x20, 0x11, MC_GET_AUTOGUIDE_RATE, 0),
            (0x20, 0x10, MC_SET_AUTOGUIDE_RATE, 1), (0x20, 0x11, MC_SET_AUTOGUIDE_RATE, 1),
        }))
        controllers = VirtualCelestronMotorControllers()
        dispatcher = AUXDispatcher(MountController(self.fake), synthetic_profile=profile, virtual_mcs=controllers)
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x80")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x80")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SET_AUTOGUIDE_RATE, b"\x33")).reply.payload, b"")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x33")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x80")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_SET_AUTOGUIDE_RATE, b"\x44")).reply.payload, b"")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x33")
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x44")
        malformed = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SET_AUTOGUIDE_RATE, b""))
        self.assertIsNone(malformed.reply)
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_AUTOGUIDE_RATE)).reply.payload, b"\x33")
        self.assertEqual(self.fake.commands, [])

    def test_virtual_configuration_gets_use_initial_values_without_backend(self) -> None:
        profile = SyntheticAUXProfile(frozenset({
            (0x20, 0x10, MC_GET_POS_BACKLASH, 0), (0x20, 0x11, MC_GET_POS_BACKLASH, 0),
            (0x20, 0x10, MC_GET_APPROACH, 0), (0x20, 0x11, MC_GET_APPROACH, 0),
        }))
        dispatcher = AUXDispatcher(
            MountController(self.fake), synthetic_profile=profile,
            virtual_mcs=VirtualCelestronMotorControllers(),
        )
        for destination, command in ((0x10, MC_GET_POS_BACKLASH), (0x11, MC_GET_POS_BACKLASH),
                                     (0x10, MC_GET_APPROACH), (0x11, MC_GET_APPROACH)):
            self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, destination, command)).reply.payload, b"\x00")
        self.assertEqual(self.fake.commands, [])

    def test_test_only_position_adapter_handles_wrap_without_raw_leakage(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
        )
        fake = FakeMountBackend(az_position=99, alt_position=0)
        dispatcher = AUXDispatcher(
            MountController(fake), AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_POSITION, 0)})),
            virtual_mcs=VirtualCelestronMotorControllers(),
        )
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(result.status, "mount_query_reply")
        expected = ((99 * (1 << 24) + 50) // 100).to_bytes(3, "big")
        self.assertEqual(result.reply.payload, expected)
        self.assertEqual(adapter.from_aux("az", int.from_bytes(expected, "big")), 99)
        self.assertEqual(fake.commands, [])

    def test_dynamic_virtual_mc_positions_are_axis_isolated_and_big_endian(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
        )
        fake = FakeMountBackend(az_position=0x10, alt_position=0x20)
        dispatcher = AUXDispatcher(
            MountController(fake), AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=SyntheticAUXProfile(frozenset({
                (0x20, 0x10, MC_GET_POSITION, 0), (0x20, 0x11, MC_GET_POSITION, 0),
            })),
            virtual_mcs=VirtualCelestronMotorControllers(),
        )
        az = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        alt = dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_GET_POSITION))
        self.assertEqual(az.reply.payload, bytes.fromhex("100000"))
        self.assertEqual(alt.reply.payload, bytes.fromhex("200000"))
        self.assertEqual(serialize(az.reply), bytes.fromhex("3B06102001100000B9"))
        self.assertEqual(serialize(alt.reply), bytes.fromhex("3B06112001200000A8"))
        self.assertEqual(fake.commands, [])

    def test_dynamic_position_rejects_malformed_and_unknown_destination(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
        )
        dispatcher = AUXDispatcher(
            MountController(self.fake), AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_POSITION, 0)})),
            virtual_mcs=VirtualCelestronMotorControllers(),
        )
        self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION, b"\x00")).reply)
        self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0x12, MC_GET_POSITION)).reply)
        self.assertEqual(self.fake.commands, [])

    def test_position_backend_exception_produces_no_reply(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
        )
        controller = MountController(self.fake)
        dispatcher = AUXDispatcher(
            controller, AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_POSITION, 0)})),
            virtual_mcs=VirtualCelestronMotorControllers(),
        )
        with patch.object(controller, "get_az_position", side_effect=MountError("offline")):
            result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(result.status, "mount_query_error")
        self.assertIsNone(result.reply)

    def test_tcp_records_decoded_frame_and_disabled_dispatch(self) -> None:
        logger = logging.getLogger("aux-test")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        server = AUXTCPServer(self.dispatcher, logger=logger)
        records = server.handle_chunk(AUXStreamParser(), ("127.0.0.1", 1234),
                                      serialize(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x01")))
        self.assertEqual(records[0]["dispatch_result"], "recognized_motion_disabled")
        self.assertEqual(records[0]["payload_hex"], "01")

    def test_tcp_connection_sends_no_reply_and_resets_per_session(self) -> None:
        logger = logging.getLogger("aux-loopback-test")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        server = AUXTCPServer(self.dispatcher, logger=logger)
        server_side, client_side = socket.socketpair()
        try:
            thread = threading.Thread(
                target=server.handle_connection, args=(server_side, ("127.0.0.1", 2000)), daemon=True
            )
            thread.start()
            client_side.sendall(serialize(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x01")))
            client_side.settimeout(0.1)
            with self.assertRaises(socket.timeout):
                client_side.recv(1)
            client_side.close()
            thread.join(1)
            self.assertFalse(thread.is_alive())
        finally:
            server_side.close()
            client_side.close()
        self.assertEqual(self.fake.commands, [])

    def test_aux_modules_do_not_import_hardware_or_coordinate_modules(self) -> None:
        forbidden = {"serial", "mount_model", "nxw436_driver", "nxw436_mount_backend", "nxw436_position_controller"}
        for path in Path("celestron_aux").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            self.assertFalse(imported & forbidden, path)


if __name__ == "__main__":
    unittest.main()
