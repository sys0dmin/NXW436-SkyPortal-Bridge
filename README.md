# Мост Celestron NXW436 для SkyPortal

Экспериментальный мост, который позволяет SkyPortal/SkySafari управлять старой
Celestron NexStar GT с платой `NXW436 Rev A (11/2005)` без штатного Hand
Controller.

Проект не является planetarium application. SkyPortal/SkySafari остаются
клиентами, а мост переводит Celestron-compatible AUX semantics в существующий
нейтральный API и далее в подтверждённый NXW436 J1 UART protocol.

```text
SkyPortal / SkySafari
        |
HBG3-compatible Wi-Fi/TCP frontend
        |
  Celestron AUX frames
        |
  semantic dispatcher
        |
  MountController
        |
  NXW436MountBackend
        |
  J1 UART, 4800 8N1
        |
  NXW436 motor board
```

## Текущий Статус

Ручное управление v1 экспериментально подтверждено на реальном оборудовании:

- SkyPortal завершает инициализацию и открывает управление телескопом.
- Кнопки ручного управления SkyPortal перемещают AZ и ALT через полный путь
  `TCP/AUX ->`
  `MountController` -> NXW436 path.
- Raw NXW436 encoder feedback возвращается через `MC_GET_POSITION`; перекрестие
  SkyPortal следует за физическим движением монтировки.
- Session-local AUX coordinates корректно обрабатывают модульный wrap.
- Идемпотентный STOP при инициализации получает ACK без physical UART STOP, пока frontend
  не владеет движением оси.
- После контролируемого движения отпускание кнопки SkyPortal использует сохранённое
  direction backend и verified same-prefix double STOP.
- Необязательная немедленная post-STOP position telemetry не может подавить
  успешный AUX STOP ACK. Последующие normal position polls остаются
  авторитетными.

Это экспериментальное управление оборудованием, а не завершённый продукт с
pointing, alignment, tracking, GoTo или safety certification.

## Подтверждённый NXW436 J1 Protocol

Факты, подтверждённые на этой плате:

| Function | AZ | ALT |
| --- | --- | --- |
| Position query | `01` | `15` |
| Positive move | `06 xx xx xx` | `1A xx xx xx` |
| Negative move | `07 xx xx xx` | `1B xx xx xx` |
| STOP | Same active direction prefix + `00 00 00`, sent twice | Same active direction prefix + `00 00 00`, sent twice |

Positions are three-byte big-endian encoder values modulo:

```text
0x102A00 = 1,059,328 counts/revolution
```

Do not infer additional NXW436 commands from other Celestron mount families.

## Ручные Скорости SkyPortal

Политика ручных скоростей на реальном оборудовании дискретна и основана на
доказательствах. Это не формула и не интерполяция между payloads.

| SkyPortal UI speed | AUX rate | Neutral tier | NXW436 payload |
| --- | ---: | --- | --- |
| 1 | `0x02` | `MANUAL_CONSERVATIVE` | `0000F5` |
| 2 | `0x05` | `FINE` | `003978` |
| 3 | `0x07` | `MEDIUM` | `0072F1` |
| 4 | `0x09` | `MANUAL_HIGH` | `00E5E3` |

Все остальные ручные AUX rates отклоняются без UART movement и без success ACK.

`00E5E3` имеет текущую direct USB-TTL проверку для AZ+, AZ-, ALT+ и ALT-:
valid encoder feedback, commanded-direction progress, no sustained reverse,
same-prefix double STOP и stable post-STOP samples.

## Модель Координат

AUX frontend никогда не выдаёт raw NXW436 encoder counts напрямую.
Преобразование полностью принадлежит `celestron_aux.AUXCoordinateAdapter`:

```text
NXW436 raw encoder coordinate
        <-> session-local reference/sign
        <-> AUX 24-bit coordinate
```

Режим оборудования создаёт temporary session zero из текущих encoder positions
и требует явного выбора AZ/ALT sign. Он проверяет только relative movement и
согласованность отображения. Это не north/horizon zero, celestial alignment,
permanent calibration или pointing accuracy.

## Границы Безопасности

- Режим реального оборудования включается явно:
  `--backend nxw436 --serial COMx`.
- Он никогда не переходит на `FakeMountBackend`.
- Обычные tests не открывают COM и не отправляют motor commands.
- AUX frontend не знает J1 wiring, NXW436 opcodes, payloads или serial transport
  details.
- `NXW436MountBackend` не знает TCP, Wi-Fi, SkyPortal или AUX framing.
- STOP никогда не угадывает direction и не отправляет оба physical prefixes.
- `MC_SET_POS_GUIDERATE (0x06)` остаётся disabled; это не manual motion и не
  tracking.

## Запуск Оборудования

Не запускайте эту команду без проверки текущего experiment state, mechanical
clearance, cable wrap и явного serial port.

```powershell
python .\nxw436_hardware_experiment.py --backend nxw436 --serial COMx --bind <PC_LAN_IP> --broadcast <LAN_BROADCAST_IP> --mac <WIFI_MAC> --az-direction + --alt-direction +
```

Программа запуска записывает AUX RX/TX, raw NXW436 position frames, session-zero
conversion, motion/STOP events и persisted capture evidence в `captures/`.

## Автономная Проверка

```powershell
python -m unittest discover -p "test_*.py"
python -m py_compile mount_api.py fake_mount_backend.py nxw436_driver.py nxw436_mount_backend.py nxw436_hardware_experiment.py nxw436_low_speed_characterize.py nxw436_speed_analysis.py celestron_aux\coordinates.py celestron_aux\dispatcher.py celestron_aux\hbg3_infrastructure_experiment.py celestron_aux\messages.py celestron_aux\tcp_server.py celestron_aux\virtual_mc.py
git diff --check
```

## Отложенная Работа

Следующее намеренно не входит в scope manual control v1:

- SkyPortal GoTo и `MC_SLEW_DONE`;
- tracking и guide-rate semantics;
- alignment и absolute celestial pointing;
- permanent AZ/ALT calibration;
- ESP32 port;
- speed interpolation или compensation;
- дополнительные unsupported AUX manual rates.

## Доказательства

Авторитетная хронология находится в
[`research/NXW436.md`](research/NXW436.md). Frontend/AUX contracts и
experimental classifications находятся в
[`research/AUX_NXW436_TRANSLATION.md`](research/AUX_NXW436_TRANSLATION.md).
Текущий project handoff и constraints находятся в
[`research/KILO_HANDOFF.md`](research/KILO_HANDOFF.md).
