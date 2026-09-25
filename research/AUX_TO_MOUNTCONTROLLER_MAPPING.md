# Celestron AUX -> MountController: gap analysis

## Назначение и граница

Цель проекта - compatibility layer: Celestron AUX/Wi-Fi frontend переводит
семантику в `MountController`, а тот использует существующий NXW436 backend.
Это не перенос AUX в `nxw436_driver.py` и не второй position controller.

```text
AUX frontend -> MountController -> NXW436MountBackend -> NXW436 UART
```

Ни один AUX identifier, address, payload, checksum или transport detail не
должен попасть в NXW436 backend. NXW436 raw count, `POSITION_MODULUS`, speed
payload, recovery и double STOP не должны попасть во frontend wire contract.

## Уровни доказательности

`CONFIRMED_LOCAL` - подтверждено на нашей NXW436; `CONFIRMED_MULTIPLE_IMPLEMENTATIONS`
- несколько независимых implementations; `STRONG_REVERSE_ENGINEERING_EVIDENCE`
- конкретный внешний source; `UNKNOWN` - необходим capture/эксперимент.

### Reference sources

| ID | Source | Роль |
| --- | --- | --- |
| R1 | https://github.com/rtolesnikov/NexStarAux/blob/master/NexStarAux.py#L47-L131 | AUX addresses и MC identifiers. |
| R2 | https://github.com/rtolesnikov/NexStarAux/blob/master/NexStarAux.py#L178-L240 | Frame/CRC validation. |
| R3 | https://github.com/rtolesnikov/NexStarAux/blob/master/NexStarAux.py#L285-L350 | Position, GoTo, `SLEW_DONE`. |
| R4 | https://github.com/rpineau/SkyPortalWiFi/blob/master/SkyPortalWiFi.h#L42-L104 | MC addresses/commands. |
| R5 | https://github.com/rpineau/SkyPortalWiFi/blob/master/SkyPortalWiFi.cpp | Manual move, direction-preserving stop, GoTo, status. |
| R6 | https://github.com/derryx/NexstarMountLib/blob/9bcce5b3975f5028314563580d5b0508dfe92294/NexstarMount.cpp#L31-L253 | AUX parser/checksum и GPS responder. |
| R7 | https://github.com/alex-vg/esp-skyportal-module/blob/master/ESPSkyPortalModule/ESPSkyPortalModule.ino#L109-L176 | TCP-to-AUX binary frame proxy. |
| L1 | `AGENTS.md`, `research/MOUNT_CONTROL_ARCHITECTURE.md` | Локальные safety/API constraints. |

## Transport/frame reference

```text
0x3B | length | source | destination | command | payload | checksum
```

`length` покрывает source/destination/command/payload; полный размер равен
`length + 3`; checksum - two's-complement суммы байтов от `length` до payload.
Статус: `CONFIRMED_MULTIPLE_IMPLEMENTATIONS` (R2, R4-R7). Это не подтверждает
initial sequence SkyPortal/SkySafari.

Conventional addresses: `0x20` PC/client, `0x10` RA/AZM, `0x11` DEC/ALT,
`0xB0` GPS. Статус: `CONFIRMED_MULTIPLE_IMPLEMENTATIONS`; geometry и реальная
client address mapping для этой mount остаются `UNKNOWN`.

## Mapping

| AUX command | Source | Destination | Payload | Expected reply | Meaning | MountController mapping | Supported now? | Prerequisite | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `MC_GET_VER` `0xFE` | candidate `0x20` | `0x10`/`0x11` | empty | 2 version bytes in R4/R5 | motor version | Нет | Нет | Synthetic identity/capability contract; exact bytes and client need `UNKNOWN` | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_GET_POSITION` `0x01` | candidate `0x20` | `0x10`/`0x11` | empty | 3-byte position | axis coordinate | Только после frontend coordinate adapter над `get_az_position()` / `get_alt_position()` | Только raw neutral read | API coordinate не является AUX coordinate; нужен approved transform and axis mapping | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_MOVE_POS` `0x24` | candidate `0x20` | `0x10`/`0x11` | 1-byte rate | reply not capture-proven | positive manual slew | `move_az/alt(PLUS, tier)` | Neutral operation exists | AUX rate -> `SpeedTier`; client command/reply capture | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_MOVE_NEG` `0x25` | candidate `0x20` | `0x10`/`0x11` | 1-byte rate | reply not capture-proven | negative manual slew | `move_az/alt(MINUS, tier)` | Neutral operation exists | AUX rate -> `SpeedTier`; client command/reply capture | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| move rate `0` | same prior direction | same axis | `00` | reply not capture-proven | stop in R5 | `stop_az()` / `stop_alt()` | Conditional | Only if `safe_stop_available`; otherwise explicit failure, never guessed direction | `STRONG_REVERSE_ENGINEERING_EVIDENCE` + `CONFIRMED_LOCAL` safety |
| `MC_GOTO_FAST` `0x02` | candidate `0x20` | `0x10`/`0x11` | 3-byte target | reply not capture-proven | axis GoTo | frontend adapter -> `goto_az/alt(target)` | Neutral operation exists | AUX payload never passes directly to `goto_*`; approved transform and operation state required | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_GOTO_SLOW` `0x17` | candidate `0x20` | `0x10`/`0x11` | 2/3-byte firmware variant | reply not capture-proven | slow axis GoTo | frontend adapter -> `goto_az/alt(target)` | Neutral operation exists | AUX payload never passes directly to `goto_*`; exact payload variant and coordinate transform required | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_SLEW_DONE` `0x13` | candidate `0x20` | `0x10`/`0x11` | empty | 1 status byte; R1 treats `0xFF` as done | poll completion | `get_axis_status()` plus frontend operation state | Partial | API must distinguish active operation, `completed`, acquisition and unknown-after-reconnect | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_SET_POSITION` `0x04` | candidate `0x20` | `0x10`/`0x11` | 3-byte virtual coordinate | reply not capture-proven | virtual sync/alignment in R5 | Нет | Нет | Alignment model; never write NXW436 encoder position | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_SET_POS_GUIDERATE` `0x06`, `MC_SET_NEG_GUIDERATE` `0x07` | candidate `0x20` | `0x10`/`0x11` | implementation rate bytes | reply not capture-proven | tracking/guide rate | Нет | Нет | Tracking semantic API and encoder feedback; `00001E` remains backend-only | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| GPS queries `0x01,0x02,0x03,0x04,0x33,0x36,0x37,0x55,0xA0,0xFE` | candidate `0x20` | `0xB0` | empty in R6 | 1-3 synthetic bytes in R6 | location/time/GPS identity | Нет | Нет | Site/time model and captures proving client demand | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| Wi-Fi discovery/module query | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | transport layer | Нет | Нет | Separate Direct Connect/Infrastructure evidence | `UNKNOWN` |

## Coordinate translation gate

AUX references use a 24-bit angular space. NXW436 raw encoder space is
`0x102A00`, confirmed locally. The frontend must never expose raw NXW436 values.
Before enabling position or GoTo, specify and test:

1. AZ/ALT zero offsets and sign/orientation.
2. ALT valid mechanical range.
3. AZ wrap policy.
4. Physical reference/home relation to AUX zero.
5. `NXW436 raw <-> mechanical angle <-> AUX 24-bit coordinate` bounds.

`raw * 2^24 / 0x102A00` alone is not an approved transform.

## Missing neutral contracts

- frontend identity/version/capabilities;
- client-visible axis operation status and ownership across reconnect;
- alignment state and coordinate transforms;
- site/time/GPS model;
- tracking semantic state/rates;
- documented AUX rate-to-`SpeedTier` policy;
- cancellation/failure representation that preserves safe STOP semantics.

`GotoResult.completed` is not target acquisition. Any later status mapping must
preserve `target_acquired`, `final_position`, `final_error_counts` and
`settled_outcome`.
