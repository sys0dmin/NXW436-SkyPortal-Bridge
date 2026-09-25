# Кандидат минимального test-only AUX profile для SkyPortal/SkySafari исследования

## Статус

Это scope document для PC frontend над `MountController`, использующим
`FakeMountBackend`.
Он не утверждает compatibility с SkyPortal/SkySafari: passive captures обоих
клиентов всё ещё нужны для request order, addresses, replies и timeouts.

## REQUIRED_FOR_INITIAL_CONNECTION

| Возможность | Статус | Ограничение |
| --- | --- | --- |
| Direct Connect transport candidate: AP + TCP `:2000` | `STRONG_REVERSE_ENGINEERING_EVIDENCE` | Infrastructure discovery отдельно; не блокирует offline frontend. |
| Binary AUX stream parser: `0x3B`, length, checksum | `CONFIRMED_MULTIPLE_IMPLEMENTATIONS` | Обрабатывать fragment/coalesce/garbage/invalid length/checksum/reconnect. |
| Client/device addressing and first probes | `UNKNOWN` | Не синтезировать реальные replies до capture. |
| Version/model/device identity | `UNKNOWN` as client requirement | Нужен explicit test-only profile до evidence. |

## CANDIDATES_FOR_FUTURE_BASIC_CONTROL

Этот раздел не входит в initial test-only implementation и не разрешает motion.

| Возможность | Candidate AUX intent | Local boundary | Статус/блокер |
| --- | --- | --- | --- |
| Position display | `MC_GET_POSITION` | Только через отдельный frontend coordinate adapter над `get_az_position`/`get_alt_position` | Coordinate translation gate. |
| Manual slew | `MC_MOVE_POS`, `MC_MOVE_NEG` | `move_az/alt` | Rate-to-tier mapping и captures. |
| Stop | rate-zero same-direction convention | `stop_az/alt` | Explicit failure when `safe_stop_available=False`; no fabricated success. |
| GoTo | `MC_GOTO_FAST`, `MC_GOTO_SLOW` | Только через adapter; AUX payload не передаётся напрямую в `goto_az/alt` | Transform, async operation contract and captures. |
| Completion | `MC_SLEW_DONE` | status + frontend operation state | Do not equate `completed` to acquisition. |

## OPTIONAL/LATER

- GPS time/location service at `0xB0`.
- Alignment and virtual `MC_SET_POSITION`.
- RA/DEC transforms, mount geometry and site model.
- Tracking and guide-rate commands.
- Wi-Fi Infrastructure discovery, UDP `55555`, multi-client and reconnect
  interoperability policies.
- ESP32 port after PC+FakeMountBackend, then PC+NXW436 validation.

## Non-negotiable constraints

- AUX frontend uses only `MountController`; it does not import NXW436 serial
  details, opcodes, payloads, raw modulus or recovery logic.
- NXW436 backend never sees AUX frames, TCP, Wi-Fi or SkyPortal/SkySafari.
- No motion command is enabled solely because an external implementation has an
  identifier: command semantics and client traffic require evidence.
- `safe_stop_available=False` is an unavailable stop, not an emergency-stop
  guarantee.
- `00001E` is never a frontend/AUX value.
- No ESP32 or real hardware work is in this milestone.
