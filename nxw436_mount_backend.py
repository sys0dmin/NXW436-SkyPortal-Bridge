"""NXW436 implementation of the hardware-neutral mount-control API.

This is the only integration-layer module that knows that a motion command is
encoded as a three-byte NXW436 payload.  The existing production staged GoTo
state machine is reused unchanged.
"""

from __future__ import annotations

from typing import Protocol

from mount_api import (
    Axis,
    AxisStatus,
    Direction,
    GotoResult,
    MountStateError,
    PositionFrameError,
    SpeedTier,
    normalize_position,
    signed_modular_delta,
)
from nxw436_driver import NXW436, is_valid_raw_position
from nxw436_position_controller import PROFILES, RelativePositionController


class NXW436Transport(Protocol):
    def query_position_raw(self, axis: str) -> bytes | None: ...

    def move(self, axis: str, direction: str, payload: bytes) -> None: ...

    def stop(self, axis: str, direction: str | None = None) -> None: ...


def _payload_for(axis: Axis, speed: SpeedTier) -> bytes:
    if speed is SpeedTier.MANUAL_CONSERVATIVE:
        # Hardware-experiment-only policy; closed-loop GoTo profiles stay frozen.
        return bytes.fromhex("0000F5")
    for stage in PROFILES[axis.value]:
        if stage.name == speed.value:
            return stage.payload
    raise MountStateError(f"NXW436 has no {speed.value} payload for {axis.value}")


class NXW436MountBackend:
    """Adapter preserving the established NXW436 safety and controller logic."""

    def __init__(self, transport: NXW436Transport, *, goto_max_seconds: float = 30.0,
                 goto_settle_seconds: float = 1.0) -> None:
        self.transport = transport
        self.goto_max_seconds = goto_max_seconds
        self.goto_settle_seconds = goto_settle_seconds
        self._last_direction: dict[Axis, Direction] = {}
        self._motion_commanded: dict[Axis, bool] = {Axis.AZ: False, Axis.ALT: False}

    @classmethod
    def from_port(cls, port: str = "COM5") -> "NXW436MountBackend":
        """Construct an adapter; callers still explicitly own opening the port."""
        return cls(NXW436(port))

    def open(self) -> None:
        opener = getattr(self.transport, "open", None)
        if opener is None:
            raise MountStateError("transport does not support explicit open")
        opener()

    def close(self) -> None:
        closer = getattr(self.transport, "close", None)
        if closer is not None:
            closer()

    def __enter__(self) -> "NXW436MountBackend":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def get_position(self, axis: Axis) -> int:
        frame = self.transport.query_position_raw(axis.value)
        if frame is None:
            raise PositionFrameError(f"NXW436 {axis.value} position timeout")
        if len(frame) != 3:
            raise PositionFrameError(
                f"NXW436 {axis.value} invalid position frame length: {len(frame)}"
            )
        raw = int.from_bytes(frame, "big")
        if not is_valid_raw_position(raw):
            raise PositionFrameError(
                f"NXW436 {axis.value} invalid position frame: {frame.hex().upper()}"
            )
        return raw

    def move(self, axis: Axis, direction: Direction, speed: SpeedTier) -> None:
        self.transport.move(axis.value, direction.value, _payload_for(axis, speed))
        self._last_direction[axis] = direction
        self._motion_commanded[axis] = True

    def stop(self, axis: Axis) -> None:
        direction = self._last_direction.get(axis)
        if direction is None:
            raise MountStateError(
                f"safe STOP unavailable for {axis.value}: direction is unknown; "
                "NXW436 only has verified same-prefix STOP after known motion"
            )
        try:
            # NXW436.stop() retains the experimentally required double STOP.
            self.transport.stop(axis.value, direction.value)
        finally:
            self._motion_commanded[axis] = False

    def goto(self, axis: Axis, target_position: int) -> GotoResult:
        start = self.get_position(axis)
        target = normalize_position(target_position)
        delta = signed_modular_delta(start, target)
        if delta == 0:
            return GotoResult(
                axis, start, target, 0, True, True, start, 0, "exact",
                {"already_at_target": True},
            )
        controller = RelativePositionController(self.transport, axis.value, delta)
        try:
            details = controller.run(
                max_seconds=self.goto_max_seconds,
                settle_seconds=self.goto_settle_seconds,
            )
        finally:
            self._motion_commanded[axis] = False
        final_position = int(details["final_raw"])
        final_error = int(details["final_error_counts"])
        return GotoResult(
            axis=axis,
            start_position=start,
            target_position=target,
            requested_delta=delta,
            # Normal return means a bounded controller run completed; the
            # existing state machine intentionally may stop with an undershoot.
            completed=True,
            target_acquired=final_error == 0,
            final_position=final_position,
            final_error_counts=final_error,
            settled_outcome=str(details["settled_outcome"]),
            details=details,
        )

    def get_axis_status(self, axis: Axis) -> AxisStatus:
        return AxisStatus(
            axis=axis,
            position=self.get_position(axis),
            motion_commanded=self._motion_commanded[axis],
            last_direction=self._last_direction.get(axis),
            safe_stop_available=axis in self._last_direction,
            safe_stop_unavailable_reason=(
                None if axis in self._last_direction else
                "no direction is known in this backend process"
            ),
        )
