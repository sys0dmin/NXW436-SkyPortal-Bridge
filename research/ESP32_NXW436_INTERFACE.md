# ESP32 <-> NXW436 J1: электрический интерфейс

## Статус

Документ создан в рамках architecture audit. Firmware и hardware не запускались.
NXW436 signal/pin facts основаны на измерениях этой платы; ESP32 level-shifter
схема ниже является принятым design constraint из текущей задачи и ещё не имеет
отдельного assembled-hardware validation.

## Подтверждённый J1

| J1 | Направление | Уровень / назначение | Evidence |
| ---: | --- | --- | --- |
| `1` | — | GND | exact-board measurement, `research/NXW436.md` |
| `2` | NXW436 -> controller | board TX, 5 V UART | exact-board measurement |
| `3` | — | GND | exact-board measurement |
| `4` | controller -> NXW436 | board RX, 5 V UART | exact-board measurement |
| `5` | — | common GND / return | exact-board measurement |
| `6` | board -> HC | +12 V | voltage measured; available current for ESP32 is **not proven** |

UART:

```text
4800 baud, 8 data bits, no parity, 1 stop bit
full-duplex TX/RX
```

## Принятая схема уровней

### NXW436 TX -> ESP32 RX

```text
J1-2 --- 20k ---+--- ESP32 RX
                |
               39k
                |
               GND
```

At nominal 5 V this divider produces about `3.31 V`. ESP32 RX must not be
connected directly to J1-2.

### ESP32 TX -> NXW436 RX

```text
ESP32 TX -> SN74HCT125 input
SN74HCT125 VCC = local 5 V
SN74HCT125 output -> 1k series -> J1-4
OE is active-low and disabled by hardware default
```

`SN74HCT125` is chosen because a 3.3 V ESP32 output satisfies HCT input-high
threshold while the output supplies a 5 V logic signal to NXW436.

The HCT output must be high-impedance during power-up, reset, bootloader and
watchdog reset. Do not connect OE permanently to GND. Use a `10k` pull-up from
OE to local 5 V and an open-drain `2N7002` pull-down: drain to OE, source to
GND, gate to a dedicated ESP32 GPIO with `100k` gate pull-down. The selected
GPIO must not be a strapping pin and must not emit boot logs. The J1 UART must
not share the ROM/debug console UART.

Firmware first configures the dedicated UART TX idle-high, initializes J1 RX
and completes read-only checks of both axes; only then may it enable the HCT
output. Mandatory validation before connection to the motor board: oscilloscope
J1-4 during cold power-up, software reset and watchdog reset. No low pulse or
UART byte is permitted while OE should be disabled.

### Ground and power

```text
ESP32 / level-shifter GND -> J1-5 common GND
ESP32 power -> separately fused 12 V branch -> 5.1 V buck
```

Do not power ESP32 from the NXW436 `78D05`. Do not assume J1-6 can supply ESP32
Wi-Fi current. Until current capability, transient behavior and thermal margin
are measured, J1-6 is not an authorized ESP32 power source. The first assembly
uses a separately fused/buck-converted 12 V positive feed; its positive wire is
not connected to J1-6. Only GND is shared with J1-5.

This circuit remains `DESIGN_CONSTRAINT, unvalidated` until measured on the
assembled prototype. Add low-capacitance 3.3 V ESD protection from ESP32 RX to
GND after the divider. Place `100 nF + 1 uF` directly at SN74HCT125 VCC/GND.
Validate J1-2 voltage range/transients, divider-node rise time/capacitance and
absence of ESP32 brownout during Wi-Fi transmit bursts.

## UART ownership requirement

ESP32 firmware must have exactly one owner of the J1 `HardwareSerial` object.
No network callback, timer callback or axis worker may directly call UART.

One atomic transaction consists of:

```text
position: write 01/15 + read exactly 3 bytes or timeout
move:     write prefix + exactly 3 payload bytes
STOP:     same known prefix + 000000, 50 ms, same frame again
```

The complete double STOP is one non-interleavable operation. Serialization does
not independently prove RX freshness because replies contain no axis/request
identifier. Input bytes are
validated against the canonical modulus `0x102A00`; malformed/timeout replies
remain errors and must not be replaced with cached success.

A partial RX timeout places the UART owner into explicit `UART_DESYNC` until a
separately reviewed resynchronization procedure succeeds. Telemetry retains
partial bytes and timestamps. Do not silently treat the next three bytes as a
fresh response, and do not add an unconditional RX drain without parity tests
because that would change frozen Python behavior.

## Forbidden assumptions

- no additional NXW436 opcodes;
- no direction-independent STOP;
- no sending both STOP prefixes;
- no payload interpolation;
- no J1 shared AUX-bus framing;
- no assumption that J1-6 powers ESP32 safely;
- no automatic reconnect/resume of physical motion after a reset.

## M3 first physical bring-up checklist

Do not upload `esp32dev_readonly` or connect J1 until every item is reviewed.

1. ESP32 powered only by USB or separate 5.1 V buck; NXW436 powered by bench
   12 V; motors may remain unplugged.
2. Verify no electrical connection between ESP32/level shifter and J1 before
   boot waveform testing.
3. Wire only common ground: ESP/level-shifter ground -> J1-5.
4. Build, but do not upload yet:

```powershell
python -m platformio run -d .\firmware\esp32_nxw436 -e esp32dev_readonly
```

5. Assemble divider J1-2 -> 20k -> ESP RX node -> 39k -> GND; measure expected
   approximately 3.31 V for a 5 V high level.
6. Assemble AHCT path with OE pull-up, 2N7002 open-drain, 100k gate pull-down,
   1k series to J1-4, but keep J1-4 disconnected initially.
7. With an oscilloscope at the future J1-4 side, test cold boot, software reset
   and watchdog reset. No low pulse/UART byte is allowed while OE is disabled.
8. Connect J1-4 only after the waveform passes. Upload M3 target explicitly:

```powershell
python -m platformio run -d .\firmware\esp32_nxw436 -e esp32dev_readonly -t upload --upload-port COMx
```

9. Monitor at 115200. Expect `m3_readonly_ready` with both session zeroes.
10. Observe exactly one `01` and one `15` query and three-byte replies. Confirm
    `j1_position` for both axes.
11. Move no motor. If any prefix `06`, `07`, `1A`, `1B` appears on J1-4, stop
    immediately: this is a test failure.
12. Only after direct read-only validation, connect phone to the ESP SoftAP and
    verify SkyPortal GET_POSITION/crosshair with no manual movement.

Stop immediately on unexpected TX glitch, motor movement, brownout, inconsistent
read length or `UART_DESYNC`. Do not issue a second recovery command.
