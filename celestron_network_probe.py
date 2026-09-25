"""Passive PC-only TCP/UDP evidence capture for Celestron client research.

This is a diagnostic listener, not a mount frontend.  It never imports mount,
serial, or NXW436 modules and sends no application replies.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import select
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TCP_PORT = 2000
DEFAULT_UDP_PORT = 55555
DEFAULT_MAX_READ = 65536
DEFAULT_MAX_HEX = 256
DEFAULT_MAX_SESSION_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_PARSER_BUFFER = 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_component(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-_." else "_" for character in value)
    return cleaned.strip("._") or "unspecified"


def bounded_hex(payload: bytes, limit: int) -> str:
    shown = payload[:limit].hex().upper()
    return shown if len(payload) <= limit else f"{shown}...(+{len(payload) - limit} bytes)"


def printable(payload: bytes, limit: int) -> str:
    shown = payload[:limit]
    text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in shown)
    return text if len(payload) <= limit else f"{text}..."


def git_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def candidate_aux_frames(buffer: bytearray) -> list[dict[str, Any]]:
    """Extract only checksum-valid candidate AUX frames; never dispatch them."""
    candidates: list[dict[str, Any]] = []
    while True:
        try:
            start = buffer.index(0x3B)
        except ValueError:
            buffer.clear()
            return candidates
        if start:
            del buffer[:start]
        if len(buffer) < 2:
            return candidates
        length = buffer[1]
        total = length + 3
        if length < 3:
            candidates.append({"status": "invalid_length", "length": length})
            del buffer[0]
            continue
        if len(buffer) < total:
            return candidates
        frame = bytes(buffer[:total])
        del buffer[:total]
        checksum_valid = sum(frame[1:]) & 0xFF == 0
        candidates.append({
            "status": "checksum_valid" if checksum_valid else "bad_checksum",
            "length": length,
            "source": frame[2],
            "destination": frame[3],
            "command": frame[4],
            "frame_hex": frame.hex().upper(),
        })


@dataclass(frozen=True)
class ProbeConfig:
    bind: str
    tcp_port: int
    udp_port: int
    client: str
    scenario: str
    notes: str
    app_version: str | None
    phone_os: str | None
    network_mode: str | None
    capture_root: Path
    max_read: int
    max_hex: int
    max_session_bytes: int
    max_parser_buffer: int


class CaptureSession:
    def __init__(self, config: ProbeConfig) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = f"{stamp}-{safe_component(config.client)}-{safe_component(config.scenario)}"
        self.path = config.capture_root / name
        self.path.mkdir(parents=True, exist_ok=False)
        self._tcp_raw = (self.path / "tcp_raw.bin").open("ab")
        self._tcp_events = (self.path / "tcp_events.jsonl").open("a", encoding="utf-8")
        self._udp_events = (self.path / "udp_events.jsonl").open("a", encoding="utf-8")
        self._log = (self.path / "probe.log").open("a", encoding="utf-8")
        metadata = {
            "started_at": utc_now(),
            "application": config.client,
            "application_version": config.app_version,
            "phone_os": config.phone_os,
            "network_mode": config.network_mode,
            "scenario": config.scenario,
            "operator_notes": config.notes,
            "bind": config.bind,
            "tcp_port": config.tcp_port,
            "udp_port": config.udp_port,
            "passive": True,
            "candidate_aux_interpretation": "diagnostic only; not protocol confirmation",
            "git_revision": git_revision(),
        }
        (self.path / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.log("session created")

    def close(self) -> None:
        self.log("session closed")
        for stream in (self._tcp_raw, self._tcp_events, self._udp_events, self._log):
            stream.close()

    def log(self, message: str) -> None:
        self._log.write(f"{utc_now()} {message}\n")
        self._log.flush()

    def tcp_event(self, event: dict[str, Any], payload: bytes | None = None) -> None:
        event["timestamp"] = utc_now()
        if payload is not None:
            self._tcp_raw.write(payload)
            self._tcp_raw.flush()
        self._tcp_events.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._tcp_events.flush()

    def udp_event(self, event: dict[str, Any]) -> None:
        event["timestamp"] = utc_now()
        self._udp_events.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._udp_events.flush()


class NetworkProbe:
    def __init__(self, config: ProbeConfig) -> None:
        self.config = config
        self.session = CaptureSession(config)
        self._stop = threading.Event()
        self._aux_buffer = bytearray()
        self._tcp_bytes_received = 0
        self._tcp_listener: socket.socket | None = None
        self._udp_socket: socket.socket | None = None
        self._client_socket: socket.socket | None = None

    def request_stop(self) -> None:
        self._stop.set()

    def open_sockets(self) -> None:
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((self.config.bind, self.config.tcp_port))
        tcp.listen(1)
        tcp.setblocking(False)
        self._tcp_listener = tcp

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((self.config.bind, self.config.udp_port))
        udp.setblocking(False)
        self._udp_socket = udp
        self.session.log(f"listening tcp={tcp.getsockname()} udp={udp.getsockname()}")

    def close(self) -> None:
        for sock in (self._client_socket, self._tcp_listener, self._udp_socket):
            if sock is not None:
                sock.close()
        self.session.close()

    def run(self) -> None:
        self.open_sockets()
        try:
            while not self._stop.is_set():
                sockets = [sock for sock in (self._tcp_listener, self._udp_socket, self._client_socket) if sock]
                readable, _, _ = select.select(sockets, [], [], 0.2)
                for sock in readable:
                    if sock is self._tcp_listener:
                        self._accept_tcp()
                    elif sock is self._udp_socket:
                        self._receive_udp()
                    else:
                        self._receive_tcp()
        finally:
            self.close()

    def _accept_tcp(self) -> None:
        assert self._tcp_listener is not None
        client, address = self._tcp_listener.accept()
        client.setblocking(False)
        if self._client_socket is not None:
            self.session.tcp_event({"event": "connection_rejected", "peer": list(address)})
            client.close()
            return
        self._client_socket = client
        self._aux_buffer.clear()
        self._tcp_bytes_received = 0
        self.session.tcp_event({"event": "connect", "peer": list(address)})

    def _receive_tcp(self) -> None:
        assert self._client_socket is not None
        payload = self._client_socket.recv(self.config.max_read)
        peer = self._client_socket.getpeername()
        if not payload:
            self.session.tcp_event({"event": "disconnect", "peer": list(peer)})
            self._client_socket.close()
            self._client_socket = None
            self._aux_buffer.clear()
            return
        self._aux_buffer.extend(payload)
        self._tcp_bytes_received += len(payload)
        if self._tcp_bytes_received > self.config.max_session_bytes:
            self.session.tcp_event({
                "event": "input_limit_exceeded",
                "peer": list(peer),
                "received_bytes": self._tcp_bytes_received,
                "max_session_bytes": self.config.max_session_bytes,
            })
            self.session.log("tcp session closed after input limit")
            self._client_socket.close()
            self._client_socket = None
            self._aux_buffer.clear()
            return
        if len(self._aux_buffer) > self.config.max_parser_buffer:
            self._aux_buffer.clear()
            candidates = [{"status": "buffer_limit_exceeded"}]
        else:
            candidates = candidate_aux_frames(self._aux_buffer)
        self.session.tcp_event({
            "event": "recv",
            "peer": list(peer),
            "byte_count": len(payload),
            "hex": bounded_hex(payload, self.config.max_hex),
            "printable": printable(payload, self.config.max_hex),
            "candidate_aux_interpretation": candidates,
        }, payload)

    def _receive_udp(self) -> None:
        assert self._udp_socket is not None
        # UDP has message boundaries; a short receive buffer discards the
        # datagram on Windows, so receive the largest IPv4 payload intact.
        payload, source = self._udp_socket.recvfrom(65535)
        local = self._udp_socket.getsockname()
        self.session.udp_event({
            "event": "datagram",
            "source": list(source),
            "local": list(local),
            "byte_count": len(payload),
            "payload_base64": base64.b64encode(payload).decode("ascii"),
            "hex": bounded_hex(payload, self.config.max_hex),
            "printable": printable(payload, self.config.max_hex),
        })


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Passive PC-only Celestron network capture probe.")
    parser.add_argument("--bind", default="0.0.0.0", help="Local IPv4 address to listen on.")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--client", required=True, help="Application name, for example SkyPortal.")
    parser.add_argument("--scenario", required=True, help="Capture scenario, for example CAP-01.")
    parser.add_argument("--notes", default="", help="Operator notes saved verbatim in metadata.")
    parser.add_argument("--app-version")
    parser.add_argument("--phone-os")
    parser.add_argument("--network-mode", help="For example Android hotspot or shared Wi-Fi.")
    parser.add_argument("--capture-root", type=Path, default=Path("captures"))
    parser.add_argument("--max-read", type=int, default=DEFAULT_MAX_READ)
    parser.add_argument("--max-hex", type=int, default=DEFAULT_MAX_HEX)
    parser.add_argument("--max-session-bytes", type=int, default=DEFAULT_MAX_SESSION_BYTES)
    parser.add_argument("--max-parser-buffer", type=int, default=DEFAULT_MAX_PARSER_BUFFER)
    parser.add_argument("--passive", action="store_true", help="Explicitly document passive mode; it is always enabled.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if min(args.max_read, args.max_session_bytes, args.max_parser_buffer) <= 0 or args.max_hex < 0:
        raise SystemExit("byte limits must be positive and --max-hex must be non-negative")
    config = ProbeConfig(
        bind=args.bind, tcp_port=args.tcp_port, udp_port=args.udp_port,
        client=args.client, scenario=args.scenario, notes=args.notes,
        app_version=args.app_version, phone_os=args.phone_os,
        network_mode=args.network_mode, capture_root=args.capture_root,
        max_read=args.max_read, max_hex=args.max_hex,
        max_session_bytes=args.max_session_bytes, max_parser_buffer=args.max_parser_buffer,
    )
    probe = NetworkProbe(config)
    try:
        probe.run()
    except KeyboardInterrupt:
        probe.request_stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
