"""Axis start, transient, plateau and coast characterization for NXW436.

One payload is tested per invocation.  This is intentionally not a LUT tool.
It first verifies read-only AZ and ALT replies, then commands one selected axis
from a
complete STOP.  Up to two consecutive timeout/malformed samples are retained
as invalid evidence; the third, or reverse movement, aborts and sends STOP
twice.
"""

import argparse
import csv
import statistics
import time
from pathlib import Path

import serial

from nxw436_driver import COUNTS_PER_REV, NXW436, is_valid_raw_position, signed_delta


MAX_PLAUSIBLE_COUNTS_PER_SECOND = 10_000
MIN_PLAUSIBLE_DELTA_COUNTS = 1_000
BREAKAWAY_COUNTS = 2


class RunAbort(RuntimeError):
    """An unsafe or invalid measurement condition occurred."""


def parse_payload(text: str) -> int:
    try:
        value = int(text, 16)
    except ValueError as error:
        raise argparse.ArgumentTypeError("payload must be six hex digits") from error
    if not 0 < value <= 0x00E5E3:
        raise argparse.ArgumentTypeError("payload must be 000001..00E5E3")
    return value


def regression_slope(points: list[tuple[float, int]]) -> float | None:
    if len(points) < 3:
        return None
    times = [point[0] for point in points]
    positions = [point[1] for point in points]
    mean_time = statistics.mean(times)
    mean_position = statistics.mean(positions)
    denominator = sum((value - mean_time) ** 2 for value in times)
    if denominator == 0:
        return None
    return sum(
        (sample_time - mean_time) * (position - mean_position)
        for sample_time, position in zip(times, positions)
    ) / denominator


def annotate_velocities(rows: list[dict], window_seconds: float = 1.0) -> None:
    valid_points: list[tuple[float, int]] = []
    for row in rows:
        if not row["valid_position"]:
            row["window_slope_cps"] = ""
            continue
        valid_points.append((row["elapsed_s"], row["unwrapped_position"]))
        window = [point for point in valid_points if point[0] >= row["elapsed_s"] - window_seconds]
        slope = regression_slope(window)
        row["window_slope_cps"] = "" if slope is None else slope


def plateau_analysis(rows: list[dict], breakaway_elapsed: float | None) -> dict:
    valid = [row for row in rows if row["valid_position"]]
    if breakaway_elapsed is None or len(valid) < 12:
        return {"steady_state": "not_reached", "reason": "no confirmed breakaway or insufficient valid samples"}

    end_time = valid[-1]["elapsed_s"]
    if end_time < 6.0:
        return {"steady_state": "not_reached", "reason": "duration below plateau comparison window"}
    tail = [row for row in valid if row["elapsed_s"] >= end_time - 3.0]
    prior = [row for row in valid if end_time - 6.0 <= row["elapsed_s"] < end_time - 3.0]
    tail_slope = regression_slope([(row["elapsed_s"], row["unwrapped_position"]) for row in tail])
    prior_slope = regression_slope([(row["elapsed_s"], row["unwrapped_position"]) for row in prior])
    local = [float(row["window_slope_cps"]) for row in tail if row["window_slope_cps"] != ""]
    if tail_slope is None or prior_slope is None or len(local) < 3:
        return {"steady_state": "not_reached", "reason": "insufficient valid comparison windows"}
    reference = max(abs(tail_slope), abs(prior_slope), 1.0)
    block_difference = abs(tail_slope - prior_slope) / reference
    local_mean = statistics.mean(local)
    local_cv = statistics.pstdev(local) / max(abs(local_mean), 1.0)
    # Conservative: a plateau needs agreement between two independent 3-s
    # slopes and low variation of 1-s local slopes.
    steady = block_difference <= 0.15 and local_cv <= 0.20
    return {
        "steady_state": "reached" if steady else "not_reached",
        "reason": "tail/prior slope agreement and local-slope variation" if steady else "slope still changing or noisy",
        "tail_slope_cps": tail_slope,
        "prior_slope_cps": prior_slope,
        "tail_vs_prior_fraction": block_difference,
        "tail_local_slope_cv": local_cv,
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = (
        "timestamp", "elapsed_s", "rx_bytes_hex", "reply_kind", "raw_position", "unwrapped_position",
        "delta_counts", "instant_cps", "window_slope_cps", "commanded_payload",
        "motion_state", "valid_position", "load_context", "note",
    )
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_one_valid_position(mount: NXW436, axis: str) -> tuple[bytes, int]:
    frame = mount.query_position_raw(axis)
    if frame is None:
        raise TimeoutError(f"No position reply for {axis}")
    raw = int.from_bytes(frame, "big")
    if not is_valid_raw_position(raw):
        raise RunAbort(f"malformed {axis} raw position outside modulus: {raw}")
    return frame, raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("+", "-"))
    parser.add_argument("--axis", choices=("alt", "az"), default="alt")
    parser.add_argument(
        "--az-off-tripod", action="store_true",
        help="required for --axis az; asserts that AZ has free travel and cannot wrap cables",
    )
    parser.add_argument("--payload", required=True, type=parse_payload)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--sample-seconds", type=float, default=0.15)
    parser.add_argument("--post-stop-seconds", type=float, default=0.25)
    parser.add_argument(
        "--load-context", default="unspecified",
        help="e.g. horizontal-start-near-max-gravity-load; stored in raw and summary CSV",
    )
    parser.add_argument("--run-label", required=True)
    parser.add_argument(
        "--overwrite", action="store_true",
        help="replace an existing raw/summary pair; use only for intentional reruns",
    )
    args = parser.parse_args()
    if not 1.0 <= args.duration <= 15.0:
        parser.error("--duration must be 1.0..15.0")
    if not 0.03 <= args.sample_seconds <= 0.5:
        parser.error("--sample-seconds must be 0.03..0.5")
    if not 0.05 <= args.post_stop_seconds <= 2.0:
        parser.error("--post-stop-seconds must be 0.05..2.0")
    if not args.run_label.replace("-", "").replace("_", "").isalnum():
        parser.error("--run-label may contain only letters, digits, '-' and '_'")
    if args.axis == "az" and not args.az_off_tripod:
        parser.error("AZ motion is blocked: remove the head from the tripod or secure all cables, then pass --az-off-tripod")

    payload = args.payload.to_bytes(3, "big")
    raw_output = Path(f"nxw436_{args.axis}_long_run_{args.run_label}_raw.csv")
    summary_output = Path(f"nxw436_{args.axis}_long_run_{args.run_label}_summary.csv")
    if not args.overwrite and (raw_output.exists() or summary_output.exists()):
        parser.error(
            f"run label '{args.run_label}' already has output; choose a new --run-label "
            "or explicitly use --overwrite"
        )
    rows: list[dict] = []
    summary: dict = {
        "run_label": args.run_label,
        "axis": args.axis,
        "payload_hex": f"{args.payload:06X}",
        "direction": args.direction,
        "load_context": args.load_context,
        "duration_requested_s": args.duration,
        "run_status": "not_started",
    }
    bad_samples_total = 0

    print(f"{args.axis.upper()} only. One payload from complete STOP; no preload and no next payload.")
    if args.axis == "az":
        print("AZ safety interlock acknowledged: --az-off-tripod.")
    print("Read-only AZ and ALT preflight is mandatory before MOVE.")
    print("Up to two consecutive timeout/malformed samples are logged and skipped.")
    print("The third, or opposite-direction movement, aborts and sends STOP twice.")
    with NXW436(args.port) as mount:
        try:
            az_frame, az_preflight = read_one_valid_position(mount, "az")
            alt_frame, alt_preflight = read_one_valid_position(mount, "alt")
            print(f"PREFLIGHT AZ={az_preflight:06X} ALT={alt_preflight:06X}")
            input(f"Confirm {args.axis.upper()} clearance and stable GND, then press Enter to command motion... ")

            mount.stop(args.axis, args.direction)
            time.sleep(0.25)
            start_frame, start_raw = read_one_valid_position(mount, args.axis)
            expected_sign = 1 if args.direction == "+" else -1
            previous_raw = start_raw
            previous_time = time.perf_counter()
            unwrapped = start_raw
            breakaway_elapsed = None
            last_before_stop = start_raw
            consecutive_bad_samples = 0
            command_started = time.perf_counter()
            deadline = command_started + args.duration
            mount.move(args.axis, args.direction, payload)
            summary.update({
                "start_raw": start_raw, "start_rx_bytes_hex": start_frame.hex().upper(),
                "preflight_az": az_preflight, "preflight_az_rx_bytes_hex": az_frame.hex().upper(),
                "preflight_alt": alt_preflight, "preflight_alt_rx_bytes_hex": alt_frame.hex().upper(),
            })
            rows.append({
                "timestamp": command_started, "elapsed_s": 0.0,
                "rx_bytes_hex": start_frame.hex().upper(), "reply_kind": "valid-start", "raw_position": start_raw,
                "unwrapped_position": start_raw, "delta_counts": 0, "instant_cps": "",
                "window_slope_cps": "", "commanded_payload": f"{args.payload:06X}",
                "motion_state": "waiting-for-breakaway", "valid_position": True, "note": "start",
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
                if frame is None:
                    consecutive_bad_samples += 1
                    bad_samples_total += 1
                    rows.append({
                        "timestamp": received, "elapsed_s": elapsed,
                        "rx_bytes_hex": "", "reply_kind": "timeout", "raw_position": "",
                        "unwrapped_position": unwrapped, "delta_counts": "",
                        "instant_cps": "", "window_slope_cps": "",
                        "commanded_payload": f"{args.payload:06X}", "motion_state": "invalid",
                        "valid_position": False, "note": "timeout-no-rx-frame",
                    })
                    if consecutive_bad_samples >= 3:
                        raise RunAbort(f"three consecutive timeout/malformed {args.axis.upper()} samples")
                    continue
                raw = int.from_bytes(frame, "big")
                if not is_valid_raw_position(raw):
                    consecutive_bad_samples += 1
                    bad_samples_total += 1
                    rows.append({
                        "timestamp": received, "elapsed_s": elapsed,
                        "rx_bytes_hex": frame.hex().upper(), "reply_kind": "malformed-outside-modulus",
                        "raw_position": raw, "unwrapped_position": unwrapped, "delta_counts": "",
                        "instant_cps": "", "window_slope_cps": "",
                        "commanded_payload": f"{args.payload:06X}", "motion_state": "invalid",
                        "valid_position": False, "note": "rejected-malformed-raw-outside-modulus",
                    })
                    if consecutive_bad_samples >= 3:
                        raise RunAbort(f"three consecutive timeout/malformed {args.axis.upper()} samples")
                    continue
                delta = signed_delta(previous_raw, raw)
                interval = received - previous_time
                plausible_limit = max(MIN_PLAUSIBLE_DELTA_COUNTS, MAX_PLAUSIBLE_COUNTS_PER_SECOND * interval)
                valid = abs(delta) <= plausible_limit
                if not valid:
                    consecutive_bad_samples += 1
                    bad_samples_total += 1
                    rows.append({
                        "timestamp": received, "elapsed_s": elapsed,
                        "rx_bytes_hex": frame.hex().upper(), "reply_kind": "implausible-jump", "raw_position": raw,
                        "unwrapped_position": unwrapped, "delta_counts": delta,
                        "instant_cps": "", "window_slope_cps": "",
                        "commanded_payload": f"{args.payload:06X}", "motion_state": "invalid",
                        "valid_position": False, "note": "rejected-implausible-position-jump",
                    })
                    if consecutive_bad_samples >= 3:
                        raise RunAbort(f"three consecutive timeout/malformed {args.axis.upper()} samples")
                    continue
                consecutive_bad_samples = 0
                unwrapped += delta
                delta_from_start = signed_delta(start_raw, raw)
                if delta_from_start * expected_sign <= -BREAKAWAY_COUNTS:
                    rows.append({
                        "timestamp": received, "elapsed_s": elapsed,
                        "rx_bytes_hex": frame.hex().upper(), "reply_kind": "valid-opposite-direction",
                        "raw_position": raw, "unwrapped_position": unwrapped, "delta_counts": delta,
                        "instant_cps": delta / interval, "window_slope_cps": "",
                        "commanded_payload": f"{args.payload:06X}", "motion_state": "opposite-direction",
                        "valid_position": True, "note": "unexpected-opposite-direction-motion",
                    })
                    raise RunAbort(f"unexpected opposite-direction {args.axis.upper()} movement: {delta_from_start} counts")
                if breakaway_elapsed is None and delta_from_start * expected_sign >= BREAKAWAY_COUNTS:
                    breakaway_elapsed = elapsed
                state = "waiting-for-breakaway" if breakaway_elapsed is None else "moving-transient"
                instant = delta / interval
                rows.append({
                    "timestamp": received, "elapsed_s": elapsed,
                    "rx_bytes_hex": frame.hex().upper(), "reply_kind": "valid", "raw_position": raw,
                    "unwrapped_position": unwrapped, "delta_counts": delta,
                    "instant_cps": instant, "window_slope_cps": "",
                    "commanded_payload": f"{args.payload:06X}", "motion_state": state,
                    "valid_position": True, "note": "",
                })
                previous_raw, previous_time = raw, received
                last_before_stop = raw

            summary["run_status"] = "completed"
        except (RunAbort, TimeoutError, serial.SerialException) as error:
            summary.update({"run_status": "aborted", "abort_reason": str(error)})
            print(f"ABORT: {error}")
        except KeyboardInterrupt:
            summary.update({"run_status": "aborted", "abort_reason": "KeyboardInterrupt"})
            print("ABORT: KeyboardInterrupt")
        finally:
            summary["bad_samples_total"] = bad_samples_total
            # A STOP is unconditional after a command may have been attempted.
            try:
                mount.stop(args.axis, args.direction)
            except Exception as stop_error:
                summary["stop_error"] = str(stop_error)
                print(f"EMERGENCY STOP WRITE FAILED: {stop_error}")

        if summary["run_status"] == "completed":
            time.sleep(args.post_stop_seconds)
            try:
                after_stop_frame, after_stop = read_one_valid_position(mount, args.axis)
            except (TimeoutError, RunAbort) as error:
                summary.update({"run_status": "aborted", "abort_reason": f"{args.axis.upper()} position timeout after STOP"})
                print(f"ABORT: {error}")
            else:
                summary.update({
                    "last_before_stop_raw": last_before_stop,
                    "after_stop_raw": after_stop,
                    "after_stop_rx_bytes_hex": after_stop_frame.hex().upper(),
                    "post_stop_coast_counts": signed_delta(last_before_stop, after_stop),
                    "post_stop_coast_degrees": signed_delta(last_before_stop, after_stop) * 360.0 / COUNTS_PER_REV,
                    "breakaway_latency_s": "" if breakaway_elapsed is None else breakaway_elapsed,
                    "starts_from_rest": "yes" if breakaway_elapsed is not None else "no",
                })

    annotate_velocities(rows)
    for row in rows:
        row["load_context"] = args.load_context
    if summary["run_status"] == "completed":
        plateau = plateau_analysis(rows, breakaway_elapsed)
        summary.update(plateau)
        if plateau["steady_state"] == "reached":
            final_valid_time = max(row["elapsed_s"] for row in rows if row["valid_position"])
            for row in rows:
                if row["valid_position"] and row["elapsed_s"] >= final_valid_time - 3.0:
                    if row["motion_state"] == "moving-transient":
                        row["motion_state"] = "steady-state-tail"
    else:
        summary.setdefault("steady_state", "not_reached")
    write_csv(raw_output, rows)
    with summary_output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=sorted(summary))
        writer.writeheader()
        writer.writerow(summary)
    print("Saved:", raw_output, summary_output)
    if summary["run_status"] != "completed":
        raise SystemExit(2)
    print(
        f"RESULT starts_from_rest={summary.get('starts_from_rest')} "
        f"breakaway_latency_s={summary.get('breakaway_latency_s')} "
        f"steady_state={summary.get('steady_state')} "
        f"coast_counts={summary.get('post_stop_coast_counts')}"
    )


if __name__ == "__main__":
    main()
