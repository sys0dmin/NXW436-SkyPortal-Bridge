from __future__ import annotations

import ast
import logging
import socket
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from aux.dispatcher import AUXCapabilities, AUXDispatcher, SyntheticAUXProfile, VirtualMountIdentity
from aux.framing import deserialize, serialize
from aux.messages import AUXFrame, MC_GET_MODEL, MC_GET_POSITION, MC_GET_VER, MC_MOVE_POS
from aux.parser import AUXStreamParser
from aux.tcp_server import AUXTCPServer
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

    def test_test_only_position_adapter_handles_wrap_without_raw_leakage(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
        )
        fake = FakeMountBackend(az_position=99, alt_position=0)
        dispatcher = AUXDispatcher(
            MountController(fake), AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_POSITION, 0)})),
        )
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(result.status, "mount_query_reply")
        expected = ((99 * (1 << 24) + 50) // 100).to_bytes(3, "big")
        self.assertEqual(result.reply.payload, expected)
        self.assertEqual(adapter.from_aux("az", int.from_bytes(expected, "big")), 99)
        self.assertEqual(fake.commands, [])

    def test_position_backend_exception_produces_no_reply(self) -> None:
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
            alt=AxisCoordinateConfig(neutral_modulus=100, neutral_zero=0, aux_zero=0, direction=1),
        )
        controller = MountController(self.fake)
        dispatcher = AUXDispatcher(
            controller, AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_POSITION, 0)})),
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
        for path in Path("aux").glob("*.py"):
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
