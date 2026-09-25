# ESP32 ↔ NXW436 J1: техническая спецификация

## Граница протоколов

Это не AUX electrical adapter. Будущий поток: SkyPortal/SkySafari -> Wi-Fi ->
modern protocol adapter -> Mount API -> NXW436Driver -> old GT UART 4800 8N1 -> J1.
Old GT UART, modern AUX и SkyPortal Wi-Fi являются разными слоями.

## Подтверждённый J1

| J1 | Функция |
|---:|---|
| 1 | GND |
| 2 | NXW436 TX, 5-V UART output |
| 3 | GND |
| 4 | NXW436 RX, вход U3 PIC16F73 RC7 |
| 5 | GND / power return |
| 6 | +12 V |

Использовать J1-5 как сигнальную землю. Не подключать ESP32 GPIO напрямую
ни к J1-2, ни к J1-4.

## Почему 3.3-V TX ESP32 недостаточен

U3 RC7/RX/DT — Schmitt Trigger input. Datasheet PIC16F73 задаёт VIH min =
0.8*VDD; при измеренном VDD=5.00 V это 4.0 V. ESP32 high=3.3 V не имеет
гарантированного запаса и не допускается как прямое подключение.

PIC16F7X datasheet подтверждает RC7/RX/DT как pin 18 в 28-pin корпусе и
Schmitt input; DC characteristics задают VIH ST = 0.8*VDD.

## Рекомендуемая схема сигналов

### NXW436 TX (J1-2) -> ESP32 RX

J1-2 -> R1 20k -> ESP32_UART_RX; от ESP32_UART_RX поставить R2 39k на
GND J1-5. При 5.00 V на входе ESP32 получается 3.305 V. Эквивалентное
сопротивление источника около 13.2 kOhm; для 4800 baud и короткой платы
достаточно. Опционально установить low-capacitance 3.3-V ESD diode от
ESP32_UART_RX к GND после делителя. Не использовать internal clamp ESP32
как штатную защиту.

### ESP32 TX -> NXW436 RX (J1-4)

ESP32_UART_TX подключить к 1A SN74HCT125. 1OE подключить к GND. VCC
SN74HCT125 подключить к local +5 V, GND к общему GND. 1Y через R3 1k
подключить к J1-4.

SN74HCT125 имеет TTL-compatible VIH max 2.0 V при VCC 4.5...5.5 V:
ESP32 3.3-V high гарантированно принимается и выдаётся как 5-V UART high.
Buffer non-inverting, UART polarity не меняется. Развязка U1: 100 nF X7R
непосредственно VCC-GND и 1 uF рядом. Не питать HCT от UTC78D05 NXW436.

## Питание от J1-6

Рекомендуемая topology: J1-6 +12V -> F1 resettable fuse (0.75 A hold,
около 1.5 A trip) -> TVS D1 SMBJ18A к GND -> Schottky D2 SS34 ->
buck 12V-to-5.1V, не менее 1.5 A continuous -> ESP32 devboard 5V/VIN
и SN74HCT125 VCC. J1-5 является common GND.

Конденсаторы у buck: input 100 uF / 25 V + 1 uF + 100 nF; output
220 uF / 10 V + 10 uF + 100 nF. У ESP32 board дополнительно 10 uF и
100 nF. Документация ESP32 требует источник не менее 500 mA; маломощный
линейный regulator не подходит.

SS34 выбран для первого прототипа: его падение на 12 V допустимо. В
серийной версии можно заменить его на P-MOSFET ideal-diode. TVS и fuse
разместить у входного разъёма.

### Ограничение источника J1

Допустимый ток J1-6 конкретной NXW436 ещё не измерен. До подтверждения не
считать его способным стабильно питать Wi-Fi. Сначала подать питание через
лабораторный БП, измерить добавочный ток bridge при Wi-Fi transmit и проверить
напряжение J1-6. При просадке prototype питается отдельным regulated 5 V;
GND с J1-5 остаётся общим. Никогда не питать ESP32 от 5-V UTC78D05 платы.

## BOM первой платы interface

| Ref | Деталь | Номинал / пример |
|---|---|---|
| U1 | 5-V TTL buffer | SN74HCT125D |
| R1 | divider upper | 20 kOhm, 1% |
| R2 | divider lower | 39 kOhm, 1% |
| R3 | output series | 1 kOhm |
| C1 | HCT bypass | 100 nF X7R |
| C2 | HCT bulk | 1 uF X7R |
| F1 | resettable fuse | 0.75 A hold |
| D1 | TVS | SMBJ18A |
| D2 | reverse diode | SS34 |
| U2 | buck | 12-to-5.1 V, >=1.5 A continuous |
| Cin | input bulk | 100 uF, 25 V |
| Cout | output bulk | 220 uF, 10 V |

Перед KiCad проверить реальный ток J1-6, осциллограммы обоих UART направлений,
уровни после divider/HCT и отсутствие ESP32 brownout при Wi-Fi TX.

## Software architecture

NXW436Transport owns UART framing/retry/timeout. NXW436Driver owns old-GT
command map and raw position arithmetic. Mount API exposes position/move/stop.
CelestronProtocolAdapter, Wi-Fi, SkyPortal, SkySafari and physical HC — future
layers. Только NXW436Driver имеет право формировать old-GT байты.
