"""USB-TTL adapter self-test; never connect it to NXW436 while running.

Physically disconnect TX, RX and GND from J1, then jumper the adapter's TX
directly to its RX.  The script sends a fixed byte sequence and expects the
same bytes back.  It does not know or address the mount.
"""

import argparse
import time

import serial

from nxw436_driver import BAUD


TOKEN = bytes.fromhex("55 AA 01 15 C3 3C")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    args = parser.parse_args()
    print("DISCONNECT USB-TTL TX/RX/GND FROM J1. Jumper USB-TTL TX directly to RX.")
    input("Press Enter only after the adapter is isolated from NXW436... ")
    with serial.Serial(
        args.port, BAUD, bytesize=8, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=0.05,
    ) as ser:
        ser.reset_input_buffer()
        ser.write(TOKEN)
        ser.flush()
        reply = bytearray()
        deadline = time.perf_counter() + 1.0
        while len(reply) < len(TOKEN) and time.perf_counter() < deadline:
            reply.extend(ser.read(len(TOKEN) - len(reply)))
    print("TX:", TOKEN.hex(" ").upper())
    print("RX:", bytes(reply).hex(" ").upper() if reply else "<nothing>")
    if bytes(reply) != TOKEN:
        raise SystemExit("FAIL: adapter, COM port, or TX<->RX jumper is not working")
    print("PASS: COM port and USB-TTL TX/RX path work at 4800 8N1.")


if __name__ == "__main__":
    main()
