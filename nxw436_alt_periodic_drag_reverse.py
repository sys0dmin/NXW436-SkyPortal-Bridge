import csv
import math
import statistics
import sys
import time

import serial


# ============================================================
# CONFIG
# ============================================================

COM_PORT = "COM5"          # <-- поменяй при необходимости
BAUDRATE = 4800

SPEED = 8
REVOLUTIONS = 5

# Установленное экспериментально число encoder counts на оборот ALT
COUNTS_PER_REV = 1_059_328

# Разбивка оборота для поиска повторяемых механических тормозящих зон
BINS_PER_REV = 360

# Частота опроса позиции
POLL_INTERVAL = 0.020      # 20 ms

# Защита от бесконечного вращения
MAX_TEST_TIME = 20 * 60    # 20 минут

# После STOP наблюдаем выбег
COAST_TIME = 2.0
COAST_POLL_INTERVAL = 0.020

RAW_CSV = "nxw436_alt_periodic_drag_reverse.csv"
BINS_CSV = "nxw436_alt_periodic_drag_reverse_bins.csv"


# ============================================================
# NXW436 OLD GT PROTOCOL
# ============================================================

# ALT position query
CMD_GET_ALT_POSITION = bytes([0x15])

# ALT reverse movement.
#
# Forward в предыдущем тесте:
#   1A 00 E5 E3
#
# Reverse:
CMD_ALT_REVERSE = bytes([
    0x1B,
    0x00,
    0xE5,
    0xE3,
])

# Скорость 0 = STOP.
#
# Для ALT направление оставляем reverse (1B),
# но magnitude = 0.
CMD_ALT_STOP = bytes([
    0x1B,
    0x00,
    0x00,
    0x00,
])


# ============================================================
# SERIAL HELPERS
# ============================================================

def open_serial():
    return serial.Serial(
        port=COM_PORT,
        baudrate=BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.25,
        write_timeout=0.25,
    )


def flush_input(ser):
    ser.reset_input_buffer()


def send_command(ser, data):
    ser.write(data)
    ser.flush()


def read_exact(ser, count):
    data = ser.read(count)

    if len(data) != count:
        raise TimeoutError(
            f"Ожидалось {count} байт, получено {len(data)}: "
            f"{data.hex(' ').upper()}"
        )

    return data


def get_alt_position(ser):
    """
    NXW436:
        TX: 15
        RX: XX XX XX

    Ответ — 24-bit position.
    """

    send_command(
        ser,
        CMD_GET_ALT_POSITION,
    )

    data = read_exact(
        ser,
        3,
    )

    value = (
        (data[0] << 16)
        | (data[1] << 8)
        | data[2]
    )

    return value


def stop_alt(ser):
    send_command(
        ser,
        CMD_ALT_STOP,
    )


# ============================================================
# POSITION MATH
# ============================================================

POSITION_MODULO = COUNTS_PER_REV


def signed_delta(new, old):
    """
    Возвращает signed изменение позиции с учётом wrap-around.
    """

    delta = new - old

    half = POSITION_MODULO // 2

    if delta > half:
        delta -= POSITION_MODULO

    elif delta < -half:
        delta += POSITION_MODULO

    return delta


def counts_to_degrees(counts):
    return (
        counts
        * 360.0
        / COUNTS_PER_REV
    )


def position_to_degrees(position):
    return (
        (position % COUNTS_PER_REV)
        * 360.0
        / COUNTS_PER_REV
    )


# ============================================================
# MAIN TEST
# ============================================================

def main():

    print()
    print("NXW436 ALT PERIODIC DRAG TEST — REVERSE")
    print("=======================================")
    print()

    print(f"Speed          : {SPEED}")
    print(f"Revolutions    : {REVOLUTIONS}")
    print(f"Counts/rev     : {COUNTS_PER_REV}")
    print(f"Bins/rev       : {BINS_PER_REV}")
    print(
        f"Bin size       : "
        f"{360.0 / BINS_PER_REV:.3f} deg"
    )
    print()

    ser = open_serial()

    samples = []

    try:

        time.sleep(0.2)

        start_raw = get_alt_position(ser)

        print(
            f"Start position : "
            f"0x{start_raw:06X} "
            f"({start_raw})"
        )

        print()
        print(
            f"ALT сделает примерно "
            f"{REVOLUTIONS} полных оборотов В ОБРАТНУЮ СТОРОНУ."
        )
        print("Труба должна быть снята.")
        print("Провода убрать из зоны вращения.")
        print("Ctrl+C = STOP.")
        print()

        input("Enter -> начать...")

        print()
        print(
            "CMD            : "
            + CMD_ALT_REVERSE.hex(" ").upper()
        )
        print()

        flush_input(ser)

        # ----------------------------------------------------
        # START MOTOR
        # ----------------------------------------------------

        send_command(
            ser,
            CMD_ALT_REVERSE,
        )

        print("RUNNING...")
        print()

        test_start = time.perf_counter()

        previous_time = test_start
        previous_raw = start_raw

        cumulative_counts = 0

        # Reverse должен уменьшать координату.
        # Для удобства прогресс отображаем положительным.
        target_counts = (
            REVOLUTIONS
            * COUNTS_PER_REV
        )

        next_progress = 0.25

        while True:

            now = time.perf_counter()

            if (
                now - test_start
                > MAX_TEST_TIME
            ):
                print()
                print(
                    "MAX_TEST_TIME достигнут."
                )
                break

            try:
                raw = get_alt_position(ser)

            except TimeoutError:
                time.sleep(POLL_INTERVAL)
                continue

            sample_time = time.perf_counter()

            dt = (
                sample_time
                - previous_time
            )

            delta = signed_delta(
                raw,
                previous_raw,
            )

            # Для reverse ожидаем отрицательный delta.
            # Переводим его в положительное пройденное расстояние.
            travel_delta = -delta

            # Если вдруг направление оказалось противоположным,
            # не скрываем это.
            if dt > 0:
                speed_cps = (
                    travel_delta
                    / dt
                )
            else:
                speed_cps = 0.0

            cumulative_counts += travel_delta

            cumulative_revs = (
                cumulative_counts
                / COUNTS_PER_REV
            )

            cumulative_deg = (
                cumulative_revs
                * 360.0
            )

            position_deg = (
                position_to_degrees(raw)
            )

            elapsed = (
                sample_time
                - test_start
            )

            samples.append({
                "time_s": elapsed,
                "raw_position": raw,
                "raw_hex": f"{raw:06X}",
                "delta_counts": delta,
                "travel_delta": travel_delta,
                "speed_counts_s": speed_cps,
                "position_deg": position_deg,
                "cumulative_counts": cumulative_counts,
                "cumulative_revs": cumulative_revs,
                "cumulative_deg": cumulative_deg,
            })

            previous_raw = raw
            previous_time = sample_time

            # ------------------------------------------------
            # PROGRESS EVERY 0.25 REV
            # ------------------------------------------------

            if (
                cumulative_revs
                >= next_progress
            ):

                print(
                    f"Progress: "
                    f"-{next_progress:.3f} rev  "
                    f"-{next_progress * 360:.1f} deg  "
                    f"raw=0x{raw:06X}"
                )

                next_progress += 0.25

            # ------------------------------------------------
            # DONE
            # ------------------------------------------------

            if (
                cumulative_counts
                >= target_counts
            ):
                print()
                print(
                    "Нужное число оборотов достигнуто."
                )
                break

            time.sleep(
                POLL_INTERVAL
            )

    except KeyboardInterrupt:

        print()
        print()
        print("Ctrl+C.")

    finally:

        # ====================================================
        # STOP
        # ====================================================

        try:

            before_stop = get_alt_position(ser)

            stop_alt(ser)

            stop_time = time.perf_counter()

            last_raw = before_stop

            # Дадим механике остановиться
            while (
                time.perf_counter()
                - stop_time
                < COAST_TIME
            ):

                time.sleep(
                    COAST_POLL_INTERVAL
                )

                try:
                    last_raw = get_alt_position(ser)

                except TimeoutError:
                    pass

            # Reverse: движение уменьшает координату
            coast_signed = signed_delta(
                last_raw,
                before_stop,
            )

            coast_counts = abs(
                coast_signed
            )

            coast_deg = counts_to_degrees(
                coast_counts
            )

            print()
            print(
                f"STOP coast     : "
                f"{coast_counts} counts"
            )

            print(
                f"                 "
                f"{coast_deg:.6f} deg"
            )

            print(
                f"Final raw      : "
                f"0x{last_raw:06X}"
            )

        except Exception as e:

            print()
            print(
                "Ошибка при STOP:",
                e,
            )

        try:
            stop_alt(ser)

        except Exception:
            pass

        ser.close()

    # ========================================================
    # RAW CSV
    # ========================================================

    if not samples:

        print()
        print("Нет данных.")
        return

    with open(
        RAW_CSV,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        fieldnames = list(
            samples[0].keys()
        )

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(samples)

    # ========================================================
    # POSITION BINS
    # ========================================================

    bin_size = (
        360.0
        / BINS_PER_REV
    )

    bins = [
        []
        for _ in range(BINS_PER_REV)
    ]

    # Первый кусок после запуска может содержать acceleration.
    # Для поиска periodic drag всё равно сохраняем всё,
    # но экстремально маленькие/отрицательные скорости исключаем.
    for sample in samples:

        speed = sample[
            "speed_counts_s"
        ]

        if speed <= 0:
            continue

        pos_deg = sample[
            "position_deg"
        ]

        index = int(
            pos_deg
            / bin_size
        )

        index %= BINS_PER_REV

        bins[index].append(
            speed
        )

    bin_rows = []

    for i, values in enumerate(bins):

        if not values:
            continue

        center_deg = (
            i * bin_size
            + bin_size / 2
        )

        mean_speed = statistics.mean(
            values
        )

        median_speed = statistics.median(
            values
        )

        min_speed = min(
            values
        )

        max_speed = max(
            values
        )

        # Приблизительное число проходов через bin.
        #
        # Точнее считать по отдельным revolution IDs,
        # но для нашего теста максимум = REVOLUTIONS.
        revs_seen = min(
            REVOLUTIONS,
            max(
                1,
                round(
                    len(values)
                    * BINS_PER_REV
                    / max(1, len(samples))
                    * REVOLUTIONS
                ),
            ),
        )

        bin_rows.append({
            "position_deg": center_deg,
            "mean_counts_s": mean_speed,
            "median_counts_s": median_speed,
            "min_counts_s": min_speed,
            "max_counts_s": max_speed,
            "samples": len(values),
            "revs": revs_seen,
        })

    with open(
        BINS_CSV,
        "w",
        newline="",
        encoding="utf-8",
    ) as f:

        fieldnames = [
            "position_deg",
            "mean_counts_s",
            "median_counts_s",
            "min_counts_s",
            "max_counts_s",
            "samples",
            "revs",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(
            bin_rows
        )

    # ========================================================
    # RESULTS
    # ========================================================

    print()
    print(
        f"Samples        : "
        f"{len(samples)}"
    )

    print(
        f"RAW CSV        : "
        f"{RAW_CSV}"
    )

    print(
        f"BINS CSV       : "
        f"{BINS_CSV}"
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

    slowest = sorted(
        bin_rows,
        key=lambda x: x[
            "median_counts_s"
        ],
    )[:20]

    for row in slowest:

        print(
            f"{row['position_deg']:9.3f} | "
            f"{row['mean_counts_s']:11.2f} | "
            f"{row['median_counts_s']:12.2f} | "
            f"{row['min_counts_s']:10.2f} | "
            f"{row['revs']:4d}"
        )

    print()
    print("ALT REVERSE STOP. COM закрыт.")


if __name__ == "__main__":
    main()
