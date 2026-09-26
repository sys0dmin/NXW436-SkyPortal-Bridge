"""Explicit, injectable AUX 24-bit coordinate transforms.

No physical NXW436 calibration is defined here. Production composition must
remain unconfigured until offsets/signs/limits are evidenced.
"""

from __future__ import annotations

from dataclasses import dataclass


AUX_FULL_TURN = 1 << 24


class CoordinateUnavailable(RuntimeError):
    """The physical coordinate calibration has not been established."""


@dataclass(frozen=True)
class AxisCoordinateConfig:
    neutral_modulus: int
    neutral_zero: int
    aux_zero: int
    direction: int
    wraps: bool = True
    neutral_min: int | None = None
    neutral_max: int | None = None

    def __post_init__(self) -> None:
        if self.neutral_modulus <= 0 or self.direction not in {-1, 1}:
            raise ValueError("explicit positive modulus and direction are required")
        if not 0 <= self.neutral_zero < self.neutral_modulus or not 0 <= self.aux_zero < AUX_FULL_TURN:
            raise ValueError("reference zero is outside its explicit coordinate range")
        if not self.wraps and (self.neutral_min is None or self.neutral_max is None):
            raise ValueError("non-wrapping axes require explicit limits")
        if not self.wraps and not 0 <= self.neutral_min <= self.neutral_max < self.neutral_modulus:
            raise ValueError("non-wrapping limits are invalid")


class AUXCoordinateAdapter:
    """Testable transform that has no default physical calibration constants."""

    def __init__(self, *, az: AxisCoordinateConfig, alt: AxisCoordinateConfig) -> None:
        self._axes = {"az": az, "alt": alt}

    def configuration(self, axis: str) -> AxisCoordinateConfig:
        return self._axes[axis]

    def to_aux(self, axis: str, neutral: int) -> int:
        config = self._axes[axis]
        self._check_bounds(config, neutral)
        offset = (neutral - config.neutral_zero) * config.direction
        scaled = self._round_div(offset * AUX_FULL_TURN, config.neutral_modulus)
        return (config.aux_zero + scaled) % AUX_FULL_TURN

    def from_aux(self, axis: str, aux: int) -> int:
        config = self._axes[axis]
        delta = self._signed_delta((aux - config.aux_zero) % AUX_FULL_TURN, AUX_FULL_TURN)
        neutral = config.neutral_zero + self._round_div(delta * config.neutral_modulus, AUX_FULL_TURN) * config.direction
        if config.wraps:
            return neutral % config.neutral_modulus
        self._check_bounds(config, neutral)
        return neutral

    @staticmethod
    def _round_div(numerator: int, denominator: int) -> int:
        return (numerator + denominator // 2) // denominator if numerator >= 0 else -((-numerator + denominator // 2) // denominator)

    @staticmethod
    def _signed_delta(value: int, modulus: int) -> int:
        return value - modulus if value > modulus // 2 else value

    @staticmethod
    def _check_bounds(config: AxisCoordinateConfig, value: int) -> None:
        if not config.wraps and not config.neutral_min <= value <= config.neutral_max:
            raise CoordinateUnavailable("axis coordinate is outside explicit mechanical limits")
