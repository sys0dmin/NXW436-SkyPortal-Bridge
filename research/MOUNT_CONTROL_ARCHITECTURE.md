# Mount-control integration boundary

## Purpose

The PC integration layer exposes telescope operations, not NXW436 J1 packets.
It is deliberately small so a future ESP32 implementation can retain the same
conceptual boundary without copying Python serial code.

`MountController` provides:

- `get_az_position()` / `get_alt_position()`;
- `move_az()` / `move_alt()` with an axis, direction and named speed tier;
- `stop_az()` / `stop_alt()`;
- `goto_az()` / `goto_alt()` with an absolute modular encoder target;
- `get_axis_status(axis)`.

The canonical position modulus is `POSITION_MODULUS` in `mount_model.py`:
`0x102A00`. `mount_api.py` imports it; the legacy driver exports compatible
`RAW_MODULO` and `COUNTS_PER_REV` aliases for diagnostics. Absolute targets are
normalized and relative GoTo displacement is computed by central modular
arithmetic.

`GotoResult.completed` means only that the bounded GoTo controller returned
normally after its configured STOP/settle sequence. It is not an assertion of
exact target acquisition. A frontend must use `final_error_counts` and
`target_acquired` (plus `settled_outcome`) to present positioning accuracy. The
current controller is permitted to finish with a residual undershoot.

## Backends

`nxw436_mount_backend.py` is the real adapter.  It owns J1 framing, position
frame validation, payload mapping and the existing `RelativePositionController`
implementation.  Existing staged GoTo behaviour remains there: profiles,
thresholds, AZ stop margin 200, ALT stop margin 350, RX validation, suspect
reverse confirmation, AZ MEDIUM single-resend recovery, and double STOP are
not retuned or replaced.

### STOP state limitation

The verified NXW436 STOP command uses the same direction prefix as the last
motion command. Therefore `NXW436MountBackend.stop(axis)` is safe only after
this backend process has itself commanded that axis and remembered its
direction. In that state it forwards the existing double-STOP behaviour.

After backend/process restart, or before a motion command, the direction is
unknown. `stop(axis)` then raises `MountStateError`; it does not guess a
direction, emit both prefixes, or send any other motion command. This is an
unresolved hardware-protocol limitation, made visible through
`AxisStatus.safe_stop_available` and `safe_stop_unavailable_reason`. A future
network frontend must preserve its motion-command session state and report an
unknown-direction stop as unavailable; it must not claim an emergency stop
guarantee that has not been physically demonstrated.

`fake_mount_backend.py` is deterministic and in-memory.  It simulates modular
positions, `+`/`-` movement, stop state, and immediate GoTo completion.  It is
for offline integration tests only; it intentionally does not claim to model
motor speed, coast, UART faults, mechanics or NXW436 protocol timing.

## Scope boundary

This milestone adds no ESP32 firmware, network protocol, SkyPortal/SkySafari
integration, payload calibration, or mechanical changes.  A future network
frontend must invoke this API (or an equivalent firmware boundary), rather
than constructing NXW436 opcodes itself.
