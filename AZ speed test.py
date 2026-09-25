import serial
import time
import statistics

# ============================================================
# NXW436 Rev A - AZ speed characterization
# ============================================================

PORT = "COM5"
BAUD = 4800

COUNTS_PER_REV = 0x102A00
DEG_PER_COUNT = 360.0 / COUNTS_PER_REV

AZ_POS  = bytes.fromhex("01")
AZ_STOP = bytes.fromhex("06 00 00 00")

SPEEDS = {
    1: bytes.fromhex("00 00 F5"),
    2: bytes.fromhex("00 01 E8"),
    3: bytes.fromhex("00 03 D3"),
    4: bytes.fromhex("00 07 A7"),
    5: bytes.fromhex("00 0F 52"),
    6: bytes.fromhex("00 39 78"),
    7: bytes.fromhex("00 72 F1"),
    8: bytes.fromhex("00 E5 E3"),
    9: bytes.fromhex("FF FF FF"),
}

SETTLE_TIME = {
    1: 3.0,
    2: 2.0,
    3: 1.5,
    4: 1.0,
    5: 1.0,
    6: 0.8,
    7: 0.8,
    8: 0.8,
    9: 0.8,
}

MEASURE_TIME = {
    1: 5.0,
    2: 4.0,
    3: 4.0,
    4: 3.0,
    5: 3.0,
    6: 2.0,
    7: 2.0,
    8: 2.0,
    9: 2.0,
}

POLL_INTERVAL = 0.05
STOP_SETTLE_TIME = 1.0
READ_TIMEOUT = 0.15


ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.02,
)


# ============================================================
# Helpers
# ============================================================

def send(data):
    ser.write(data)
    ser.flush()


def read_exact(n, timeout=READ_TIMEOUT):
    data = bytearray()
    deadline = time.perf_counter() + timeout

    while len(data) < n:

        if time.perf_counter() >= deadline:
            return None

        chunk = ser.read(n - len(data))

        if chunk:
            data.extend(chunk)

    return bytes(data)


def get_position():
    send(AZ_POS)

    raw = read_exact(3)

    if raw is None:
        return None

    return int.from_bytes(raw, "big")


def stop():
    send(AZ_STOP)


def forward_delta(old, new):
    return (new - old) % COUNTS_PER_REV


def counts_to_deg(counts):
    return counts * DEG_PER_COUNT


def wait_position():

    for _ in range(5):

        pos = get_position()

        if pos is not None:
            return pos

        time.sleep(0.05)

    raise RuntimeError(
        "AZ: нет ответа на команду 01"
    )


# ============================================================
# Test one speed
# ============================================================

def test_speed(speed):

    payload = SPEEDS[speed]

    # 06 = выбранное направление AZ
    move_cmd = bytes([0x06]) + payload

    print()
    print("=" * 70)
    print(f"AZ SPEED {speed}")
    print(
        f"CMD: {move_cmd.hex(' ').upper()}"
    )
    print("=" * 70)

    # --------------------------------------------------------
    # Полная остановка
    # --------------------------------------------------------

    stop()
    time.sleep(1.0)

    start_pos = wait_position()

    print(
        f"START       : "
        f"0x{start_pos:06X} ({start_pos})"
    )

    # --------------------------------------------------------
    # Запуск
    # --------------------------------------------------------

    send(move_cmd)

    settle = SETTLE_TIME[speed]

    print(
        f"Разгон/установка скорости: "
        f"{settle:.1f} s"
    )

    time.sleep(settle)

    measure_start_pos = wait_position()
    measure_start_time = time.perf_counter()

    print(
        f"MEASURE START: "
        f"0x{measure_start_pos:06X}"
    )

    samples = []

    previous_pos = measure_start_pos
    previous_time = measure_start_time

    deadline = (
        measure_start_time
        + MEASURE_TIME[speed]
    )

    # --------------------------------------------------------
    # Measurement
    # --------------------------------------------------------

    while time.perf_counter() < deadline:

        time.sleep(POLL_INTERVAL)

        pos = get_position()
        now = time.perf_counter()

        if pos is None:

            print(
                "WARN: нет ответа"
            )

            continue

        dt = now - previous_time

        dc = forward_delta(
            previous_pos,
            pos
        )

        if dt > 0:

            samples.append(
                dc / dt
            )

        previous_pos = pos
        previous_time = now

    measure_end_time = time.perf_counter()

    measure_end_pos = wait_position()

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    stop()

    stop_command_pos = measure_end_pos

    time.sleep(
        STOP_SETTLE_TIME
    )

    final_pos = wait_position()

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    total_counts = forward_delta(
        measure_start_pos,
        measure_end_pos
    )

    total_time = (
        measure_end_time
        - measure_start_time
    )

    average_cps = (
        total_counts
        / total_time
    )

    average_dps = counts_to_deg(
        average_cps
    )

    coast_counts = forward_delta(
        stop_command_pos,
        final_pos
    )

    coast_deg = counts_to_deg(
        coast_counts
    )

    if samples:

        median_cps = statistics.median(
            samples
        )

        min_cps = min(samples)
        max_cps = max(samples)

    else:

        median_cps = 0
        min_cps = 0
        max_cps = 0

    print()
    print("--- RESULT ---")

    print(
        f"Measure time : "
        f"{total_time:.3f} s"
    )

    print(
        f"Counts       : "
        f"{total_counts}"
    )

    print(
        f"Average      : "
        f"{average_cps:.2f} counts/s"
    )

    print(
        f"Median       : "
        f"{median_cps:.2f} counts/s"
    )

    print(
        f"Min/Max      : "
        f"{min_cps:.2f} / "
        f"{max_cps:.2f} counts/s"
    )

    print(
        f"Angular      : "
        f"{average_dps:.6f} deg/s"
    )

    print(
        f"              "
        f"{average_dps * 60:.4f} arcmin/s"
    )

    print(
        f"Coast        : "
        f"{coast_counts} counts"
    )

    print(
        f"              "
        f"{coast_deg:.6f} deg"
    )

    return {
        "speed": speed,
        "counts_per_sec": average_cps,
        "deg_per_sec": average_dps,
        "coast_counts": coast_counts,
        "coast_deg": coast_deg,
    }


# ============================================================
# MAIN
# ============================================================

results = []

try:

    ser.reset_input_buffer()

    print()
    print(
        "NXW436 AZ SPEED CHARACTERIZATION"
    )
    print(
        "================================"
    )

    initial = wait_position()

    print(
        f"Current AZ: 0x{initial:06X}"
    )

    print()
    print(
        "Тест последовательно запустит "
        "AZ speed 1..9."
    )
    print(
        "Ctrl+C в любой момент = STOP."
    )
    print()

    input(
        "Enter -> начать..."
    )

    for speed in range(1, 10):

        result = test_speed(
            speed
        )

        results.append(
            result
        )

        print()

        input(
            f"Speed {speed} закончен. "
            "Enter -> следующий..."
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print()
    print("=" * 78)
    print("AZ SUMMARY")
    print("=" * 78)

    print(
        f"{'SPD':>3} | "
        f"{'counts/s':>12} | "
        f"{'deg/s':>12} | "
        f"{'coast cnt':>10} | "
        f"{'coast deg':>10}"
    )

    print("-" * 78)

    for r in results:

        print(
            f"{r['speed']:>3} | "
            f"{r['counts_per_sec']:>12.2f} | "
            f"{r['deg_per_sec']:>12.6f} | "
            f"{r['coast_counts']:>10} | "
            f"{r['coast_deg']:>10.6f}"
        )

except KeyboardInterrupt:

    print()
    print(
        "Ctrl+C -> AZ STOP"
    )

finally:

    try:

        stop()
        time.sleep(0.1)
        stop()

    except Exception:

        pass

    ser.close()

    print()
    print(
        "AZ STOP. COM закрыт."
    )