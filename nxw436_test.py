import serial
import time

PORT = "COM5"       # <-- свой COM
BAUD = 4800

ser = serial.Serial(
    PORT,
    BAUD,
    bytesize=8,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=0.01
)

def drain():
    """Выкинуть старый мусор из RX перед экспериментом."""
    time.sleep(0.05)
    ser.reset_input_buffer()


def exchange(data: bytes, quiet=0.100, max_wait=1.0):
    """
    Отправляет raw bytes и собирает всё пришедшее,
    пока UART не молчит quiet секунд.
    """
    drain()

    print(f"\nTX [{len(data)}]: {data.hex(' ').upper()}")

    t0 = time.perf_counter()
    ser.write(data)
    ser.flush()

    received = bytearray()
    last_rx = None

    while time.perf_counter() - t0 < max_wait:
        chunk = ser.read(ser.in_waiting or 1)

        if chunk:
            now = time.perf_counter()
            received.extend(chunk)
            last_rx = now

            print(
                f"RX +{(now-t0)*1000:8.2f} ms "
                f"[{len(chunk)}]: {chunk.hex(' ').upper()}"
            )

        elif last_rx is not None:
            if time.perf_counter() - last_rx >= quiet:
                break

    elapsed = (time.perf_counter() - t0) * 1000

    print(
        f"RX TOTAL [{len(received)}]: "
        f"{received.hex(' ').upper() if received else '<none>'}"
    )
    print(f"Elapsed: {elapsed:.1f} ms")

    return bytes(received)


try:
    while True:
        s = input("\nHEX command (q = quit): ").strip()

        if s.lower() == "q":
            break

        try:
            data = bytes.fromhex(s)
        except ValueError:
            print("Неверный HEX. Например: 01 или 1A 00 00 00")
            continue

        exchange(data)

finally:
    ser.close()