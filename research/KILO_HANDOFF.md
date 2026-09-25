# Kilo handoff: NXW436 / NexStar GT

## Current milestone

Reverse engineering of the minimum NXW436 control path is complete enough for
software integration. The neutral mount API and NXW436 backend are hardened.
The next milestone is **research, then PC-hosted implementation** of the
minimum Celestron-compatible network frontend needed by SkyPortal/SkySafari.
Develop and test that frontend against `FakeMountBackend` first.

This project is not building a replacement planetarium client. SkyPortal and
SkySafari are intended phone clients. ESP32 firmware and physical control are
later milestones.

## Completed milestones

- Identified the exact board as NXW436 Rev A, PCB 11/2005, old GT generation.
- Measured the J1 electrical/UART path and verified a minimum set of position,
  movement and same-prefix STOP commands on the actual board.
- Confirmed 24-bit big-endian positions and modulus `0x102A00`.
- Built diagnostics and preserved their raw CSV evidence in the repository.
- Implemented the staged feedback GoTo controller with RX validation, modular
  arithmetic, reverse-sample confirmation, logging, double STOP, and narrowly
  evidenced AZ MEDIUM resend recovery.
- Characterized a post-rebuild empirical near-sidereal operating point.
- Added the frontend -> `MountController` -> backend integration boundary and
  an in-memory fake backend.

## Important source files

| File | Responsibility |
| --- | --- |
| `mount_model.py` | Neutral canonical `POSITION_MODULUS`. |
| `mount_api.py` | `Axis`, `Direction`, `SpeedTier`, status/result models, modular arithmetic, and `MountController`. |
| `fake_mount_backend.py` | Deterministic offline backend; no physical/protocol claims. |
| `nxw436_mount_backend.py` | Adapter from neutral API to the existing NXW436 driver/controller. |
| `nxw436_driver.py` | Verified J1 UART framing, position reads, move commands and double same-prefix STOP. |
| `nxw436_position_controller.py` | Frozen staged relative GoTo state machine and recovery policy. |
| `nxw436_goto_relative.py` | Current explicit hardware CLI with preflight/logging and dry-run. |
| `research/NXW436.md` | Detailed evidence history; authoritative project research record. |
| `research/MOUNT_CONTROL_ARCHITECTURE.md` | API boundary, GoTo and STOP semantics. |

## Verified protocol facts

Exact-board evidence establishes J1 at 4800 8N1, 5 V logic. Pins are:
`1 GND`, `2 board TX -> HC RX`, `3 GND`, `4 HC TX -> board RX`,
`5 GND/return`, `6 +12 V`.

Only these commands are verified for this board:

| Purpose | Command |
| --- | --- |
| AZ position | `01` |
| AZ positive / negative | `06 xx xx xx` / `07 xx xx xx` |
| ALT position | `15` |
| ALT positive / negative | `1A xx xx xx` / `1B xx xx xx` |
| STOP | same active movement prefix + `00 00 00` |

Position is 24-bit big-endian modulo `0x102A00` (`1,059,328`). Do not derive
unlisted commands from historical devices or internet captures.

## Production semantics and limitations

The current GoTo profile is frozen: AZ stop margin 200 counts, ALT 350 counts;
stages, payloads, thresholds, settle policy, RX policy, logging and recovery
remain unchanged.

`GotoResult.completed` says the bounded controller run completed normally. It
does not certify exact acquisition. Network code must separately inspect
`target_acquired`, `final_position`, `final_error_counts`, and
`settled_outcome`.

Verified STOP needs the last motion prefix. A backend process that commanded
the axis knows its direction and can issue the verified double STOP. After a
process restart/no known direction, safe STOP is unavailable. This is exposed
by `AxisStatus.safe_stop_available`; do not guess, send both prefixes, or claim
an emergency stop guarantee.

## Sidereal and mechanics

Post-rebuild `00001E` was near sidereal but varies as a full mechanical system:
AZ- runs were 12.22 and 12.11 cps (mean 12.165); AZ+ 12.19 and 11.91 cps (mean
12.05); target is about 12.30 cps. It is an empirical feed-forward baseline,
not a fixed rate command or formula. Future tracking needs encoder feedback.

One motor/gearbox assembly is noisy even externally unloaded. Brush,
commutator and gearbox explanations remain hypotheses. Mechanical work is
intentionally deferred.

## Test baseline

No normal test may open COM. Run exactly:

```powershell
python -m py_compile mount_model.py mount_api.py nxw436_driver.py nxw436_mount_backend.py fake_mount_backend.py test_mount_api.py nxw436_position_controller.py nxw436_goto_relative.py
python -m unittest discover -p "test_*.py"
python .\nxw436_goto_relative.py --axis az --degrees 5 --az-off-tripod --dry-run
python .\nxw436_goto_relative.py --help
```

Baseline at handoff is 42 passing tests. Hardware commands require an explicit
task and operator authorization; do not use them as an ordinary smoke test.

## Next planned milestone

1. Research reliable, cited Celestron-compatible network protocol behavior:
   discovery, transport/session lifecycle, minimum first-client messages,
   coordinates, framing, responses and timeouts.
2. Separate primary documentation from captures, reports and hypotheses.
3. Implement only the evidenced minimum as a PC-hosted frontend over
   `FakeMountBackend`.
4. Add protocol/frontend tests without J1, COM, ESP32 or motor movement.
5. Review architecture before connecting the real backend.

## Do not do yet

- Do not implement SkyPortal/SkySafari or Celestron Wi-Fi protocol before its
  research report exists.
- Do not start ESP32 firmware or physical handset work.
- Do not change NXW436 commands, payloads, calibration, mechanics, GoTo stage
  thresholds, stop margins, recovery, STOP behavior or RX policy.
- Do not diagnose/fix the noisy motor by changing software settings.
- Do not open COM or issue motion in routine development/testing.

For full chronology, raw-data interpretation and evidence ranking, read
`research/NXW436.md`; do not substitute this handoff for that record.
