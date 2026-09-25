"""Short, start-from-rest ALT breakaway diagnostic for NXW436.

This is intentionally not a speed calibration.  Each payload starts from a
double STOP, receives no preload, and is followed by a double STOP in finally.
"""

import argparse
import csv
import time
from pathlib import Path

from nxw436_driver import COUNTS_PER_REV, NXW436, is_valid_raw_position, signed_delta


PAYLOADS = (0x0000F5, 0x003978, 0x0072F1)


def payload_bytes(value: int) -> bytes:
    return value.to_bytes(3, "big")


def parse_payloads(text: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip(), 16) for item in text.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--payloads must be comma-separated hex values") from error
    if not values or any(value <= 0 or value > 0x00E5E3 for value in values):
        raise argparse.ArgumentTypeError("each payload must be 000001..00E5E3")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("+", "-"))
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--sample-seconds", type=float, default=0.15)
    parser.add_argument("--payloads", type=parse_payloads, default=PAYLOADS)
    parser.add_argument("--run-label", default="alt-gen2-breakaway")
    args = parser.parse_args()
    if not 1.0 <= args.seconds <= 5.0:
        parser.error("--seconds must be 1.0..5.0")
    if not 0.03 <= args.sample_seconds <= 0.5:
        parser.error("--sample-seconds must be 0.03..0.5")
    if not args.run_label.replace("-", "").replace("_", "").isalnum():
        parser.error("--run-label may contain only letters, digits, '-' and '_'")

    output = Path(f"nxw436_alt_breakaway_{args.run_label}.csv")
    print("ALT only. Every payload starts from complete STOP; no preload is used.")
    print("Payloads:", " ".join(f"{value:06X}" for value in args.payloads))
    print("Ctrl+C, timeout, exception, and normal completion send STOP twice.")
    input("Confirm tube clearance and press Enter to begin... ")

    rows = []
    samples_output = Path(f"nxw436_alt_breakaway_{args.run_label}_samples.csv")
    sample_rows = []
    with NXW436(args.port) as mount:
        for sequence, payload in enumerate(args.payloads, start=1):
            try:
                mount.stop("alt", args.direction)
                time.sleep(0.25)
                start_raw = mount.get_position("alt")
                previous_raw = start_raw
                deadline = time.perf_counter() + args.seconds
                sample_started = time.perf_counter()
                mount.move("alt", args.direction, payload_bytes(payload))
                samples = 0
                timeouts = 0
                while True:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    time.sleep(min(args.sample_seconds, remaining))
                    if time.perf_counter() > deadline:
                        break
                    try:
                        # Single-shot polls were empirically too brittle after a
                        # move command on this 4800-baud link.  Use the driver's
                        # established bounded retry policy; a failed whole poll
                        # is still counted below and ultimately stops the axis.
                        raw = mount.get_position("alt")
                    except TimeoutError:
                        timeouts += 1
                        if timeouts >= 3:
                            raise RuntimeError("three consecutive position timeouts")
                        continue
                    timeouts = 0
                    if time.perf_counter() > deadline:
                        # Do not let a delayed retry become a post-window sample.
                        break
                    if not is_valid_raw_position(raw):
                        raise RuntimeError(f"malformed ALT raw position outside modulus: {raw}")
                    delta = signed_delta(previous_raw, raw)
                    if abs(delta) > COUNTS_PER_REV // 4:
                        raise RuntimeError(f"impossible position jump: {delta}")
                    previous_raw = raw
                    samples += 1
                    sample_rows.append({
                        "sequence": sequence,
                        "payload_hex": f"{payload:06X}",
                        "elapsed_s": time.perf_counter() - sample_started,
                        "raw_position": raw,
                        "delta_from_previous": delta,
                        "delta_from_start": signed_delta(start_raw, raw),
                    })
                end_raw = previous_raw
                total_delta = signed_delta(start_raw, end_raw)
                expected_sign = 1 if args.direction == "+" else -1
                if abs(total_delta) < 2:
                    outcome = "no-measurable-motion"
                elif total_delta * expected_sign > 0:
                    outcome = "commanded-direction-motion"
                else:
                    outcome = "opposite-direction-motion"
                print(
                    f"{payload:06X}: start={start_raw:06X} end={end_raw:06X} "
                    f"delta={total_delta:+d} samples={samples} outcome={outcome}"
                )
                rows.append({
                    "sequence": sequence,
                    "payload_hex": f"{payload:06X}",
                    "direction": args.direction,
                    "duration_s": args.seconds,
                    "start_raw": start_raw,
                    "end_raw": end_raw,
                    "delta_counts": total_delta,
                    "samples": samples,
                    "outcome": outcome,
                })
            finally:
                # This must be unconditional: a failing baseline query is not
                # evidence that the axis is stopped or that a previous command
                # was accepted as STOP.
                try:
                    mount.stop("alt", args.direction)
                except Exception as stop_error:
                    print(f"EMERGENCY STOP WRITE FAILED: {stop_error}")

    with output.open("w", newline="", encoding="utf-8") as file:
        fields = ("sequence", "payload_hex", "direction", "duration_s", "start_raw",
                  "end_raw", "delta_counts", "samples", "outcome")
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with samples_output.open("w", newline="", encoding="utf-8") as file:
        fields = ("sequence", "payload_hex", "elapsed_s", "raw_position",
                  "delta_from_previous", "delta_from_start")
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sample_rows)
    print("Saved:", output, samples_output)


if __name__ == "__main__":
    main()
