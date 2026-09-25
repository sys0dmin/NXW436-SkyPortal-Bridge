"""Read-only single-axis UART burst characterization for NXW436 J1.

Each request transmits only the confirmed one-byte position command (AZ 01 or
ALT 15).  It never sends a motor command or STOP.  The resulting CSV files
retain every raw reply and quantify byte/bit deviations from the modal valid
three-byte reply observed during this stationary burst.
"""

from __future__ import annotations

import argparse
import csv
import time
from collections import Counter
from pathlib import Path

from nxw436_driver import NXW436, POSITION_COMMANDS, is_valid_raw_position


def changed_bit_positions(value: int) -> str:
    """Return differing bit indices, least-significant bit first."""
    return ",".join(str(bit) for bit in range(24) if value & (1 << bit))


def modal_valid_frame(frames: list[bytes | None]) -> bytes | None:
    valid = [frame for frame in frames if frame is not None and is_valid_raw_position(int.from_bytes(frame, "big"))]
    if not valid:
        return None
    return Counter(valid).most_common(1)[0][0]


def analyze_frames(frames: list[bytes | None], modal: bytes | None) -> list[dict[str, object]]:
    """Produce pure per-frame XOR/Hamming metadata for tests and CSV output."""
    rows: list[dict[str, object]] = []
    for frame in frames:
        if frame is None:
            rows.append({"reply_kind": "timeout", "rx_bytes_hex": "", "raw_position": "", "valid_position": False,
                         "xor_to_modal_hex": "", "hamming_distance": "", "changed_bit_positions_lsb0": ""})
            continue
        raw = int.from_bytes(frame, "big")
        xor = "" if modal is None else raw ^ int.from_bytes(modal, "big")
        rows.append({
            "reply_kind": "valid" if is_valid_raw_position(raw) else "malformed-outside-modulus",
            "rx_bytes_hex": frame.hex().upper(),
            "raw_position": raw,
            "valid_position": is_valid_raw_position(raw),
            "xor_to_modal_hex": "" if modal is None else f"{xor:06X}",
            "hamming_distance": "" if modal is None else xor.bit_count(),
            "changed_bit_positions_lsb0": "" if modal is None else changed_bit_positions(xor),
        })
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--axis", choices=("az", "alt"), required=True, help="one axis per burst")
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--inter-query-seconds", type=float, default=0.02)
    parser.add_argument("--open-settle-seconds", type=float, default=0.5)
    parser.add_argument("--reply-timeout-seconds", type=float, default=0.30)
    parser.add_argument("--label", required=True)
    parser.add_argument("--dry-run", action="store_true", help="show output paths; do not open COM")
    args = parser.parse_args()
    if not 1 <= args.queries <= 1000:
        parser.error("--queries must be 1..1000")
    if not 0.01 <= args.inter_query_seconds <= 2.0:
        parser.error("--inter-query-seconds must be 0.01..2.0")
    if not 0 <= args.open_settle_seconds <= 5:
        parser.error("--open-settle-seconds must be 0..5")
    if not 0.03 <= args.reply_timeout_seconds <= 1.0:
        parser.error("--reply-timeout-seconds must be 0.03..1.0")
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("--label may contain only letters, digits, '-' and '_'")

    base = Path(f"nxw436_uart_burst_{args.label}")
    raw_path = base.with_name(base.name + "_raw.csv")
    histogram_path = base.with_name(base.name + "_histogram.csv")
    summary_path = base.with_name(base.name + "_summary.csv")
    if any(path.exists() for path in (raw_path, histogram_path, summary_path)):
        parser.error("output label already exists; choose a unique --label")
    print(f"READ-ONLY single-axis burst: {args.axis.upper()} tx={POSITION_COMMANDS[args.axis]:02X}; queries={args.queries}")
    print("Outputs:", raw_path, histogram_path, summary_path)
    if args.dry_run:
        return

    samples: list[dict[str, object]] = []
    frames: list[bytes | None] = []
    command = bytes([POSITION_COMMANDS[args.axis]])
    with NXW436(args.port) as mount:
        time.sleep(args.open_settle_seconds)
        for index in range(1, args.queries + 1):
            started = time.perf_counter()
            mount._send(command)
            reply = mount._read_exact(timeout=args.reply_timeout_seconds)
            received = time.perf_counter()
            frames.append(reply)
            samples.append({
                "sample_index": index,
                "timestamp": time.time(),
                "monotonic_s": received,
                "elapsed_ms": (received - started) * 1000,
                "axis": args.axis,
                "tx_bytes_hex": command.hex().upper(),
            })
            time.sleep(args.inter_query_seconds)

    modal = modal_valid_frame(frames)
    analysis = analyze_frames(frames, modal)
    raw_rows = [sample | analyzed for sample, analyzed in zip(samples, analysis, strict=True)]
    write_csv(raw_path, raw_rows)

    histogram = Counter("TIMEOUT" if frame is None else frame.hex().upper() for frame in frames)
    histogram_rows = []
    for frame_hex, count in histogram.most_common():
        if frame_hex == "TIMEOUT":
            histogram_rows.append({"rx_bytes_hex": frame_hex, "raw_position": "", "valid_position": False,
                                   "count": count, "fraction": count / args.queries})
        else:
            raw = int(frame_hex, 16)
            histogram_rows.append({"rx_bytes_hex": frame_hex, "raw_position": raw,
                                   "valid_position": is_valid_raw_position(raw), "count": count,
                                   "fraction": count / args.queries})
    write_csv(histogram_path, histogram_rows)

    valid_count = sum(1 for row in analysis if row["reply_kind"] == "valid")
    invalid_count = sum(1 for row in analysis if row["reply_kind"] == "malformed-outside-modulus")
    timeout_count = sum(1 for row in analysis if row["reply_kind"] == "timeout")
    modal_hex = "" if modal is None else modal.hex().upper()
    summary = [{
        "axis": args.axis,
        "queries": args.queries,
        "valid_replies": valid_count,
        "invalid_outside_modulus": invalid_count,
        "timeouts": timeout_count,
        "modal_valid_rx_bytes_hex": modal_hex,
        "modal_valid_raw_position": "" if modal is None else int.from_bytes(modal, "big"),
        "modal_valid_count": 0 if modal is None else histogram[modal_hex],
        "distinct_reply_patterns": len(histogram),
        "nonmodal_reply_count": args.queries - (0 if modal is None else histogram[modal_hex]),
    }]
    write_csv(summary_path, summary)
    print(
        f"RESULT valid={valid_count} invalid={invalid_count} timeout={timeout_count} "
        f"modal={modal_hex or 'NONE'} modal_count={summary[0]['modal_valid_count']} "
        f"nonmodal={summary[0]['nonmodal_reply_count']}"
    )


if __name__ == "__main__":
    main()
