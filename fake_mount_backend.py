"""Deterministic in-memory backend for integration and UI tests.

It models modular positions and software motion state only.  It intentionally
does not model NXW436 timing, UART faults, mechanics, coast, or payload rates.
"""

from __future__ import annotations

import time
from typing import Callable

from mount_api import (
    Axis,
    AxisStatus,
    Direction,
    GotoResult,
    MountStateError,
    SpeedTier,
    normalize_position,
    signed_modular_delta,
)


class FakeMountBackend:
    SIMULATED_COUNTS_PER_SECOND = {
        SpeedTier.SLOW: 10.0,
        SpeedTier.FINE: 50.0,
        SpeedTier.MEDIUM: 200.0,
        SpeedTier.FAST: 1000.0,
    }

    def __init__(self, *, az_position: int = 0, alt_position: int = 0,
                 monotonic_clock: Callable[[], float] = time.monotonic) -> None:
        self._positions = {
            Axis.AZ: normalize_position(az_position),
            Axis.ALT: normalize_position(alt_position),
        }
        self._motion_commanded = {Axis.AZ: False, Axis.ALT: False}
        self._last_direction: dict[Axis, Direction] = {}
        self._speed: dict[Axis, SpeedTier] = {}
        self._fractional_counts = {Axis.AZ: 0.0, Axis.ALT: 0.0}
        self._clock = monotonic_clock
        now = self._clock()
        self._last_update = {Axis.AZ: now, Axis.ALT: now}
        self.commands: list[tuple[str, Axis, Direction | None, SpeedTier | None]] = []

    def get_position(self, axis: Axis) -> int:
        self._integrate(axis)
        return self._positions[axis]

    def move(self, axis: Axis, direction: Direction, speed: SpeedTier) -> None:
        self._integrate(axis)
        self._last_direction[axis] = direction
        self._speed[axis] = speed
        self._motion_commanded[axis] = True
        self.commands.append(("move", axis, direction, speed))

    def stop(self, axis: Axis) -> None:
        self._integrate(axis)
        if axis not in self._last_direction:
            raise MountStateError(
                f"safe STOP unavailable for {axis.value}: no direction is known"
            )
        self._motion_commanded[axis] = False
        self._speed.pop(axis, None)
        self.commands.append(("stop", axis, self._last_direction[axis], None))

    def advance(self, axis: Axis, counts: int) -> None:
        """Test helper: advance the encoder while a matching move is commanded."""
        if not self._motion_commanded[axis]:
            raise MountStateError(f"cannot advance stopped {axis.value}")
        self._integrate(axis)
        direction = self._last_direction[axis]
        signed_counts = counts if direction is Direction.PLUS else -counts
        self._positions[axis] = normalize_position(self._positions[axis] + signed_counts)

    def goto(self, axis: Axis, target_position: int) -> GotoResult:
        self._integrate(axis)
        start = self._positions[axis]
        target = normalize_position(target_position)
        delta = signed_modular_delta(start, target)
        self._positions[axis] = target
        self._motion_commanded[axis] = False
        self._speed.pop(axis, None)
        self.commands.append(("goto", axis, None, None))
        return GotoResult(
            axis, start, target, delta, True, True, target, 0, "exact",
            {"backend": "fake"},
        )

    def get_axis_status(self, axis: Axis) -> AxisStatus:
        self._integrate(axis)
        return AxisStatus(
            axis=axis,
            position=self._positions[axis],
            motion_commanded=self._motion_commanded[axis],
            last_direction=self._last_direction.get(axis),
            safe_stop_available=axis in self._last_direction,
            safe_stop_unavailable_reason=(
                None if axis in self._last_direction else
                "no direction is known in this backend process"
            ),
        )

    def _integrate(self, axis: Axis) -> None:
        now = self._clock()
        elapsed = now - self._last_update[axis]
        self._last_update[axis] = now
        if elapsed <= 0 or not self._motion_commanded[axis]:
            return
        direction = self._last_direction[axis]
        speed = self._speed[axis]
        signed = self.SIMULATED_COUNTS_PER_SECOND[speed] * elapsed
        if direction is Direction.MINUS:
            signed = -signed
        total = self._fractional_counts[axis] + signed
        whole = int(total)
        self._fractional_counts[axis] = total - whole
        self._positions[axis] = normalize_position(self._positions[axis] + whole)
