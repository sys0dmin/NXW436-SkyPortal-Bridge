"""Offline wrap-aware NXW436 encoder speed analysis."""

from __future__ import annotations

from collections.abc import Sequence

from mount_model import POSITION_MODULUS


def analyze_raw_speed(samples: Sequence[tuple[float, int]]) -> tuple[float, float]:
    """Return counts/sec and degrees/sec from first/last timestamped raw samples."""
    if len(samples) < 2:
        raise ValueError("at least two samples are required")
    start_time, start_raw = samples[0]
    end_time, end_raw = samples[-1]
    elapsed = end_time - start_time
    if elapsed <= 0:
        raise ValueError("timestamps must increase")
    delta = (end_raw - start_raw) % POSITION_MODULUS
    if delta > POSITION_MODULUS // 2:
        delta -= POSITION_MODULUS
    counts_per_second = delta / elapsed
    return counts_per_second, counts_per_second * 360.0 / POSITION_MODULUS
