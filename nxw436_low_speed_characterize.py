"""Bounded, encoder-based low-speed characterization for one NXW436 axis.

This is a diagnostic tool, not part of the production position controller and
not a confirmation of any external NexStar 114GT payload table.  Every payload
starts from rest, is measured only after a configurable warm-up interval, and
ends with a mandatory double STOP plus settle interval.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import time
from pathlib import Path

import serial

from nxw436_driver import COUNTS_PER_REV, NXW436, is_valid_raw_position, signed_delta


BREAKAWAY_COUNTS = 2
MAX_CONSECUTIVE_INVALID = 2
MAX_PLAUSIBLE_COUNTS_PER_SECOND = 10_000
MIN_PLAUSIBLE_DELTA_COUNTS = 1_000


class CharacterizationAbort(RuntimeError):
    """A run cannot safely provide an encoder-derived measurement."""


def parse_payload(text: str) -> int:
    try:
        value = int(text, 16)
    except ValueError as error:
        raise argparse.ArgumentTypeError("payload must be six hexadecimal digits") from error
    if not 0 < value <= 0x00E5E3:
        raise argparse.ArgumentTypeError("payload must be 000001..00E5E3")
    return value


def valid_label(label: str) -> bool:
    return label.replace("-", "").replace("_", "").isalnum()


def motion_stability_metrics(samples: list[tuple[float, int]], direction_sign: int) -> tuple[bool, str, float | None, float | None, float | None]:
    """Compare time-halves, avoiding false failure from sub-sample encoder quantization."""
    if len(samples) < 3:
        return False, "insufficient-valid-post-warmup-samples", None, None, None
    midpoint = len(samples) // 2
    first_half = (samples[midpoint - 1][1] - samples[0][1]) * direction_sign
    second_half = (samples[-1][1] - samples[midpoint][1]) * direction_sign
    if first_half <= 0 or second_half <= 0:
        return False, "no-commanded-progress-in-both-halves", None, None, None
    first_elapsed = samples[midpoint - 1][0] - samples[0][0]
    second_elapsed = samples[-1][0] - samples[midpoint][0]
    if first_elapsed <= 0 or second_elapsed <= 0:
        return False, "nonpositive-half-duration", None, None, None
    first_cps = first_half / first_elapsed
    second_cps = second_half / second_elapsed
    ratio = min(first_cps, second_cps) / max(first_cps, second_cps)
    if ratio < 0.5:
        return False, "half-rate-ratio-below-0.5", first_cps, second_cps, ratio
    return True, "commanded-progress-with-comparable-half-rates", first_cps, second_cps, ratio


def stable_motion(samples: list[tuple[float, int]], direction_sign: int) -> tuple[bool, str]:
    """Compatibility wrapper used by both low-speed diagnostic utilities."""
    stable, reason, _, _, _ = motion_stability_metrics(samples, direction_sign)
    return stable, reason


def is_material_reverse_increment(delta_counts: int, direction_sign: int) -> bool:
    """Ignore one-count quantization/backlash jitter; retain meaningful reverse motion."""
    return delta_counts * direction_sign <= -BREAKAWAY_COUNTS


def read_valid_position(mount: NXW436, axis: str) -> tuple[bytes, int]:
    frame = mount.query_position_raw(axis)
    if frame is None:
        raise TimeoutError(f"No position reply for {axis}")
    raw = int.from_bytes(frame, "big")
    if not is_valid_raw_position(raw):
        raise CharacterizationAbort(f"malformed {axis} position outside modulus: {raw}")
    return frame, raw


def output_paths(axis: str, label: str) -> tuple[Path, Path]:
    prefix = f"nxw436_{axis}_low_speed_{label}"
    return Path(f"{prefix}_raw.csv"), Path(f"{prefix}_summary.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("+", "-"))
    parser.add_argument("--axis", choices=("alt", "az"), required=True)
    parser.add_argument("--payloads", nargs="+", required=True, type=parse_payload,
                        help="ordered explicit 24-bit hex payloads; no default sweep is assumed")
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--duration", type=float, default=8.0,
                        help="total commanded time per payload, seconds")
    parser.add_argument("--warmup-seconds", type=float, default=3.0,
                        help="unmeasured acceleration interval after each MOVE")
    parser.add_argument("--sample-seconds", type=float, default=0.18)
    parser.add_argument("--settle-seconds", type=float, default=0.75,
                        help="quiet interval after STOP STOP before the next payload")
    parser.add_argument("--post-stop-samples", type=int, default=3,
                        help="raw position samples after verified STOP STOP")
    parser.add_argument("--load-context", default="unspecified",
                        help="mechanical/load description stored in raw and summary CSV")
    parser.add_argument("--az-off-tripod", action="store_true",
                        help="required for AZ; asserts free travel and no cable wrap")
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--dry-run", action="store_true", help="validate and print plan without opening COM")
    args = parser.parse_args()

    if not 3.0 <= args.duration <= 15.0:
        parser.error("--duration must be 3.0..15.0")
    if not 0.5 <= args.warmup_seconds < args.duration - 1.0:
        parser.error("--warmup-seconds must be >=0.5 and leave at least 1.0 s for measurement")
    if not 0.05 <= args.sample_seconds <= 0.5:
        parser.error("--sample-seconds must be 0.05..0.5")
    if not 0.25 <= args.settle_seconds <= 5.0:
        parser.error("--settle-seconds must be 0.25..5.0")
    if not 1 <= args.post_stop_samples <= 10:
        parser.error("--post-stop-samples must be 1..10")
    if not valid_label(args.run_label):
        parser.error("--run-label may contain only letters, digits, '-' and '_'")
    if args.axis == "az" and not args.az_off_tripod:
        parser.error("AZ motion is blocked: secure free travel, then pass --az-off-tripod")
    if len(set(args.payloads)) != len(args.payloads):
        parser.error("--payloads must not contain duplicates")

    raw_path, summary_path = output_paths(args.axis, args.run_label)
    if raw_path.exists() or summary_path.exists():
        parser.error(f"output already exists for label '{args.run_label}'; choose a unique label")
    plan = " ".join(f"{payload:06X}" for payload in args.payloads)
    print(f"{args.axis.upper()} low-speed diagnostic; direction={args.direction}; payloads={plan}")
    print("Every point: STOP STOP -> settle -> read start -> MOVE -> warm-up -> measure -> STOP STOP -> settle.")
    print("No production controller, GoTo profile, stop margin, or recovery policy is used or changed.")
    if args.axis == "az":
        print("AZ safety interlock acknowledged: --az-off-tripod.")
    if args.dry_run:
        print(f"DRY RUN: would write {raw_path} and {summary_path}; COM is not opened.")
        return

    raw_rows: list[dict] = []
    summaries: list[dict] = []
    expected_sign = 1 if args.direction == "+" else -1
    print("Read-only AZ and ALT preflight is mandatory before the first MOVE.")
    with NXW436(args.port) as mount:
        az_frame, az_preflight = read_valid_position(mount, "az")
        alt_frame, alt_preflight = read_valid_position(mount, "alt")
        print(f"PREFLIGHT AZ={az_preflight:06X} ALT={alt_preflight:06X}")
        input(f"Confirm {args.axis.upper()} clearance and stable GND, then press Enter to begin... ")

        for point_index, payload_value in enumerate(args.payloads, start=1):
            payload = payload_value.to_bytes(3, "big")
            summary: dict = {
                "run_label": args.run_label,
                "point_index": point_index,
                "axis": args.axis,
                "direction": args.direction,
                "payload_hex": f"{payload_value:06X}",
                "duration_requested_s": args.duration,
                "warmup_seconds": args.warmup_seconds,
                "sample_seconds": args.sample_seconds,
                "settle_seconds": args.settle_seconds,
                "load_context": args.load_context,
                "preflight_az_raw": az_preflight,
                "preflight_az_rx_bytes_hex": az_frame.hex().upper(),
                "preflight_alt_raw": alt_preflight,
                "preflight_alt_rx_bytes_hex": alt_frame.hex().upper(),
                "run_status": "not_started",
                "post_stop_samples_requested": args.post_stop_samples,
            }
            command_started = time.perf_counter()
            print(f"POINT {point_index}/{len(args.payloads)} payload={payload_value:06X}")
            try:
                mount.stop(args.axis, args.direction)
                time.sleep(args.settle_seconds)
                start_frame, start_raw = read_valid_position(mount, args.axis)
                previous_raw, previous_time = start_raw, time.perf_counter()
                command_started = previous_time
                warmup_deadline = command_started + args.warmup_seconds
                deadline = command_started + args.duration
                measure_start_raw: int | None = None
                measure_start_time: float | None = None
                end_raw = start_raw
                end_time = command_started
                commanded_progress_from_rest = 0
                valid_measurement_samples: list[tuple[float, int]] = []
                invalid_samples = 0
                consecutive_invalid = 0
                reverse_samples = 0
                mount.move(args.axis, args.direction, payload)
                summary.update({
                    "start_raw": start_raw,
                    "start_rx_bytes_hex": start_frame.hex().upper(),
                    "command_monotonic_s": command_started,
                })
                while True:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    time.sleep(min(args.sample_seconds, remaining))
                    now = time.perf_counter()
                    if now > deadline:
                        break
                    frame = mount.query_position_raw(args.axis)
                    received = time.perf_counter()
                    elapsed = received - command_started
                    common = {
                        "point_index": point_index, "payload_hex": f"{payload_value:06X}",
                        "timestamp": time.time(), "monotonic_s": received, "elapsed_s": elapsed,
                        "phase": "warmup" if received < warmup_deadline else "measurement",
                        "load_context": args.load_context,
                    }
                    if frame is None:
                        invalid_samples += 1
                        consecutive_invalid += 1
                        raw_rows.append(common | {"rx_bytes_hex": "", "reply_kind": "timeout", "raw_position": "", "delta_counts": "", "valid": False, "note": "no-rx-frame"})
                        if consecutive_invalid > MAX_CONSECUTIVE_INVALID:
                            raise CharacterizationAbort("three consecutive invalid position samples")
                        continue
                    raw = int.from_bytes(frame, "big")
                    if not is_valid_raw_position(raw):
                        invalid_samples += 1
                        consecutive_invalid += 1
                        raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "malformed-outside-modulus", "raw_position": raw, "delta_counts": "", "valid": False, "note": "rejected"})
                        if consecutive_invalid > MAX_CONSECUTIVE_INVALID:
                            raise CharacterizationAbort("three consecutive invalid position samples")
                        continue
                    delta = signed_delta(previous_raw, raw)
                    interval = received - previous_time
                    plausible_limit = max(MIN_PLAUSIBLE_DELTA_COUNTS, MAX_PLAUSIBLE_COUNTS_PER_SECOND * interval)
                    if abs(delta) > plausible_limit:
                        invalid_samples += 1
                        consecutive_invalid += 1
                        raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "implausible-jump", "raw_position": raw, "delta_counts": delta, "valid": False, "note": "rejected"})
                        if consecutive_invalid > MAX_CONSECUTIVE_INVALID:
                            raise CharacterizationAbort("three consecutive invalid position samples")
                        continue
                    consecutive_invalid = 0
                    commanded_progress_from_rest = signed_delta(start_raw, raw) * expected_sign
                    if is_material_reverse_increment(delta, expected_sign):
                        reverse_samples += 1
                    raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "valid", "raw_position": raw, "delta_counts": delta, "valid": True, "note": ""})
                    if commanded_progress_from_rest <= -BREAKAWAY_COUNTS:
                        raise CharacterizationAbort(
                            f"confirmed opposite-direction movement: {commanded_progress_from_rest} counts"
                        )
                    previous_raw, previous_time = raw, received
                    end_raw, end_time = raw, received
                    if received >= warmup_deadline:
                        if measure_start_raw is None:
                            measure_start_raw, measure_start_time = raw, received
                        valid_measurement_samples.append((received, raw))

                if measure_start_raw is None or measure_start_time is None:
                    raise CharacterizationAbort("no valid post-warmup position sample")
                measured_delta = signed_delta(measure_start_raw, end_raw)
                measured_elapsed = end_time - measure_start_time
                if measured_elapsed <= 0:
                    raise CharacterizationAbort("nonpositive measurement elapsed time")
                stable, stability_reason = stable_motion(
                    [(sample_time, signed_delta(measure_start_raw, sample_raw)) for sample_time, sample_raw in valid_measurement_samples],
                    expected_sign,
                )
                if reverse_samples:
                    stable, stability_reason = False, "valid-reverse-increment-observed"
                summary.update({
                    "measure_start_raw": measure_start_raw,
                    "end_raw": end_raw,
                    "elapsed_measurement_s": measured_elapsed,
                    "signed_modular_delta_counts": measured_delta,
                    "counts_per_s": measured_delta / measured_elapsed,
                    "degrees_per_s": measured_delta * 360.0 / COUNTS_PER_REV / measured_elapsed,
                    "starts_from_rest": commanded_progress_from_rest >= BREAKAWAY_COUNTS,
                    "commanded_progress_from_rest_counts": commanded_progress_from_rest,
                    "stable_motion": stable,
                    "stability_reason": stability_reason,
                    "valid_measurement_samples": len(valid_measurement_samples),
                    "invalid_samples_total": invalid_samples,
                    "reverse_samples_total": reverse_samples,
                    "run_status": "completed",
                })
            except (CharacterizationAbort, TimeoutError, serial.SerialException) as error:
                summary.update({"run_status": "aborted", "abort_reason": str(error)})
                print(f"ABORT POINT {point_index}: {error}")
            except KeyboardInterrupt:
                summary.update({"run_status": "aborted", "abort_reason": "KeyboardInterrupt"})
                print("ABORT: KeyboardInterrupt")
                summaries.append(summary)
                raise
            finally:
                try:
                    mount.stop(args.axis, args.direction)
                except Exception as stop_error:
                    summary["stop_error"] = str(stop_error)
                    print(f"EMERGENCY STOP WRITE FAILED: {stop_error}")
                time.sleep(args.settle_seconds)
                post_stop_positions: list[int] = []
                for post_index in range(1, args.post_stop_samples + 1):
                    frame = mount.query_position_raw(args.axis)
                    received = time.perf_counter()
                    common = {
                        "point_index": point_index, "payload_hex": f"{payload_value:06X}",
                        "timestamp": time.time(), "monotonic_s": received,
                        "elapsed_s": received - command_started, "phase": "post_stop",
                        "load_context": args.load_context,
                    }
                    if frame is None:
                        raw_rows.append(common | {"rx_bytes_hex": "", "reply_kind": "timeout", "raw_position": "", "delta_counts": "", "valid": False, "note": f"post-stop-{post_index}"})
                    else:
                        raw = int.from_bytes(frame, "big")
                        valid = is_valid_raw_position(raw)
                        raw_rows.append(common | {"rx_bytes_hex": frame.hex().upper(), "reply_kind": "post-stop-valid" if valid else "malformed-outside-modulus", "raw_position": raw, "delta_counts": "", "valid": valid, "note": f"post-stop-{post_index}"})
                        if valid:
                            post_stop_positions.append(raw)
                    if post_index < args.post_stop_samples:
                        time.sleep(args.sample_seconds)
                summary["post_stop_valid_samples"] = len(post_stop_positions)
                if post_stop_positions:
                    summary["post_stop_first_raw"] = post_stop_positions[0]
                    summary["post_stop_last_raw"] = post_stop_positions[-1]
                    summary["post_stop_delta_counts"] = signed_delta(post_stop_positions[0], post_stop_positions[-1])
            summaries.append(summary)
            if summary["run_status"] != "completed":
                break

    raw_fields = ("point_index", "payload_hex", "timestamp", "monotonic_s", "elapsed_s", "phase", "load_context",
                  "rx_bytes_hex", "reply_kind", "raw_position", "delta_counts", "valid", "note")
    with raw_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=raw_fields)
        writer.writeheader()
        writer.writerows(raw_rows)
    summary_fields = sorted({key for row in summaries for key in row})
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summaries)
    print("Saved:", raw_path, summary_path)
    if any(row["run_status"] != "completed" for row in summaries):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
