# ============================================================
# NXW436 Rev A
# ALT PERIODIC DRAG / GEARBOX TEST
#
# Filename:
#   nxw436_alt_periodic_drag_test.py
#
# Purpose:
#   Search for repeatable speed dips versus gearbox position.
#
# Test:
#   ALT speed 8, one direction, 5 full revolutions.
#
# Output:
#   nxw436_alt_periodic_drag.csv
#   nxw436_alt_periodic_drag_bins.csv
# ============================================================

import serial
import time
import csv
import statistics
from collections import defaultdict


# ============================================================
# CONFIG
# ============================================================

PORT = "COM5"
BAUD = 4800

COUNTS_PER_REV = 0x102A00
DEG_PER_COUNT = 360.0 / COUNTS_PER_REV

ALT_POS = bytes.fromhex("15")

ALT_FORWARD_PREFIX = 0x1A
ALT_STOP = bytes.fromhex("1A 00 00 00")

# Speed 8
SPEED_PAYLOAD = bytes.fromhex("00 E5 E3")

TEST_SPEED = 8

# Сколько полных оборотов хотим записать
REVOLUTIONS = 5

# Разбиваем каждый оборот на ячейки.
#
# 360 bins = 1 градус на ячейку.
#
# Этого достаточно для первого поиска периодики и не слишком
# мелко для нашего UART.
BINS_PER_REV = 360

POLL_INTERVAL = 0.02
READ_TIMEOUT = 0.15

# Первые секунды после старта не используем для анализа скорости:
# мотор должен выйти на нормальный режим.
SETTLE_TIME = 1.5

# Максимальное время теста на случай механического клина.
MAX_TEST_TIME = 15 * 60

# Если позиция не меняется столько времени при поданной команде,
# считаем это серьёзным затыком и останавливаем тест.
STALL_TIMEOUT = 5.0

RAW_CSV = "nxw436_alt_periodic_drag.csv"
BINS_CSV = "nxw436_alt_periodic_drag_bins.csv"


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
# UART
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


def get_alt_position():

    send(ALT_POS)

    raw = read_exact(3)

    if raw is None:
        return None

    return int.from_bytes(raw, "big")


def wait_alt_position():

    for _ in range(10):

        pos = get_alt_position()

        if pos is not None:
            return pos

        time.sleep(0.05)

    raise RuntimeError(
        "ALT: нет ответа на команду 15"
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

    return counts * DEG_PER_COUNT


def normalize_count(value):

    return value % COUNTS_PER_REV


# ============================================================
# MOTOR
# ============================================================

def start_alt():

    cmd = (
        bytes([ALT_FORWARD_PREFIX])
        + SPEED_PAYLOAD
    )

    send(cmd)

    return cmd


def stop_alt():

    send(ALT_STOP)


def emergency_stop():

    try:

        stop_alt()
        time.sleep(0.05)
        stop_alt()

    except Exception:
        pass


# ============================================================
# MAIN TEST
# ============================================================

def run_test():

    print()
    print("NXW436 ALT PERIODIC DRAG TEST")
    print("=============================")
    print()

    print(
        f"Speed          : {TEST_SPEED}"
    )

    print(
        f"Revolutions    : {REVOLUTIONS}"
    )

    print(
        f"Counts/rev     : {COUNTS_PER_REV}"
    )

    print(
        f"Bins/rev       : {BINS_PER_REV}"
    )

    print(
        f"Bin size       : "
        f"{360 / BINS_PER_REV:.3f} deg"
    )

    print()

    start_raw = wait_alt_position()

    print(
        f"Start position : "
        f"0x{start_raw:06X} "
        f"({start_raw})"
    )

    print()

    print(
        "ALT сделает примерно "
        f"{REVOLUTIONS} полных оборотов."
    )

    print(
        "Труба должна быть снята."
    )

    print(
        "Провода убрать из зоны вращения."
    )

    print(
        "Ctrl+C = STOP."
    )

    print()

    input(
        "Enter -> начать..."
    )

    # --------------------------------------------------------
    # Initial state
    # --------------------------------------------------------

    raw_previous = wait_alt_position()

    unwrapped = 0

    start_time = time.perf_counter()
    previous_time = start_time

    last_movement_time = start_time

    samples = []

    cmd = start_alt()

    print()
    print(
        "CMD            : "
        f"{cmd.hex(' ').upper()}"
    )

    print()
    print("RUNNING...")
    print()

    next_progress = 0.25

    while True:

        time.sleep(POLL_INTERVAL)

        now = time.perf_counter()

        raw = get_alt_position()

        if raw is None:

            print(
                "WARN: нет ответа ALT"
            )

            continue

        dt = now - previous_time

        delta = signed_delta(
            raw_previous,
            raw
        )

        unwrapped += delta

        if delta != 0:
            last_movement_time = now

        cps = (
            delta / dt
            if dt > 0
            else 0.0
        )

        deg_s = counts_to_deg(cps)

        elapsed = now - start_time

        revolutions = (
            unwrapped
            / COUNTS_PER_REV
        )

        absolute_deg = (
            unwrapped
            * DEG_PER_COUNT
        )

        phase_count = (
            raw
            % COUNTS_PER_REV
        )

        phase_deg = (
            phase_count
            * DEG_PER_COUNT
        )

        revolution_index = int(
            abs(unwrapped)
            // COUNTS_PER_REV
        )

        samples.append({
            "time": elapsed,
            "raw": raw,
            "unwrapped": unwrapped,
            "delta": delta,
            "dt": dt,
            "cps": cps,
            "deg_s": deg_s,
            "phase_count": phase_count,
            "phase_deg": phase_deg,
            "revolution": revolution_index,
        })

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        if abs(revolutions) >= next_progress:

            print(
                f"Progress: "
                f"{revolutions:+.3f} rev  "
                f"{absolute_deg:+.1f} deg  "
                f"raw=0x{raw:06X}"
            )

            next_progress += 0.25

        # ----------------------------------------------------
        # Finished?
        # ----------------------------------------------------

        if abs(unwrapped) >= (
            REVOLUTIONS
            * COUNTS_PER_REV
        ):

            print()
            print(
                "Нужное число оборотов достигнуто."
            )

            break

        # ----------------------------------------------------
        # Stall detection
        # ----------------------------------------------------

        if (
            now - last_movement_time
            > STALL_TIMEOUT
        ):

            print()
            print(
                "!!! STALL DETECTED !!!"
            )

            print(
                f"Позиция не менялась "
                f"{STALL_TIMEOUT:.1f} секунд."
            )

            break

        # ----------------------------------------------------
        # Safety timeout
        # ----------------------------------------------------

        if elapsed > MAX_TEST_TIME:

            print()
            print(
                "MAX_TEST_TIME достигнут."
            )

            break

        raw_previous = raw
        previous_time = now

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    stop_alt()

    stop_time = time.perf_counter()

    time.sleep(1.0)

    final_raw = wait_alt_position()

    coast = signed_delta(
        raw,
        final_raw
    )

    print()
    print(
        f"STOP coast     : "
        f"{coast} counts"
    )

    print(
        f"                 "
        f"{counts_to_deg(coast):.6f} deg"
    )

    print(
        f"Final raw      : "
        f"0x{final_raw:06X}"
    )

    return samples


# ============================================================
# SAVE RAW CSV
# ============================================================

def save_raw_csv(samples):

    with open(
        RAW_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "time_s",
            "raw_hex",
            "raw",
            "unwrapped",
            "delta",
            "dt_s",
            "counts_s",
            "deg_s",
            "phase_count",
            "phase_deg",
            "revolution",
        ])

        for s in samples:

            writer.writerow([
                f"{s['time']:.6f}",
                f"0x{s['raw']:06X}",
                s["raw"],
                s["unwrapped"],
                s["delta"],
                f"{s['dt']:.6f}",
                f"{s['cps']:.6f}",
                f"{s['deg_s']:.9f}",
                s["phase_count"],
                f"{s['phase_deg']:.6f}",
                s["revolution"],
            ])

    print(
        f"RAW CSV        : {RAW_CSV}"
    )


# ============================================================
# POSITION BIN ANALYSIS
# ============================================================

def analyze_bins(samples):

    counts_per_bin = (
        COUNTS_PER_REV
        / BINS_PER_REV
    )

    # bin -> revolution -> list of speeds
    data = defaultdict(
        lambda: defaultdict(list)
    )

    for s in samples:

        # Игнорируем разгон
        if s["time"] < SETTLE_TIME:
            continue

        # Нулевые значения НЕ выбрасываем.
        #
        # Именно они могут быть признаком подклинивания.

        bin_index = int(
            s["phase_count"]
            / counts_per_bin
        )

        if bin_index >= BINS_PER_REV:
            bin_index = BINS_PER_REV - 1

        rev = s["revolution"]

        data[bin_index][rev].append(
            s["cps"]
        )

    results = []

    for bin_index in range(BINS_PER_REV):

        rev_data = data.get(
            bin_index,
            {}
        )

        per_rev = []

        for rev, velocities in rev_data.items():

            if not velocities:
                continue

            # Средняя скорость внутри данной угловой
            # области конкретного оборота.

            avg = statistics.mean(
                velocities
            )

            per_rev.append(avg)

        if per_rev:

            mean_speed = statistics.mean(
                per_rev
            )

            median_speed = statistics.median(
                per_rev
            )

            minimum = min(per_rev)
            maximum = max(per_rev)

            rev_count = len(per_rev)

        else:

            mean_speed = 0.0
            median_speed = 0.0
            minimum = 0.0
            maximum = 0.0
            rev_count = 0

        start_deg = (
            bin_index
            * 360
            / BINS_PER_REV
        )

        center_deg = (
            start_deg
            + 180 / BINS_PER_REV
        )

        results.append({
            "bin": bin_index,
            "center_deg": center_deg,
            "mean_cps": mean_speed,
            "median_cps": median_speed,
            "min_cps": minimum,
            "max_cps": maximum,
            "revolutions": rev_count,
        })

    return results


# ============================================================
# SAVE BIN CSV
# ============================================================

def save_bins_csv(results):

    with open(
        BINS_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "bin",
            "center_deg",
            "mean_counts_s",
            "median_counts_s",
            "min_counts_s",
            "max_counts_s",
            "revolutions",
        ])

        for r in results:

            writer.writerow([
                r["bin"],
                f"{r['center_deg']:.3f}",
                f"{r['mean_cps']:.6f}",
                f"{r['median_cps']:.6f}",
                f"{r['min_cps']:.6f}",
                f"{r['max_cps']:.6f}",
                r["revolutions"],
            ])

    print(
        f"BINS CSV       : {BINS_CSV}"
    )


# ============================================================
# REPORT SLOWEST AREAS
# ============================================================

def report_slowest(results):

    valid = [
        r
        for r in results
        if r["revolutions"] >= 3
    ]

    if not valid:

        print()
        print(
            "Недостаточно данных для анализа."
        )

        return

    # Используем median между оборотами.
    # Ищем самые медленные участки.

    valid.sort(
        key=lambda r: r["median_cps"]
    )

    print()
    print("=" * 72)

    print(
        "SLOWEST REPEATABLE POSITION BINS"
    )

    print("=" * 72)

    print(
        "Показываются 20 самых медленных "
        "угловых областей."
    )

    print()

    print(
        f"{'POS deg':>9} | "
        f"{'mean c/s':>11} | "
        f"{'median c/s':>12} | "
        f"{'min c/s':>10} | "
        f"{'revs':>4}"
    )

    print("-" * 65)

    for r in valid[:20]:

        print(
            f"{r['center_deg']:>9.3f} | "
            f"{r['mean_cps']:>11.2f} | "
            f"{r['median_cps']:>12.2f} | "
            f"{r['min_cps']:>10.2f} | "
            f"{r['revolutions']:>4}"
        )


# ============================================================
# MAIN
# ============================================================

try:

    ser.reset_input_buffer()

    samples = run_test()

    print()
    print(
        f"Samples        : {len(samples)}"
    )

    save_raw_csv(
        samples
    )

    results = analyze_bins(
        samples
    )

    save_bins_csv(
        results
    )

    report_slowest(
        results
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
        "ALT STOP. COM закрыт."
    )