"""Guarded physical speed measurement for one NXW436 axis and raw payload."""

import argparse
import time

from nxw436_driver import COUNTS_PER_REV, NXW436, signed_delta


def parse_payload(text: str) -> bytes:
    try:
        payload = bytes.fromhex(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("payload must be six hex digits") from error
    if len(payload) != 3:
        raise argparse.ArgumentTypeError("payload must be exactly three bytes")
    if payload == b"\0\0\0":
        raise argparse.ArgumentTypeError("000000 is STOP, not a speed test")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("axis", choices=("az", "alt"))
    parser.add_argument("direction", choices=("+", "-"))
    parser.add_argument("--payload", required=True, type=parse_payload)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--interval", type=float, default=0.20)
    parser.add_argument("--preload-payload", type=parse_payload)
    parser.add_argument("--preload-seconds", type=float, default=0.5)
    parser.add_argument("--az-off-tripod", action="store_true")
    args = parser.parse_args()
    if not 0 < args.seconds <= 10:
        parser.error("--seconds must be >0 and <=10")
    if not 0.02 <= args.interval <= 1:
        parser.error("--interval must be 0.02..1 seconds")
    if args.preload_payload is not None and not 0 < args.preload_seconds <= 1:
        parser.error("--preload-seconds must be >0 and <=1")
    if args.axis == "az" and not args.az_off_tripod:
        parser.error(
            "AZ motion is blocked: clear cables / remove mount from tripod, "
            "then add --az-off-tripod"
        )

    print("WARNING: payload is sent exactly as supplied; only use documented values.")
    if int.from_bytes(args.payload, "big") > 0x0000F5:
        print("WARNING: this payload is faster than the measured speed-1 point.")
    print("Movement is limited to", args.seconds, "s; STOP will be sent twice.")
    with NXW436(args.port) as mount:
        before = mount.get_position(args.axis)
        samples = [(time.perf_counter(), before)]
        print(f"START {args.axis.upper()} {before:06X} raw={before}")
        measurement_before = before
        if args.preload_payload is not None:
            print(
                "PRELOAD", args.preload_payload.hex(" ").upper(),
                f"for {args.preload_seconds:.1f}s",
            )
        print("MOVE", args.payload.hex(" ").upper())
        try:
            if args.preload_payload is not None:
                mount.move(args.axis, args.direction, args.preload_payload)
                time.sleep(args.preload_seconds)
                measurement_before = mount.get_position(args.axis)
                print(
                    f"TARGET_START {args.axis.upper()} "
                    f"{measurement_before:06X} raw={measurement_before}"
                )
            mount.move(args.axis, args.direction, args.payload)
            motion_started = time.perf_counter()
            deadline = motion_started + args.seconds
            while time.perf_counter() < deadline:
                time.sleep(args.interval)
                try:
                    value = mount.get_position(args.axis)
                except TimeoutError:
                    continue
                samples.append((time.perf_counter(), value))
        finally:
            mount.stop(args.axis, args.direction)
            motion_ended = time.perf_counter()
        time.sleep(mount.post_command_delay)
        after = mount.get_position(args.axis)
        stopped = time.perf_counter()
        samples.append((stopped, after))

    if len(samples) < 3:
        raise RuntimeError("No successful position sample during motion")

    # The last successful poll is the last known position before STOP.  Do not
    # issue a final query before STOP: a timeout there can extend fast motion.
    last_sample_time, last_sample = samples[-2]
    elapsed = last_sample_time - motion_started
    delta = signed_delta(measurement_before, last_sample)
    post_stop_delta = signed_delta(last_sample, after)
    counts_per_second = delta / elapsed
    degrees = delta * 360.0 / COUNTS_PER_REV
    deg_per_second = degrees / elapsed
    print(f"LAST_SAMPLE {args.axis.upper()} {last_sample:06X} raw={last_sample}")
    print(f"STOP     {args.axis.upper()} {after:06X} raw={after}")
    print(f"motion_elapsed_s={elapsed:.3f}")
    print(f"motion_delta_counts={delta}")
    print(f"post_stop_delta_counts={post_stop_delta}")
    print(f"counts_per_s={counts_per_second:.3f}")
    print(f"deg_per_s={deg_per_second:.6f}")
    print(f"deg_per_min={deg_per_second * 60:.6f}")
    print(f"deg_per_hour={deg_per_second * 3600:.6f}")
    print(f"samples={len(samples)}")


if __name__ == "__main__":
    main()
