"""Summarize the fixed-v1 +/-5 degree NXW436 repeatability dataset.

Reads summary CSV files only; it never changes raw/transition datasets or sends
serial commands.  By default it requires acceptance sample 0 plus r1..r3 for
each AZ/ALT direction.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

from nxw436_driver import signed_delta

CASES = (("az", "+"), ("az", "-"), ("alt", "+"), ("alt", "-"))


def label_for(axis: str, direction: str, repeat: int) -> str:
    word = "plus" if direction == "+" else "minus"
    return f"goto-{axis}-{word}-5-v1" if repeat == 0 else f"goto-repeat-{axis}-{word}-5-r{repeat}"


def signfix_label_for(direction: str, repeat: int) -> str:
    word = "plus" if direction == "+" else "minus"
    return f"goto-az-{word}-5-recovery-signfix-r{repeat}"


def read_summary(label: str) -> dict:
    path = Path(f"nxw436_goto_relative_{label}_summary.csv")
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as file:
        row = next(csv.DictReader(file))
    # Sample 0 predates explicit absolute-error/coast columns; derive them
    # from already preserved stop/final raw positions without touching its CSV.
    if row.get("run_status") == "completed":
        if not row.get("final_abs_error_counts"):
            row["final_abs_error_counts"] = str(abs(float(row["final_error_counts"])))
        if not row.get("settled_coast_counts"):
            row["settled_coast_counts"] = str(signed_delta(int(row["stop_raw"]), int(row["final_raw"])))
    return row | {"label": label, "source_file": str(path)}


def values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows]


def describe(rows: list[dict], axis: str, direction: str, aborted_count: int) -> dict:
    signed = values(rows, "final_error_counts")
    absolute = values(rows, "final_abs_error_counts")
    stop = values(rows, "error_at_stop_counts")
    coast = values(rows, "settled_coast_counts")
    time_s = values(rows, "motion_time_to_stop_s")
    sd = lambda data: statistics.stdev(data) if len(data) > 1 else 0.0
    return {
        "axis": axis, "direction": direction, "completed_n": len(rows), "aborted_n": aborted_count,
        "mean_signed_settled_error_counts": statistics.mean(signed),
        "median_signed_settled_error_counts": statistics.median(signed),
        "min_signed_settled_error_counts": min(signed), "max_signed_settled_error_counts": max(signed),
        "settled_error_stddev_counts": sd(signed),
        "mean_absolute_error_counts": statistics.mean(absolute), "worst_absolute_error_counts": max(absolute),
        "mean_position_at_stop_error_counts": statistics.mean(stop),
        "mean_settled_coast_counts": statistics.mean(coast), "settled_coast_stddev_counts": sd(coast),
        "mean_motion_time_s": statistics.mean(time_s), "motion_time_stddev_s": sd(time_s),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--exclude-sample0", action="store_true")
    parser.add_argument(
        "--signfix-az",
        action="store_true",
        help="summarize the post-sign-fix AZ r1..r5 dataset only",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace only existing aggregate outputs from this analysis tool")
    args = parser.parse_args()
    if args.signfix_az and args.exclude_sample0:
        parser.error("--exclude-sample0 is only valid for the v1 dataset")
    if args.signfix_az:
        cases = (("az", "+"), ("az", "-"))
        repeats = (1, 2, 3, 4, 5)
        label_builder = lambda axis, direction, repeat: signfix_label_for(direction, repeat)
        default_output = "nxw436_repeatability_az_signfix_summary.csv"
    else:
        cases = CASES
        repeats = (1, 2, 3) if args.exclude_sample0 else (0, 1, 2, 3)
        label_builder = label_for
        default_output = "nxw436_repeatability_v1_summary.csv"
    output = Path(args.output or default_output)
    grouped: dict[tuple[str, str], list[dict]] = {}
    aborted_rows: list[dict] = []
    for axis, direction in cases:
        all_rows = [read_summary(label_builder(axis, direction, repeat)) for repeat in repeats]
        for row in all_rows:
            row["expected_axis"] = axis
            row["expected_direction"] = direction
        grouped[(axis, direction)] = [row for row in all_rows if row.get("run_status") == "completed"]
        aborted_rows.extend(row for row in all_rows if row.get("run_status") != "completed")
    if any(not rows for rows in grouped.values()):
        parser.error("at least one case has no completed samples")
    summary_rows = [describe(rows, axis, direction, sum(1 for row in aborted_rows if row["expected_axis"] == axis and row["expected_direction"] == direction)) for (axis, direction), rows in grouped.items()]
    comparisons = []
    for axis in sorted({axis for axis, _ in cases}):
        plus = next(row for row in summary_rows if row["axis"] == axis and row["direction"] == "+")
        minus = next(row for row in summary_rows if row["axis"] == axis and row["direction"] == "-")
        comparisons.append({
            "axis": axis,
            "plus_minus_mean_signed_error_difference_counts": plus["mean_signed_settled_error_counts"] - minus["mean_signed_settled_error_counts"],
            "plus_minus_mean_absolute_error_difference_counts": plus["mean_absolute_error_counts"] - minus["mean_absolute_error_counts"],
            "plus_minus_mean_coast_difference_counts": plus["mean_settled_coast_counts"] - minus["mean_settled_coast_counts"],
            "plus_minus_mean_motion_time_difference_s": plus["mean_motion_time_s"] - minus["mean_motion_time_s"],
        })
    comparison = output.with_name(output.stem + "_directional.csv")
    aborted_output = output.with_name(output.stem + "_aborted.csv")
    existing = [path for path in (output, comparison, aborted_output) if path.exists()]
    if existing and not args.overwrite:
        parser.error(f"aggregate output exists ({', '.join(str(path) for path in existing)}); choose another --output or pass --overwrite")
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary_rows[0])); writer.writeheader(); writer.writerows(summary_rows)
    with comparison.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(comparisons[0])); writer.writeheader(); writer.writerows(comparisons)
    with aborted_output.open("w", newline="", encoding="utf-8") as file:
        fields = ("label", "expected_axis", "expected_direction", "requested_degrees", "run_status", "abort_reason", "source_file")
        writer = csv.DictWriter(file, fieldnames=fields); writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in aborted_rows)
    print("Saved:", output, comparison, aborted_output)
    for row in summary_rows:
        print(f"{row['axis'].upper()}{row['direction']} completed={row['completed_n']} aborted={row['aborted_n']} mean_error={row['mean_signed_settled_error_counts']:.2f} stddev={row['settled_error_stddev_counts']:.2f} worst_abs={row['worst_absolute_error_counts']:.2f}")


if __name__ == "__main__":
    main()
