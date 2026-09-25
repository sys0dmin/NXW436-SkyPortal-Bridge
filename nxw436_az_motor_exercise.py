"""Bounded free-running AZ motor exercise; not a position-control experiment.

Use only with the AZ motor mechanically disengaged from the mount gear train.
The fixed medium payload is the project-established ``0072F1``.  Each leg is
time bounded and always ends in the driver's mandatory double STOP.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from nxw436_driver import NXW436
from nxw436_low_speed_characterize import valid_label


MEDIUM_PAYLOAD = bytes.fromhex("0072F1")
MAX_SECONDS_PER_DIRECTION = 120.0


def output_path(label: str) -> Path:
    return Path(f"nxw436_az_motor_exercise_{label}_events.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--az-off-tripod", action="store_true", required=True,
                        help="asserts free AZ travel and no cable-wrap risk")
    parser.add_argument("--az-drive-disengaged", action="store_true", required=True,
                        help="asserts motor is mechanically decoupled from the AZ gear train")
    parser.add_argument("--seconds-per-direction", type=float, default=120.0)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not 1.0 <= args.seconds_per_direction <= MAX_SECONDS_PER_DIRECTION:
        parser.error(f"--seconds-per-direction must be 1.0..{MAX_SECONDS_PER_DIRECTION:.1f}")
    if not 0.5 <= args.settle_seconds <= 10.0:
        parser.error("--settle-seconds must be 0.5..10.0")
    if not valid_label(args.run_label):
        parser.error("--run-label may contain only letters, digits, '-' and '_'")
    path = output_path(args.run_label)
    if path.exists():
        parser.error(f"output already exists for label '{args.run_label}'; choose a unique label")

    print("AZ motor exercise: motor must be disengaged; no encoder calibration is inferred.")
    print(f"Plan: AZ+ 0072F1 for {args.seconds_per_direction:.1f}s -> STOP STOP -> "
          f"settle {args.settle_seconds:.1f}s -> AZ- 0072F1 for {args.seconds_per_direction:.1f}s -> STOP STOP.")
    if args.dry_run:
        print(f"DRY RUN: would write {path}; COM is not opened.")
        return

    events: list[dict[str, object]] = []
    active_direction: str | None = None
    try:
        with NXW436(args.port) as mount:
            for direction in ("+", "-"):
                active_direction = direction
                events.append({"monotonic_s": time.perf_counter(), "event": "MOVE", "direction": direction,
                               "payload_hex": MEDIUM_PAYLOAD.hex().upper(), "note": ""})
                mount.move("az", direction, MEDIUM_PAYLOAD)
                time.sleep(args.seconds_per_direction)
                mount.stop("az", direction)
                events.append({"monotonic_s": time.perf_counter(), "event": "STOP_STOP", "direction": direction,
                               "payload_hex": "000000", "note": "normal-leg-end"})
                active_direction = None
                if direction == "+":
                    time.sleep(args.settle_seconds)
    except KeyboardInterrupt:
        events.append({"monotonic_s": time.perf_counter(), "event": "INTERRUPTED", "direction": active_direction or "",
                       "payload_hex": "", "note": "KeyboardInterrupt"})
        raise
    finally:
        # NXW436.__exit__ closes the transport.  A normal leg already stopped;
        # an exception while a leg is active needs an immediate best-effort stop.
        if active_direction is not None:
            try:
                with NXW436(args.port) as emergency_mount:
                    emergency_mount.stop("az", active_direction)
                events.append({"monotonic_s": time.perf_counter(), "event": "STOP_STOP", "direction": active_direction,
                               "payload_hex": "000000", "note": "emergency-finally"})
            except Exception as error:
                events.append({"monotonic_s": time.perf_counter(), "event": "STOP_FAILED", "direction": active_direction,
                               "payload_hex": "000000", "note": str(error)})
        with path.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("monotonic_s", "event", "direction", "payload_hex", "note"))
            writer.writeheader()
            writer.writerows(events)
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
