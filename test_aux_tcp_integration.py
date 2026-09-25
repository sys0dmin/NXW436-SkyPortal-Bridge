from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
import ast
from pathlib import Path

from aux.dispatcher import AUXDispatcher, SyntheticAUXProfile, VirtualMountIdentity
from aux.framing import deserialize, serialize
from aux.messages import AUXFrame, MC_GET_POSITION, MC_GET_VER
from aux.tcp_server import AUXTCPServer
from fake_mount_backend import FakeMountBackend
from mount_api import MountController


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AuxTcpIntegrationTests(unittest.TestCase):
    def test_live_tcp_recovers_persists_raw_and_never_replies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = FakeMountBackend()
            port = free_port()
            server = AUXTCPServer(
                AUXDispatcher(MountController(fake)), bind="127.0.0.1", port=port,
                capture_root=Path(temporary), client_label="SkyPortal",
            )
            stop_event = threading.Event()
            ready_event = threading.Event()
            thread = threading.Thread(target=server.serve_until, args=(stop_event, ready_event), daemon=True)
            thread.start()
            self.assertTrue(ready_event.wait(1))
            valid = serialize(AUXFrame(0x20, 0x10, MC_GET_POSITION))
            invalid = valid[:-1] + b"\x00"
            sent = bytearray()
            with socket.create_connection(("127.0.0.1", port)) as client:
                client.sendall(valid)
                sent.extend(valid)
                client.sendall(valid[:2])
                client.sendall(valid[2:])
                sent.extend(valid)
                client.sendall(valid + valid)
                sent.extend(valid + valid)
                client.sendall(b"junk" + valid)
                sent.extend(b"junk" + valid)
                client.sendall(invalid + valid)
                sent.extend(invalid + valid)
                client.sendall(valid[:3])
                sent.extend(valid[:3])
                client.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    client.recv(1)
            with socket.create_connection(("127.0.0.1", port)) as client:
                client.sendall(valid)
                sent.extend(valid)
                client.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    client.recv(1)
            time.sleep(0.1)
            stop_event.set()
            thread.join(1)
            server.close()
            self.assertFalse(thread.is_alive())
            session = next(Path(temporary).iterdir())
            self.assertEqual((session / "tcp_raw.bin").read_bytes(), bytes(sent))
            metadata = json.loads((session / "metadata.json").read_text())
            self.assertFalse(metadata["outbound_replies"])
            records = [json.loads(line) for line in (session / "aux_frames.jsonl").read_text().splitlines()]
            self.assertTrue(any(record["dispatch_result"] == "parser_error:AUX checksum mismatch" for record in records))
            valid_records = [record for record in records if record["checksum_valid"]]
            self.assertGreaterEqual(len(valid_records), 7)
            self.assertEqual({record["connection_id"] for record in valid_records}, {"tcp-000001", "tcp-000002"})
            self.assertEqual(fake.commands, [])
            self.assertTrue((session / "server.log").exists())

    def test_default_dispatcher_has_no_reply_for_recognized_action(self) -> None:
        result = AUXDispatcher(MountController(FakeMountBackend())).dispatch(
            AUXFrame(0x20, 0x10, 0x24, b"\x01")
        )
        self.assertIsNone(result.reply)

    def test_local_reply_is_sent_and_persisted_as_exact_tx_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = AUXTCPServer(
                AUXDispatcher(
                    MountController(FakeMountBackend()), identity=VirtualMountIdentity((3, 8)),
                    synthetic_profile=SyntheticAUXProfile(frozenset({(0x20, 0x10, MC_GET_VER, 0)})),
                ),
                capture_root=Path(temporary), client_label="SkyPortal", allow_synthetic_replies=True,
                synthetic_profile_name="unit-test-version",
            )
            server_side, client_side = socket.socketpair()
            try:
                thread = threading.Thread(
                    target=server.handle_connection, args=(server_side, ("127.0.0.1", 2000)), daemon=True
                )
                thread.start()
                request = serialize(AUXFrame(0x20, 0x10, MC_GET_VER))
                client_side.sendall(request)
                reply = client_side.recv(64)
                self.assertEqual(deserialize(reply), AUXFrame(0x10, 0x20, MC_GET_VER, b"\x03\x08"))
                client_side.close()
                thread.join(1)
            finally:
                server_side.close()
                client_side.close()
            server.close()
            session = next(Path(temporary).iterdir())
            self.assertEqual((session / "tcp_tx.bin").read_bytes(), reply)
            self.assertTrue(json.loads((session / "metadata.json").read_text())["synthetic_not_client_capture"])
            tx_record = json.loads((session / "aux_tx_frames.jsonl").read_text().strip())
            self.assertEqual(tx_record["tx_hex"], reply.hex().upper())


if __name__ == "__main__":
    unittest.main()
