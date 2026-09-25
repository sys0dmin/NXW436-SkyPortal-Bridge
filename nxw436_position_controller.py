"""Conservative feedback controller for relative NXW436 moves.

This module sits above the experimentally verified UART driver.  It deliberately
uses a staged state machine rather than a fitted payload-to-speed equation.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import serial

from nxw436_driver import COUNTS_PER_REV, NXW436, is_valid_raw_position, signed_delta
from nxw436_progress_detector import evaluate_no_progress

MAX_CPS = 10_000
MIN_JUMP = 1_000
MAX_CONSECUTIVE_INVALID = 2
OPPOSITE_COUNTS = 2
AZ_MEDIUM_PROGRESS_WINDOW_S = 1.5
AZ_MEDIUM_MIN_PROGRESS_COUNTS = 20
AZ_MEDIUM_RECOVERY_WINDOW_S = 2.0


class ControllerAbort(RuntimeError):
    pass


@dataclass(frozen=True)
class Stage:
    name: str
    payload: bytes
    minimum_remaining_counts: int


# Conservative, discrete post-swap regions.  Thresholds intentionally exceed
# measured coast for the stage that precedes them; they are initial tuning values.
PROFILES = {
    "alt": (
        Stage("FAST", bytes.fromhex("00E5E3"), 7000),
        Stage("MEDIUM", bytes.fromhex("0072F1"), 3000),
        Stage("FINE", bytes.fromhex("003978"), 1000),
        Stage("SLOW", bytes.fromhex("002000"), 350),
    ),
    "az": (
        Stage("FAST", bytes.fromhex("00E5E3"), 5000),
        Stage("MEDIUM", bytes.fromhex("0072F1"), 2500),
        Stage("FINE", bytes.fromhex("003978"), 1000),
        Stage("SLOW", bytes.fromhex("002000"), 300),
    ),
}

# Stop margins are deliberately separate from stage-selection thresholds.  This
# allows isolated positioning experiments without changing payload selection.
# Current experimental set: AZ 200 counts; ALT remains at its established 350.
STOP_MARGINS = {
    "alt": 350,
    "az": 200,
}


def degrees_to_counts(degrees: Decimal) -> int:
    """Round magnitude half-up, then restore sign; avoids binary float rounding."""
    magnitude = (abs(degrees) * Decimal(COUNTS_PER_REV) / Decimal(360)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(magnitude if degrees >= 0 else -magnitude)


def choose_stage(axis: str, remaining_counts: int) -> Stage:
    for stage in PROFILES[axis]:
        if remaining_counts > stage.minimum_remaining_counts:
            return stage
    return PROFILES[axis][-1]


class RelativePositionController:
    def __init__(self, mount: NXW436, axis: str, target_delta: int, *, sample_seconds: float = 0.15,
                 stop_margin_counts: int | None = None):
        if axis not in PROFILES:
            raise ValueError(f"Unknown axis: {axis}")
        if target_delta == 0:
            raise ValueError("target delta must not be zero")
        if stop_margin_counts is not None and (axis != "az" or stop_margin_counts not in (200, 300)):
            raise ValueError("only AZ experimental stop margins 200 or 300 may be selected")
        self.mount, self.axis, self.target_delta = mount, axis, target_delta
        self.stop_margin_counts = STOP_MARGINS[axis] if stop_margin_counts is None else stop_margin_counts
        self.direction = "+" if target_delta > 0 else "-"
        self.sign = 1 if target_delta > 0 else -1
        self.sample_seconds = sample_seconds
        self.rows: list[dict] = []
        self.transitions: list[dict] = []
        self.invalid_total = 0
        self.consecutive_invalid = 0
        self.command_started = 0.0
        self.previous_raw: int | None = None
        self.previous_time = 0.0
        self.unwrapped = 0
        self.start_raw = 0
        self.target_unwrapped = 0
        self.movement_commanded = False
        self.active_stage: Stage | None = None
        self.stop_raw: int | None = None
        self.stop_unwrapped: int | None = None
        self.stop_elapsed_s: float | None = None
        self.in_settle = False
        self.first_post_stop_raw: int | None = None
        self.first_post_stop_unwrapped: int | None = None
        self.motion_confirmed = False
        self.az_medium_history: list[tuple[float, int]] = []
        self.az_medium_no_progress_detected = False
        self.az_medium_recovery_attempted = False
        self.az_medium_recovery_start_raw: int | None = None
        self.az_medium_recovery_start_unwrapped: int | None = None
        self.az_medium_recovery_deadline: float | None = None
        self.az_medium_recovery_success: bool | None = None
        self.az_medium_recovery_terminal_progress: int | None = None
        self.az_medium_recovery_failure_reason = ""
        self.pending_opposite_raw: int | None = None

    def _read_valid(self) -> tuple[bytes, int] | None:
        frame = self.mount.query_position_raw(self.axis)
        if frame is None:
            return None
        raw = int.from_bytes(frame, "big")
        return (frame, raw) if is_valid_raw_position(raw) else (frame, -1)

    def _record(self, *, elapsed: float, frame: bytes | None, kind: str, raw: int | str,
                delta: int | str = "", state: str, note: str = "") -> None:
        self.rows.append({
            "timestamp": time.time(), "monotonic_s": time.perf_counter(), "elapsed_s": elapsed,
            "rx_bytes_hex": "" if frame is None else frame.hex().upper(), "reply_kind": kind,
            "raw_position": raw, "unwrapped_position": self.unwrapped,
            "target_unwrapped": self.target_unwrapped, "remaining_counts": self.target_unwrapped - self.unwrapped,
            "delta_counts": delta, "state": state,
            "payload_hex": "" if self.active_stage is None else self.active_stage.payload.hex().upper(),
            "no_progress_detected": self.az_medium_no_progress_detected,
            "recovery_attempted": self.az_medium_recovery_attempted,
            "recovery_count": int(self.az_medium_recovery_attempted),
            "recovery_payload": "0072F1" if self.az_medium_recovery_attempted else "",
            "recovery_start_position": "" if self.az_medium_recovery_start_raw is None else self.az_medium_recovery_start_raw,
            "recovery_start_unwrapped": "" if self.az_medium_recovery_start_unwrapped is None else self.az_medium_recovery_start_unwrapped,
            "recovery_progress_counts": self._az_medium_recovery_progress(),
            "recovery_success": "" if self.az_medium_recovery_success is None else self.az_medium_recovery_success,
            "recovery_failure_reason": self.az_medium_recovery_failure_reason,
            "note": note,
        })

    def _transition(self, name: str, payload: bytes | None, reason: str) -> None:
        self.transitions.append({
            "elapsed_s": time.perf_counter() - self.command_started,
            "state": name, "payload_hex": "" if payload is None else payload.hex().upper(),
            "raw_position": self.previous_raw, "unwrapped_position": self.unwrapped,
            "remaining_counts": self.target_unwrapped - self.unwrapped, "reason": reason,
            "no_progress_detected": self.az_medium_no_progress_detected,
            "recovery_attempted": self.az_medium_recovery_attempted,
            "recovery_count": int(self.az_medium_recovery_attempted),
            "recovery_payload": "0072F1" if self.az_medium_recovery_attempted else "",
            "recovery_start_position": "" if self.az_medium_recovery_start_raw is None else self.az_medium_recovery_start_raw,
            "recovery_progress_counts": self._az_medium_recovery_progress(),
            "recovery_success": "" if self.az_medium_recovery_success is None else self.az_medium_recovery_success,
            "recovery_failure_reason": self.az_medium_recovery_failure_reason,
        })

    def _az_medium_recovery_progress(self) -> int | str:
        if self.az_medium_recovery_start_unwrapped is None:
            return ""
        return (self.unwrapped - self.az_medium_recovery_start_unwrapped) * self.sign

    def recovery_log_fields(self) -> dict:
        """Recovery fields also required when ``run`` aborts before its return."""
        return {
            "no_progress_detected": self.az_medium_no_progress_detected,
            "recovery_attempted": self.az_medium_recovery_attempted,
            "recovery_count": int(self.az_medium_recovery_attempted),
            "recovery_payload": "0072F1" if self.az_medium_recovery_attempted else "",
            "recovery_start_position": self.az_medium_recovery_start_raw,
            "recovery_start_unwrapped": self.az_medium_recovery_start_unwrapped,
            "recovery_progress_counts": self.az_medium_recovery_terminal_progress if self.az_medium_recovery_terminal_progress is not None else self._az_medium_recovery_progress(),
            "recovery_success": self.az_medium_recovery_success,
            "recovery_failure_reason": self.az_medium_recovery_failure_reason,
            "invalid_replies_total": self.invalid_total,
        }

    def _activate_stage(self, stage: Stage, reason: str) -> None:
        self.mount.move(self.axis, self.direction, stage.payload)
        self.active_stage = stage
        if self.axis == "az" and stage.name == "MEDIUM":
            self.az_medium_history = []
        self._transition(stage.name, stage.payload, reason)

    def _az_medium_recovery_eligible(self, remaining: int) -> bool:
        return (
            self.axis == "az"
            and self.active_stage is not None
            and self.active_stage.name == "MEDIUM"
            and self.motion_confirmed
            and not self.az_medium_recovery_attempted
            and remaining * self.sign > self.active_stage.minimum_remaining_counts
        )

    def _check_az_medium_recovery(self, received: float) -> None:
        if not self.az_medium_recovery_attempted or self.az_medium_recovery_success is not None:
            return
        if self.az_medium_recovery_deadline is not None and received >= self.az_medium_recovery_deadline:
            self.az_medium_recovery_terminal_progress = self._az_medium_recovery_progress()
            self.az_medium_recovery_failure_reason = "no-valid-commanded-progress-in-recovery-window"
            self._transition("AZ_MEDIUM_RECOVERY_FAILED", self.active_stage.payload, self.az_medium_recovery_failure_reason)
            raise ControllerAbort("az_medium_recovery_failed")
        if self._az_medium_recovery_progress() >= AZ_MEDIUM_MIN_PROGRESS_COUNTS:
            self.az_medium_recovery_terminal_progress = self._az_medium_recovery_progress()
            self.az_medium_recovery_success = True
            self._transition("AZ_MEDIUM_RECOVERY_SUCCESS", self.active_stage.payload, "valid-commanded-direction-progress")

    def _observe_az_medium_progress(self, received: float, remaining: int) -> None:
        self._check_az_medium_recovery(received)
        if not self._az_medium_recovery_eligible(remaining):
            return
        # The shared detector expects positive progress in the commanded
        # direction. Raw encoder progress is negative for AZ-.
        self.az_medium_history.append((received, self.unwrapped * self.sign))
        evidence = evaluate_no_progress(
            self.az_medium_history,
            window_s=AZ_MEDIUM_PROGRESS_WINDOW_S,
            min_progress_counts=AZ_MEDIUM_MIN_PROGRESS_COUNTS,
            nominal_sample_interval=self.sample_seconds,
        )
        if evidence is None or not evidence.confirmed:
            return
        self.az_medium_no_progress_detected = True
        reason = f"valid progress {evidence.commanded_progress} counts over {evidence.span_s:.3f}s"
        self._transition("AZ_MEDIUM_NO_PROGRESS", self.active_stage.payload, reason)
        self.az_medium_recovery_attempted = True
        self.az_medium_recovery_start_raw = self.previous_raw
        self.az_medium_recovery_start_unwrapped = self.unwrapped
        self.az_medium_recovery_deadline = received + AZ_MEDIUM_RECOVERY_WINDOW_S
        self.mount.move(self.axis, self.direction, self.active_stage.payload)
        self._transition("AZ_MEDIUM_RESEND", self.active_stage.payload, "single-same-payload-resend")

    def _handle_valid_position(self, *, received: float, elapsed: float, frame: bytes, raw: int,
                               enforce_target: bool) -> None:
        """Accept a trusted position, or require a second sample for reverse motion.

        A single valid-looking reply opposite to the commanded direction may be
        an electrically corrupted frame.  It is retained verbatim in ``rows``
        but never changes the trusted position.  The next valid reply is
        compared with that same trusted position: normal-direction progress
        rejects the outlier, while another reverse result confirms an abort.
        """
        assert self.previous_raw is not None
        delta = signed_delta(self.previous_raw, raw)
        limit = max(MIN_JUMP, MAX_CPS * max(received - self.previous_time, 0.001))
        if abs(delta) > limit:
            self.invalid_total += 1; self.consecutive_invalid += 1
            self._record(elapsed=elapsed, frame=frame, kind="implausible-jump", raw=raw,
                         delta=delta, state="INVALID")
            return

        if delta * self.sign <= -OPPOSITE_COUNTS:
            if self.pending_opposite_raw is None:
                self.pending_opposite_raw = raw
                self._record(
                    elapsed=elapsed,
                    frame=frame,
                    kind="valid-opposite-pending",
                    raw=raw,
                    delta=delta,
                    state="OPPOSITE_PENDING",
                    note="raw logged; trusted position unchanged; awaiting next valid reply",
                )
                return
            self._record(
                elapsed=elapsed,
                frame=frame,
                kind="valid-opposite-confirmed",
                raw=raw,
                delta=delta,
                state="ABORT",
                note="second reverse-direction reply relative to trusted position",
            )
            raise ControllerAbort("confirmed opposite-direction encoder movement")

        rejected_pending = self.pending_opposite_raw
        self.pending_opposite_raw = None
        self.consecutive_invalid = 0
        self.unwrapped += delta
        self.previous_raw, self.previous_time = raw, received
        if (self.unwrapped - self.start_raw) * self.sign >= AZ_MEDIUM_MIN_PROGRESS_COUNTS:
            self.motion_confirmed = True
        if self.in_settle and self.first_post_stop_raw is None:
            self.first_post_stop_raw = raw
            self.first_post_stop_unwrapped = self.unwrapped
        remaining = self.target_unwrapped - self.unwrapped
        note = "" if rejected_pending is None else f"rejected pending opposite raw {rejected_pending:06X}"
        self._record(elapsed=elapsed, frame=frame, kind="valid", raw=raw, delta=delta,
                     state=self.active_stage.name if self.active_stage else "START", note=note)
        if not enforce_target:
            return
        if remaining * self.sign <= 0:
            self.stop_raw, self.stop_unwrapped, self.stop_elapsed_s = raw, self.unwrapped, elapsed
            self._transition("STOP", None, "target-crossed-or-reached")
            raise StopIteration
        if remaining * self.sign <= self.stop_margin_counts:
            self.stop_raw, self.stop_unwrapped, self.stop_elapsed_s = raw, self.unwrapped, elapsed
            self._transition("STOP", None, "conservative-stop-margin")
            raise StopIteration
        self._observe_az_medium_progress(received, remaining)
        next_stage = choose_stage(self.axis, remaining * self.sign)
        if next_stage != self.active_stage:
            self._activate_stage(next_stage, "remaining-distance-threshold")

    def _handle_sample(self, *, enforce_target: bool = True) -> None:
        received = time.perf_counter()
        elapsed = received - self.command_started
        self._check_az_medium_recovery(received)
        reply = self._read_valid()
        if reply is None:
            self.invalid_total += 1; self.consecutive_invalid += 1
            self._record(elapsed=elapsed, frame=None, kind="timeout", raw="", state="INVALID")
        else:
            frame, raw = reply
            if raw < 0:
                self.invalid_total += 1; self.consecutive_invalid += 1
                self._record(elapsed=elapsed, frame=frame, kind="malformed-outside-modulus",
                             raw=int.from_bytes(frame, "big"), state="INVALID")
            else:
                self._handle_valid_position(
                    received=received,
                    elapsed=elapsed,
                    frame=frame,
                    raw=raw,
                    enforce_target=enforce_target,
                )
        if self.consecutive_invalid > MAX_CONSECUTIVE_INVALID:
            raise ControllerAbort("three consecutive timeout/malformed position replies")

    def run(self, *, max_seconds: float, settle_seconds: float) -> dict:
        # The direction prefix is known before first motion; pre-STOP clears a stale command.
        self.mount.stop(self.axis, self.direction)
        time.sleep(0.25)
        reply = self._read_valid()
        if reply is None or reply[1] < 0:
            raise ControllerAbort("no valid initial position")
        frame, self.start_raw = reply
        self.previous_raw, self.unwrapped = self.start_raw, self.start_raw
        self.previous_time = time.perf_counter()
        self.target_unwrapped = self.start_raw + self.target_delta
        self.command_started = time.perf_counter()
        self._record(elapsed=0.0, frame=frame, kind="valid-start", raw=self.start_raw, state="START")
        self.active_stage = choose_stage(self.axis, abs(self.target_delta))
        self._activate_stage(self.active_stage, "initial-distance")
        self.movement_commanded = True
        deadline = self.command_started + max_seconds
        stop_reason = "timeout"
        try:
            while time.perf_counter() < deadline:
                time.sleep(self.sample_seconds)
                try:
                    self._handle_sample()
                except StopIteration:
                    stop_reason = "target-or-stop-margin"
                    break
            else:
                raise ControllerAbort("controller maximum duration exceeded")
        finally:
            if self.stop_unwrapped is None:
                self.stop_raw, self.stop_unwrapped = self.previous_raw, self.unwrapped
                self.stop_elapsed_s = time.perf_counter() - self.command_started
            if self.movement_commanded:
                self.mount.stop(self.axis, self.direction)
        settle_deadline = time.perf_counter() + settle_seconds
        self.in_settle = True
        while time.perf_counter() < settle_deadline:
            time.sleep(min(self.sample_seconds, settle_deadline - time.perf_counter()))
            if time.perf_counter() >= settle_deadline:
                break
            self._handle_sample(enforce_target=False)
        final_error = self.target_unwrapped - self.unwrapped
        stop_error = self.target_unwrapped - self.stop_unwrapped
        signed_final = final_error * self.sign
        first_post_stop_unwrapped = self.first_post_stop_unwrapped if self.first_post_stop_unwrapped is not None else self.stop_unwrapped
        first_post_stop_raw = self.first_post_stop_raw if self.first_post_stop_raw is not None else self.stop_raw
        first_coast = first_post_stop_unwrapped - self.stop_unwrapped
        settled_coast = self.unwrapped - self.stop_unwrapped
        return {
            "axis": self.axis, "direction": self.direction, "target_delta_counts": self.target_delta,
            "stop_margin_counts": self.stop_margin_counts,
            "start_raw": self.start_raw, "target_raw_modulo": self.target_unwrapped % COUNTS_PER_REV,
            "stop_reason": stop_reason, "stop_raw": self.stop_raw,
            "position_at_stop_unwrapped": self.stop_unwrapped,
            "error_at_stop_counts": stop_error,
            "error_at_stop_degrees": stop_error * 360.0 / COUNTS_PER_REV,
            "motion_time_to_stop_s": self.stop_elapsed_s,
            "first_post_stop_raw": first_post_stop_raw,
            "first_post_stop_unwrapped": first_post_stop_unwrapped,
            "first_post_stop_coast_counts": first_coast,
            "first_post_stop_coast_degrees": first_coast * 360.0 / COUNTS_PER_REV,
            "final_raw": self.previous_raw,
            "final_error_counts": final_error,
            "final_error_degrees": final_error * 360.0 / COUNTS_PER_REV,
            "final_abs_error_counts": abs(final_error),
            "final_abs_error_degrees": abs(final_error) * 360.0 / COUNTS_PER_REV,
            "settled_coast_counts": settled_coast,
            "settled_coast_degrees": settled_coast * 360.0 / COUNTS_PER_REV,
            "settled_outcome": "overshoot" if signed_final < 0 else ("undershoot" if signed_final > 0 else "exact"),
            "invalid_replies_total": self.invalid_total,
            **self.recovery_log_fields(),
            "total_elapsed_including_settle_s": time.perf_counter() - self.command_started,
        }

    def write_logs(self, raw_path: Path, transition_path: Path) -> None:
        for path, rows in ((raw_path, self.rows), (transition_path, self.transitions)):
            fields = sorted({key for row in rows for key in row})
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader(); writer.writerows(rows)
