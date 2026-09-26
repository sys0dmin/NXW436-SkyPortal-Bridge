"""Stateful TCP stream parser for AUX candidate frames."""

from __future__ import annotations

from dataclasses import dataclass

from celestron_aux.framing import AUXFrameError, deserialize
from celestron_aux.messages import AUXFrame, MIN_LENGTH, SOM


@dataclass(frozen=True)
class ParserEvent:
    raw: bytes
    frame: AUXFrame | None
    checksum_valid: bool
    error: str | None


class AUXStreamParser:
    """Buffers TCP chunks and emits valid frames or bounded malformed candidates."""

    def __init__(self, *, max_buffer: int = 4096) -> None:
        if max_buffer < 5:
            raise ValueError("max_buffer must permit a minimal AUX frame")
        self.max_buffer = max_buffer
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes) -> list[ParserEvent]:
        self._buffer.extend(chunk)
        events: list[ParserEvent] = []
        while True:
            try:
                start = self._buffer.index(SOM)
            except ValueError:
                self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 2:
                break
            length = self._buffer[1]
            if length < MIN_LENGTH:
                events.append(ParserEvent(bytes(self._buffer[:2]), None, False, "impossible_length"))
                del self._buffer[0]
                continue
            total = length + 3
            if total > self.max_buffer:
                events.append(ParserEvent(bytes(self._buffer[:2]), None, False, "frame_exceeds_buffer_limit"))
                del self._buffer[0]
                continue
            if len(self._buffer) < total:
                break
            raw = bytes(self._buffer[:total])
            del self._buffer[:total]
            try:
                frame = deserialize(raw)
            except AUXFrameError as error:
                events.append(ParserEvent(raw, None, False, str(error)))
            else:
                events.append(ParserEvent(raw, frame, True, None))
        if len(self._buffer) > self.max_buffer:
            events.append(ParserEvent(bytes(self._buffer[:self.max_buffer]), None, False, "buffer_limit_exceeded"))
            self._buffer.clear()
        return events
