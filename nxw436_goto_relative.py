"""Closed-loop relative position move for the experimentally verified NXW436."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

import serial

from nxw436_driver import COUNTS_PER_REV, NXW436, is_valid_raw_position
from nxw436_position_controller import STOP_MARGINS, ControllerAbort, PROFILES, RelativePositionController, degrees_to_counts


def parse_degrees(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("degrees must be a decimal number") from error
    if value == 0 or abs(value) > Decimal("30"):
        raise argparse.ArgumentTypeError("degrees must be non-zero and within -30..30")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--axis", required=True, choices=("az", "alt"))
    parser.add_argument("--degrees", required=True, type=parse_degrees)
    parser.add_argument("--az-stop-margin", type=int, choices=(200, 300), help="explicit AZ-only experimental stop margin")
    parser.add_argument("--az-off-tripod", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=30.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--label", default="")
    parser.add_argument("--dry-run", action="store_true", help="print exact count target/profile, do not open COM port")
    args = parser.parse_args()
    if not 2 <= args.max_seconds <= 60 or not 0.2 <= args.settle_seconds <= 5:
        parser.error("--max-seconds must be 2..60 and --settle-seconds must be 0.2..5")
    if args.axis == "az" and not args.az_off_tripod:
        parser.error("AZ movement is blocked: physically ensure free non-wrapping travel, then pass --az-off-tripod")
    if args.axis != "az" and args.az_stop_margin is not None:
        parser.error("--az-stop-margin applies only to AZ; ALT margin is fixed")
    target = degrees_to_counts(args.degrees)
    stop_margin = STOP_MARGINS[args.axis] if args.az_stop_margin is None else args.az_stop_margin
    if args.label and not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("--label may contain only letters, digits, '-' and '_'")
    profile = ", ".join(f"{stage.name}={stage.payload.hex().upper()}>{stage.minimum_remaining_counts}" for stage in PROFILES[args.axis])
    print(
        f"target_delta_counts={target}; modulus={COUNTS_PER_REV}; "
        f"profile: {profile}; stop_margin_counts={stop_margin}"
    )
    if args.dry_run:
        return
    label = args.label or f"{args.axis}_{'plus' if target > 0 else 'minus'}_{abs(target)}"
    raw_path = Path(f"nxw436_goto_relative_{label}_raw.csv")
    transitions_path = Path(f"nxw436_goto_relative_{label}_transitions.csv")
    summary_path = Path(f"nxw436_goto_relative_{label}_summary.csv")
    if any(path.exists() for path in (raw_path, transitions_path, summary_path)):
        parser.error("output label already exists; choose a unique --label")
    controller: RelativePositionController | None = None
    summary: dict = {
        "requested_degrees": str(args.degrees),
        "target_delta_counts": target,
        "axis": args.axis,
        "stop_margin_counts": stop_margin,
    }
    try:
        with NXW436(args.port) as mount:
            preflight = {}
            for preflight_axis in ("az", "alt"):
                frame = mount.query_position_raw(preflight_axis)
                if frame is None:
                    raise ControllerAbort(f"preflight timeout for {preflight_axis.upper()}")
                raw = int.from_bytes(frame, "big")
                if not is_valid_raw_position(raw):
                    raise ControllerAbort(f"preflight malformed {preflight_axis.upper()} reply: {frame.hex().upper()}")
                preflight[f"preflight_{preflight_axis}_raw"] = raw
                preflight[f"preflight_{preflight_axis}_rx_bytes_hex"] = frame.hex().upper()
            summary.update(preflight)
            print(f"PREFLIGHT AZ={preflight['preflight_az_raw']:06X} ALT={preflight['preflight_alt_raw']:06X}")
            input(f"Confirm {args.axis.upper()} clearance and stable GND, then press Enter to command motion... ")
            controller = RelativePositionController(mount, args.axis, target, stop_margin_counts=args.az_stop_margin)
            summary.update(controller.run(max_seconds=args.max_seconds, settle_seconds=args.settle_seconds))
            summary["run_status"] = "completed"
    except (ControllerAbort, TimeoutError, serial.SerialException, KeyboardInterrupt) as error:
        if controller is not None:
            summary.update(controller.recovery_log_fields())
        summary.update({"run_status": "aborted", "abort_reason": type(error).__name__ + ": " + str(error)})
        print("ABORT:", summary["abort_reason"])
    finally:
        if controller is not None:
            controller.write_logs(raw_path, transitions_path)
    with summary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=sorted(summary)); writer.writeheader(); writer.writerow(summary)
    print("Saved:", raw_path, transitions_path, summary_path)
    if summary.get("run_status") != "completed":
        raise SystemExit(2)
    print(
        f"RESULT final_error_counts={summary['final_error_counts']} "
        f"final_error_degrees={summary['final_error_degrees']:.6f} "
        f"settled_outcome={summary['settled_outcome']}"
    )


if __name__ == "__main__":
    main()
