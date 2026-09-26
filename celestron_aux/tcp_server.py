"""PC-side TCP listener that logs AUX parser/dispatcher events without replies."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
import time
from typing import Callable
from datetime import datetime, timezone
from pathlib import Path

from celestron_aux.dispatcher import AUXDispatcher
from celestron_aux.framing import serialize
from celestron_aux.parser import AUXStreamParser, ParserEvent


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class AUXCaptureSession:
    """Primary raw TCP evidence plus a separate parser interpretation log."""

    def __init__(self, root: Path, *, client: str, bind: str, port: int,
                 outbound_replies: bool, synthetic_profile_name: str | None,
                 metadata_extra: dict[str, object] | None) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = root / f"{stamp}-{client}-AUX"
        self.path.mkdir(parents=True, exist_ok=False)
        self._raw = (self.path / "tcp_raw.bin").open("ab")
        self._frames = (self.path / "aux_frames.jsonl").open("a", encoding="utf-8")
        self._tx_raw = (self.path / "tcp_tx.bin").open("ab")
        self._tx_frames = (self.path / "aux_tx_frames.jsonl").open("a", encoding="utf-8")
        self._log = (self.path / "server.log").open("a", encoding="utf-8")
        metadata = {
            "started_at": _timestamp(), "client": client, "bind": bind, "port": port,
            "outbound_replies": outbound_replies,
            "synthetic_profile": synthetic_profile_name,
            "synthetic_not_client_capture": outbound_replies,
            "interpretation": "aux_frames.jsonl is diagnostic; tcp_raw.bin is primary evidence",
        }
        if metadata_extra:
            metadata.update(metadata_extra)
        (self.path / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_raw(self, chunk: bytes) -> None:
        self._raw.write(chunk)
        self._raw.flush()

    def write_record(self, record: dict[str, object]) -> None:
        self._frames.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._frames.flush()

    def write_tx(self, record: dict[str, object], wire: bytes) -> None:
        self._tx_raw.write(wire)
        self._tx_raw.flush()
        self._tx_frames.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._tx_frames.flush()

    def log(self, message: str) -> None:
        self._log.write(f"{_timestamp()} {message}\n")
        self._log.flush()

    def close(self, reason: str = "unspecified") -> None:
        self.log(f"tcp_server_closing reason={reason}")
        self._raw.close()
        self._frames.close()
        self._tx_raw.close()
        self._tx_frames.close()
        self._log.close()


class AUXTCPServer:
    def __init__(self, dispatcher: AUXDispatcher, *, bind: str = "0.0.0.0", port: int = 2000,
                 logger: logging.Logger | None = None, capture_root: Path | None = None,
                 client_label: str = "SkyPortal", allow_synthetic_replies: bool = False,
                  synthetic_profile_name: str | None = None,
                  record_observer: Callable[[dict[str, object]], None] | None = None,
                  metadata_extra: dict[str, object] | None = None,
                  client_idle_timeout: float = 15.0) -> None:
        self.dispatcher = dispatcher
        self.bind = bind
        self.port = port
        self.logger = logger or logging.getLogger("celestron_aux.tcp_server")
        self.allow_synthetic_replies = allow_synthetic_replies
        self.record_observer = record_observer
        self.capture = (AUXCaptureSession(capture_root, client=client_label, bind=bind, port=port,
                                           outbound_replies=allow_synthetic_replies,
                                           synthetic_profile_name=synthetic_profile_name,
                                           metadata_extra=metadata_extra)
                        if capture_root is not None else None)
        self._connection_count = 0
        self._active_connection = False
        self.client_idle_timeout = client_idle_timeout

    @property
    def active_connection(self) -> bool:
        return self._active_connection

    def handle_chunk(self, parser: AUXStreamParser, peer: tuple[str, int], chunk: bytes,
                     connection_id: str = "direct", client: socket.socket | None = None) -> list[dict[str, object]]:
        if self.capture is not None:
            self.capture.write_raw(chunk)
            self.capture.log(f"rx_frame connection={connection_id} byte_count={len(chunk)}")
        records: list[dict[str, object]] = []
        for event in parser.feed(chunk):
            record = self._record_event(peer, event, connection_id)
            records.append(record)
            if client is not None and self.allow_synthetic_replies and "tx_wire" in record:
                wire = record.pop("tx_wire")
                assert isinstance(wire, bytes)
                client.sendall(wire)
                if self.capture is not None:
                    self.capture.write_tx({key: value for key, value in record.items() if key != "tx_wire"}, wire)
        return records

    def _record_event(self, peer: tuple[str, int], event: ParserEvent, connection_id: str) -> dict[str, object]:
        record: dict[str, object] = {
            "timestamp": _timestamp(), "peer": f"{peer[0]}:{peer[1]}",
            "connection_id": connection_id, "raw_hex": event.raw.hex().upper(),
            "checksum_valid": event.checksum_valid,
        }
        if event.frame is None:
            record.update({"source": None, "destination": None, "command": None,
                           "payload_hex": None, "dispatch_result": f"parser_error:{event.error}"})
        else:
            frame = event.frame
            result = self.dispatcher.dispatch(frame)
            record.update({"source": frame.source, "destination": frame.destination,
                           "command": frame.command, "payload_hex": frame.payload.hex().upper(),
                           "dispatch_result": result.status})
            if result.reply is not None:
                wire = serialize(result.reply)
                record["tx_hex"] = wire.hex().upper()
                record["tx_wire"] = wire
        self.logger.info(json.dumps({key: value for key, value in record.items() if key != "tx_wire"}, sort_keys=True))
        if self.capture is not None:
            self.capture.write_record({key: value for key, value in record.items() if key != "tx_wire"})
        if self.record_observer is not None:
            self.record_observer({key: value for key, value in record.items() if key != "tx_wire"})
        return record

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind, self.port))
            listener.listen(1)
            while True:
                client, peer = listener.accept()
                with client:
                    self.handle_connection(client, peer)

    def handle_connection(self, client: socket.socket, peer: tuple[str, int],
                          stop_event: threading.Event | None = None) -> None:
        """Consume one stream; passive mode intentionally sends no bytes."""
        parser = AUXStreamParser()
        self._connection_count += 1
        connection_id = f"tcp-{self._connection_count:06d}"
        self._active_connection = True
        if self.capture is not None:
            self.capture.log(f"client_connected connection={connection_id} peer={peer[0]}:{peer[1]}")
        try:
            if hasattr(client, "settimeout"):
                client.settimeout(0.5)
            last_rx = time.monotonic()
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    if time.monotonic() - last_rx >= self.client_idle_timeout:
                        if self.capture is not None:
                            self.capture.log(f"client_timeout connection={connection_id}")
                        break
                    continue
                except ConnectionResetError:
                    if self.capture is not None:
                        self.capture.log(f"client_reset connection={connection_id}")
                    break
                except OSError as error:
                    if self.capture is not None:
                        self.capture.log(f"client_reset connection={connection_id} error={type(error).__name__}")
                    break
                if not chunk:
                    if self.capture is not None:
                        self.capture.log(f"client_eof connection={connection_id}")
                    break
                last_rx = time.monotonic()
                self.handle_chunk(parser, peer, chunk, connection_id, client)
        finally:
            self._active_connection = False
            if self.capture is not None:
                self.capture.log(f"client_handler_exit connection={connection_id}")

    def serve_until(self, stop_event: threading.Event, ready_event: threading.Event | None = None) -> None:
        """Test-friendly listener loop; production callers normally use serve_forever."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind, self.port))
            listener.listen(1)
            listener.settimeout(0.1)
            if self.capture is not None:
                self.capture.log(f"tcp_server_started bind={self.bind}:{self.port}")
            if ready_event is not None:
                ready_event.set()
            while not stop_event.is_set():
                try:
                    client, peer = listener.accept()
                except TimeoutError:
                    if self.capture is not None:
                        self.capture.log("tcp_server_still_listening")
                    continue
                with client:
                    self.handle_connection(client, peer, stop_event)
                if self.capture is not None:
                    self.capture.log("tcp_server_still_listening")

    def close(self, reason: str = "unspecified") -> None:
        if self.capture is not None:
            self.capture.close(reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe, no-reply AUX TCP skeleton.")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2000)
    args = parser.parse_args(argv)
    raise SystemExit("AUXTCPServer requires an explicit application-owned MountController")


if __name__ == "__main__":
    main()
