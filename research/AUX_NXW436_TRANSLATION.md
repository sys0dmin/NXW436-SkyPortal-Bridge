# AUX -> NXW436 translation contract

## Boundary

`AUXSemanticDispatcher` replaces the HBG3 call to physical `auxbus_tx_enq()`.
It maps only evidenced semantic intents to `MountController`; it never
raw-forwards AUX frames to NXW436 UART.

| AUX class | Command | Destination | Request payload | Expected reply | MountController operation | NXW436 capability | Translation status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `LOCAL_REPLY` | `MC_GET_VER` `0xFE` | candidate `0x10` / `0x11` | empty | 2 configured version bytes, source/destination swapped | none | none | `STRONG_REVERSE_ENGINEERING_EVIDENCE` for command/reply shape: `research/AUX_TO_MOUNTCONTROLLER_MAPPING.md`; client source/address/order and identity bytes are `UNKNOWN`. Only explicit synthetic profile may reply. |
| `LOCAL_REPLY` | `MC_GET_MODEL` `0x05` | observed `0x10` | empty | payload `11 89`, source/destination swapped | none | none | **Real SkyPortal capture:** after `20 -> 10 FE`, exact `20 -> 10 05` was observed. **HBG3 v3.8:** `hack_response_for_compatibility()` exposes `11 89`; [`H1:554-600`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L554-L600). Only tuple `(20,10,05,0)` is enabled. |
| `MOUNT_QUERY` | `MC_GET_POSITION` `0x01` | candidate `0x10` / `0x11` | empty | 3-byte AUX coordinate, source/destination swapped | `get_az_position()` / `get_alt_position()` | position read | `STRONG_REVERSE_ENGINEERING_EVIDENCE` for reference shape; actual client tuple is `UNKNOWN`. Only explicit synthetic profile and `AUXCoordinateAdapter` may reply. |
| `MOUNT_QUERY` | `MC_SLEW_DONE` `0x13` | `0x10` / `0x11` | empty | reference implementations use 1 status byte | no current safe status contract | partial | `UNSUPPORTED`: no synthetic completion state until operation ownership/reply contract is evidenced. |
| `MOUNT_ACTION` | `MC_MOVE_POS` `0x24` | observed `0x10` | observed `00` | **No synthetic reply:** exact device reply is `NOT_PROVEN` | none | none invoked | **Real SkyPortal capture:** `20 -> 10 24 00` occurred three times, all captured with no TX. `NexStarAux` table records `(0x24, 1, 0)`, but `rx=0` is unused by `encode_message()` and its stream parser neither waits for nor correlates a reply ([table](https://github.com/rtolesnikov/NexStarAux/blob/29c42ab6b6a7bf8b55a70d2afc929688c0937a82/NexStarAux.py#L68-L107), [encode/parser](https://github.com/rtolesnikov/NexStarAux/blob/29c42ab6b6a7bf8b55a70d2afc929688c0937a82/NexStarAux.py#L181-L196), [receive](https://github.com/rtolesnikov/NexStarAux/blob/29c42ab6b6a7bf8b55a70d2afc929688c0937a82/NexStarAux.py#L308-L350)). Therefore it proves neither A (no frame) nor B (zero-payload frame). HBG3 raw-forwards valid frames and would forward a valid motor-originated zero-payload reply to TCP, but does not generate one ([H1:451-516](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L451-L516), [bridge](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L602-L641)). An independent HC serial protocol implementation expects one-byte `0x01` ACK, but it uses `0x50...0x23`, not raw AUX, so is non-transferable ([Move](https://github.com/dokeeffe/auxremote/blob/126845f0c561dbeee2cdb0de8f0c51bd1ec1d232/app/src/main/java/com/bobs/serialcommands/Move.java#L13-L63), [adapter](https://github.com/dokeeffe/auxremote/blob/126845f0c561dbeee2cdb0de8f0c51bd1ec1d232/app/src/main/java/com/bobs/io/NexstarAuxSerialAdapter.java#L24-L105)). The arithmetically valid but unproven B candidate is `3B 03 10 20 24 A9`; it is not implemented. Remains `GATED`; no `move_*`/`stop_*` call. |
| `MOUNT_ACTION` | `MC_MOVE_NEG` `0x25` | `0x10` / `0x11` | 1 rate byte | not capture-proven | `move_az/alt` | available backend operation | `GATED`. |
| `MOUNT_ACTION` | move rate `0` | same axis/direction | `00` | not capture-proven | `stop_az/alt` | conditional | `GATED`: only later with `safe_stop_available`; never fabricate success. |
| `MOUNT_ACTION` | `MC_GOTO_FAST` `0x02` | `0x10` / `0x11` | 3-byte target | not capture-proven | `goto_az/alt` through adapter | available backend operation | `GATED`: transform and async result contract required. |
| `MOUNT_ACTION` | `MC_GOTO_SLOW` `0x17` | `0x10` / `0x11` | firmware variant | not capture-proven | `goto_az/alt` through adapter | available backend operation | `GATED`. |
| `UNSUPPORTED` | `MC_SET_POSITION` `0x04` | `0x10` / `0x11` | 3-byte coordinate | not capture-proven | none | none | Alignment model excluded. |
| `UNSUPPORTED` | guide/tracking `0x06` / `0x07` | `0x10` / `0x11` | rate bytes | not capture-proven | none | no tracking API | Tracking excluded. |
| `UNSUPPORTED` | GPS / Wi-Fi device queries | varies | varies | varies | none | none | GPS/discovery excluded. |
| `UNKNOWN` | any other command | any | any | none | none | none | Logged, no reply. |

## Real client acceptance evidence

`CLIENT_ACCEPTED_EXPERIMENTALLY` is distinct from motor-controller protocol
evidence. In a real SkyPortal run, after experimental reply
`3B03102024A9` to `20 -> 10 24 00`, SkyPortal immediately sent
`20 -> 11 24 00`. The experimental profile therefore also emits
`3B03112024A8` only for that exact second tuple. This advances the observed
client state machine, but does **not** change the `MC_MOVE_POS` device-response
status above from `NOT_PROVEN`; neither ACK is a claim about real motor
controller behavior.

## Real startup sequence and backlash blocker

Persisted capture `captures/20260925T184354Z-SkyPortal-HBG3-Infrastructure-AUX`
records the following client progression:

```text
20 -> 10 24 00
10 -> 20 24   [experimental TX 3B03102024A9]
20 -> 11 24 00
11 -> 20 24   [experimental TX 3B03112024A8]
20 -> B4 FE  x3  [no reply]
20 -> 12 FE  x3  [no reply]
20 -> 10 40     x2  [no reply]
```

The two move ACKs are `CLIENT_ACCEPTED_EXPERIMENTALLY`, not real-device
evidence. `B4` and `12` version probes are `OPTIONAL/ABSENT` devices in this
experiment: no emulation is authorized.

`0x40` is `MC_GET_POS_BACKLASH`: empty request, one-byte positive backlash
response. `0x41` is separately named `MC_GET_NEG_BACKLASH`; it and destination
`0x11` remain unobserved and disabled. Evidence: immutable
[NexStarAux table](https://github.com/rtolesnikov/NexStarAux/blob/29c42ab6b6a7bf8b55a70d2afc929688c0937a82/NexStarAux.py#L68-L107) and
[HBG3 v9.11 constants/focus emulation](https://rtr.ca/hbg3/hbg3.ino.txt#L1264-L1265),
[one-byte GET reply](https://rtr.ca/hbg3/hbg3.ino.txt#L5120-L5129).

For observed request `3B032010408D`, a response carrying a **conditional**
value `00` would serialize as `3B04102040008C`. HBG3 v3.8 has no local `0x40`
special case; it only bridges physical responses. `00` is semantically valid
as zero compensation with medium confidence from HBG3 emulator range `0..99`
and Celestron backlash documentation, but a real Celestron MC capture returning
`00` for this tuple is absent. SkyPortal's required value is also unknown.
Therefore `0x40` command/reply shape is evidenced, but `00` is not captured
from a real motor controller. The exact AZ response `(20,10,40,empty) ->
3B04102040008C` is `CLIENT_ACCEPTED_EXPERIMENTALLY`: SkyPortal immediately
advanced to observed `20 -> 11 40`. The exact ALT request is now real-observed;
the profile experimentally replies `(20,11,40,empty) -> 3B04112040008B` pending
the next capture. Both paths are `EXPERIMENTAL_ZERO_BACKLASH` /
`NOT_DEVICE_CAPTURE_PROVEN`, do not query a backend, and do not enable `0x41`
or any other backlash value.

## Approach blocker

Persisted capture `captures/20260925T190148Z-SkyPortal-HBG3-Infrastructure-AUX`
contains `20 -> 10 FC` twice (`3B032010FCD1`), both with no TX. Two independent
raw AUX sources identify `0xFC` as `MC_GET_APPROACH`: empty request, one-byte
response; `0xFD` is separate `MC_SET_APPROACH` with one-byte request
([NexStarAux](https://github.com/rtolesnikov/NexStarAux/blob/29c42ab6b6a7bf8b55a70d2afc929688c0937a82/NexStarAux.py#L47-L131),
[HBG3 v9.11 constants/virtual MC](https://rtr.ca/hbg3/hbg3.ino.txt#L1264-L1287)).

The conditionally serialized response frames are `3B041020FC00D0` for value
`00` and `3B041020FC01CF` for value `01`. Their shape is evidenced; neither
value is captured from a real motor controller, and sources/captures do not
establish `00=positive` or `01=negative`. HBG3 v3.8 has no local `0xFC`
handler, only physical raw forwarding. The experimental HBG3 profile alone now
returns `EXPERIMENTAL_APPROACH_VALUE_00` / `NOT_DEVICE_CAPTURE_PROVEN` /
`VALUE_SEMANTICS_UNKNOWN` for exact `(20,10,FC,empty) -> 3B041020FC00D0`.
`00` is an opaque compatibility experiment, not a physical direction or
captured MC value. AZ `(20,10,FC,empty) -> 3B041020FC00D0` is
`CLIENT_ACCEPTED_EXPERIMENTALLY`: SkyPortal immediately advanced to
`REAL_OBSERVED` ALT `20 -> 11 FC`. The profile now experimentally replies
`(20,11,FC,empty) -> 3B041120FC00CF` pending the next capture. Both retain
`VALUE_SEMANTICS_UNKNOWN` and `NOT_DEVICE_CAPTURE_PROVEN`; neither queries a
backend or enables `0xFD`, value `01` or any other approach tuple.

## HBG3 compatibility max-slew-rate blocker

Persisted capture `captures/20260925T192239Z-SkyPortal-HBG3-Infrastructure-AUX`
records `20 -> 10 21` twice (`3B03201021AC`), both with no TX. The immutable
HBG3 v3.8 `hack_response_for_compatibility()` establishes
`HBG3_COMPATIBILITY_BEHAVIOR`, not real MC behavior: it runs only after a
checksum-valid physical AUX reply with full frame `len == 6`, `src == 0x10`,
`cmd == 0x21`. Destination is not tested; `src == 0x11` does not match.

For canonical physical frame `3B03102021AC`, it rewrites `length` to `07`,
inserts four constant payload bytes `A0 11 94 54`, recomputes checksum and
forwards payload `A0 11 94 54`; through this project's additive-zero AUX
serializer the valid wire frame is `3B07102021A01194540F` (`C7` is not a valid
checksum for these bytes). The v3.8 comment calls this
`MC_GET_MAX_SLEW_RATE` and Evolution-derived. It does not inspect mount model,
state or original payload. Source: immutable
[HBG3 v3.8 compatibility code](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L554-L600).

HBG3 v9.11 virtual emulator differs: it names `0x21` `MC_GET_CUSTOM_RATE9`
and returns `0F A0 11 94`; this is separate emulator behavior, not confirmation
of v3.8 rewrite or device semantics. No real motor-controller capture confirms
`0x21` semantics, response, `A0 11 94 54`, source `0x10`, source `0x11`, or
NXW436 applicability. `0x21`, `0x23` and destination `0x11` remain
unimplemented, except for the isolated experimental HBG3 v3.8 TCP-visible
compatibility result `(20,10,21,empty) -> 3B07102021A01194540F`. This is marked
`HBG3_V38_COMPATIBILITY_RESPONSE`, `CLIENT_ACCEPTANCE_NOT_YET_PROVEN` and
`NOT_DEVICE_CAPTURE_PROVEN`; it does not emulate an intermediate physical AUX
reply, query a backend, enable destination `0x11`, `0x23`, or HBG3 v9.11 bytes
`0F A0 11 94`.

## HBG3 compatibility max-rate blocker

Persisted capture `captures/20260925T194028Z-SkyPortal-HBG3-Infrastructure-AUX`
confirms `20 -> 10 21` with accepted TX `3B07102021A01194540F`, then observed
`20 -> 10 23` twice with no TX. Therefore the `0x21` result is now
`CLIENT_ACCEPTED_EXPERIMENTALLY`; invalid prior candidate checksum `C7` is not
a valid AUX wire frame and is not retained as such.

In pinned HBG3 v3.8, `0x23` is named `MC_GET_MAX_RATE`. Its compatibility
rewrite is applied only to a checksum-valid physical AUX response where
`len == 6`, `SOM == 3B`, `src == 10`, `cmd == 23`; destination is not checked.
The comment describes GPS-8 physical form `3B03102023AA`. The code replaces
the original checksum with payload `00`, changes length `03 -> 04`, recomputes
checksum and forwards `3B0410202300A9`. Source:
[immutable HBG3 v3.8](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L554-L600).

This is `HBG3_V38_COMPATIBILITY_BEHAVIOR`, not `REAL_MOTOR_CONTROLLER_BEHAVIOR`:
NXW436 response existence/form is unknown. HBG3 v9.11 calls `0x23`
`MC_GET_CUSTOM_RATE9_ENA` and its virtual emulator also outputs `00`, producing
the same `3B0410202300A9`; that coincidence is separate
`HBG3_V911_BEHAVIOR`, not validation of the v3.8 rewrite or real device.
Independent NexStarAux validates only AUX framing/checksum, not `0x23`
semantics. The experimental HBG3 profile alone now returns
`HBG3_V38_COMPATIBILITY_RESPONSE` / `CLIENT_ACCEPTANCE_NOT_YET_PROVEN` /
`NOT_DEVICE_CAPTURE_PROVEN` for exact `(20,10,23,empty) -> 3B0410202300A9`.
It performs no backend operation and does not enable destination `0x11` or
later commands. This is the v3.8 final TCP-visible rewrite result, not a
physical MC response and not an adoption of HBG3 v9.11 semantics.

## Autoguide-rate blocker

Persisted capture `captures/20260925T195604Z-SkyPortal-HBG3-Infrastructure-AUX`
confirms accepted AZ `0x23` TX `3B0410202300A9`, followed immediately by
`REAL_SKYPORTAL_REQUEST_OBSERVED` `20 -> 10 47` (`3B0320104786`) with no TX.

The documented normal raw AUX contract names `0x47` `MC_GET_AUTOGUIDE_RATE`
(`MTR_GET_AUTOGUIDE_RATE` in an independent scanner): empty request, one-byte
response retaining command `0x47`, structurally `3B04102047VVCC`. `VV` is an
unknown rate value and is not selected. Sources:
[Paquette AUX commands](https://www.paquettefamily.ca/nexstar/NexStar_AUX_Commands_10.pdf),
[independent scanner](https://github.com/platini2/celestronauxbus/blob/main/celestron.py#L137-L140),
[rate decode](https://github.com/platini2/celestronauxbus/blob/main/celestron.py#L382-L384).

`nexstar-evo` records a separate **observed firmware anomaly** on one Evolution
Alt-Az mount: `0x47` requests received `cmd=F0,payload=47` frames
(`3B041020F04795` for AZM, `3B041120F04794` for ALT), which its author calls a
probable firmware bug rather than normal protocol behavior
([capture](https://github.com/jochym/nexstar-evo/blob/master/analysis/connect-align-setup.uniq_cmds#L20-L35),
[analysis](https://github.com/jochym/nexstar-evo/blob/master/doc/notes.md#L108-L120)).

Classification: `EXPECTED_NORMAL_RESPONSE_LENGTH_1`, `RESPONSE_VALUE_UNKNOWN`,
`NO_CLIENT_ACCEPTANCE_EVIDENCE`, `NO_DEVICE_CAPTURE_FOR_TARGET_MODEL`. No rate
value, `F0` anomaly, destination `0x11`, tracking or NXW436 coupling is enabled.

## Virtual MC configuration façade

The experimental frontend now owns independent virtual MC configuration state
for `AZM=0x10` and `ALT=0x11`. Initial values are `positive_backlash=00`,
`approach=00`, `autoguide_rate=80`. This is state-only frontend configuration:
it has no `MountController`/backend operation and no physical direction,
position, movement, GoTo or tracking meaning.

`MC_GET_AUTOGUIDE_RATE (0x47)` is profile-authorized for empty requests from
`0x20` to either virtual MC and returns its current one-byte state. Its initial
`80` is evidence-classified as the official AUX example/default 50% sidereal
autoguide rate and a published HBG3/SkyPortal trace showing `47 -> 80` on both
axes. `MC_SET_AUTOGUIDE_RATE (0x46)` is profile-authorized only for exactly one
byte payload; it updates only the addressed virtual state and returns a
zero-payload ACK. The Evolution `F0/47` anomaly remains disabled.

Existing profile-authorized `0x40` and `0xFC` responses now read their initial
`00` bytes from the same virtual state, preserving their separate experimental
and device-evidence annotations. `0x41`, `0xFD`, position, motion, GoTo,
tracking and all other configuration remain disabled.

## Hardware position observability

Hardware `MC_GET_POSITION` now logs at the hardware composition boundary for
each existing SkyPortal poll: axis, NXW436 query command (`01` AZ / `15` ALT),
raw three-byte reply, raw integer, session-zero, explicit sign, converted AUX
integer and three-byte AUX payload. Query transport errors are also logged and
produce no synthetic position response. Manual events log pre-motion raw state
and post-STOP raw state; no additional high-frequency polling is introduced.

The first valid poll after session-zero calibration intentionally returns AUX
`000000` when raw equals session zero. A later changing valid raw count cannot
be rounded back to `000000` by normal scaling: one native count maps to roughly
16 AUX units. Therefore telemetry distinguishes startup reference behavior,
unchanged/stale raw encoder reads, process/session restart, and adapter issues
without changing protocol behavior or compensating axis speed.

## Manual-motion FakeMountBackend experiment

Persisted capture `captures/20260926T081948Z-SkyPortal-HBG3-Infrastructure-AUX`
records `REAL_SKYPORTAL_REQUEST_OBSERVED` `20 -> 11 24 09` repeatedly after
successful position replies. `0x24` is manual positive move with exactly one
rate byte; `0x25` is symmetric negative move in independent AUX references.
The SkyPortal UI evidence supports a practical synthetic rate domain `0..9`;
wire byte values above `9` are rejected by this experiment.

Nonzero rates are mapped only to simulation `SpeedTier` values for
`FakeMountBackend` lazy monotonic-time movement. This is neither an AUX-to-
NXW436 mapping nor a physical velocity claim. Rate zero invokes the neutral
backend STOP only when that backend has an established direction; the retained
startup `HYPOTHETICAL_ZERO_PAYLOAD_ACK` remains separately annotated. ACKs are
sent only after successful fake backend acceptance; backend failure emits no
successful ACK. `MC_MOVE_NEG` is enabled symmetrically only in the Fake backend
experiment, not real hardware.

Observed `20 -> 10 06 00 00 00` is separately classified as
`MC_SET_POS_GUIDERATE`, not manual motion. Independent sources indicate its
two/three-byte guide-rate payload, but reply contract, units and tracking
mapping remain unknown. It remains disabled and is never coupled to NXW436 J1
opcode names or tracking behavior.

## Controlled NXW436 hardware experiment policy

The explicit `nxw436_hardware_experiment.py` launcher is the only composition
allowed to instantiate `NXW436MountBackend`; it requires `--backend nxw436`
and explicit `--serial COMx`, opens the backend explicitly, reads both encoder
positions before accepting manual motion, and uses session-local zeroes only.
Those zeroes and explicit axis signs are temporary relative-display calibration,
not AZ north, ALT horizon, pointing alignment or persistent physical constants.

`REAL_SKYPORTAL_CAPTURE_OBSERVED` UI mapping is: UI 1 -> AUX `02`, UI 2 ->
`05`, UI 3 -> `07`, UI 4 -> `09`, observed on both axes and directions.
The first real-hardware policy enables only AUX `02` for `MC_MOVE_POS/NEG` and
maps it to dedicated neutral `SpeedTier.MANUAL_CONSERVATIVE`; existing
`NXW436MountBackend` selects verified `0000F5` only for that tier. This is
`EXPERIMENTAL_CONSERVATIVE_MANUAL_POLICY`,
not a numeric rate conversion or NXW436 physical-rate claim. `05/07/09` return
no ACK and issue no UART command in hardware mode.

SkyPortal `MC_MOVE_POS 24 00` after a prior negative move uses
`MountController.stop_*`; backend remembered actual direction selects the
verified same-prefix double STOP. Unknown direction yields no ACK and no
guessed/both-prefix stop. Hardware movement and position errors likewise return
no success ACK. GoTo, tracking, SLEW_DONE, alignment and `0x06` remain disabled.

SkyPortal startup also experimentally sends `MC_MOVE_POS 24 00` before any
manual movement. Hardware frontend session state therefore distinguishes
`FRONTEND_IDLE` (no successful movement issued by this session) from `OWNED`
(successful movement with backend-known direction). In `FRONTEND_IDLE`, exact
startup STOP returns zero-payload AUX ACK with zero NXW436 UART bytes and no
direction guess. In `OWNED`, STOP must traverse `MountController.stop_*` and
backend double same-prefix STOP before ACK. A failed owned STOP remains no-ACK;
this frontend idempotency rule does not weaken `NXW436MountBackend` safe-stop
semantics.

Post-STOP raw position sampling is explicitly best-effort telemetry, outside
the STOP transaction. After successful backend STOP, frontend ownership becomes
idle and AUX ACK is sent even if final position query times out, returns no
frame, or raises a position-read error; telemetry records
`manual_post_stop_position_unavailable:<ExceptionClass>`. Only physical STOP
operation failure suppresses ACK and retains owned/unsafe state.

## Dynamic position startup blocker

Persisted capture `captures/20260926T080844Z-SkyPortal-HBG3-Infrastructure-AUX`
shows accepted virtual autoguide responses `20 -> 10 47 -> 10 -> 20 47 80` and
`20 -> 11 47 -> 11 -> 20 47 80`. It then records alternating
`REAL_SKYPORTAL_REQUEST_OBSERVED` `MC_GET_POSITION (0x01)` empty requests to
`AZM=0x10` and `ALT=0x11`, with ten no-TX retries before this integration.

`MC_GET_POSITION` is now `CURRENT_STARTUP_BLOCKER` and
`DYNAMIC_BACKEND_REQUIRED`: the experimental profile delegates exact empty
AZM/ALT requests through `VirtualCelestronMotorControllers` to
`AUXCoordinateAdapter` and then `MountController(FakeMountBackend)`, returning
a three-byte big-endian AUX coordinate with normal source/destination swap.
The launcher calibration is explicitly synthetic/test-only (`neutral_modulus`
`256`, zero offsets, direction `+1`, fake AZ/ALT positions `0x10`/`0x20`) and
is not an AZ/ALT physical reference, sign, range or NXW436 calibration.

## Coordinate adapter

`AUXCoordinateAdapter` is the sole conversion boundary:

```text
neutral axis coordinate <-> explicitly configured mechanical reference <-> AUX 24-bit coordinate
```

It owns AUX full-turn modulus `2^24`, rounding, modular arithmetic and both
directions. It receives its neutral modulus/configuration explicitly; it does
not import `POSITION_MODULUS` or any NXW436 module.

Real configuration remains unavailable until evidence establishes AZ zero/sign/
wrap, ALT zero/sign/range and physical reference. Therefore default frontend
composition has no adapter and cannot answer `MC_GET_POSITION`.
