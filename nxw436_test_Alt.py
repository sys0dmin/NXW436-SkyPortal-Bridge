import serial
import time
from collections import deque

# ============================================================
# Celestron NXW436 Rev A
# ALT rollover hunter
# ============================================================

PORT = "COM5"       # <-- поменяй только если у тебя другой COM
BAUD = 4800

# Подтверждённые команды NXW436 Rev A
ALT_POS  = bytes.fromhex("15")

# ALT движение, скорость 6
ALT_MOVE = bytes.fromhex("1A 00 39 78")

# ALT STOP
ALT_STOP = bytes.fromhex("1A 00 00 00")

HISTORY_SIZE = 25

ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.05,
)

history = deque(maxlen=HISTORY_SIZE)


def read_exact(n, timeout=0.15):
    """Прочитать ровно n байт или вернуть None."""

    data = bytearray()
    deadline = time.perf_counter() + timeout

    while len(data) < n and time.perf_counter() < deadline:
        chunk = ser.read(n - len(data))

        if chunk:
            data.extend(chunk)

    if len(data) != n:
        return None

    return bytes(data)


def send_command(data):
    ser.write(data)
    ser.flush()


def get_alt_position():
    """
    15 -> 3-byte ALT position

    Возвращает:
        (raw_bytes, integer)

    либо:
        (None, None)
    """

    ser.reset_input_buffer()

    send_command(ALT_POS)

    raw = read_exact(3)

    if raw is None:
        return None, None

    value = int.from_bytes(
        raw,
        byteorder="big"
    )

    return raw, value


def stop_alt():
    """Остановить ALT."""

    send_command(ALT_STOP)
    time.sleep(0.05)


print()
print("NXW436 ALT rollover hunter")
print("==========================")
print()
print("ALT POS : 15")
print("ALT MOVE: 1A 00 39 78")
print("ALT STOP: 1A 00 00 00")
print()


try:

    # --------------------------------------------------------
    # Проверяем связь до запуска двигателя
    # --------------------------------------------------------

    raw, pos = get_alt_position()

    if raw is None:
        raise RuntimeError(
            "Нет ответа на 15. "
            "Мотор НЕ запускаю."
        )

    print(
        f"START: "
        f"{raw.hex(' ').upper()} "
        f"= 0x{pos:06X} "
        f"= {pos}"
    )

    print()
    input(
        "Enter -> запустить ALT "
        "на скорости 6..."
    )

    # --------------------------------------------------------
    # Запуск
    # --------------------------------------------------------

    ser.reset_input_buffer()

    send_command(ALT_MOVE)

    print()
    print("ALT запущен.")
    print("Ищу rollover...")
    print("Ctrl+C = аварийный STOP")
    print()

    previous = None
    sample = 0

    while True:

        raw, pos = get_alt_position()

        if raw is None:
            print("Нет ответа на 15")
            continue

        sample += 1
        timestamp = time.perf_counter()

        history.append(
            (
                sample,
                timestamp,
                raw,
                pos
            )
        )

        print(
            f"{sample:05d}  "
            f"{raw.hex(' ').upper()}  "
            f"0x{pos:06X}  "
            f"{pos:8d}"
        )

        # ----------------------------------------------------
        # Ищем rollover:
        #
        # большое значение внезапно стало маленьким.
        #
        # Порог -500000 выбран специально большим,
        # чтобы обычное движение/дребезг не считались rollover.
        # ----------------------------------------------------

        if previous is not None:

            delta = pos - previous

            if delta < -500_000:

                print()
                print("*** ALT ROLLOVER DETECTED ***")
                print()

                stop_alt()

                print("ALT STOP отправлен.")

                # Даём механике окончательно остановиться
                time.sleep(0.15)

                final_raw, final_pos = get_alt_position()

                print()
                print("Последние измерения:")
                print("--------------------")

                for n, ts, r, p in history:

                    print(
                        f"{n:05d}  "
                        f"{r.hex(' ').upper()}  "
                        f"0x{p:06X}  "
                        f"{p:8d}"
                    )

                if final_raw is not None:

                    print()
                    print(
                        "FINAL: "
                        f"{final_raw.hex(' ').upper()} "
                        f"= 0x{final_pos:06X} "
                        f"= {final_pos}"
                    )

                else:

                    print()
                    print(
                        "FINAL: "
                        "не удалось прочитать положение"
                    )

                break

        previous = pos

        # Небольшая пауза между запросами
        time.sleep(0.02)


except KeyboardInterrupt:

    print()
    print("Ctrl+C!")

    try:
        stop_alt()
        print("ALT STOP отправлен.")
    except Exception:
        pass


except Exception as e:

    print()
    print(f"ERROR: {e}")

    try:
        stop_alt()
        print("ALT STOP отправлен.")
    except Exception:
        pass


finally:

    ser.close()

    print()
    print("COM закрыт.")