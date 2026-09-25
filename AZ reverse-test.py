import serial
import time
import statistics

# ============================================================
# NXW436 Rev A - AZ REVERSE / DEAD-TIME TEST
# ============================================================

PORT = "COM5"
BAUD = 4800

COUNTS_PER_REV = 0x102A00
DEG_PER_COUNT = 360.0 / COUNTS_PER_REV

AZ_POS = bytes.fromhex("01")

# Направления
AZ_FORWARD_PREFIX = 0x06
AZ_REVERSE_PREFIX = 0x07

# STOP обеими командами, на всякий случай
AZ_STOP_FORWARD = bytes.fromhex("06 00 00 00")
AZ_STOP_REVERSE = bytes.fromhex("07 00 00 00")

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

TEST_SPEEDS = [1, 3, 6, 9]

# ------------------------------------------------------------
# Настройки эксперимента
# ------------------------------------------------------------

# Сколько ехать вперёд до STOP
FORWARD_TIME = {
    1: 5.0,
    3: 4.0,
    6: 2.0,
    9: 1.0,
}

# Сколько ехать назад после реверса
REVERSE_TIME = {
    1: 5.0,
    3: 4.0,
    6: 2.0,
    9: 1.0,
}

# Время наблюдения после STOP
STOP_OBSERVE_TIME = 1.0

# Частота опроса позиции
POLL_INTERVAL = 0.03

READ_TIMEOUT = 0.15

# Сколько counts считаем реальным движением.
# Отсекаем единичный дребезг/квантование.
MOVEMENT_THRESHOLD = 2


# ============================================================
# SERIAL
# ============================================================

ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.02,
)


# ============================================================
# Low-level helpers
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
# Position math
# ============================================================

def signed_delta(old, new):
    """
    Signed delta с учётом rollover.

    Например:

    0x1029F0 -> 0x000010
    будет положительным движением.

    0x000010 -> 0x1029F0
    будет отрицательным.
    """

    delta = new - old

    half = COUNTS_PER_REV // 2

    if delta > half:
        delta -= COUNTS_PER_REV

    elif delta < -half:
        delta += COUNTS_PER_REV

    return delta


def counts_to_deg(counts):
    return counts * DEG_PER_COUNT


# ============================================================
# Commands
# ============================================================

def move_forward(speed):

    cmd = (
        bytes([AZ_FORWARD_PREFIX])
        + SPEEDS[speed]
    )

    send(cmd)

    return cmd


def move_reverse(speed):

    cmd = (
        bytes([AZ_REVERSE_PREFIX])
        + SPEEDS[speed]
    )

    send(cmd)

    return cmd


def stop_forward():
    send(AZ_STOP_FORWARD)


def stop_reverse():
    send(AZ_STOP_REVERSE)


def emergency_stop():

    try:
        stop_forward()
        time.sleep(0.05)
        stop_reverse()
        time.sleep(0.05)
        stop_forward()

    except Exception:
        pass


# ============================================================
# Sample movement
# ============================================================

def sample_for(duration):

    samples = []

    start_time = time.perf_counter()
    previous_time = start_time

    previous_pos = wait_position()

    samples.append(
        (
            0.0,
            previous_pos,
            0,
            0.0
        )
    )

    deadline = start_time + duration

    while time.perf_counter() < deadline:

        time.sleep(POLL_INTERVAL)

        pos = get_position()
        now = time.perf_counter()

        if pos is None:
            print("WARN: нет ответа")
            continue

        dt = now - previous_time

        delta = signed_delta(
            previous_pos,
            pos
        )

        cps = (
            delta / dt
            if dt > 0
            else 0
        )

        samples.append(
            (
                now - start_time,
                pos,
                delta,
                cps
            )
        )

        previous_pos = pos
        previous_time = now

    return samples


# ============================================================
# Analyze movement
# ============================================================

def analyze_samples(samples):

    if len(samples) < 2:
        return {
            "counts": 0,
            "time": 0,
            "cps": 0,
            "deg_s": 0,
            "median_cps": 0,
        }

    first = samples[0]
    last = samples[-1]

    total_counts = signed_delta(
        first[1],
        last[1]
    )

    total_time = (
        last[0] - first[0]
    )

    cps = (
        total_counts / total_time
        if total_time > 0
        else 0
    )

    velocities = [
        s[3]
        for s in samples[1:]
    ]

    median_cps = (
        statistics.median(velocities)
        if velocities
        else 0
    )

    return {
        "counts": total_counts,
        "time": total_time,
        "cps": cps,
        "deg_s": counts_to_deg(cps),
        "median_cps": median_cps,
    }


# ============================================================
# Observe STOP
# ============================================================

def observe_stop(stop_function):

    before_stop = wait_position()

    t_stop = time.perf_counter()

    stop_function()

    samples = []

    previous_pos = before_stop

    deadline = (
        t_stop + STOP_OBSERVE_TIME
    )

    while time.perf_counter() < deadline:

        time.sleep(POLL_INTERVAL)

        pos = get_position()
        now = time.perf_counter()

        if pos is None:
            continue

        delta = signed_delta(
            previous_pos,
            pos
        )

        samples.append(
            (
                now - t_stop,
                pos,
                delta
            )
        )

        previous_pos = pos

    final_pos = wait_position()

    coast = signed_delta(
        before_stop,
        final_pos
    )

    return {
        "start": before_stop,
        "final": final_pos,
        "coast": coast,
        "coast_deg": counts_to_deg(coast),
        "samples": samples,
    }


# ============================================================
# Detect reverse response
# ============================================================

def measure_reverse_dead_time(speed):

    start_pos = wait_position()

    cmd_time = time.perf_counter()

    cmd = move_reverse(speed)

    previous_pos = start_pos

    accumulated = 0

    first_negative_time = None
    first_negative_pos = None

    samples = []

    deadline = (
        cmd_time + REVERSE_TIME[speed]
    )

    while time.perf_counter() < deadline:

        time.sleep(POLL_INTERVAL)

        pos = get_position()
        now = time.perf_counter()

        if pos is None:
            continue

        delta = signed_delta(
            previous_pos,
            pos
        )

        accumulated += delta

        samples.append(
            (
                now - cmd_time,
                pos,
                delta,
                accumulated
            )
        )

        # Ищем первое убедительное движение назад.
        #
        # Не единичный -1 count,
        # а накопленное движение минимум threshold.
        if (
            first_negative_time is None
            and accumulated <= -MOVEMENT_THRESHOLD
        ):

            first_negative_time = (
                now - cmd_time
            )

            first_negative_pos = pos

        previous_pos = pos

    return {
        "cmd": cmd,
        "start_pos": start_pos,
        "samples": samples,
        "dead_time": first_negative_time,
        "first_negative_pos": first_negative_pos,
    }


# ============================================================
# One complete test
# ============================================================

def test_speed(speed):

    print()
    print("=" * 76)
    print(
        f"AZ REVERSE TEST - SPEED {speed}"
    )
    print("=" * 76)

    # --------------------------------------------------------
    # Начальное состояние
    # --------------------------------------------------------

    emergency_stop()

    time.sleep(1.0)

    initial_pos = wait_position()

    print(
        f"START POSITION : "
        f"0x{initial_pos:06X} "
        f"({initial_pos})"
    )

    # --------------------------------------------------------
    # FORWARD
    # --------------------------------------------------------

    cmd = move_forward(speed)

    print()
    print(
        "FORWARD CMD    : "
        f"{cmd.hex(' ').upper()}"
    )

    print(
        f"FORWARD TIME   : "
        f"{FORWARD_TIME[speed]:.1f} s"
    )

    forward_samples = sample_for(
        FORWARD_TIME[speed]
    )

    forward_result = analyze_samples(
        forward_samples
    )

    print()
    print("--- FORWARD ---")

    print(
        f"Counts         : "
        f"{forward_result['counts']}"
    )

    print(
        f"Average        : "
        f"{forward_result['cps']:.2f} counts/s"
    )

    print(
        f"Median         : "
        f"{forward_result['median_cps']:.2f} counts/s"
    )

    print(
        f"Angular        : "
        f"{forward_result['deg_s']:.6f} deg/s"
    )

    # --------------------------------------------------------
    # STOP after forward
    # --------------------------------------------------------

    print()
    print("STOP FORWARD...")

    stop_result = observe_stop(
        stop_forward
    )

    print(
        f"STOP position  : "
        f"0x{stop_result['start']:06X}"
    )

    print(
        f"Final position : "
        f"0x{stop_result['final']:06X}"
    )

    print(
        f"Coast          : "
        f"{stop_result['coast']} counts"
    )

    print(
        f"                 "
        f"{stop_result['coast_deg']:.6f} deg"
    )

    # --------------------------------------------------------
    # REVERSE
    # --------------------------------------------------------

    time.sleep(0.5)

    print()
    print("REVERSE...")

    reverse = measure_reverse_dead_time(
        speed
    )

    print(
        "REVERSE CMD    : "
        f"{reverse['cmd'].hex(' ').upper()}"
    )

    if reverse["dead_time"] is None:

        print(
            "DEAD TIME      : "
            "движение назад не обнаружено!"
        )

    else:

        print(
            f"DEAD TIME      : "
            f"{reverse['dead_time'] * 1000:.1f} ms"
        )

        print(
            f"FIRST REVERSE  : "
            f"0x{reverse['first_negative_pos']:06X}"
        )

    # Анализ всего reverse участка

    reverse_samples_for_analysis = []

    for sample in reverse["samples"]:

        t, pos, delta, accumulated = sample

        # cps здесь пересчитаем ниже через общий путь
        reverse_samples_for_analysis.append(
            (
                t,
                pos,
                delta,
                0
            )
        )

    if len(reverse["samples"]) >= 2:

        first = reverse["samples"][0]
        last = reverse["samples"][-1]

        reverse_counts = signed_delta(
            first[1],
            last[1]
        )

        reverse_time = (
            last[0] - first[0]
        )

        reverse_cps = (
            reverse_counts / reverse_time
            if reverse_time > 0
            else 0
        )

    else:

        reverse_counts = 0
        reverse_time = 0
        reverse_cps = 0

    print(
        f"Reverse counts : "
        f"{reverse_counts}"
    )

    print(
        f"Reverse avg    : "
        f"{reverse_cps:.2f} counts/s"
    )

    print(
        f"Reverse angular: "
        f"{counts_to_deg(reverse_cps):.6f} deg/s"
    )

    # --------------------------------------------------------
    # STOP reverse
    # --------------------------------------------------------

    print()
    print("STOP REVERSE...")

    reverse_stop = observe_stop(
        stop_reverse
    )

    print(
        f"Reverse coast  : "
        f"{reverse_stop['coast']} counts"
    )

    print(
        f"                 "
        f"{counts_to_deg(reverse_stop['coast']):.6f} deg"
    )

    final_pos = wait_position()

    print()
    print(
        f"FINAL POSITION : "
        f"0x{final_pos:06X}"
    )

    # --------------------------------------------------------
    # Return result
    # --------------------------------------------------------

    return {
        "speed": speed,

        "forward_cps":
            forward_result["cps"],

        "forward_deg_s":
            forward_result["deg_s"],

        "forward_coast":
            stop_result["coast"],

        "reverse_dead_time":
            reverse["dead_time"],

        "reverse_cps":
            reverse_cps,

        "reverse_deg_s":
            counts_to_deg(reverse_cps),

        "reverse_coast":
            reverse_stop["coast"],
    }


# ============================================================
# MAIN
# ============================================================

results = []

try:

    ser.reset_input_buffer()

    print()
    print(
        "NXW436 AZ REVERSE / DEAD-TIME TEST"
    )

    print(
        "=================================="
    )

    pos = wait_position()

    print(
        f"Current AZ: 0x{pos:06X}"
    )

    print()
    print(
        "Будут протестированы скорости:"
    )

    print(
        TEST_SPEEDS
    )

    print()
    print(
        "На каждой скорости:"
    )

    print(
        "06 -> STOP -> 07 -> STOP"
    )

    print()
    print(
        "ВАЖНО: speed 9 реально очень быстрый."
    )

    print(
        "Убери провода из зоны вращения."
    )

    print(
        "Ctrl+C = аварийный STOP."
    )

    print()

    input(
        "Enter -> начать..."
    )

    for speed in TEST_SPEEDS:

        result = test_speed(speed)

        results.append(result)

        print()
        print(
            "-" * 76
        )

        input(
            f"Speed {speed} закончен. "
            "Enter -> следующий..."
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print()
    print("=" * 100)

    print(
        "AZ REVERSE SUMMARY"
    )

    print("=" * 100)

    print(
        f"{'SPD':>3} | "
        f"{'FWD c/s':>10} | "
        f"{'FWD °/s':>9} | "
        f"{'F coast':>8} | "
        f"{'dead ms':>9} | "
        f"{'REV c/s':>10} | "
        f"{'REV °/s':>9} | "
        f"{'R coast':>8}"
    )

    print("-" * 100)

    for r in results:

        if r["reverse_dead_time"] is None:

            dead_ms = "NONE"

        else:

            dead_ms = (
                f"{r['reverse_dead_time'] * 1000:.1f}"
            )

        print(
            f"{r['speed']:>3} | "
            f"{r['forward_cps']:>10.2f} | "
            f"{r['forward_deg_s']:>9.5f} | "
            f"{r['forward_coast']:>8} | "
            f"{dead_ms:>9} | "
            f"{r['reverse_cps']:>10.2f} | "
            f"{r['reverse_deg_s']:>9.5f} | "
            f"{r['reverse_coast']:>8}"
        )


except KeyboardInterrupt:

    print()
    print()
    print(
        "CTRL+C -> АВАРИЙНЫЙ STOP"
    )


except Exception as e:

    print()
    print(
        f"ERROR: {e}"
    )


finally:

    emergency_stop()

    ser.close()

    print()
    print(
        "AZ STOP. COM закрыт."
    )