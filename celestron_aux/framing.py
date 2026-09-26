"""Deterministic AUX binary frame serialization and validation."""

from __future__ import annotations

from celestron_aux.messages import AUXFrame, MAX_LENGTH, MIN_LENGTH, SOM


class AUXFrameError(ValueError):
    """Raised when a complete candidate frame is structurally invalid."""


def checksum(body: bytes) -> int:
    """Return the byte which makes the additive sum of ``body`` equal zero."""
    return (-sum(body)) & 0xFF


def serialize(frame: AUXFrame) -> bytes:
    length = MIN_LENGTH + len(frame.payload)
    body = bytes((length, frame.source, frame.destination, frame.command)) + frame.payload
    return bytes((SOM,)) + body + bytes((checksum(body),))


def deserialize(raw: bytes) -> AUXFrame:
    if len(raw) < MIN_LENGTH + 3:
        raise AUXFrameError("frame is too short")
    if raw[0] != SOM:
        raise AUXFrameError("missing AUX start marker")
    length = raw[1]
    if not MIN_LENGTH <= length <= MAX_LENGTH:
        raise AUXFrameError("impossible AUX length")
    if len(raw) != length + 3:
        raise AUXFrameError("AUX length does not match frame size")
    if sum(raw[1:]) & 0xFF:
        raise AUXFrameError("AUX checksum mismatch")
    return AUXFrame(raw[2], raw[3], raw[4], raw[5:-1])
