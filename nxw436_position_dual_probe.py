"""Short, guarded NXW436 J1 diagnostic.

It uses the same 4800 8N1 and read-until-three-bytes transaction as the
working forward test.  By default it is passive.  An optional motor command
is deliberately limited to 8 seconds and is always followed by two STOPs.
"""

import argparse
import time

import serial


RAW_MODULO = 0x102A00
DEFAULT_PORT = "COM5"
BAUD = 4800
READ_TIMEOUT = 0.15


def read_exact(ser, count=3, timeout=READ_TIMEOUT):
    data = bytearray()
    deadline = time.perf_counter() + timeout
    while len(data) < count:
        if time.perf_counter() >= deadline:
            return None
        chunk = ser.read(count - len(data))
        if chunk:
            data.extend(chunk)
    return bytes(data)


def send(ser, data):
    ser.write(data)
    ser.flush()


def query(ser, command):
    send(ser, bytes([command]))
    raw = read_exact(ser)
    if raw is None:
        return None, None
    return raw, int.from_bytes(raw, "big")


def signed_delta(previous, current):
    delta = current - previous
    if delta > RAW_MODULO // 2:
        delta -= RAW_MODULO
    elif delta < -(RAW_MODULO // 2):
        delta += RAW_MODULO
    return delta


def stop(ser, prefix):
    send(ser, bytes([prefix, 0x00, 0x00, 0x00]))
    time.sleep(0.05)
    send(ser, bytes([prefix, 0x00, 0x00, 0x00]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.20)
    parser.add_argument("--move", choices=("1A", "1B", "06", "07"))
    parser.add_argument("--speed", choices=(1, 8), type=int, default=8)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    if not 1 <= args.samples <= 100:
        parser.error("--samples must be 1..100")
    if not 0 < args.seconds <= 10:
        parser.error("--seconds must be >0 and <=10")

    ser = serial.Serial(
        args.port, BAUD, bytesize=8, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=0.02,
    )
    previous = {0x01: None, 0x15: None}
    move_prefix = int(args.move, 16) if args.move else None

    try:
        # One cleanup before the first request; never between a request and RX.
        ser.reset_input_buffer()
        print("NXW436 J1 dual position probe: 4800 8N1")
        if move_prefix is not None:
            payload = bytes.fromhex("00 00 F5") if args.speed == 1 else bytes.fromhex("00 E5 E3")
            command = bytes([move_prefix]) + payload
            print("MOVE", command.hex(" ").upper(), "for", args.seconds, "s")
            send(ser, command)
            deadline = time.perf_counter() + args.seconds
        else:
            deadline = None

        for sample in range(1, args.samples + 1):
            fields = [f"{sample:02d}"]
            for command in (0x01, 0x15):
                raw, value = query(ser, command)
                if raw is None:
                    fields.append(f"{command:02X}=TIMEOUT")
                    continue
                delta = "-" if previous[command] is None else str(
                    signed_delta(previous[command], value)
                )
                previous[command] = value
                fields.append(
                    f"{command:02X}={raw.hex().upper()} raw={value} d={delta}"
                )
            print(" | ".join(fields))
            if deadline is not None and time.perf_counter() >= deadline:
                break
            time.sleep(args.interval)
    finally:
        if move_prefix is not None:
            stop(ser, move_prefix)
            print("STOP sent twice:", f"{move_prefix:02X} 00 00 00")
        ser.close()


if __name__ == "__main__":
    main()
