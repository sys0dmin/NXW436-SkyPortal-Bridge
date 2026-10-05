# Kilo handoff: NXW436 / NexStar GT

## Current milestone

Experimental HBG3-compatible SkyPortal control is proven end-to-end:

```text
SkyPortal -> TCP/AUX frontend -> MountController -> NXW436 backend -> physical motor movement
```

The PC frontend has passed a full `FakeMountBackend` manual-motion loop and a
controlled real NXW436 rate-`0x02` experiment. SkyPortal manual motion, dynamic
position replies, direction-aware double STOP and session-local coordinate
display are experimental evidence, not a finished production protocol or
pointing/alignment solution.

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
- Implemented HBG3-compatible TCP/AUX framing, startup replies and capture
  evidence with an explicit experimental profile.
- Proved SkyPortal startup and telescope control UI against the PC frontend.
- Proved experimental manual AUX motion through `FakeMountBackend`, including
  changing position feedback, modular wrap and STOP.
- Proved controlled real NXW436 manual movement and encoder/AUX feedback for
  AUX rate `0x02`, mapped by `EXPERIMENTAL_CONSERVATIVE_MANUAL_POLICY` to
  `SpeedTier.MANUAL_CONSERVATIVE` -> `0000F5`.
- Restored unstable AZ encoder-feedback wire; direct USB-TTL and integrated
  SkyPortal runs now show changing raw `01`, successful modular wrap and
  crosshair movement following physical encoder motion.
- Fixed frontend STOP regression: a successful physical same-prefix double STOP
  is ACKed independently of optional post-STOP position telemetry.

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

Current checkpoint: 98 tests passing. Hardware commands require an explicit
task and operator authorization; do not use them as an ordinary smoke test.

## Current phase

Encoder feedback wiring was repaired after the prior constant-AZ observation.
Real SkyPortal end-to-end tests now show raw AZ `01` change during physical
movement, correct wrap through `0x102A00`, changing AUX coordinates and a
SkyPortal crosshair following the physical mount. Session-zero remains relative
display calibration only; no pointing/alignment claim is made.

The manual-rate expansion proof completed: current real policy enables
`0x02 -> 0000F5`, `0x05 -> 003978`, `0x07 -> 0072F1`,
`0x09 -> 00E5E3`. The final mapping rests on current four-quadrant direct-USB
evidence for `00E5E3`, not external 114GT interpolation. Do not compensate
AZ/ALT speed, interpolate payloads, remap channels, or alter GoTo profiles.

The former experimental startup kick/preload workarounds were removed after
mechanical work. Manual movement and the initial GoTo stage now send their
requested payload directly. Frozen GoTo stages and AZ same-payload MEDIUM
recovery remain unchanged; recovery is not startup assist. Tracking remains
unimplemented and no implicit kick is part of future motion architecture.

Manual control v1 is closed: real SkyPortal map-guided manual pointing used the
full `0x02/0x05/0x07/0x09` policy, live encoder feedback and crosshair movement.
The next separate phase is SkyPortal GoTo plus `MC_SLEW_DONE`; do not begin it
without explicit authorization.

GoTo design audit is complete, but no implementation has started. The blocking
issue is synchronous `RelativePositionController.run()` behind an inline TCP
dispatcher. Before production GoTo, design an application-owned per-axis job
coordinator and one serialized UART transaction owner. Preserve the frozen
controller stages and recovery; do not guess the AUX not-done status byte or
start GoTo until a Fake/client capture establishes the response contract.

An isolated `GoToCoordinator` and cooperative cancellation hook are now
implemented and validated only with an explicit FakeMountBackend profile. It
keeps TCP dispatch responsive and supports independent AZ/ALT job state. Real
NXW436 execution remains disabled by default and not hardware-validated.

An explicit hardware integration path is now present but disabled by default.
It requires `--enable-goto` plus `--max-goto-delta-counts`, rejects targets
outside the wrap-aware native bound before starting a job, and uses the same
coordinator/adapter/UART transaction serialization validated with mocks. No real
hardware GoTo has been executed; treat this as `HARDWARE_NOT_VALIDATED`.

The first controlled hardware GoTo reached both FAST and SLOW stages. FAST
completed on both axes. During SLOW, ALT physically stopped but its internal job
became `FAILED`; absence of an ALT `SLEW_DONE` terminal response caused repeated
polling and client EOF. A typed safely-stopped failure projection now preserves
internal FAILED telemetry while returning protocol `FF` only when the backend
proves the controller's STOP completed. Unsafe/generic failures remain unmapped.

Precision telemetry then identified an absolute-target integration bug: backend
computed a relative delta before the controller's pre-STOP/read, while the
controller applied that old delta to a new post-STOP start position. This shifted
the physical target by any position change between the two reads. The controller
now optionally receives the absolute native target and recomputes its delta from
the post-STOP initial sample. Frozen stage thresholds, payloads, stop margins and
recovery are unchanged. The fix is unit-tested but needs one new hardware GoTo.

The first real SkyPortal -> Fake GoTo capture is now available at
`captures/20260926T231936Z-SkyPortal-HBG3-Infrastructure-AUX`. It confirms one
AZ `MC_GOTO_FAST (0x02)` request with a 3-byte target, continued changing
`MC_GET_POSITION` replies while the fake job was active, and client retries of
the same GoTo after receiving no GoTo reply. It contains no `MC_GOTO_SLOW
(0x17)` or `MC_SLEW_DONE (0x13)` frames, so no status byte has been enabled or
guessed. ALT GoTo and gate-release completion remain untested.

The subsequent fake-only capture accepted empty GoTo ACKs for AZM and ALT and
began `MC_SLEW_DONE (0x13)` polling. The fake profile now projects
source-confirmed HBG3 virtual semantics `GOTO_ACTIVE -> 0x00` and
`COMPLETED -> 0xFF` per axis. Hardware GoTo remains disabled; ALT/two-axis
completion and post-gate client behavior remain to be captured.

The first interactive completion run did create `release-goto`, but the old
capture lacked gate lifecycle observability; its exact non-detection cause was
not proven. The launcher now resolves/logs one absolute path. Capture
`20260927T001148Z` confirms gate detection, both FAST jobs completing,
per-axis `SLEW_DONE=FF`, and real SkyPortal immediately issuing
`MC_GOTO_SLOW=0x17` for AZ and ALT. The one-shot gate remained set, so fake SLOW
jobs completed immediately in that capture. The launcher now rearms the same
absolute gate path after both FAST jobs complete; the operator must create the
file a second time to release SLOW. Offline regression validates both stages;
real prolonged-SLOW client acceptance remains to be captured.

Capture `20260927T001935Z` now validates the full real-client fake sequence:
FAST AZ/ALT -> empty ACKs -> active `SLEW_DONE=00` -> first gate -> `FF` ->
SLOW AZ/ALT -> empty ACKs -> active `00` -> second gate -> `FF`. SkyPortal kept
position polling throughout and sent no further GoTo after SLOW completion in
the observed window. Hardware GoTo remains disabled.

Cancellation capture `20260927T142919Z` confirms SkyPortal sends `0x24/00` to
AZ and ALT to cancel an active two-axis GoTo; coordinator jobs transition
through STOPPING to CANCELLED. No subsequent nonzero manual MOVE was captured.
That run also exposed a fake-only modulus mismatch (`256` adapter versus
`0x102A00` backend) that caused rapid crosshair wraps; the fake experiment now
uses canonical `POSITION_MODULUS`. Hardware mapping was unaffected.

Capture `20260927T000015Z` confirms real SkyPortal acceptance of both axis GoTo
ACKs and per-axis active `SLEW_DONE=00`. It polls position and status for both
axes every about `0.53..0.55 s` without retrying GoTo. The gate was not
released, so `COMPLETED -> FF`, post-completion behavior and any `0x17` slow
GoTo remain unvalidated.

## Do not do yet

- Do not enable real AUX rate `0x09` or any unlisted rate without new exact-board
  evidence; current policy enables only `0x02`, `0x05` and `0x07`.
- Do not start ESP32 firmware or physical handset work.
- Do not change NXW436 commands, payloads, calibration, mechanics, GoTo stage
  thresholds, stop margins, recovery, STOP behavior or RX policy.
- Do not diagnose/fix the noisy motor by changing software settings.
- Do not open COM or issue motion in routine development/testing.

For full chronology, raw-data interpretation and evidence ranking, read
`research/NXW436.md`; do not substitute this handoff for that record.
