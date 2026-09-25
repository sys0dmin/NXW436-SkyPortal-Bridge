"""Black-box continuous ALT payload-to-speed characterization for NXW436."""

import argparse
import csv
import statistics
import time
from pathlib import Path

from nxw436_driver import (
    COUNTS_PER_REV, GT114_SPEED_PAYLOADS, NXW436,
    is_valid_raw_position, signed_delta,
)


DEFAULT_GRID = (
    0x00E5E3, 0x00A000, 0x0072F1, 0x005000, 0x003978,
    0x002000, 0x001000, 0x000800, 0x000400, 0x000200, 0x0000F5,
)
MAX_PAYLOAD = 0x00E5E3
DEFAULT_RUN_LABEL = "alt-ramp"
MAX_PLAUSIBLE_COUNTS_PER_SECOND = 10_000
MIN_PLAUSIBLE_DELTA_COUNTS = 1_000


def parse_grid(text: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip(), 16) for item in text.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("grid must be comma-separated hex values") from error
    if not values or len(values) > 16:
        raise argparse.ArgumentTypeError("grid must contain 1..16 payloads")
    if any(value <= 0 or value > MAX_PAYLOAD for value in values):
        raise argparse.ArgumentTypeError("each payload must be 000001..00E5E3")
    return values


def payload_bytes(value: int) -> bytes:
    return value.to_bytes(3, "big")


def tail_trend(step_rows):
    """Return the least-squares position slope of the final third of a step."""
    steady_rows = [
        row for row in step_rows
        if row["steady"] and row.get("valid_position", True)
    ]
    tail = steady_rows[-max(3, len(steady_rows) // 3):]
    if len(tail) < 3:
        return None
    times = [row["elapsed_step_s"] for row in tail]
    positions = [row["unwrapped_position"] for row in tail]
    mean_time = statistics.mean(times)
    mean_position = statistics.mean(positions)
    denominator = sum((value - mean_time) ** 2 for value in times)
    if denominator == 0:
        return None
    return sum(
        (sample_time - mean_time) * (position - mean_position)
        for sample_time, position in zip(times, positions)
    ) / denominator


def summarize(step_rows, direction, minimum_cps):
    steady = [
        row["instant_cps"] for row in step_rows
        if row["steady"] and row.get("valid_position", True)
        and row["instant_cps"] is not None
    ]
    tail = steady[-max(2, len(steady) // 3):]
    if not steady:
        return None
    median = statistics.median(steady)
    tail_median = statistics.median(tail)
    active_fraction = sum(abs(value) >= 1.0 for value in tail) / len(tail)
    slope = tail_trend(step_rows)
    if abs(tail_median) < minimum_cps:
        classification = "stalled"
    elif active_fraction < 0.75:
        classification = "intermittent"
    elif slope is None:
        classification = "insufficient-tail-data"
    else:
        classification = "sustained"
    if slope is None or classification in ("stalled", "intermittent"):
        trend = "unknown"
    elif abs(slope - tail_median) <= max(minimum_cps, abs(tail_median) * 0.15):
        trend = "steady"
    elif abs(slope) > abs(median) * 1.25:
        trend = "accelerating"
    elif abs(slope) < abs(median) * 0.75:
        trend = "decelerating"
    else:
        trend = "transitioning"
    return {
        "payload_hex": step_rows[0]["payload_hex"],
        "payload_int": step_rows[0]["payload_int"],
        "direction_of_sweep": direction,
        "mean_counts_s": statistics.mean(steady),
        "median_counts_s": median,
        "std_counts_s": statistics.stdev(steady) if len(steady) > 1 else 0.0,
        "tail_median_counts_s": tail_median,
        "tail_position_slope_counts_s": slope,
        "motion_trend": trend,
        "deg_s": median * 360.0 / COUNTS_PER_REV,
        "arcsec_s": median * 1296000.0 / COUNTS_PER_REV,
        "tail_active_fraction": active_fraction,
        "sustained_motion": classification == "sustained",
        "classification": classification,
        "steady_samples": len(steady),
    }


def write_svg(rows, svg_path):
    valid = [row for row in rows if row is not None]
    if not valid:
        return
    width, height, margin = 860, 480, 70
    payloads = [row["payload_int"] for row in valid]
    speeds = [abs(row["median_counts_s"]) for row in valid]
    x_min, x_max = min(payloads), max(payloads)
    y_max = max(max(speeds), 1.0)

    def x(value):
        return margin + (value - x_min) * (width - 2 * margin) / max(x_max - x_min, 1)

    def y(value):
        return height - margin - value * (height - 2 * margin) / y_max

    points = " ".join(f"{x(row['payload_int']):.1f},{y(abs(row['median_counts_s'])):.1f}" for row in valid)
    labels = "".join(
        f'<text x="{x(row["payload_int"]):.1f}" y="{height - 38}" font-size="10" text-anchor="middle">{row["payload_hex"]}</text>'
        for row in valid
    )
    svg_path.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
<rect width="100%" height="100%" fill="white"/>
<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="black"/>
<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="black"/>
<text x="{width/2}" y="24" text-anchor="middle">NXW436 ALT steady speed ramp</text>
<text x="{width/2}" y="{height-12}" text-anchor="middle">payload (24-bit BE)</text>
<text x="18" y="{height/2}" transform="rotate(-90 18 {height/2})" text-anchor="middle">absolute median counts/s</text>
<polyline fill="none" stroke="#1261a0" stroke-width="2" points="{points}"/>
{labels}
</svg>''',
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("direction", choices=("+", "-"))
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--sweep", choices=("high-to-low", "low-to-high"), default="high-to-low")
    parser.add_argument("--grid", type=parse_grid)
    parser.add_argument("--step-seconds", type=float, default=3.0)
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    parser.add_argument("--sample-seconds", type=float, default=0.15)
    parser.add_argument("--warmup-seconds", type=float, default=3.0)
    parser.add_argument("--minimum-cps", type=float, default=1.0)
    parser.add_argument(
        "--run-label",
        default=DEFAULT_RUN_LABEL,
        help="filename label; never reuse a label for a different mechanical baseline",
    )
    args = parser.parse_args()
    if not 2.0 <= args.step_seconds <= 3.0:
        parser.error("--step-seconds must be 2.0..3.0")
    if not 0.0 <= args.settle_seconds < args.step_seconds:
        parser.error("--settle-seconds must be >=0 and less than step duration")
    if not 0.03 <= args.sample_seconds <= 0.5:
        parser.error("--sample-seconds must be 0.03..0.5")
    if not 0.0 <= args.warmup_seconds <= 10.0:
        parser.error("--warmup-seconds must be 0..10")
    if not args.run_label.replace("-", "").replace("_", "").isalnum():
        parser.error("--run-label may contain only letters, digits, '-' and '_'")

    raw_csv = Path(f"nxw436_speed_ramp_{args.run_label}_raw.csv")
    summary_csv = Path(f"nxw436_speed_ramp_{args.run_label}_summary.csv")
    svg = Path(f"nxw436_speed_ramp_{args.run_label}.svg")

    grid = args.grid or DEFAULT_GRID
    if args.sweep == "low-to-high":
        grid = tuple(reversed(grid))
    preload_seconds = 0.5 if args.sweep == "low-to-high" else args.warmup_seconds
    total_seconds = len(grid) * args.step_seconds + preload_seconds
    if total_seconds > 60:
        parser.error("total commanded duration exceeds 60 seconds")

    print("ALT only. No STOP occurs between payload steps.")
    print("Grid:", " ".join(f"{value:06X}" for value in grid))
    print(f"Duration: {total_seconds:.1f} s plus serial overhead.")
    print("Ctrl+C, timeout, malformed response, or impossible jump sends STOP twice.")
    print("Output label:", args.run_label)
    input("Enter to begin continuous ramp...")

    raw_rows = []
    summary_rows = []
    consecutive_timeouts = 0
    unwrapped = None
    previous_raw = None
    previous_time = None
    running = False

    with NXW436(args.port) as mount:
        try:
            if args.sweep == "high-to-low":
                mount.move("alt", args.direction, payload_bytes(max(grid)))
                running = True
                time.sleep(args.warmup_seconds)
            else:
                mount.move("alt", args.direction, GT114_SPEED_PAYLOADS[8])
                running = True
                time.sleep(preload_seconds)

            for step, payload in enumerate(grid, start=1):
                mount.move("alt", args.direction, payload_bytes(payload))
                running = True
                step_started = time.perf_counter()
                step_rows = []
                first_sample_in_step = True
                step_deadline = step_started + args.step_seconds
                while time.perf_counter() < step_deadline:
                    time.sleep(min(args.sample_seconds, step_deadline - time.perf_counter()))
                    now = time.perf_counter()
                    if now > step_deadline:
                        break
                    try:
                        raw = mount.get_position("alt", attempts=1)
                    except TimeoutError:
                        consecutive_timeouts += 1
                        if consecutive_timeouts >= 3:
                            raise RuntimeError("three consecutive position timeouts")
                        continue
                    consecutive_timeouts = 0
                    if not is_valid_raw_position(raw):
                        raw_rows.append({
                            "timestamp": now, "step": step, "payload_hex": f"{payload:06X}",
                            "payload_int": payload, "raw_position": raw,
                            "unwrapped_position": unwrapped, "direction": args.direction,
                            "elapsed_step_s": now - step_started, "instant_cps": None,
                            "steady": now - step_started >= args.settle_seconds,
                            "valid_position": False,
                            "note": "rejected-malformed-raw-outside-modulus",
                        })
                        raise RuntimeError(f"malformed ALT raw position outside modulus: {raw}")
                    if previous_raw is None:
                        delta = 0
                        unwrapped = raw
                        instant_cps = None
                    else:
                        delta = signed_delta(previous_raw, raw)
                        elapsed_since_previous = now - previous_time
                        maximum_delta = max(
                            MIN_PLAUSIBLE_DELTA_COUNTS,
                            MAX_PLAUSIBLE_COUNTS_PER_SECOND * elapsed_since_previous,
                        )
                        if abs(delta) > maximum_delta:
                            valid_position = False
                            note = "rejected-implausible-position-jump"
                            instant_cps = None
                        else:
                            valid_position = True
                            note = ""
                            unwrapped += delta
                            instant_cps = None if first_sample_in_step else delta / elapsed_since_previous
                    if previous_raw is None:
                        valid_position = True
                        note = ""
                    elapsed_step = now - step_started
                    row = {
                        "timestamp": now,
                        "step": step,
                        "payload_hex": f"{payload:06X}",
                        "payload_int": payload,
                        "raw_position": raw,
                        "unwrapped_position": unwrapped,
                        "direction": args.direction,
                        "elapsed_step_s": elapsed_step,
                        "instant_cps": instant_cps,
                        "steady": elapsed_step >= args.settle_seconds,
                        "valid_position": valid_position,
                        "note": note,
                    }
                    raw_rows.append(row)
                    step_rows.append(row)
                    if valid_position:
                        previous_raw, previous_time = raw, now
                        first_sample_in_step = False
                result = summarize(step_rows, args.sweep, args.minimum_cps)
                if result is not None:
                    summary_rows.append(result)
                    slope_text = (
                        f"{result['tail_position_slope_counts_s']:.3f}"
                        if result["tail_position_slope_counts_s"] is not None else "n/a"
                    )
                    print(
                        f"step {step:02d} {payload:06X}: "
                        f"median={result['median_counts_s']:.3f} cps, "
                        f"tail={result['tail_median_counts_s']:.3f}, "
                        f"slope={slope_text}, "
                        f"class={result['classification']}, trend={result['motion_trend']}"
                    )
        finally:
            if running:
                mount.stop("alt", args.direction)

    with raw_csv.open("w", newline="", encoding="utf-8") as file:
        fields = ("timestamp", "step", "payload_hex", "payload_int", "raw_position",
                  "unwrapped_position", "direction", "elapsed_step_s", "instant_cps", "steady",
                  "valid_position", "note")
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(raw_rows)
    with summary_csv.open("w", newline="", encoding="utf-8") as file:
        fields = ("payload_hex", "payload_int", "direction_of_sweep", "mean_counts_s",
                  "median_counts_s", "std_counts_s", "tail_median_counts_s",
                  "tail_position_slope_counts_s", "motion_trend", "deg_s",
                  "arcsec_s", "tail_active_fraction", "sustained_motion",
                  "classification", "steady_samples")
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    write_svg(summary_rows, svg)
    print("Saved:", raw_csv, summary_csv, svg)


if __name__ == "__main__":
    main()
