"""Bounded AZ start-from-rest and sustain characterization for NXW436.

This diagnostic is intentionally outside the production controller.  It keeps
start threshold and running threshold separate: ``start`` uses every selected
payload from STOP, while ``sustain`` first gives a short verified 0072F1 kick,
then observes the selected payload.  Candidate payloads are always explicit;
no external 114GT speed table is treated as NXW436 calibration.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import time
from pathlib import Path

import serial

from nxw436_driver import COUNTS_PER_REV, NXW436, is_valid_raw_position, signed_delta
from nxw436_low_speed_characterize import (
    is_material_reverse_increment, motion_stability_metrics, parse_payload, valid_label,
)


KICK_PAYLOAD = bytes.fromhex("0072F1")  # measured on this NXW436; not an external-table assumption
MAX_CONSECUTIVE_INVALID = 2
MAX_PLAUSIBLE_COUNTS_PER_SECOND = 10_000
MIN_PLAUSIBLE_DELTA_COUNTS = 1_000
BREAKAWAY_COUNTS = 2
MIN_OBSERVATION_SECONDS = 3.0
# Endurance observations remain intentionally bounded.  Five-minute sidereal
# checks need headroom for an explicit 300 s request without making this a
# general unattended motor-run utility.
MAX_OBSERVATION_SECONDS = 360.0


class CharacterizationAbort(RuntimeError):
    pass


def classify_trial(*, reliability_progress: int, stable: bool, invalid_samples: int,
                   min_progress_counts: int) -> tuple[bool, str]:
    if invalid_samples:
        return False, "invalid-samples-observed"
    if reliability_progress < min_progress_counts:
        return False, "insufficient-commanded-progress"
    if not stable:
        return False, "motion-not-stable"
    return True, "reliable"


WINDOW_SECONDS = 30.0


def output_paths(label: str) -> tuple[Path, Path, Path]:
    prefix = f"nxw436_az_start_sustain_{label}"
    return (
        Path(f"{prefix}_raw.csv"),
        Path(f"{prefix}_summary.csv"),
        Path(f"{prefix}_windows.csv"),
    )


def valid_observation_seconds(value: float) -> bool:
    """Keep trials bounded while permitting an explicitly requested long window."""
    return MIN_OBSERVATION_SECONDS <= value <= MAX_OBSERVATION_SECONDS


def read_valid_position(mount: NXW436) -> tuple[bytes, int]:
    frame = mount.query_position_raw("az")
    if frame is None:
        raise TimeoutError("No AZ position reply")
    raw = int.from_bytes(frame, "big")
    if not is_valid_raw_position(raw):
        raise CharacterizationAbort(f"malformed AZ raw position outside modulus: {raw}")
    return frame, raw


def _as_bool(value: object) -> bool:
    return value is True or value == "True"


def _as_float(value: object) -> float:
    return float(value)  # keeps malformed CSV evidence explicit rather than guessed


def _as_int(value: object) -> int:
    return int(value)


def _linear_slope_per_minute(points: list[tuple[float, float]]) -> float | None:
    """Ordinary least-squares slope; diagnostic only, not a motor model."""
    if len(points) < 2:
        return None
    mean_x = statistics.fmean(point[0] for point in points)
    mean_y = statistics.fmean(point[1] for point in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator * 60.0


def analyze_measurement_windows(
    rows: list[dict], *, direction_sign: int, target_cps: float | None,
    window_seconds: float = WINDOW_SECONDS,
) -> tuple[list[dict], dict]:
    """Produce non-interpolated window metrics from one trial's raw evidence."""
    measurement_rows = [row for row in rows if row.get("phase") == "candidate-measurement"]
    valid_rows = [
        row for row in measurement_rows
        if _as_bool(row.get("valid")) and row.get("raw_position") not in (None, "")
    ]
    if not valid_rows:
        return [], {}

    origin = _as_float(valid_rows[0]["elapsed_candidate_s"])
    end = _as_float(valid_rows[-1]["elapsed_candidate_s"]) - origin
    windows: list[dict] = []
    start = 0.0
    index = 1
    while start <= end:
        nominal_end = min(start + window_seconds, end)
        window_rows = [
            row for row in measurement_rows
            if start <= _as_float(row["elapsed_candidate_s"]) - origin <= nominal_end
        ]
        window_valid = [
            row for row in window_rows
            if _as_bool(row.get("valid")) and row.get("raw_position") not in (None, "")
        ]
        reverse_count = sum(
            1 for row in window_valid
            if row.get("delta_counts") not in (None, "")
            and is_material_reverse_increment(_as_int(row["delta_counts"]), direction_sign)
        )
        row: dict = {
            "window_index": index,
            "window_nominal_start_s": start,
            "window_nominal_end_s": nominal_end,
            "valid_samples": len(window_valid),
            "invalid_samples": len(window_rows) - len(window_valid),
            "reverse_increments": reverse_count,
        }
        if len(window_valid) >= 2:
            first, last = window_valid[0], window_valid[-1]
            first_time = _as_float(first["elapsed_candidate_s"]) - origin
            last_time = _as_float(last["elapsed_candidate_s"]) - origin
            elapsed = last_time - first_time
            window_delta = signed_delta(_as_int(first["raw_position"]), _as_int(last["raw_position"]))
            cps = window_delta / elapsed if elapsed > 0 else None
            row.update({
                "sample_start_s": first_time,
                "sample_end_s": last_time,
                "start_raw": _as_int(first["raw_position"]),
                "end_raw": _as_int(last["raw_position"]),
                "signed_delta_counts": window_delta,
                "elapsed_s": elapsed,
                "counts_per_s": cps,
                "commanded_cps": "" if cps is None else cps * direction_sign,
                "degrees_per_s": "" if cps is None else cps * 360.0 / COUNTS_PER_REV,
            })
        else:
            row.update({
                "sample_start_s": "", "sample_end_s": "", "start_raw": "", "end_raw": "",
                "signed_delta_counts": "", "elapsed_s": "", "counts_per_s": "",
                "commanded_cps": "", "degrees_per_s": "",
            })
        windows.append(row)
        if nominal_end >= end:
            break
        start += window_seconds
        index += 1

    speed_points = [
        ((float(row["sample_start_s"]) + float(row["sample_end_s"])) / 2.0, float(row["commanded_cps"]))
        for row in windows if row["commanded_cps"] != ""
    ]
    commanded_speeds = [point[1] for point in speed_points]
    metrics: dict = {
        "window_seconds": window_seconds,
        "window_count": len(windows),
        "window_cps_min": min(commanded_speeds) if commanded_speeds else "",
        "window_cps_max": max(commanded_speeds) if commanded_speeds else "",
        "window_cps_stddev": statistics.stdev(commanded_speeds) if len(commanded_speeds) >= 2 else 0.0 if commanded_speeds else "",
        "window_speed_slope_cps_per_min": _linear_slope_per_minute(speed_points),
        "window_first_cps": commanded_speeds[0] if commanded_speeds else "",
        "window_last_cps": commanded_speeds[-1] if commanded_speeds else "",
    }
    first_valid = valid_rows[0]
    first_raw = _as_int(first_valid["raw_position"])
    whole_elapsed = end
    whole_commanded_cps = ""
    if whole_elapsed > 0:
        whole_commanded_cps = (
            signed_delta(first_raw, _as_int(valid_rows[-1]["raw_position"]))
            * direction_sign / whole_elapsed
        )
    metrics["window_analysis_whole_cps"] = whole_commanded_cps
    for requested_seconds, label in ((60.0, "1m"), (180.0, "3m"), (300.0, "5m")):
        effective_seconds = min(requested_seconds, end)
        checkpoint_rows = [
            row for row in valid_rows
            if _as_float(row["elapsed_candidate_s"]) - origin <= effective_seconds
        ]
        if not checkpoint_rows:
            metrics[f"tracking_{label}_sample_s"] = ""
            metrics[f"tracking_{label}_error_counts"] = ""
            metrics[f"tracking_{label}_error_degrees"] = ""
            continue
        checkpoint = checkpoint_rows[-1]
        actual_seconds = _as_float(checkpoint["elapsed_candidate_s"]) - origin
        actual_progress = signed_delta(first_raw, _as_int(checkpoint["raw_position"])) * direction_sign
        if target_cps is None:
            error_counts = ""
            error_degrees = ""
        else:
            error_counts = actual_progress - target_cps * actual_seconds
            error_degrees = error_counts * 360.0 / COUNTS_PER_REV
        metrics[f"tracking_{label}_sample_s"] = actual_seconds
        metrics[f"tracking_{label}_error_counts"] = error_counts
        metrics[f"tracking_{label}_error_degrees"] = error_degrees
    return windows, metrics


def load_raw_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("+", "-"))
    parser.add_argument("--mode", choices=("start", "sustain"), required=True)
    parser.add_argument("--payloads", nargs="+", required=True, type=parse_payload,
                        help="ordered explicit candidate payloads; no default sweep")
    parser.add_argument("--trials", type=int, default=3, help="independent STOP-to-STOP trials per payload")
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--az-off-tripod", action="store_true", required=True,
                        help="asserts free AZ travel and no cable-wrap risk")
    parser.add_argument("--observation-seconds", type=float, default=8.0)
    parser.add_argument("--sample-seconds", type=float, default=0.18)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--kick-seconds", type=float, default=0.5,
                        help="sustain mode only: duration of measured 0072F1 breakaway kick")
    parser.add_argument("--warmup-seconds", type=float, default=1.0,
                        help="sustain mode only: unmeasured interval after transition from kick")
    parser.add_argument("--min-progress-counts", type=int, default=20)
    parser.add_argument("--target-cps", type=float,
                        help="optional target for sustain reporting; does not alter commands")
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--compare-raw", type=Path,
                        help="optional prior raw CSV for an offline, same-method comparison")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.trials <= 5:
        parser.error("--trials must be 1..5")
    if not valid_observation_seconds(args.observation_seconds):
        parser.error(
            f"--observation-seconds must be {MIN_OBSERVATION_SECONDS:.1f}.."
            f"{MAX_OBSERVATION_SECONDS:.1f}"
        )
    if not 0.05 <= args.sample_seconds <= 0.5:
        parser.error("--sample-seconds must be 0.05..0.5")
    if not 0.5 <= args.settle_seconds <= 5.0:
        parser.error("--settle-seconds must be 0.5..5.0")
    if not 0.2 <= args.kick_seconds <= 1.0:
        parser.error("--kick-seconds must be 0.2..1.0")
    if not 0.5 <= args.warmup_seconds < args.observation_seconds - 1.0:
        parser.error("--warmup-seconds must be >=0.5 and leave 1.0 s for sustain measurement")
    if args.min_progress_counts < 2:
        parser.error("--min-progress-counts must be >=2")
    if args.target_cps is not None and args.target_cps <= 0:
        parser.error("--target-cps must be >0")
    if not valid_label(args.run_label):
        parser.error("--run-label may contain only letters, digits, '-' and '_'")
    if len(set(args.payloads)) != len(args.payloads):
        parser.error("--payloads must not contain duplicates")
    raw_path, summary_path, windows_path = output_paths(args.run_label)
    if raw_path.exists() or summary_path.exists() or windows_path.exists():
        parser.error(f"output already exists for label '{args.run_label}'; choose a unique label")
    if args.compare_raw is not None and not args.compare_raw.is_file():
        parser.error(f"--compare-raw does not exist: {args.compare_raw}")

    plan = " ".join(f"{value:06X}" for value in args.payloads)
    print(f"AZ {args.mode} diagnostic, direction={args.direction}; trials={args.trials}; payloads={plan}")
    print("Every trial is independent: STOP STOP -> settle -> start read -> [kick] -> candidate -> STOP STOP -> settle.")
    print("Reliability means every trial has valid samples, >= min commanded progress, and two-half stability.")
    if args.mode == "sustain":
        print(f"Kick is fixed measured NXW436 payload 0072F1 for {args.kick_seconds:.2f}s; no FAST kick is used.")
    if args.dry_run:
        print(f"DRY RUN: would write {raw_path}, {summary_path}, and {windows_path}; COM is not opened.")
        return

    direction_sign = 1 if args.direction == "+" else -1
    raw_rows: list[dict] = []
    window_rows: list[dict] = []
    summaries: list[dict] = []
    interrupted = False
    with NXW436(args.port) as mount:
        az_frame, az_preflight = read_valid_position(mount)
        alt_frame = mount.query_position_raw("alt")
        if alt_frame is None or not is_valid_raw_position(int.from_bytes(alt_frame, "big")):
            raise CharacterizationAbort("ALT read-only preflight failed")
        alt_preflight = int.from_bytes(alt_frame, "big")
        print(f"PREFLIGHT AZ={az_preflight:06X} ALT={alt_preflight:06X}")
        input("Confirm AZ clearance and stable GND, then press Enter to begin... ")

        for payload_index, payload_value in enumerate(args.payloads, start=1):
            for trial_index in range(1, args.trials + 1):
                payload = payload_value.to_bytes(3, "big")
                summary: dict = {
                    "run_label": args.run_label, "mode": args.mode, "direction": args.direction,
                    "payload_index": payload_index, "payload_hex": f"{payload_value:06X}",
                    "trial_index": trial_index, "trials_requested": args.trials,
                    "observation_seconds": args.observation_seconds, "sample_seconds": args.sample_seconds,
                    "settle_seconds": args.settle_seconds, "kick_payload_hex": "0072F1" if args.mode == "sustain" else "",
                    "kick_seconds": args.kick_seconds if args.mode == "sustain" else "",
                    "warmup_seconds": args.warmup_seconds if args.mode == "sustain" else "",
                    "min_progress_counts": args.min_progress_counts, "target_cps": args.target_cps if args.target_cps is not None else "",
                    "preflight_az_raw": az_preflight, "preflight_alt_raw": alt_preflight,
                    "run_status": "not_started",
                }
                print(f"POINT {payload_index}/{len(args.payloads)} TRIAL {trial_index}/{args.trials} payload={payload_value:06X}")
                trial_raw_start = len(raw_rows)
                try:
                    mount.stop("az", args.direction)
                    time.sleep(args.settle_seconds)
                    start_frame, start_raw = read_valid_position(mount)
                    command_started = time.perf_counter()
                    previous_raw, previous_time = start_raw, command_started
                    candidate_started = command_started
                    if args.mode == "sustain":
                        mount.move("az", args.direction, KICK_PAYLOAD)
                        time.sleep(args.kick_seconds)
                        mount.move("az", args.direction, payload)
                        candidate_started = time.perf_counter()
                    else:
                        mount.move("az", args.direction, payload)
                    deadline = candidate_started + args.observation_seconds
                    measure_deadline = candidate_started + (args.warmup_seconds if args.mode == "sustain" else 0.0)
                    measure_start_raw: int | None = None
                    measure_start_time: float | None = None
                    measure_samples: list[tuple[float, int]] = []
                    end_raw, end_time = start_raw, command_started
                    invalid_samples = consecutive_invalid = material_reverse_samples = 0
                    while True:
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            break
                        time.sleep(min(args.sample_seconds, remaining))
                        now = time.perf_counter()
                        if now > deadline:
                            break
                        frame = mount.query_position_raw("az")
                        received = time.perf_counter()
                        elapsed_candidate = received - candidate_started
                        phase = "candidate-warmup" if received < measure_deadline else "candidate-measurement"
                        common = {
                            "payload_index": payload_index, "trial_index": trial_index,
                            "payload_hex": f"{payload_value:06X}", "timestamp": time.time(),
                            "monotonic_s": received, "elapsed_candidate_s": elapsed_candidate,
                            "phase": phase,
                        }
                        if frame is None:
                            invalid_samples += 1
                            consecutive_invalid += 1
                            raw_rows.append(common | {"rx_bytes_hex": "", "reply_kind": "timeout", "raw_position": "", "delta_counts": "", "valid": False, "note": "no-rx"})
                            if consecutive_invalid > MAX_CONSECUTIVE_INVALID:
                                raise CharacterizationAbort("three consecutive invalid AZ samples")
                            continue
                        raw = int.from_bytes(frame, "big")
                        if not is_valid_raw_position(raw):
                            invalid_samples += 1
                            consecutive_invalid += 1
                            raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "malformed-outside-modulus", "raw_position": raw, "delta_counts": "", "valid": False, "note": "rejected"})
                            if consecutive_invalid > MAX_CONSECUTIVE_INVALID:
                                raise CharacterizationAbort("three consecutive invalid AZ samples")
                            continue
                        delta = signed_delta(previous_raw, raw)
                        interval = received - previous_time
                        plausible_limit = max(MIN_PLAUSIBLE_DELTA_COUNTS, MAX_PLAUSIBLE_COUNTS_PER_SECOND * interval)
                        if abs(delta) > plausible_limit:
                            invalid_samples += 1
                            consecutive_invalid += 1
                            raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "implausible-jump", "raw_position": raw, "delta_counts": delta, "valid": False, "note": "rejected"})
                            if consecutive_invalid > MAX_CONSECUTIVE_INVALID:
                                raise CharacterizationAbort("three consecutive invalid AZ samples")
                            continue
                        consecutive_invalid = 0
                        if is_material_reverse_increment(delta, direction_sign):
                            material_reverse_samples += 1
                        raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "valid", "raw_position": raw, "delta_counts": delta, "valid": True, "note": ""})
                        previous_raw, previous_time = raw, received
                        end_raw, end_time = raw, received
                        if received >= measure_deadline:
                            if measure_start_raw is None:
                                measure_start_raw, measure_start_time = raw, received
                            measure_samples.append((received, signed_delta(measure_start_raw, raw)))

                    if measure_start_raw is None or measure_start_time is None:
                        raise CharacterizationAbort("no valid candidate measurement sample")
                    measurement_delta = signed_delta(measure_start_raw, end_raw)
                    measurement_elapsed = end_time - measure_start_time
                    commanded_progress = signed_delta(start_raw, end_raw) * direction_sign
                    # In sustain mode the kick proves only that the axis was
                    # moving before the candidate.  The candidate must itself
                    # produce enough post-warm-up encoder progress.
                    reliability_progress = (
                        measurement_delta * direction_sign if args.mode == "sustain" else commanded_progress
                    )
                    stable, stability_reason, first_half_cps, second_half_cps, half_rate_ratio = motion_stability_metrics(
                        measure_samples, direction_sign
                    )
                    if material_reverse_samples:
                        stable, stability_reason = False, "material-reverse-increment-observed"
                    reliable, reliability_reason = classify_trial(
                        reliability_progress=reliability_progress, stable=stable,
                        invalid_samples=invalid_samples, min_progress_counts=args.min_progress_counts,
                    )
                    cps = measurement_delta / measurement_elapsed
                    summary.update({
                        "start_raw": start_raw, "start_rx_bytes_hex": start_frame.hex().upper(),
                        "measure_start_raw": measure_start_raw, "end_raw": end_raw,
                        "commanded_progress_counts": commanded_progress,
                        "reliability_progress_counts": reliability_progress,
                        "measurement_delta_counts": measurement_delta,
                        "measurement_elapsed_s": measurement_elapsed,
                        "counts_per_s": cps, "degrees_per_s": cps * 360.0 / COUNTS_PER_REV,
                        "target_cps_error": "" if args.target_cps is None else cps * direction_sign - args.target_cps,
                        "invalid_samples_total": invalid_samples,
                        "material_reverse_samples_total": material_reverse_samples,
                        "stable_motion": stable, "stability_reason": stability_reason,
                        "first_half_cps": first_half_cps, "second_half_cps": second_half_cps,
                        "half_rate_ratio": half_rate_ratio,
                        "trial_reliable": reliable, "reliability_reason": reliability_reason,
                        "run_status": "completed",
                    })
                    windows, window_metrics = analyze_measurement_windows(
                        raw_rows[trial_raw_start:], direction_sign=direction_sign,
                        target_cps=args.target_cps,
                    )
                    for row in windows:
                        row.update({
                            "run_label": args.run_label, "payload_index": payload_index,
                            "trial_index": trial_index, "payload_hex": f"{payload_value:06X}",
                        })
                    window_rows.extend(windows)
                    summary.update(window_metrics)
                    if args.compare_raw is not None:
                        reference_windows, reference_metrics = analyze_measurement_windows(
                            load_raw_rows(args.compare_raw), direction_sign=direction_sign,
                            target_cps=args.target_cps,
                        )
                        summary.update({
                            "comparison_raw_path": str(args.compare_raw),
                            "comparison_reference_window_first_cps": reference_metrics.get("window_first_cps", ""),
                            "comparison_reference_window_last_cps": reference_metrics.get("window_last_cps", ""),
                            "comparison_reference_window_slope_cps_per_min": reference_metrics.get("window_speed_slope_cps_per_min", ""),
                            "comparison_reference_window_stddev_cps": reference_metrics.get("window_cps_stddev", ""),
                            "comparison_mean_cps_delta": "" if reference_metrics.get("window_analysis_whole_cps", "") == ""
                            else cps * direction_sign - reference_metrics["window_analysis_whole_cps"],
                        })
                except (CharacterizationAbort, TimeoutError, serial.SerialException) as error:
                    summary.update({"run_status": "aborted", "abort_reason": str(error)})
                    print(f"ABORT: {error}")
                except KeyboardInterrupt:
                    summary.update({"run_status": "aborted", "abort_reason": "KeyboardInterrupt"})
                    interrupted = True
                    print("ABORT: KeyboardInterrupt")
                finally:
                    try:
                        mount.stop("az", args.direction)
                    except Exception as stop_error:
                        summary["stop_error"] = str(stop_error)
                        print(f"EMERGENCY STOP WRITE FAILED: {stop_error}")
                    time.sleep(args.settle_seconds)
                summaries.append(summary)
                if interrupted or summary["run_status"] != "completed":
                    break
            if interrupted or summaries[-1]["run_status"] != "completed":
                break

    raw_fields = ("payload_index", "trial_index", "payload_hex", "timestamp", "monotonic_s", "elapsed_candidate_s",
                  "phase", "rx_bytes_hex", "reply_kind", "raw_position", "delta_counts", "valid", "note")
    with raw_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=raw_fields)
        writer.writeheader()
        writer.writerows(raw_rows)
    fields = sorted({key for row in summaries for key in row})
    with summary_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    window_fields = (
        "run_label", "payload_index", "trial_index", "payload_hex", "window_index",
        "window_nominal_start_s", "window_nominal_end_s", "sample_start_s", "sample_end_s",
        "start_raw", "end_raw", "signed_delta_counts", "elapsed_s", "counts_per_s",
        "commanded_cps", "degrees_per_s", "valid_samples", "invalid_samples", "reverse_increments",
    )
    with windows_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=window_fields)
        writer.writeheader()
        writer.writerows(window_rows)
    print("Saved:", raw_path, summary_path, windows_path)
    if interrupted or any(row["run_status"] != "completed" for row in summaries):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
