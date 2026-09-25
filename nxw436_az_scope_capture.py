"""AZ+ FAST->MEDIUM scope capture with delayed same-MEDIUM resend on stall."""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import serial

from nxw436_az_transition_cycles import (
    FAST,
    MEDIUM,
    MAX_BAD,
    MAX_CPS,
    MIN_PROGRESS,
    SAMPLE_S,
)
from nxw436_driver import NXW436, is_valid_raw_position, signed_delta
from nxw436_progress_detector import evaluate_no_progress


FAST_S = 2.0
MEDIUM_S = 8.0
STALL_HOLD_S = 2.0
RECOVERY_S = 2.0


class Abort(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--az-off-tripod", action="store_true")
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--label", required=True)
    parser.add_argument("--observation", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.az_off_tripod:
        parser.error("AZ movement blocked: pass --az-off-tripod only after securing free non-wrapping travel")
    if not 1 <= args.cycles <= 20:
        parser.error("--cycles must be 1..20")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("invalid --label")

    print(
        f"AZ+ scope capture: cycles={args.cycles}; FAST={FAST.hex().upper()} {FAST_S}s; "
        f"MEDIUM={MEDIUM.hex().upper()} {MEDIUM_S}s; stall-hold={STALL_HOLD_S}s; "
        f"post-resend={RECOVERY_S}s"
    )
    if args.dry_run:
        return

    raw_path = Path(f"nxw436_az_scope_capture_{args.label}_raw.csv")
    event_path = Path(f"nxw436_az_scope_capture_{args.label}_events.csv")
    summary_path = Path(f"nxw436_az_scope_capture_{args.label}_summary.csv")
    if any(path.exists() for path in (raw_path, event_path, summary_path)):
        parser.error("label already exists")

    raw_rows: list[dict] = []
    events: list[dict] = []
    summaries: list[dict] = []
    started = time.perf_counter()
    commanded = False
    cycle_index = 0
    cycle_summary_recorded = False

    def event(cycle: int, name: str, command: bytes = b"", note: str = "") -> None:
        now = time.perf_counter()
        events.append(
            {
                "cycle_index": cycle,
                "monotonic_s": now,
                "elapsed_s": now - started,
                "event": name,
                "command_hex": command.hex().upper(),
                "note": note,
            }
        )

    def move(mount: NXW436, payload: bytes, cycle: int, event_name: str) -> None:
        command = bytes([0x06]) + payload
        event(cycle, event_name, command)
        mount.move("az", "+", payload)

    def stop_twice(mount: NXW436, cycle: int, pre: bool = False) -> None:
        move(mount, b"\0\0\0", cycle, "PRE_STOP_1" if pre else "STOP_1")
        time.sleep(0.05)
        move(mount, b"\0\0\0", cycle, "PRE_STOP_2" if pre else "STOP_2")

    def read_az(mount: NXW436):
        frame = mount.query_position_raw("az")
        if frame is None:
            return frame, None, "timeout"
        raw = int.from_bytes(frame, "big")
        return frame, raw, "valid" if is_valid_raw_position(raw) else "malformed-outside-modulus"

    with NXW436(args.port) as mount:
        try:
            for axis in ("az", "alt"):
                frame = mount.query_position_raw(axis)
                if frame is None or not is_valid_raw_position(int.from_bytes(frame, "big")):
                    raise Abort(f"preflight {axis.upper()} invalid")

            input("Start scope capture now. Confirm AZ free travel and stable GND, then press Enter... ")

            for cycle_index in range(1, args.cycles + 1):
                cycle_summary_recorded = False
                start_raw = ""
                previous_raw = ""
                unwrapped = 0
                fast_progress = 0
                medium_progress = 0
                invalid = jumps = timeouts = 0
                no_progress = resend_attempted = resend_recovered = False
                stall_raw = ""
                stall_unwrapped = ""

                event(cycle_index, "CYCLE_START")
                stop_twice(mount, cycle_index, pre=True)
                time.sleep(0.25)
                frame, start_raw, kind = read_az(mount)
                if kind != "valid":
                    raise Abort(f"cycle {cycle_index} initial {kind}")

                previous_raw = start_raw
                previous_time = time.perf_counter()
                unwrapped = start_raw
                cycle_started = previous_time
                bad = 0

                def sample(phase: str, payload: bytes):
                    nonlocal previous_raw, previous_time, unwrapped, bad, invalid, jumps, timeouts
                    now = time.perf_counter()
                    frame, current_raw, kind = read_az(mount)
                    delta = ""
                    instant_cps = ""
                    valid = False
                    if kind == "valid":
                        delta = signed_delta(previous_raw, current_raw)
                        limit = max(1000, MAX_CPS * max(now - previous_time, 0.001))
                        if abs(delta) > limit:
                            kind = "implausible-jump"
                            jumps += 1
                        else:
                            valid = True
                            bad = 0
                            instant_cps = delta / max(now - previous_time, 0.001)
                            unwrapped += delta
                            previous_raw = current_raw
                            previous_time = now
                    if not valid:
                        bad += 1
                        invalid += 1
                        timeouts += kind == "timeout"
                    raw_rows.append(
                        {
                            "cycle_index": cycle_index,
                            "monotonic_s": now,
                            "elapsed_s": now - started,
                            "cycle_elapsed_s": now - cycle_started,
                            "phase": phase,
                            "direction": "+",
                            "payload_hex": payload.hex().upper(),
                            "rx_bytes_hex": "" if frame is None else frame.hex().upper(),
                            "raw_position": "" if current_raw is None else current_raw,
                            "valid": valid,
                            "invalid_reason": "" if valid else kind,
                            "unwrapped_position": unwrapped,
                            "delta_counts": delta,
                            "cumulative_progress": unwrapped - start_raw,
                            "instant_cps": instant_cps,
                        }
                    )
                    if not valid and bad > MAX_BAD:
                        raise Abort(f"cycle {cycle_index} three consecutive invalid replies")
                    return valid, now

                def observe(phase: str, payload: bytes, seconds: float, detect: bool = False) -> bool:
                    history = []
                    end = time.perf_counter() + seconds
                    while time.perf_counter() < end:
                        time.sleep(SAMPLE_S)
                        valid, sample_time = sample(phase, payload)
                        if not valid:
                            continue
                        history.append((sample_time, unwrapped))
                        evidence = evaluate_no_progress(
                            history,
                            window_s=1.5,
                            min_progress_counts=MIN_PROGRESS,
                            nominal_sample_interval=SAMPLE_S,
                        ) if detect else None
                        if evidence is not None and evidence.confirmed:
                            event(
                                cycle_index,
                                "NO_PROGRESS_CONFIRMED",
                                note=(
                                    f"raw={previous_raw}; unwrapped={unwrapped}; "
                                    f"progress={evidence.commanded_progress}; span_s={evidence.span_s:.3f}"
                                ),
                            )
                            return True
                    return False

                move(mount, FAST, cycle_index, "TX_FAST")
                commanded = True
                observe("FAST", FAST, FAST_S)
                fast_progress = unwrapped - start_raw
                if fast_progress < MIN_PROGRESS:
                    raise Abort(f"cycle {cycle_index} FAST progress not confirmed")
                event(cycle_index, "FAST_PROGRESS_CONFIRMED", note=str(fast_progress))

                move(mount, MEDIUM, cycle_index, "TX_MEDIUM")
                print(f">>> CYCLE {cycle_index}: MEDIUM 0072F1 ACTIVE <<<")
                medium_start = unwrapped
                no_progress = observe("MEDIUM", MEDIUM, MEDIUM_S, detect=True)
                medium_progress = unwrapped - medium_start
                outcome = "normal-medium-complete"

                if no_progress:
                    stall_raw = previous_raw
                    stall_unwrapped = unwrapped
                    event(cycle_index, "STALL_CAPTURE_WINDOW_START", note=f"raw={stall_raw}; unwrapped={stall_unwrapped}")
                    print(">>> STALL CAPTURE WINDOW: MEDIUM unchanged for 2.0 s; capture motor waveform now <<<")
                    observe("STALL_HOLD", MEDIUM, STALL_HOLD_S)
                    event(cycle_index, "STALL_CAPTURE_WINDOW_END")

                    resend_attempted = True
                    move(mount, MEDIUM, cycle_index, "TX_MEDIUM_RESEND")
                    event(cycle_index, "POST_RESEND_CAPTURE_WINDOW_START", note=f"raw_before={previous_raw}")
                    print(">>> POST-RESEND CAPTURE WINDOW: same MEDIUM resent; capture recovery waveform now <<<")
                    before_resend = unwrapped
                    observe("MEDIUM_RESEND", MEDIUM, RECOVERY_S)
                    resend_recovered = unwrapped - before_resend >= MIN_PROGRESS
                    event(
                        cycle_index,
                        "POST_RESEND_CAPTURE_WINDOW_END",
                        note=f"recovery_progress={unwrapped - before_resend}",
                    )
                    outcome = "recovered-by-medium-resend" if resend_recovered else "resend-recovery-failed"

                stop_twice(mount, cycle_index)
                commanded = False
                event(cycle_index, "CYCLE_END", note=outcome)
                summaries.append(
                    {
                        "cycle_index": cycle_index,
                        "direction": "+",
                        "start_raw": start_raw,
                        "end_raw": previous_raw,
                        "fast_progress": fast_progress,
                        "medium_progress": medium_progress,
                        "no_progress_detected": no_progress,
                        "stall_raw": stall_raw,
                        "stall_unwrapped": stall_unwrapped,
                        "resend_attempted": resend_attempted,
                        "resend_recovered": resend_recovered,
                        "invalid_samples_total": invalid,
                        "implausible_jump_count": jumps,
                        "timeout_count": timeouts,
                        "run_status": "completed",
                        "final_outcome": outcome,
                        "operator_observation": args.observation,
                    }
                )
                cycle_summary_recorded = True
                if no_progress:
                    break

            series_status = "completed"
        except (Abort, serial.SerialException, KeyboardInterrupt) as error:
            series_status = "aborted"
            event(cycle_index, "SERIES_ABORT", note=f"{type(error).__name__}: {error}")
            print("ABORT:", type(error).__name__, error)
            if cycle_index and not cycle_summary_recorded:
                summaries.append(
                    {
                        "cycle_index": cycle_index,
                        "direction": "+",
                        "start_raw": start_raw,
                        "end_raw": previous_raw,
                        "fast_progress": fast_progress,
                        "medium_progress": medium_progress,
                        "no_progress_detected": no_progress,
                        "stall_raw": stall_raw,
                        "stall_unwrapped": stall_unwrapped,
                        "resend_attempted": resend_attempted,
                        "resend_recovered": resend_recovered,
                        "invalid_samples_total": invalid,
                        "implausible_jump_count": jumps,
                        "timeout_count": timeouts,
                        "run_status": "aborted",
                        "final_outcome": type(error).__name__.lower(),
                        "operator_observation": args.observation,
                    }
                )
        finally:
            if commanded:
                try:
                    stop_twice(mount, cycle_index)
                except Exception as error:
                    event(cycle_index, "STOP_ERROR", note=str(error))

    for path, rows in ((raw_path, raw_rows), (event_path, events), (summary_path, summaries)):
        fieldnames = sorted({key for row in rows for key in row}) or ["empty"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    print("Saved:", raw_path, event_path, summary_path)
    if series_status != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
