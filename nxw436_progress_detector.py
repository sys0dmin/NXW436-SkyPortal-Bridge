"""Pure valid-sample progress checks shared by NXW436 diagnostics and control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ProgressEvidence:
    confirmed: bool
    span_s: float
    commanded_progress: int


def evaluate_no_progress(
    valid_history: Sequence[tuple[float, int]],
    *,
    window_s: float,
    min_progress_counts: int,
    nominal_sample_interval: float,
) -> ProgressEvidence | None:
    """Compare the newest valid sample with the newest valid sample >= one window old.

    Invalid samples are intentionally absent from ``valid_history``.  The upper
    age bound tolerates two nominal sampling intervals without comparing an
    unrelated, much older position.
    """
    if len(valid_history) < 2:
        return None
    current_time, current_position = valid_history[-1]
    oldest_allowed = current_time - (window_s + 2 * nominal_sample_interval)
    newest_allowed = current_time - window_s
    historical: tuple[float, int] | None = None
    for sample_time, sample_position in reversed(valid_history[:-1]):
        if sample_time > newest_allowed:
            continue
        if sample_time < oldest_allowed:
            break
        historical = (sample_time, sample_position)
        break
    if historical is None:
        return None
    span_s = current_time - historical[0]
    progress = current_position - historical[1]
    return ProgressEvidence(progress < min_progress_counts, span_s, progress)
