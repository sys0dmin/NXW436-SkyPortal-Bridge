# Celestron NXW436 SkyPortal Bridge

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

## Current Status

Manual control v1 is experimentally proven on real hardware:

- SkyPortal completes startup and opens telescope controls.
- SkyPortal manual buttons move AZ and ALT through the complete TCP/AUX ->
  `MountController` -> NXW436 path.
- Raw NXW436 encoder feedback returns through `MC_GET_POSITION`; the SkyPortal
  crosshair follows physical mount movement.
- Session-local AUX coordinates handle modular wrap.
- Startup-idempotent STOP is acknowledged without physical UART STOP before
  the frontend owns movement on an axis.
- After owned motion, SkyPortal release uses backend remembered direction and
  verified same-prefix double STOP.
- Optional immediate post-STOP position telemetry cannot suppress a successful
  AUX STOP ACK. Subsequent normal position polling remains authoritative.

This is experimental hardware control, not a finished pointing/alignment,
tracking, GoTo, or safety-certified product.

## Verified NXW436 J1 Protocol

Exact-board facts:

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

## SkyPortal Manual Rates

The real hardware manual policy is discrete and evidence-backed. It is not a
formula or interpolation between payloads.

| SkyPortal UI speed | AUX rate | Neutral tier | NXW436 payload |
| --- | ---: | --- | --- |
| 1 | `0x02` | `MANUAL_CONSERVATIVE` | `0000F5` |
| 2 | `0x05` | `FINE` | `003978` |
| 3 | `0x07` | `MEDIUM` | `0072F1` |
| 4 | `0x09` | `MANUAL_HIGH` | `00E5E3` |

All other manual AUX rates are rejected with no UART movement and no success
ACK.

`00E5E3` has current direct USB-TTL validation for AZ+, AZ-, ALT+, and ALT-:
valid encoder feedback, commanded-direction progress, no sustained reverse,
same-prefix double STOP, and stable post-STOP samples.

## Coordinate Model

The AUX frontend never exposes raw NXW436 encoder counts directly. Conversion
is owned solely by `celestron_aux.AUXCoordinateAdapter`:

```text
NXW436 raw encoder coordinate
        <-> session-local reference/sign
        <-> AUX 24-bit coordinate
```

Hardware mode initializes a temporary session zero from the current encoder
positions and requires explicit AZ/ALT sign selection. This validates relative
movement and display consistency only. It is not north/horizon zero, celestial
alignment, permanent calibration, or pointing accuracy.

## Safety Boundaries

- Real hardware mode is explicit: `--backend nxw436 --serial COMx`.
- It never falls back to `FakeMountBackend`.
- Normal tests do not open COM or issue motor commands.
- The AUX frontend does not know J1 wiring, NXW436 opcodes, payloads, or serial
  transport details.
- `NXW436MountBackend` does not know TCP, Wi-Fi, SkyPortal, or AUX framing.
- STOP never guesses a direction and never sends both physical prefixes.
- `MC_SET_POS_GUIDERATE (0x06)` remains disabled; it is not manual motion or
  tracking.

## Hardware Launcher

Do not run this command without reviewing the current experiment state,
mechanical clearance, cable wrap, and explicit serial port.

```powershell
python .\nxw436_hardware_experiment.py --backend nxw436 --serial COMx --bind <PC_LAN_IP> --broadcast <LAN_BROADCAST_IP> --mac <WIFI_MAC> --az-direction + --alt-direction +
```

The launcher records AUX RX/TX, raw NXW436 position frames, session-zero
conversion, motion/STOP events, and persisted capture evidence under
`captures/`.

## Offline Validation

```powershell
python -m unittest discover -p "test_*.py"
python -m py_compile mount_api.py fake_mount_backend.py nxw436_driver.py nxw436_mount_backend.py nxw436_hardware_experiment.py nxw436_low_speed_characterize.py nxw436_speed_analysis.py celestron_aux\coordinates.py celestron_aux\dispatcher.py celestron_aux\hbg3_infrastructure_experiment.py celestron_aux\messages.py celestron_aux\tcp_server.py celestron_aux\virtual_mc.py
git diff --check
```

## Deferred Work

The following are intentionally out of scope for manual control v1:

- SkyPortal GoTo and `MC_SLEW_DONE`;
- tracking and guide-rate semantics;
- alignment and absolute celestial pointing;
- permanent AZ/ALT calibration;
- ESP32 port;
- speed interpolation or compensation;
- additional unsupported AUX manual rates.

## Evidence

The authoritative chronological record is [`research/NXW436.md`](research/NXW436.md).
Frontend/AUX contracts and experimental classifications are in
[`research/AUX_NXW436_TRANSLATION.md`](research/AUX_NXW436_TRANSLATION.md).
Current project handoff and constraints are in
[`research/KILO_HANDOFF.md`](research/KILO_HANDOFF.md).
