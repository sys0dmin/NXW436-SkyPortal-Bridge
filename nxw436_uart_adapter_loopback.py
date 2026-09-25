"""Read-only-for-the-mount USB-UART loopback diagnostic.

Use only with NXW436 J1 physically disconnected and the adapter's TX and RX
pins temporarily linked.  This exercises the PC, USB cable, adapter, and its
TTL RX/TX pins without sending any byte to the telescope board.
"""

from __future__ import annotations

import argparse
import time

import serial


PATTERNS = (bytes.fromhex("55AA00"), bytes.fromhex("FF00A5"), bytes.fromhex("3CC3F0"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--adapter-loopback-confirmed", action="store_true")
    args = parser.parse_args()
    if not args.adapter_loopback_confirmed:
        parser.error(
            "physically disconnect J1, link adapter TX to RX, then pass "
            "--adapter-loopback-confirmed"
        )
    if not 1 <= args.frames <= 500:
        parser.error("--frames must be 1..500")

    print("MOUNT MUST BE DISCONNECTED. Testing USB-UART TX<->RX loopback only.")
    mismatches = 0
    with serial.Serial(args.port, baudrate=4800, bytesize=8, parity="N", stopbits=1, timeout=0.3) as link:
        time.sleep(0.5)
        link.reset_input_buffer()
        for index in range(1, args.frames + 1):
            sent = PATTERNS[(index - 1) % len(PATTERNS)]
            link.write(sent)
            link.flush()
            received = link.read(len(sent))
            if received != sent:
                mismatches += 1
                print(f"{index:03d} tx={sent.hex().upper()} rx={received.hex().upper() or 'TIMEOUT'} MISMATCH")
            else:
                print(f"{index:03d} tx={sent.hex().upper()} rx={received.hex().upper()} OK")
    print(f"RESULT frames={args.frames} mismatches={mismatches}")
    raise SystemExit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
