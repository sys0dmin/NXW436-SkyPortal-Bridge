"""Bounded AZ recovery from a persistent raw position just above RAW_MODULO.

This is deliberately not part of the production controller.  It exists only
for the observed state where AZ repeatedly reports a small value above the
confirmed encoder modulus after crossing zero.  The utility refuses every
other preflight state, moves AZ- with the already measured 0072F1 payload for
a short fixed interval, sends STOP twice in ``finally``, then verifies that
AZ replies have returned to the normal representable range.
"""

from __future__ import annotations

import argparse
import time

from nxw436_driver import NXW436, RAW_MODULO, is_valid_raw_position


RECOVERY_PAYLOAD = bytes.fromhex("0072F1")
PREFLIGHT_SAMPLES = 3
MAX_ABOVE_MODULO_COUNTS = 0x40


def is_recoverable_above_modulo(value: int) -> bool:
    """Only accept the narrow persistent state observed at the AZ wrap edge."""
    return RAW_MODULO <= value <= RAW_MODULO + MAX_ABOVE_MODULO_COUNTS


def read_raw_az(mount: NXW436) -> int:
    frame = mount.query_position_raw("az")
    if frame is None:
        raise TimeoutError("No AZ position reply")
    return int.from_bytes(frame, "big")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--az-off-tripod", action="store_true", required=True,
                        help="asserts free AZ travel and no cable-wrap risk")
    parser.add_argument("--seconds", type=float, default=1.0,
                        help="bounded AZ- recovery interval, 0.2..1.0 s")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0.2 <= args.seconds <= 1.0:
        parser.error("--seconds must be 0.2..1.0")

    print("AZ wrap recovery only: requires persistent raw in 102A00..102A40.")
    print("It sends AZ- 0072F1 once, then STOP twice, then reads AZ ten times.")
    if args.dry_run:
        print("DRY RUN: COM is not opened and no command is sent.")
        return

    with NXW436(args.port) as mount:
        before = [read_raw_az(mount) for _ in range(PREFLIGHT_SAMPLES)]
        print("PREFLIGHT " + " ".join(f"{value:06X}" for value in before))
        if not all(is_recoverable_above_modulo(value) for value in before):
            raise RuntimeError("Refusing recovery: AZ is not in the narrow above-modulo state")

        try:
            mount.move("az", "-", RECOVERY_PAYLOAD)
            time.sleep(args.seconds)
        finally:
            mount.stop("az", "-")

        after = [read_raw_az(mount) for _ in range(10)]
        print("POSTFLIGHT " + " ".join(f"{value:06X}" for value in after))
        valid = [value for value in after if is_valid_raw_position(value)]
        if not valid:
            raise RuntimeError("Recovery did not return AZ to the valid raw range")
        print(f"RECOVERED first_valid={valid[0]:06X}")


if __name__ == "__main__":
    main()
