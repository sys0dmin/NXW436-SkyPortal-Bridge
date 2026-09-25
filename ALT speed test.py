import serial
import time
import statistics

# ============================================================
# NXW436 Rev A - ALT speed characterization
# ============================================================

PORT = "COM5"
BAUD = 4800

COUNTS_PER_REV = 0x102A00
DEG_PER_COUNT = 360.0 / COUNTS_PER_REV

ALT_POS  = bytes.fromhex("15")
ALT_STOP = bytes.fromhex("1A 00 00 00")

# Historical GT speed payloads, already observed to work
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

# ------------------------------------------------------------
# Experiment settings
# ------------------------------------------------------------

# Даём скорости установиться до начала измерения.
# Для speed 1 специально побольше: мы уже видели,
# что она может долго раскачиваться.
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

# Продолжительность непосредственно измерительного участка
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

# Сколько ждать после STOP для измерения выбега
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
    send(ALT_POS)

    raw = read_exact(3)

    if raw is None:
        return None

    return int.from_bytes(raw, "big")


def stop():
    send(ALT_STOP)


def forward_delta(old, new):
    """
    Положительное движение через rollover.
    """
    return (new - old) % COUNTS_PER_REV


def counts_to_deg(counts):
    return counts * DEG_PER_COUNT


def wait_position():
    for _ in range(5):
        pos = get_position()

        if pos is not None:
            return pos

        time.sleep(0.05)

    raise RuntimeError("ALT: нет ответа на команду 15")


# ============================================================
# Test one speed
# ============================================================

def test_speed(speed):
    payload = SPEEDS[speed]
    move_cmd = bytes([0x1A]) + payload

    print()
    print("=" * 70)
    print(f"ALT SPEED {speed}")
    print(f"CMD: {move_cmd.hex(' ').upper()}")
    print("=" * 70)

    # --------------------------------------------------------
    # Полная остановка перед опытом
    # --------------------------------------------------------

    stop()
    time.sleep(1.0)

    start_pos = wait_position()

    print(
        f"START       : 0x{start_pos:06X} "
        f"({start_pos})"
    )

    # --------------------------------------------------------
    # Запуск
    # --------------------------------------------------------

    send(move_cmd)

    settle = SETTLE_TIME[speed]

    print(
        f"Разгон/установка скорости: {settle:.1f} s"
    )

    time.sleep(settle)

    measure_start_pos = wait_position()
    measure_start_time = time.perf_counter()

    print(
        f"MEASURE START: 0x{measure_start_pos:06X}"
    )

    samples = []

    previous_pos = measure_start_pos
    previous_time = measure_start_time

    deadline = measure_start_time + MEASURE_TIME[speed]

    # --------------------------------------------------------
    # Измерительный участок
    # --------------------------------------------------------

    while time.perf_counter() < deadline:
        time.sleep(POLL_INTERVAL)

        pos = get_position()
        now = time.perf_counter()

        if pos is None:
            print("WARN: нет ответа")
            continue

        dt = now - previous_time
        dc = forward_delta(previous_pos, pos)

        if dt > 0:
            cps = dc / dt
            samples.append(cps)

        previous_pos = pos
        previous_time = now

    measure_end_time = time.perf_counter()
    measure_end_pos = wait_position()

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    stop()

    stop_command_pos = measure_end_pos

    time.sleep(STOP_SETTLE_TIME)

    final_pos = wait_position()

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    total_counts = forward_delta(
        measure_start_pos,
        measure_end_pos
    )

    total_time = (
        measure_end_time - measure_start_time
    )

    average_cps = total_counts / total_time
    average_dps = counts_to_deg(average_cps)

    coast_counts = forward_delta(
        stop_command_pos,
        final_pos
    )

    coast_deg = counts_to_deg(coast_counts)

    if samples:
        median_cps = statistics.median(samples)
        min_cps = min(samples)
        max_cps = max(samples)
    else:
        median_cps = 0
        min_cps = 0
        max_cps = 0

    print()
    print("--- RESULT ---")

    print(
        f"Measure time : {total_time:.3f} s"
    )

    print(
        f"Counts       : {total_counts}"
    )

    print(
        f"Average      : {average_cps:.2f} counts/s"
    )

    print(
        f"Median       : {median_cps:.2f} counts/s"
    )

    print(
        f"Min/Max      : "
        f"{min_cps:.2f} / {max_cps:.2f} counts/s"
    )

    print(
        f"Angular      : {average_dps:.6f} deg/s"
    )

    print(
        f"              {average_dps * 60:.4f} arcmin/s"
    )

    print(
        f"Coast        : {coast_counts} counts"
    )

    print(
        f"              {coast_deg:.6f} deg"
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
    print("NXW436 ALT SPEED CHARACTERIZATION")
    print("=================================")

    initial = wait_position()

    print(
        f"Current ALT: 0x{initial:06X}"
    )

    print()
    print(
        "Тест последовательно запустит ALT speed 1..9."
    )
    print(
        "Ctrl+C в любой момент = STOP."
    )
    print()

    input("Enter -> начать...")

    for speed in range(1, 10):

        result = test_speed(speed)
        results.append(result)

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
    print("ALT SUMMARY")
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
    print("Ctrl+C -> ALT STOP")

finally:

    try:
        stop()
        time.sleep(0.1)
        stop()
    except Exception:
        pass

    ser.close()

    print()
    print("ALT STOP. COM закрыт.")