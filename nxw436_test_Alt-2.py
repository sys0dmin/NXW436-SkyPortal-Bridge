import serial
import time
from collections import deque

# ============================================================
# Celestron NXW436 Rev A
# ALT precise rollover hunter v2
#
# FAST (speed 6) -> 0x101000
# STOP + settle
# SLOW (speed 1)
# -> precise rollover capture
# ============================================================

PORT = "COM5"       # поменяй, если COM другой
BAUD = 4800

# ------------------------------------------------------------
# NXW436 Rev A — подтверждённые команды ALT
# ------------------------------------------------------------

ALT_POS = bytes.fromhex("15")

# ALT UP, speed 6
ALT_FAST = bytes.fromhex("1A 00 39 78")

# ALT UP, speed 1
ALT_SLOW = bytes.fromhex("1A 00 00 F5")

# ALT STOP
ALT_STOP = bytes.fromhex("1A 00 00 00")

# ------------------------------------------------------------
# Настройки эксперимента
# ------------------------------------------------------------

# Переключаемся на медленный ход сильно заранее
SLOWDOWN_POSITION = 0x101000

# Большой скачок вниз = rollover
ROLLOVER_THRESHOLD = -500_000

# Последние N измерений
HISTORY_SIZE = 200

# После FAST -> STOP ждём полной остановки
SETTLE_TIME = 0.5

# FAST можно опрашивать не слишком агрессивно
FAST_POLL_DELAY = 0.02

# На SLOW дополнительная задержка практически не нужна.
# Сам UART 4800 бод всё равно ограничивает частоту.
SLOW_POLL_DELAY = 0.0

# Таймаут ответа на 15
READ_TIMEOUT = 0.15


# ============================================================
# Serial
# ============================================================

ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.02,
)

history = deque(maxlen=HISTORY_SIZE)


# ============================================================
# Helpers
# ============================================================

def read_exact(n, timeout=READ_TIMEOUT):
    """
    Читаем ровно n байт.
    Возвращаем bytes либо None.
    """

    data = bytearray()
    deadline = time.perf_counter() + timeout

    while len(data) < n:

        if time.perf_counter() >= deadline:
            return None

        chunk = ser.read(n - len(data))

        if chunk:
            data.extend(chunk)

    return bytes(data)


def send_command(data):
    ser.write(data)
    ser.flush()


def get_alt_position():
    """
    15 -> 3-byte ALT position, big-endian.

    ВАЖНО:
    input buffer здесь специально НЕ сбрасываем.
    """

    send_command(ALT_POS)

    raw = read_exact(3)

    if raw is None:
        return None, None

    pos = int.from_bytes(
        raw,
        byteorder="big"
    )

    return raw, pos


def stop_alt():
    send_command(ALT_STOP)
    ser.flush()


def print_position(prefix, raw, pos):

    print(
        f"{prefix}"
        f"{raw.hex(' ').upper()}  "
        f"0x{pos:06X}  "
        f"{pos:8d}"
    )


# ============================================================
# MAIN
# ============================================================

print()
print("NXW436 ALT PRECISE ROLLOVER HUNTER v2")
print("======================================")
print()
print("ALT POS       : 15")
print("FAST          : 1A 00 39 78   speed 6")
print("SLOW          : 1A 00 00 F5   speed 1")
print("STOP          : 1A 00 00 00")
print()
print(
    f"FAST -> SLOW  : 0x{SLOWDOWN_POSITION:06X}"
)
print(
    f"SETTLE        : {SETTLE_TIME:.1f} s"
)
print()


try:

    # --------------------------------------------------------
    # Перед началом очищаем старые байты ОДИН раз
    # --------------------------------------------------------

    ser.reset_input_buffer()

    # --------------------------------------------------------
    # Проверяем связь
    # --------------------------------------------------------

    raw, pos = get_alt_position()

    if raw is None:

        raise RuntimeError(
            "Нет ответа на 15. "
            "Мотор НЕ запускаю."
        )

    print_position(
        "START: ",
        raw,
        pos
    )

    print()

    input(
        "Enter -> начать эксперимент..."
    )

    # --------------------------------------------------------
    # FAST
    # --------------------------------------------------------

    print()
    print("Запускаю ALT speed 6...")
    print("Ctrl+C = аварийный STOP")
    print()

    send_command(ALT_FAST)

    mode = "FAST"

    previous = None
    sample = 0

    # ========================================================
    # Main polling loop
    # ========================================================

    while True:

        raw, pos = get_alt_position()

        if raw is None:

            print("Нет ответа на 15")

            time.sleep(0.02)

            continue

        sample += 1

        history.append(
            (
                sample,
                mode,
                raw,
                pos
            )
        )

        print(
            f"{sample:05d}  "
            f"{mode:6s}  "
            f"{raw.hex(' ').upper()}  "
            f"0x{pos:06X}  "
            f"{pos:8d}"
        )

        # ====================================================
        # ROLLOVER
        # ====================================================

        if previous is not None:

            delta = pos - previous

            if delta < ROLLOVER_THRESHOLD:

                print()
                print("************************************")
                print("*** ALT ROLLOVER DETECTED !!!    ***")
                print("************************************")
                print()

                print(
                    f"BEFORE : "
                    f"0x{previous:06X} "
                    f"({previous})"
                )

                print(
                    f"AFTER  : "
                    f"0x{pos:06X} "
                    f"({pos})"
                )

                print(
                    f"RAW DELTA : {delta}"
                )

                # --------------------------------------------
                # Немедленно стопаем
                # --------------------------------------------

                stop_alt()

                print()
                print("ALT STOP отправлен.")

                # Ждём остановки
                time.sleep(0.3)

                final_raw, final_pos = get_alt_position()

                # --------------------------------------------
                # История
                # --------------------------------------------

                print()
                print("Последние измерения:")
                print(
                    "---------------------------------------------"
                )

                for n, m, r, p in history:

                    print(
                        f"{n:05d}  "
                        f"{m:6s}  "
                        f"{r.hex(' ').upper()}  "
                        f"0x{p:06X}  "
                        f"{p:8d}"
                    )

                print(
                    "---------------------------------------------"
                )

                if final_raw is not None:

                    print()
                    print_position(
                        "FINAL : ",
                        final_raw,
                        final_pos
                    )

                break

        # ====================================================
        # FAST -> STOP -> SETTLE -> SLOW
        # ====================================================

        if (
            mode == "FAST"
            and pos >= SLOWDOWN_POSITION
        ):

            print()
            print("============================================")
            print(
                f"Достигли 0x{pos:06X}"
            )
            print("Останавливаю FAST...")
            print("============================================")

            # --------------------------------------------
            # STOP
            # --------------------------------------------

            stop_alt()

            print(
                f"Жду {SETTLE_TIME:.1f} сек..."
            )

            time.sleep(SETTLE_TIME)

            # --------------------------------------------
            # После полной остановки снова читаем позицию
            # --------------------------------------------

            settle_raw, settle_pos = get_alt_position()

            if settle_raw is None:

                raise RuntimeError(
                    "После STOP не удалось "
                    "прочитать ALT position."
                )

            print()
            print_position(
                "SETTLED: ",
                settle_raw,
                settle_pos
            )

            # --------------------------------------------
            # Проверяем, не умудрились ли уже перелететь
            # rollover
            # --------------------------------------------

            if settle_pos < 0x100000:

                print()
                print(
                    "ВНИМАНИЕ: rollover произошёл "
                    "ещё во время торможения."
                )

                stop_alt()

                break

            # --------------------------------------------
            # Запускаем SLOW
            # --------------------------------------------

            print()
            print("Запускаю ALT speed 1...")
            print()

            send_command(ALT_SLOW)

            mode = "SLOW"

            # ВАЖНО:
            # previous ставим именно в фактическую
            # позицию после остановки.
            previous = settle_pos

            # И сразу идём на следующий опрос,
            # не ждём лишних 50/100 мс.
            continue

        # ====================================================
        # Следующая итерация
        # ====================================================

        previous = pos

        if mode == "FAST":

            time.sleep(
                FAST_POLL_DELAY
            )

        else:

            if SLOW_POLL_DELAY > 0:

                time.sleep(
                    SLOW_POLL_DELAY
                )


# ============================================================
# Ctrl+C
# ============================================================

except KeyboardInterrupt:

    print()
    print()
    print("Ctrl+C — аварийный STOP!")

    try:

        stop_alt()

        print(
            "ALT STOP отправлен."
        )

    except Exception as e:

        print(
            f"Ошибка STOP: {e}"
        )


# ============================================================
# ERROR
# ============================================================

except Exception as e:

    print()
    print(
        f"ERROR: {e}"
    )

    try:

        stop_alt()

        print(
            "ALT STOP отправлен."
        )

    except Exception:

        pass


# ============================================================
# FINALLY
# ============================================================

finally:

    # На всякий случай второй STOP.
    # Старый Celestron переживёт лишние четыре байта.
    # Редуктор, возможно, лишний полный оборот — уже нет.

    try:

        stop_alt()

        time.sleep(0.05)

    except Exception:

        pass

    ser.close()

    print()
    print("COM закрыт.")
    