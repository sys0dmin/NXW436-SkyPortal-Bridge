import serial
import time
import statistics

# ============================================================
# NXW436 Rev A
# AZ DIRECT REVERSE TEST
#
# Filename:
#   nxw436_az_direct_reverse_test.py
#
# Test:
#   06 speed -> DIRECT 07 speed -> STOP
#
# ВАЖНО:
#   Между 06 и 07 НЕТ команды STOP.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

PORT = "COM5"
BAUD = 4800

COUNTS_PER_REV = 0x102A00
DEG_PER_COUNT = 360.0 / COUNTS_PER_REV

AZ_POS = bytes.fromhex("01")

AZ_FORWARD_PREFIX = 0x06
AZ_REVERSE_PREFIX = 0x07

AZ_STOP_FORWARD = bytes.fromhex("06 00 00 00")
AZ_STOP_REVERSE = bytes.fromhex("07 00 00 00")


# ============================================================
# SPEED TABLE
# ============================================================

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

TEST_SPEEDS = [3, 6, 9]


# ============================================================
# TEST TIMINGS
# ============================================================

# Время движения 06 перед прямым реверсом.
#
# Для 3 даём нормально раскрутиться.
# Для 9 долго крутить эту мясорубку смысла нет.

FORWARD_TIME = {
    3: 3.0,
    6: 2.0,
    9: 1.0,
}

# Максимальное время наблюдения после отправки 07.
#
# После обнаружения реверса продолжаем немного писать данные,
# чтобы получить уже отрицательную скорость.

REVERSE_OBSERVE_TIME = {
    3: 3.0,
    6: 2.0,
    9: 1.5,
}

# После финального STOP смотрим выбег

STOP_OBSERVE_TIME = 1.0


# ------------------------------------------------------------
# UART timing
# ------------------------------------------------------------

# Нам важна максимальная временная точность.
#
# Но UART всего 4800 бод, поэтому бешеные 1 ms тут всё равно
# бессмысленны. Один запрос/ответ физически занимает заметное
# время.

POLL_INTERVAL = 0.005

READ_TIMEOUT = 0.12


# ------------------------------------------------------------
# Reverse detection
# ------------------------------------------------------------

# Считаем, что направление действительно сменилось,
# когда получили несколько отрицательных шагов подряд.
#
# Это защищает от единичного +/-1 count.

NEGATIVE_SAMPLES_REQUIRED = 2

MIN_NEGATIVE_DELTA = -1


# ============================================================
# SERIAL
# ============================================================

ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.01,
)


# ============================================================
# BASIC UART
# ============================================================

def send(data):
    ser.write(data)
    ser.flush()


def read_exact(n, timeout=READ_TIMEOUT):

    data = bytearray()

    deadline = (
        time.perf_counter()
        + timeout
    )

    while len(data) < n:

        if time.perf_counter() >= deadline:
            return None

        chunk = ser.read(
            n - len(data)
        )

        if chunk:
            data.extend(chunk)

    return bytes(data)


def get_position():

    send(AZ_POS)

    raw = read_exact(3)

    if raw is None:
        return None

    return int.from_bytes(
        raw,
        "big"
    )


def wait_position():

    for _ in range(5):

        pos = get_position()

        if pos is not None:
            return pos

        time.sleep(0.03)

    raise RuntimeError(
        "AZ: нет ответа на 01"
    )


# ============================================================
# POSITION MATH
# ============================================================

def signed_delta(old, new):

    delta = new - old

    half = COUNTS_PER_REV // 2

    if delta > half:

        delta -= COUNTS_PER_REV

    elif delta < -half:

        delta += COUNTS_PER_REV

    return delta


def counts_to_deg(counts):

    return (
        counts
        * DEG_PER_COUNT
    )


# ============================================================
# MOTOR COMMANDS
# ============================================================

def make_forward(speed):

    return (
        bytes([AZ_FORWARD_PREFIX])
        + SPEEDS[speed]
    )


def make_reverse(speed):

    return (
        bytes([AZ_REVERSE_PREFIX])
        + SPEEDS[speed]
    )


def emergency_stop():

    try:

        send(AZ_STOP_FORWARD)
        time.sleep(0.03)

        send(AZ_STOP_REVERSE)
        time.sleep(0.03)

        send(AZ_STOP_FORWARD)

    except Exception:
        pass


# ============================================================
# FORWARD SAMPLING
# ============================================================

def run_forward(speed):

    cmd = make_forward(speed)

    start_pos = wait_position()

    send(cmd)

    command_time = time.perf_counter()

    previous_pos = start_pos
    previous_time = command_time

    samples = []

    deadline = (
        command_time
        + FORWARD_TIME[speed]
    )

    while time.perf_counter() < deadline:

        if POLL_INTERVAL:
            time.sleep(POLL_INTERVAL)

        pos = get_position()

        now = time.perf_counter()

        if pos is None:
            continue

        delta = signed_delta(
            previous_pos,
            pos
        )

        dt = (
            now
            - previous_time
        )

        cps = (
            delta / dt
            if dt > 0
            else 0.0
        )

        samples.append({
            "time": now,
            "elapsed": now - command_time,
            "pos": pos,
            "delta": delta,
            "cps": cps,
        })

        previous_pos = pos
        previous_time = now

    return {
        "cmd": cmd,
        "start_pos": start_pos,
        "samples": samples,
    }


# ============================================================
# DIRECT REVERSE
# ============================================================

def direct_reverse(speed, last_forward_pos):

    cmd = make_reverse(speed)

    # --------------------------------------------------------
    # КРИТИЧЕСКИЙ МОМЕНТ
    #
    # Никакого STOP.
    #
    # Мотор прямо сейчас едет по 06.
    # Отправляем 07 той же скорости.
    # --------------------------------------------------------

    send(cmd)

    command_time = time.perf_counter()

    previous_pos = last_forward_pos
    previous_time = command_time

    samples = []

    positive_after_command = 0

    peak_forward_counts = 0

    accumulated = 0

    negative_streak = 0

    reverse_detected = False

    reverse_time = None
    reverse_pos = None

    deadline = (
        command_time
        + REVERSE_OBSERVE_TIME[speed]
    )

    while time.perf_counter() < deadline:

        if POLL_INTERVAL:
            time.sleep(POLL_INTERVAL)

        pos = get_position()

        now = time.perf_counter()

        if pos is None:
            continue

        delta = signed_delta(
            previous_pos,
            pos
        )

        dt = (
            now
            - previous_time
        )

        cps = (
            delta / dt
            if dt > 0
            else 0.0
        )

        accumulated += delta

        # Максимальное продвижение в старом направлении
        # после команды 07.

        if accumulated > peak_forward_counts:

            peak_forward_counts = (
                accumulated
            )

        # ----------------------------------------------------
        # Direction detection
        # ----------------------------------------------------

        if delta <= MIN_NEGATIVE_DELTA:

            negative_streak += 1

        else:

            negative_streak = 0

        if (
            not reverse_detected
            and
            negative_streak
            >= NEGATIVE_SAMPLES_REQUIRED
        ):

            reverse_detected = True

            reverse_time = (
                now
                - command_time
            )

            reverse_pos = pos

        samples.append({
            "time": now,
            "elapsed": now - command_time,
            "pos": pos,
            "delta": delta,
            "cps": cps,
            "accumulated": accumulated,
        })

        previous_pos = pos
        previous_time = now

    return {
        "cmd": cmd,

        "command_time":
            command_time,

        "samples":
            samples,

        "reverse_detected":
            reverse_detected,

        "reverse_time":
            reverse_time,

        "reverse_pos":
            reverse_pos,

        "peak_forward_counts":
            peak_forward_counts,
    }


# ============================================================
# STOP OBSERVATION
# ============================================================

def stop_and_measure():

    before = wait_position()

    send(AZ_STOP_REVERSE)

    stop_time = time.perf_counter()

    previous_pos = before

    samples = []

    deadline = (
        stop_time
        + STOP_OBSERVE_TIME
    )

    while time.perf_counter() < deadline:

        if POLL_INTERVAL:
            time.sleep(POLL_INTERVAL)

        pos = get_position()

        now = time.perf_counter()

        if pos is None:
            continue

        delta = signed_delta(
            previous_pos,
            pos
        )

        samples.append({
            "elapsed":
                now - stop_time,

            "pos":
                pos,

            "delta":
                delta,
        })

        previous_pos = pos

    final = wait_position()

    coast = signed_delta(
        before,
        final
    )

    return {
        "before": before,
        "final": final,
        "coast": coast,
    }


# ============================================================
# STATISTICS
# ============================================================

def velocity_stats(samples):

    if not samples:

        return {
            "mean": 0.0,
            "median": 0.0,
            "min": 0.0,
            "max": 0.0,
        }

    values = [
        s["cps"]
        for s in samples
    ]

    return {
        "mean":
            statistics.mean(values),

        "median":
            statistics.median(values),

        "min":
            min(values),

        "max":
            max(values),
    }


# ============================================================
# ONE TEST
# ============================================================

def test_speed(speed):

    print()
    print("=" * 78)

    print(
        f"AZ DIRECT REVERSE TEST - SPEED {speed}"
    )

    print("=" * 78)

    emergency_stop()

    time.sleep(1.0)

    initial = wait_position()

    print(
        f"START POSITION : "
        f"0x{initial:06X} "
        f"({initial})"
    )

    # ========================================================
    # FORWARD
    # ========================================================

    print()

    print(
        f"FORWARD CMD    : "
        f"{make_forward(speed).hex(' ').upper()}"
    )

    print(
        f"FORWARD TIME   : "
        f"{FORWARD_TIME[speed]:.2f} s"
    )

    forward = run_forward(speed)

    if not forward["samples"]:

        raise RuntimeError(
            "Нет samples при движении 06"
        )

    forward_stats = velocity_stats(
        forward["samples"]
    )

    last_forward_pos = (
        forward["samples"][-1]["pos"]
    )

    print()

    print(
        f"POSITION BEFORE 07 : "
        f"0x{last_forward_pos:06X}"
    )

    print(
        f"Forward median     : "
        f"{forward_stats['median']:.2f} counts/s"
    )

    print(
        f"Forward median     : "
        f"{counts_to_deg(forward_stats['median']):.6f} deg/s"
    )

    # ========================================================
    # DIRECT REVERSE
    # ========================================================

    print()
    print(
        ">>> DIRECT REVERSE <<<"
    )

    print(
        "STOP НЕ ОТПРАВЛЯЕТСЯ"
    )

    print(
        f"REVERSE CMD        : "
        f"{make_reverse(speed).hex(' ').upper()}"
    )

    reverse = direct_reverse(
        speed,
        last_forward_pos
    )

    print()

    if reverse["reverse_detected"]:

        print(
            f"REVERSE DETECTED   : YES"
        )

        print(
            f"Reverse latency    : "
            f"{reverse['reverse_time'] * 1000:.1f} ms"
        )

        print(
            f"Reverse position   : "
            f"0x{reverse['reverse_pos']:06X}"
        )

    else:

        print(
            "REVERSE DETECTED   : NO"
        )

    overshoot_counts = (
        reverse["peak_forward_counts"]
    )

    print()

    print(
        f"Forward overshoot  : "
        f"{overshoot_counts} counts"
    )

    print(
        f"Forward overshoot  : "
        f"{counts_to_deg(overshoot_counts):.6f} deg"
    )

    # --------------------------------------------------------
    # Отдельно считаем отрицательные samples после реверса
    # --------------------------------------------------------

    negative_samples = [
        s
        for s in reverse["samples"]
        if s["delta"] < 0
    ]

    reverse_stats = velocity_stats(
        negative_samples
    )

    if negative_samples:

        print()

        print(
            f"Reverse median     : "
            f"{reverse_stats['median']:.2f} counts/s"
        )

        print(
            f"Reverse median     : "
            f"{counts_to_deg(reverse_stats['median']):.6f} deg/s"
        )

    # ========================================================
    # FINAL STOP
    # ========================================================

    print()
    print(
        "FINAL STOP..."
    )

    stop_result = stop_and_measure()

    print(
        f"STOP position      : "
        f"0x{stop_result['before']:06X}"
    )

    print(
        f"FINAL position     : "
        f"0x{stop_result['final']:06X}"
    )

    print(
        f"Reverse coast      : "
        f"{stop_result['coast']} counts"
    )

    print(
        f"Reverse coast      : "
        f"{counts_to_deg(stop_result['coast']):.6f} deg"
    )

    return {
        "speed":
            speed,

        "forward_median":
            forward_stats["median"],

        "reverse_latency":
            reverse["reverse_time"],

        "overshoot_counts":
            overshoot_counts,

        "reverse_median":
            reverse_stats["median"],

        "reverse_coast":
            stop_result["coast"],
    }


# ============================================================
# MAIN
# ============================================================

results = []

try:

    ser.reset_input_buffer()

    print()
    print(
        "NXW436 AZ DIRECT REVERSE TEST"
    )

    print(
        "============================="
    )

    print()

    pos = wait_position()

    print(
        f"Current AZ: 0x{pos:06X}"
    )

    print()

    print(
        "Тестируем скорости:"
    )

    print(
        TEST_SPEEDS
    )

    print()

    print(
        "Последовательность:"
    )

    print(
        "06 speed -> DIRECT 07 speed -> STOP"
    )

    print()

    print(
        "МЕЖДУ 06 И 07 STOP НЕ БУДЕТ."
    )

    print()

    print(
        "На speed 9 база резко сменит направление."
    )

    print(
        "ПРОВОДА УБРАТЬ ИЗ ЗОНЫ ВРАЩЕНИЯ."
    )

    print()

    print(
        "Ctrl+C = аварийный STOP."
    )

    print()

    input(
        "Enter -> начать..."
    )

    for speed in TEST_SPEEDS:

        result = test_speed(speed)

        results.append(
            result
        )

        print()
        print(
            "-" * 78
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

    print("=" * 105)

    print(
        "AZ DIRECT REVERSE SUMMARY"
    )

    print("=" * 105)

    print(
        f"{'SPD':>3} | "
        f"{'FWD med':>10} | "
        f"{'latency ms':>10} | "
        f"{'overshoot':>10} | "
        f"{'overshoot °':>11} | "
        f"{'REV med':>10} | "
        f"{'REV coast':>10}"
    )

    print("-" * 105)

    for r in results:

        if r["reverse_latency"] is None:

            latency = "NONE"

        else:

            latency = (
                f"{r['reverse_latency'] * 1000:.1f}"
            )

        print(
            f"{r['speed']:>3} | "
            f"{r['forward_median']:>10.2f} | "
            f"{latency:>10} | "
            f"{r['overshoot_counts']:>10} | "
            f"{counts_to_deg(r['overshoot_counts']):>11.6f} | "
            f"{r['reverse_median']:>10.2f} | "
            f"{r['reverse_coast']:>10}"
        )


except KeyboardInterrupt:

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

    try:
        ser.close()
    except Exception:
        pass

    print()
    print(
        "AZ STOP. COM закрыт."
    )