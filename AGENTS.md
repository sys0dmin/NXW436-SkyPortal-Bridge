# Celestron NexStar GT / NXW436

## Language

- Communicate with the user only in Russian.
- Write reports, explanations, conclusions, and questions in Russian.
- Keep code, identifiers, commands, file names, protocol constructs, and original technical names unchanged.

## Project goal

Build a Celestron-compatible control path, not a new planetarium application:

```text
SkyPortal / SkySafari
        |
Celestron-compatible network frontend
        |
MountController / neutral mount API
        |
NXW436MountBackend
        |
NXW436 J1 UART
        |
old Celestron GT motor board
```

A future second client is an ESP32-B physical hand controller over a wireless
link, using the same mount-control abstraction. SkyPortal/SkySafari remain the
intended phone clients.

## Current phase

Minimum NXW436 motor-control reverse engineering and mount API hardening are
complete. The next milestone is **research first**: document the minimum
Celestron-compatible network protocol expected by SkyPortal/SkySafari, then
build a PC-hosted frontend against `FakeMountBackend`. Do not touch ESP32 or
real hardware during that frontend milestone until protocol research is cited.

## Evidence hierarchy

Use this order without silently promoting hypotheses:

```text
physical measurement on this exact NXW436 board
> successful experiment on this board
> capture/log from this board
> primary documentation
> external captures / reputable technical references
> forum reports
> hypothesis
```

## Confirmed hardware facts

- Board: Celestron NXW436 Rev A, PCB `11/2005`; old GT-generation motor board,
  not SLT NXW430-A.
- J1: `1 GND`, `2 board TX -> HC RX`, `3 GND`, `4 HC TX -> board RX`,
  `5 GND / return`, `6 +12 V`.
- UART: 4800 baud, 8N1, 5 V logic.
- AZ: position `01`; positive `06 xx xx xx`; negative `07 xx xx xx`.
- ALT: position `15`; positive `1A xx xx xx`; negative `1B xx xx xx`.
- STOP: same movement prefix with payload `00 00 00`.
- Position: 24-bit big-endian; modulus `0x102A00` = `1,059,328` counts/rev.

Do not invent further NXW436 commands. Detailed evidence: `research/NXW436.md`.

## Critical semantics

- `POSITION_MODULUS` has one neutral canonical definition: `mount_model.py`.
- `GotoResult.completed` means the bounded controller run returned normally;
  it does **not** mean exact target acquisition.
- Accuracy fields are `target_acquired`, `final_position`,
  `final_error_counts`, and `settled_outcome`.
- Verified STOP is direction-dependent. If `NXW436MountBackend` remembers the
  last direction it uses same-prefix STOP; after process restart or unknown
  direction, safe STOP is not known.
- Never invent direction-independent STOP, send both prefixes by assumption,
  or silently choose a direction. Check `AxisStatus.safe_stop_available`.

## Frozen production GoTo behavior

Unless a future task explicitly authorizes a change, preserve:

- AZ stop margin: 200 counts; ALT stop margin: 350 counts.
- staged feedback controller and modular/wrap-aware arithmetic;
- RX validation and reverse-sample confirmation;
- AZ MEDIUM bounded same-payload resend recovery;
- existing double STOP behavior and logging.

Do not retune these while building the network frontend.

## Sidereal characterization

`00001E` is a useful post-rebuild empirical feed-forward baseline near
sidereal, not a deterministic `12.30 cps` command and not a payload formula.

| Axis direction | Runs (cps) | Mean |
| --- | --- | ---: |
| AZ- | 12.22, 12.11 | 12.165 |
| AZ+ | 12.19, 11.91 | 12.05 |

Target is approximately 12.30 cps. Future tracking needs slow encoder feedback
to correct open-loop variation.

## Deferred mechanical issue

One motor/gearbox assembly remains mechanically noisy even unloaded.
Brush/commutator/gearbox explanations are hypotheses. Mechanical investigation
is deferred: do not modify software calibration from an assumed diagnosis.

## Development safety and boundaries

- Normal tests must not open the physical COM port. Use `FakeMountBackend`.
- Physical tests are explicit; never silently send movement commands.
- Do not alter verified hardware behavior just to simplify an abstraction/test.
- Frontend/network code must not know J1 wiring, NXW436 opcodes, payload
  prefixes, or serial implementation details.
- NXW436 backend must not know SkyPortal/SkySafari networking, TCP/UDP sessions,
  or phone-client protocol details. `MountController` is the boundary.

## Offline validation

Accepted baseline before this handoff: 42 tests passing.

```powershell
python -m py_compile mount_model.py mount_api.py nxw436_driver.py nxw436_mount_backend.py fake_mount_backend.py test_mount_api.py nxw436_position_controller.py nxw436_goto_relative.py
python -m unittest discover -p "test_*.py"
python .\nxw436_goto_relative.py --axis az --degrees 5 --az-off-tripod --dry-run
python .\nxw436_goto_relative.py --help
```
