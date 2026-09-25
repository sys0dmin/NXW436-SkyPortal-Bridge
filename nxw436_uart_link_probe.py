"""Read-only UART reliability probe for the verified NXW436 J1 link.

It sends only the documented position-query bytes (01 and/or 15).  It never
sends a motor command or STOP, so it is safe while diagnosing an intermittent
PC-to-board link.
"""

import argparse
import time

from nxw436_driver import NXW436, POSITION_COMMANDS, is_valid_raw_position


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--axis", choices=("az", "alt", "both"), default="both")
    parser.add_argument("--queries", type=int, default=8)
    parser.add_argument("--inter-query-seconds", type=float, default=0.20)
    parser.add_argument("--open-settle-seconds", type=float, default=0.50)
    parser.add_argument("--reply-timeout-seconds", type=float, default=0.30)
    args = parser.parse_args()
    # A 100-cycle stationary run is needed to validate intermittent bit errors.
    # This remains read-only: each cycle emits only position-query bytes.
    if not 1 <= args.queries <= 100:
        parser.error("--queries must be 1..100")
    if not 0.02 <= args.inter_query_seconds <= 2.0:
        parser.error("--inter-query-seconds must be 0.02..2.0")
    if not 0 <= args.open_settle_seconds <= 5:
        parser.error("--open-settle-seconds must be 0..5")
    if not 0.03 <= args.reply_timeout_seconds <= 1.0:
        parser.error("--reply-timeout-seconds must be 0.03..1.0")

    axes = ("az", "alt") if args.axis == "both" else (args.axis,)
    print("READ-ONLY: sends position queries only; no move and no STOP commands.")
    with NXW436(args.port) as mount:
        time.sleep(args.open_settle_seconds)
        for index in range(1, args.queries + 1):
            for axis in axes:
                command = bytes([POSITION_COMMANDS[axis]])
                sent_at = time.perf_counter()
                mount._send(command)  # Deliberately one request: expose link reliability.
                reply = mount._read_exact(timeout=args.reply_timeout_seconds)
                elapsed_ms = (time.perf_counter() - sent_at) * 1000
                if reply is None:
                    print(f"{index:02d} {axis.upper():3s} tx={command.hex().upper()} rx=TIMEOUT t={elapsed_ms:.1f}ms")
                else:
                    raw = int.from_bytes(reply, "big")
                    validity = "" if is_valid_raw_position(raw) else " INVALID-OUTSIDE-MODULUS"
                    print(
                        f"{index:02d} {axis.upper():3s} tx={command.hex().upper()} "
                        f"rx={reply.hex().upper()} raw={raw} t={elapsed_ms:.1f}ms{validity}"
                    )
                time.sleep(args.inter_query_seconds)


if __name__ == "__main__":
    main()
