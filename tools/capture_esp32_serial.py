"""Capture ESP32 serial diagnostics to a timestamped text file.

This is a PC-side observation tool only. It does not write to the serial port,
discover network devices or send AUX/mount commands.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import serial


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record ESP32 Serial diagnostics without writing to it.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.baud <= 0:
        parser.error("--baud must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with serial.Serial(args.port, args.baud, timeout=0.2) as device, \
            args.output.open("w", encoding="utf-8", newline="\n") as capture:
        header = f"# esp32_serial_capture start_utc={utc_now()} port={args.port} baud={args.baud}"
        print(header)
        capture.write(header + "\n")
        try:
            while True:
                raw = device.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                record = f"{utc_now()} | {line}"
                print(record)
                capture.write(record + "\n")
                capture.flush()
        except KeyboardInterrupt:
            end = f"# esp32_serial_capture stop_utc={utc_now()}"
            print(end)
            capture.write(end + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
