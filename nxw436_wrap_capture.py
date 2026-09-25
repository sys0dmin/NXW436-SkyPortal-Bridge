"""Capture one verified raw-counter wrap on a guarded NXW436 axis.

AZ is locked unless --az-off-tripod is supplied.  That acknowledgement means
the mount is off the tripod or every cable is clear of a near-full AZ turn.
"""

import argparse
import time

import serial


RAW_MODULO = 0x102A00
POSITION = {"alt": 0x15, "az": 0x01}
FORWARD = {"alt": 0x1A, "az": 0x06}
PAYLOAD_SPEED_8 = bytes.fromhex("00 E5 E3")
PORT_DEFAULT = "COM5"


def send(ser, data):
    ser.write(data)
    ser.flush()


def read_exact(ser, count=3, timeout=0.15):
    data = bytearray()
    deadline = time.perf_counter() + timeout
    while len(data) < count:
        if time.perf_counter() >= deadline:
            return None
        chunk = ser.read(count - len(data))
        if chunk:
            data.extend(chunk)
    return bytes(data)


def position(ser, axis, attempts=5):
    for _ in range(attempts):
        send(ser, bytes([POSITION[axis]]))
        raw = read_exact(ser)
        if raw is not None:
            return int.from_bytes(raw, "big"), raw
        time.sleep(0.05)
    raise TimeoutError(f"No position reply for {axis}")


def stop(ser, axis):
    command = bytes([FORWARD[axis], 0, 0, 0])
    send(ser, command)
    time.sleep(0.05)
    send(ser, command)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("axis", choices=("alt", "az"))
    parser.add_argument("--port", default=PORT_DEFAULT)
    parser.add_argument("--max-seconds", type=float, default=240.0)
    parser.add_argument("--az-off-tripod", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.max_seconds <= 600:
        parser.error("--max-seconds must be 10..600")
    if args.axis == "az" and not args.az_off_tripod:
        parser.error(
            "AZ wrap is blocked: remove mount from tripod / clear cables, "
            "then add --az-off-tripod"
        )

    ser = serial.Serial(
        args.port, 4800, bytesize=8, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=0.02,
    )
    running = False
    try:
        ser.reset_input_buffer()
        before, before_raw = position(ser, args.axis)
        print(f"START {args.axis.upper()} {before_raw.hex().upper()} raw={before}")
        print("Move command:", (bytes([FORWARD[args.axis]]) + PAYLOAD_SPEED_8).hex(" ").upper())
        print("Ctrl+C sends STOP twice.")
        input("Enter to start wrap capture...")
        send(ser, bytes([FORWARD[args.axis]]) + PAYLOAD_SPEED_8)
        running = True
        deadline = time.perf_counter() + args.max_seconds
        previous = before
        samples = [(time.perf_counter(), previous)]

        while time.perf_counter() < deadline:
            value, raw = position(ser, args.axis)
            now = time.perf_counter()
            delta = value - previous
            samples.append((now, value))
            if delta < -(RAW_MODULO // 2):
                stop(ser, args.axis)
                running = False
                print("WRAP CAPTURED")
                print(f"BEFORE {previous:06X}")
                print(f"AFTER  {value:06X}")
                print(f"RAW delta {delta}; modulo candidate {RAW_MODULO:06X}")
                return
            previous = value
            time.sleep(0.02)
        print("MAX TIME reached without a positive-direction wrap.")
    finally:
        if running:
            stop(ser, args.axis)
            print("STOP sent twice.")
        ser.close()


if __name__ == "__main__":
    main()
