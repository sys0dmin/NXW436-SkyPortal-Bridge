"""Low-level driver for the experimentally verified NXW436 J1 protocol."""

from __future__ import annotations

import time

import serial

from mount_model import POSITION_MODULUS


# Compatibility aliases for existing diagnostics. The canonical value lives in
# hardware-neutral mount_model.py, not in this J1 transport module.
RAW_MODULO = POSITION_MODULUS
COUNTS_PER_REV = RAW_MODULO
BAUD = 4800
POSITION_COMMANDS = {"az": 0x01, "alt": 0x15}
MOVE_PREFIXES = {
    ("az", "+"): 0x06, ("az", "-"): 0x07,
    ("alt", "+"): 0x1A, ("alt", "-"): 0x1B,
}

# Captured by Mark Lord from an original NexStar 114GT HC.  On our NXW436,
# only rate 1 and 8 are physically measured so far.
GT114_SPEED_PAYLOADS = {
    1: bytes.fromhex("00 00 F5"),
    2: bytes.fromhex("00 01 E8"),
    3: bytes.fromhex("00 03 D3"),
    4: bytes.fromhex("00 07 A7"),
    5: bytes.fromhex("00 0F 52"),
    6: bytes.fromhex("00 39 78"),
    7: bytes.fromhex("00 72 F1"),
    8: bytes.fromhex("00 E5 E3"),
    9: bytes.fromhex("FF FF FF"),
}
NXW436_MEASURED_SPEEDS = frozenset((1, 8))


def signed_delta(previous: int, current: int) -> int:
    delta = current - previous
    if delta > RAW_MODULO // 2:
        delta -= RAW_MODULO
    elif delta < -(RAW_MODULO // 2):
        delta += RAW_MODULO
    return delta


def is_valid_raw_position(value: int) -> bool:
    """True only for a value representable by the confirmed encoder modulus."""
    return 0 <= value < RAW_MODULO


class NXW436:
    def __init__(self, port: str = "COM5", *, post_command_delay: float = 0.10):
        self.port = port
        self.post_command_delay = post_command_delay
        self.serial: serial.Serial | None = None
        self._last_direction: dict[str, str] = {}

    def __enter__(self) -> "NXW436":
        self.open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def open(self) -> None:
        if self.serial is None:
            self.serial = serial.Serial(
                self.port, BAUD, bytesize=8, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=0.02,
            )
            self.serial.reset_input_buffer()

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def _require_serial(self) -> serial.Serial:
        if self.serial is None:
            raise RuntimeError("NXW436 transport is not open")
        return self.serial

    def _send(self, data: bytes) -> None:
        ser = self._require_serial()
        ser.write(data)
        ser.flush()

    def _read_exact(self, count: int = 3, timeout: float = 0.15) -> bytes | None:
        ser = self._require_serial()
        data = bytearray()
        deadline = time.perf_counter() + timeout
        while len(data) < count:
            if time.perf_counter() >= deadline:
                return None
            chunk = ser.read(count - len(data))
            if chunk:
                data.extend(chunk)
        return bytes(data)

    def query_position_raw(self, axis: str, *, timeout: float = 0.15) -> bytes | None:
        """Send exactly one position query and return its raw three-byte RX frame.

        No retry is performed here: diagnostic callers can retain a timeout or
        malformed frame as evidence instead of silently hiding it.
        """
        if axis not in POSITION_COMMANDS:
            raise ValueError(f"Unknown axis: {axis}")
        self._send(bytes([POSITION_COMMANDS[axis]]))
        return self._read_exact(timeout=timeout)

    def get_position(self, axis: str, *, attempts: int = 5) -> int:
        if axis not in POSITION_COMMANDS:
            raise ValueError(f"Unknown axis: {axis}")
        for _ in range(attempts):
            frame = self.query_position_raw(axis)
            if frame is not None:
                return int.from_bytes(frame, "big")
            time.sleep(0.05)
        raise TimeoutError(f"No position reply for {axis} after {attempts} attempts")

    def move(self, axis: str, direction: str, payload: bytes) -> None:
        if (axis, direction) not in MOVE_PREFIXES:
            raise ValueError(f"Unknown axis/direction: {axis} {direction}")
        if len(payload) != 3:
            raise ValueError("payload must be exactly three bytes")
        self._send(bytes([MOVE_PREFIXES[(axis, direction)]]) + payload)
        self._last_direction[axis] = direction

    def move_speed(self, axis: str, direction: str, speed: int) -> None:
        if speed not in NXW436_MEASURED_SPEEDS:
            raise ValueError(
                f"Speed {speed} is not yet measured on this NXW436; "
                "use move(..., payload) only in a controlled experiment"
            )
        self.move(axis, direction, GT114_SPEED_PAYLOADS[speed])

    def stop(self, axis: str, direction: str | None = None) -> None:
        direction = direction or self._last_direction.get(axis)
        if direction is None:
            raise ValueError("Direction is required when this axis has not moved")
        prefix = MOVE_PREFIXES[(axis, direction)]
        command = bytes([prefix, 0, 0, 0])
        try:
            self._send(command)
        finally:
            # A second STOP is required even when the first serial write raises.
            time.sleep(0.05)
            self._send(command)
            self._last_direction.pop(axis, None)

    def move_for(self, axis: str, direction: str, payload: bytes, seconds: float) -> tuple[int, int]:
        if not 0 < seconds <= 10:
            raise ValueError("seconds must be >0 and <=10")
        before = self.get_position(axis)
        try:
            self.move(axis, direction, payload)
            time.sleep(seconds)
        finally:
            self.stop(axis, direction)
        time.sleep(self.post_command_delay)
        after = self.get_position(axis)
        return before, after
