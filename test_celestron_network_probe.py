from __future__ import annotations

import ast
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from celestron_network_probe import NetworkProbe, ProbeConfig, candidate_aux_frames, parse_args


def free_port(sock_type: int) -> int:
    with socket.socket(socket.AF_INET, sock_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ProbeTests(unittest.TestCase):
    def make_probe(self, root: Path) -> NetworkProbe:
        return NetworkProbe(ProbeConfig(
            bind="127.0.0.1", tcp_port=free_port(socket.SOCK_STREAM), udp_port=free_port(socket.SOCK_DGRAM),
            client="SkyPortal", scenario="CAP-01", notes="offline test", app_version=None,
            phone_os=None, network_mode="loopback", capture_root=root, max_read=8, max_hex=4,
            max_session_bytes=64, max_parser_buffer=32,
        ))

    def start_probe(self, probe: NetworkProbe) -> threading.Thread:
        thread = threading.Thread(target=probe.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while probe._tcp_listener is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(probe._tcp_listener)
        return thread

    def stop_probe(self, probe: NetworkProbe, thread: threading.Thread) -> None:
        probe.request_stop()
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_tcp_fragmentation_binary_and_capture_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            probe = self.make_probe(Path(temporary))
            thread = self.start_probe(probe)
            with socket.create_connection(("127.0.0.1", probe.config.tcp_port)) as client:
                client.sendall(b"\x3B\x03\x20")
                client.sendall(b"\x10\xFE\xCF\x00\xFF")
                time.sleep(0.1)
                client.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    client.recv(1)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                events_path = next(Path(temporary).iterdir()) / "tcp_events.jsonl"
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                if events and events[-1]["event"] == "disconnect":
                    break
                time.sleep(0.01)
            self.stop_probe(probe, thread)
            session = next(Path(temporary).iterdir())
            self.assertEqual((session / "tcp_raw.bin").read_bytes(), b"\x3B\x03\x20\x10\xFE\xCF\x00\xFF")
            events = [json.loads(line) for line in (session / "tcp_events.jsonl").read_text().splitlines()]
            self.assertEqual(events[0]["event"], "connect")
            self.assertEqual(events[-1]["event"], "disconnect")
            self.assertTrue(any(event["event"] == "recv" for event in events))
            self.assertTrue((session / "metadata.json").exists())
            self.assertTrue((session / "probe.log").exists())

    def test_udp_arbitrary_payload_is_logged_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            probe = self.make_probe(Path(temporary))
            thread = self.start_probe(probe)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"\x00\xFFnot-json", ("127.0.0.1", probe.config.udp_port))
            time.sleep(0.1)
            self.stop_probe(probe, thread)
            session = next(Path(temporary).iterdir())
            event = json.loads((session / "udp_events.jsonl").read_text().strip())
            self.assertEqual(event["payload_base64"], "AP9ub3QtanNvbg==")

    def test_tcp_input_limit_closes_only_that_session_without_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            probe = self.make_probe(Path(temporary))
            probe.config = ProbeConfig(**{**probe.config.__dict__, "max_session_bytes": 8})
            thread = self.start_probe(probe)
            with socket.create_connection(("127.0.0.1", probe.config.tcp_port)) as client:
                client.sendall(b"0123456789")
                client.settimeout(1)
                self.assertEqual(client.recv(1), b"")
            self.stop_probe(probe, thread)
            session = next(Path(temporary).iterdir())
            events = [json.loads(line) for line in (session / "tcp_events.jsonl").read_text().splitlines()]
            self.assertTrue(any(event["event"] == "input_limit_exceeded" for event in events))

    def test_candidate_aux_never_claims_malformed_frame_valid(self) -> None:
        buffer = bytearray(b"junk\x3B\x02\x20\x10\x3B\x03\x20\x10\xFE\xCF")
        frames = candidate_aux_frames(buffer)
        self.assertEqual(frames[0]["status"], "invalid_length")
        self.assertEqual(frames[1]["status"], "checksum_valid")

    def test_help_parsing_creates_no_probe_or_sockets(self) -> None:
        with patch("celestron_network_probe.NetworkProbe") as probe_class:
            with self.assertRaises(SystemExit) as exit_code:
                parse_args(["--help"])
        self.assertEqual(exit_code.exception.code, 0)
        probe_class.assert_not_called()

    def test_module_has_no_hardware_dependencies(self) -> None:
        tree = ast.parse(Path("celestron_network_probe.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse(any(name == "serial" or name.startswith("nxw436_") or name in {
            "mount_api", "fake_mount_backend", "nxw436_goto_relative"
        } for name in imported))


if __name__ == "__main__":
    unittest.main()
