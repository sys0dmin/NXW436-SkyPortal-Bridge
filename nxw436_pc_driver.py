"""Guarded command-line front end for nxw436_driver.NXW436."""

import argparse

from nxw436_driver import GT114_SPEED_PAYLOADS, NXW436, signed_delta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    move = sub.add_parser("move")
    move.add_argument("axis", choices=("az", "alt"))
    move.add_argument("direction", choices=("+", "-"))
    move.add_argument("--speed", type=int, choices=(1, 8), default=1)
    move.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    with NXW436(args.port) as mount:
        if args.action == "status":
            for axis in ("az", "alt"):
                value = mount.get_position(axis)
                print(f"{axis.upper():3s} {value:06X} raw={value}")
            return

        before, after = mount.move_for(
            args.axis, args.direction,
            GT114_SPEED_PAYLOADS[args.speed],
            args.seconds,
        )
        print(f"START {args.axis.upper()} raw={before:06X} ({before})")
        print(
            f"STOP  {args.axis.upper()} raw={after:06X} ({after}), "
            f"delta={signed_delta(before, after)}"
        )


if __name__ == "__main__":
    main()
