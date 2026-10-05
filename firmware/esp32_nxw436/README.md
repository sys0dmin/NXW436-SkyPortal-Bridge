# ESP32 NXW436 Firmware

Milestone 1 содержит host-buildable C++ semantic core. Milestone 2 добавляет
ESP32 fake-mount Direct Connect frontend. J1 transport и motor commands всё ещё
отсутствуют.

## Golden Fixtures

Обновить fixtures текущим Python reference:

```powershell
python .\firmware\esp32_nxw436\tools\generate_golden.py
```

Provenance и normalization описаны в `test/fixtures/README.md`.

## Native Build

Требуются PlatformIO и native GCC/Clang в `PATH`.

```powershell
python -m platformio run -d .\firmware\esp32_nxw436 -e native
python -m platformio test -d .\firmware\esp32_nxw436 -e native
```

На Windows в текущем development environment WinLibs GCC установлен через
`winget`; после установки может потребоваться новый shell либо ручное добавление
`mingw64\bin` в `PATH`.

## ESP32 Fake-Mount Build

```powershell
python -m platformio run -d .\firmware\esp32_nxw436 -e esp32dev
```

Прошивка предназначена для generic `esp32dev` и использует только USB/debug
Serial и Wi-Fi. NXW436/J1 подключать не нужно и нельзя в этом milestone.

Прошивка создаёт открытую сеть:

```text
SSID: Celestron-<last 3 MAC bytes>
IP:   1.2.3.4/28
TCP:  2000
UDP advertisement: 55555
```

Прошивка:

- принимает одного SkyPortal/SkySafari client; connection сохраняется до real
  FIN/reset либо общего 15-second RX idle timeout;
- использует parser state на connection;
- увеличивает connection epoch после reconnect;
- хранит RX buffer и parser-event batch в fixed arrays;
- выполняет fake manual movement и position changes;
- выполняет deterministic 6-second fake FAST/SLOW GoTo;
- отвечает `SLEW_DONE=00/FF`;
- обрабатывает `0x24/00` cancellation;
- не содержит `HardwareSerial`/J1/NXW436 motor code.

Активный runtime profile `AzmAltOnly` объявляет только AZM/ALT mount interface.
Source-backed HBG optional B4/B9/12/B5 emulator handlers остаются в semantic
core/fixtures для parity, но не включены в ESP runtime до отдельного A/B
evidence.

## Upload И Serial Monitor

ESP32 должен быть отключён от NXW436 и питаться только через USB.

```powershell
Set-Location .\firmware\esp32_nxw436
python -m platformio run -e esp32dev -t upload --upload-port COMx
python -m platformio device monitor -p COMx -b 115200
```

Serial diagnostics bounded по frame length и содержат Wi-Fi state, connection
epoch, AUX RX/TX, parser errors, semantic status, fake GoTo transitions,
cancellation, stale response drop и overflow.

## Scope

Реализованы:

- modular position math;
- semantic mount types/backend interface;
- deterministic fake backend;
- AUX frame codec/checksum;
- stateful stream parser;
- AUX coordinate adapter;
- virtual MC configuration;
- startup/manual/GoTo/SLEW_DONE/cancel semantic replay.

Не реализованы в Milestone 2:

- J1 UART;
- physical movement/STOP;
- staged real GoTo;
- NVS/watchdog/tracking/alignment/GPS.

STA/infrastructure mode также отложен: первый real-client test выполняется через
Direct Connect SoftAP.
