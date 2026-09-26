"""Hardware-neutral mount-control API for PC integration and future firmware.

The public API intentionally models telescope actions rather than the NXW436
wire protocol.  J1 opcodes, 24-bit payloads, serial framing and retry policy
belong in a backend adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mount_model import POSITION_MODULUS


class Axis(str, Enum):
    AZ = "az"
    ALT = "alt"


class Direction(str, Enum):
    PLUS = "+"
    MINUS = "-"


class SpeedTier(str, Enum):
    """Named motion regions; they are not physical angular-rate promises."""

    FAST = "FAST"
    MEDIUM = "MEDIUM"
    FINE = "FINE"
    SLOW = "SLOW"
    MANUAL_CONSERVATIVE = "MANUAL_CONSERVATIVE"
    MANUAL_HIGH = "MANUAL_HIGH"


class MountError(RuntimeError):
    """Base error exposed by the mount-control abstraction."""


class PositionFrameError(MountError):
    """A backend returned no valid modular encoder position."""


class MountStateError(MountError):
    """The requested action is unsafe or lacks required command state."""


def normalize_position(raw: int) -> int:
    """Normalize an encoder coordinate into the confirmed NXW436 modulus."""
    return raw % POSITION_MODULUS


def signed_modular_delta(previous: int, current: int) -> int:
    """Shortest signed displacement from ``previous`` to ``current``."""
    delta = normalize_position(current) - normalize_position(previous)
    if delta > POSITION_MODULUS // 2:
        delta -= POSITION_MODULUS
    elif delta < -(POSITION_MODULUS // 2):
        delta += POSITION_MODULUS
    return delta


@dataclass(frozen=True)
class AxisStatus:
    """Fresh encoder position plus software-commanded (not sensed) motion state."""

    axis: Axis
    position: int
    motion_commanded: bool
    last_direction: Direction | None
    safe_stop_available: bool
    safe_stop_unavailable_reason: str | None


@dataclass(frozen=True)
class GotoResult:
    """Result of a bounded controller run.

    ``completed`` means the backend's GoTo controller returned normally after
    its configured stop/settle sequence. It does *not* mean exact target
    acquisition. Consumers must inspect ``final_error_counts`` and
    ``target_acquired`` before making an accuracy claim.
    """

    axis: Axis
    start_position: int
    target_position: int
    requested_delta: int
    completed: bool
    target_acquired: bool | None
    final_position: int | None
    final_error_counts: int | None
    settled_outcome: str | None
    details: dict[str, object]


class MountBackend(Protocol):
    """Backend contract used by the frontend-safe :class:`MountController`."""

    def get_position(self, axis: Axis) -> int: ...

    def move(self, axis: Axis, direction: Direction, speed: SpeedTier) -> None: ...

    def stop(self, axis: Axis) -> None: ...

    def goto(self, axis: Axis, target_position: int) -> GotoResult: ...

    def get_axis_status(self, axis: Axis) -> AxisStatus: ...


class MountController:
    """Small frontend-facing facade with explicit AZ/ALT operations.

    Positions and goto targets are absolute modular encoder coordinates.  The
    service deliberately has no networking, UI, or telescope protocol logic.
    """

    def __init__(self, backend: MountBackend) -> None:
        self._backend = backend

    def get_az_position(self) -> int:
        return self._backend.get_position(Axis.AZ)

    def get_alt_position(self) -> int:
        return self._backend.get_position(Axis.ALT)

    def move_az(self, direction: Direction, speed: SpeedTier) -> None:
        self._backend.move(Axis.AZ, direction, speed)

    def move_alt(self, direction: Direction, speed: SpeedTier) -> None:
        self._backend.move(Axis.ALT, direction, speed)

    def stop_az(self) -> None:
        self._backend.stop(Axis.AZ)

    def stop_alt(self) -> None:
        self._backend.stop(Axis.ALT)

    def goto_az(self, target_position: int) -> GotoResult:
        return self._backend.goto(Axis.AZ, normalize_position(target_position))

    def goto_alt(self, target_position: int) -> GotoResult:
        return self._backend.goto(Axis.ALT, normalize_position(target_position))

    def get_axis_status(self, axis: Axis) -> AxisStatus:
        return self._backend.get_axis_status(axis)
