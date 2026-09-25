import serial
import time
from collections import deque

PORT = "COM5"       # <-- поставь свой COM
BAUD = 4800

# Подтверждённые команды NXW436 Rev A
AZ_POS  = bytes.fromhex("01")
AZ_MOVE = bytes.fromhex("06 00 00 F5")
AZ_STOP = bytes.fromhex("06 00 00 00")

# Сколько последних измерений хранить
HISTORY_SIZE = 20

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


def get_az_position():
    """
    01 -> 3-byte AZ position.
    Возвращает (raw_bytes, integer) или (None, None).
    """
    ser.reset_input_buffer()

    send_command(AZ_POS)

    raw = read_exact(3)

    if raw is None:
        return None, None

    value = int.from_bytes(raw, byteorder="big")

    return raw, value


def stop_az():
    send_command(AZ_STOP)
    time.sleep(0.05)


print("NXW436 AZ rollover hunter")
print("-------------------------")

try:
    # Проверяем связь ДО запуска мотора
    raw, pos = get_az_position()

    if raw is None:
        raise RuntimeError("Нет ответа на 01. Мотор НЕ запускаю.")

    print(
        f"START: {raw.hex(' ').upper()} "
        f"= 0x{pos:06X} = {pos}"
    )

    input("\nEnter -> запустить AZ на скорости F5...")

    # На всякий пожарный очищаем RX
    ser.reset_input_buffer()

    send_command(AZ_MOVE)

    print("\nAZ запущен.")
    print("Ищу rollover...")
    print("Ctrl+C = аварийный STOP\n")

    previous = None
    sample = 0

    while True:
        raw, pos = get_az_position()

        if raw is None:
            print("Нет ответа на 01")
            continue

        sample += 1
        timestamp = time.perf_counter()

        history.append(
            (sample, timestamp, raw, pos)
        )

        print(
            f"{sample:05d}  "
            f"{raw.hex(' ').upper()}  "
            f"0x{pos:06X}  "
            f"{pos:8d}"
        )

        # Ищем именно большой скачок вниз.
        # Обычное мелкое движение назад rollover не вызовет.
        if previous is not None:
            delta = pos - previous

            if delta < -500_000:

                print("\n*** ROLLOVER DETECTED ***")

                stop_az()

                print("AZ STOP отправлен.\n")

                # Финальное положение после остановки
                time.sleep(0.1)
                final_raw, final_pos = get_az_position()

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
                    print(
                        "\nFINAL: "
                        f"{final_raw.hex(' ').upper()} "
                        f"= 0x{final_pos:06X} "
                        f"= {final_pos}"
                    )

                break

        previous = pos

        # Не долбим PIC совсем уж без передышки
        time.sleep(0.02)


except KeyboardInterrupt:
    print("\nCtrl+C!")

    try:
        stop_az()
        print("AZ STOP отправлен.")
    except Exception:
        pass

except Exception as e:
    print(f"\nERROR: {e}")

    try:
        stop_az()
        print("AZ STOP отправлен.")
    except Exception:
        pass

finally:
    ser.close()
    print("\nCOM закрыт.")