# Следующий этап: SkyPortal GoTo и MC_SLEW_DONE

## Статус

Это только source-backed research. Production-код, AUX dispatcher и hardware
не изменялись. GoTo, `MC_SLEW_DONE` и real hardware execution остаются
отключёнными.

## Источники

| Источник | Доказательство |
| --- | --- |
| [`rpineau/SkyPortalWiFi.h`](https://github.com/rpineau/SkyPortalWiFi/blob/master/SkyPortalWiFi.h#L42-L104) | AUX identifiers, `MC_GOTO_FAST=0x02`, `MC_SLEW_DONE=0x13`, `MC_GOTO_SLOW=0x17`, conventional addresses. |
| [`rpineau/SkyPortalWiFi.cpp`](https://github.com/rpineau/SkyPortalWiFi/blob/master/SkyPortalWiFi.cpp) | `startSlewTo()` builds 3-byte target frames for AZ/ALT; `isSlewToComplete()` polls both axes and transitions FAST to SLOW. Exact function source is in the pinned current repository revision. |
| [`rtolesnikov/NexStarAux.py`](https://github.com/rtolesnikov/NexStarAux/blob/29c42ab6b6a7bf8b55a70d2afc929688c0937a82/NexStarAux.py#L68-L107) | `MC_GOTO_FAST=(0x02,3,0)`, `MC_GOTO_SLOW=(0x17,2,0)`, `MC_SLEW_DONE=(0x13,0,1)` metadata; 3-byte GoTo decoder and `0xFF` done interpretation. |
| [`research/AUX_NXW436_TRANSLATION.md`](AUX_NXW436_TRANSLATION.md) | Project evidence boundary and disabled-command status. |
| `mount_api.py`, `nxw436_mount_backend.py`, `nxw436_position_controller.py` | Current neutral API and blocking staged GoTo implementation. |

External implementations are reference evidence, not a capture of this exact
NXW436/SkyPortal session. The actual client startup/GoTo sequence still needs
a passive SkyPortal capture before compatibility claims.

## AUX GoTo commands

| Intent | ID | Destination | Request payload | Reference reply/status | Status |
| --- | ---: | --- | --- | --- | --- |
| `MC_GOTO_FAST` | `0x02` | `0x10` AZM or `0x11` ALT | 3-byte big-endian 24-bit angular target in `SkyPortalWiFi.startSlewTo()` and `NexStarAux` | No command-specific payload reply established; reference client waits for subsequent status polling. | `STRONG_REVERSE_ENGINEERING_EVIDENCE`; real client sequence `UNKNOWN`. |
| `MC_GOTO_SLOW` | `0x17` | `0x10` or `0x11` | Reference metadata permits 2-byte variant; `SkyPortalWiFi.startSlewTo()` writes a 3-byte target. | No command-specific payload reply established. | Firmware/client variant conflict; do not implement before capture. |

The 24-bit AUX target is a fraction-of-full-turn coordinate, not NXW436 raw
`0x102A00` counts. The existing `AUXCoordinateAdapter` must be the only
conversion boundary. No real AZ/ALT zero, sign, mechanical limits or alignment
model may be invented from these sources.

Wrap is modular in the 24-bit reference representation. Shortest-path and
direction semantics are not established by the raw GoTo frame alone; those
decisions belong to the neutral/backend controller after a valid coordinate
transform.

## MC_SLEW_DONE

Reference request for each axis:

```text
3B 03 20 10 13 <checksum>   # AZM
3B 03 20 11 13 <checksum>   # ALT
```

The expected response is one payload byte with swapped addresses and the same
command:

```text
3B 04 10 20 13 VV <checksum>
3B 04 11 20 13 VV <checksum>
```

In `NexStarAux`, `MC_SLEW_DONE` has `rx=1` and the decoder treats `0xFF` as
done. `SkyPortalWiFi.isSlewToComplete()` sends AZM and ALT polls separately,
accepts `Resp[0] == 0xFF` for each axis, and reports complete only when both
are done. It rate-limits the poll to approximately two seconds using an elapsed
timer. The source contains a historical variable-name inversion in the two
boolean assignments (`baltComplete` receives AZM and `bAzComplete` receives
ALT), but the final operation is an AND of both replies; this should be treated
as source behavior to verify, not copied blindly.

When a fast GoTo reports both axes done, the reference client starts a slow
GoTo and polls again. This is client policy, not proof that every mount must
implement both commands identically.

Unknown until real capture:

- whether SkyPortal sends `MC_SLEW_DONE` immediately after each GoTo or after a
  delay;
- whether it expects a response to GoTo itself;
- exact `VV` while slewing (likely not `0xFF`, but not established here);
- cancellation behavior and manual movement during an active GoTo;
- whether one-axis and two-axis operations are coordinated by the client.

## Current blocking architecture

`MountController.goto_*()` calls backend `goto()` synchronously. Current
`NXW436MountBackend.goto()` runs `RelativePositionController.run()` inline,
including feedback sampling, STOP and settle. `AUXTCPServer.handle_connection()`
also dispatches each decoded frame inline on the client handler.

Therefore enabling GoTo directly would block that TCP handler while the bounded
controller runs. During this interval the same session could not reliably answer
`MC_GET_POSITION` or `MC_SLEW_DONE`. This is an architecture blocker, not yet a
protocol conclusion.

## Candidate asynchronous architecture (not implemented)

```text
AUX GoTo request
    -> validate tuple and coordinate transform
    -> enqueue/start per-axis motion job
    -> return protocol-defined response, if capture proves one
    -> TCP handler remains responsive
    -> GET_POSITION reads backend while job runs
    -> MC_SLEW_DONE reads job state
```

Before implementation, define explicitly:

- one operation state per AZ/ALT axis;
- ownership and cancellation rules;
- how a manual move/STOP interacts with a GoTo job;
- how backend UART access is serialized between concurrent axis jobs;
- thread safety of `NXW436Driver` and transport reads/writes;
- failure/timeout state and safe STOP behavior;
- distinction between job completion and `target_acquired`.

Two independent axis jobs may be logically needed because the client can issue
AZ and ALT commands close together. They cannot assume simultaneous UART
access: a shared transport requires serialization or a single owner loop.

## Next evidence-only experiment

Use the existing FakeMountBackend frontend only. Do not enable real GoTo.

Capture and record, after manual startup is stable:

1. a controlled client GoTo attempt only if the current synthetic profile can
   observe it without fabricating replies;
2. all `0x02`/`0x17` requests, source/destination, payload and timestamps;
3. every `0x13` poll for both destinations, response bytes and interval;
4. position polls interleaved with status polls;
5. cancel/manual STOP and reconnect behavior.

Until that capture exists, leave `MC_GOTO_FAST`, `MC_GOTO_SLOW` and
`MC_SLEW_DONE` classified as recognized-but-disabled. Do not add a guessed
status byte or synchronous GoTo path.

## Concurrency audit

### AUX dispatcher and TCP handler

`AUXTCPServer.handle_connection()` reads a client stream and calls
`dispatcher.dispatch(frame)` inline. A future blocking `MountController.goto_*`
would therefore block the only client handler while the position controller
samples, changes stages, stops and settles. This blocks `GET_POSITION`,
`MC_SLEW_DONE`, manual STOP and every other frame for that session.

### MountController

`MountController` is a thin synchronous façade. It owns no worker, job ID,
cancellation event or richer operation state. Its `goto_az()` and `goto_alt()`
delegate immediately to the backend. This is an appropriate neutral boundary,
but not an asynchronous job coordinator.

### NXW436MountBackend

Mutable state:

- `_last_direction: dict[Axis, Direction]` for safe STOP;
- `_motion_commanded` per axis;
- `goto()` creates a `RelativePositionController` and blocks until its bounded
  `run()` returns or aborts.

The two axis controller objects could keep independent algorithm state, but the
backend object and its transport are shared. A failed STOP currently has
special safety semantics and must not be converted into a generic job success.

### RelativePositionController

Each instance owns its own stage, samples, modular history, recovery flags and
settle state. It calls the supplied transport synchronously for position,
move and STOP. It has no cancellation event or bounded cancellation checkpoint
today. Its `finally` path performs STOP, so arbitrary thread termination would
be unsafe and is prohibited.

### NXW436 driver and serial transport

`NXW436` owns one serial object and mutable `_last_direction`. A position query
is one `write(query)` followed by a three-byte `read`; movement is a write;
STOP is two same-prefix writes. There is no transaction identifier or response
axis marker. Two workers must never execute:

```text
AZ write 01
ALT write 15
AZ read ...
ALT read ...
```

The minimum serialization boundary is one shared lock/owner around every
request-response transaction, and around the complete double-STOP operation.
Locks should not be scattered through AUX code: an application-owned
`NXW436Transport`/worker owner is the preferred seam.

## UART serialization

Preferred design: one per-backend UART executor (or a single serialized
transport lock) owns all J1 transactions. AZ and ALT GoTo jobs may be
concurrent at the state-machine level, but every query, move and double STOP
is submitted atomically to the shared executor. A worker must not hold a
transaction lock while waiting for another worker or for a long sampling
interval.

`NXW436MountBackend._last_direction` updates must occur in the same ownership
domain as the successful move write. STOP must read that remembered direction
and perform both same-prefix writes atomically relative to other axis UART
transactions. The current frozen driver STOP implementation itself is not to be
rewritten in this design milestone.

## Motion ownership

Use an explicit per-axis record, not one global `slewing` boolean:

```text
IDLE
GOTO_ACTIVE
MANUAL_ACTIVE
STOPPING
COMPLETED
FAILED
CANCELLED
```

The record should contain operation ID, target, job state, cancellation event,
last known position, remembered direction and failure details. `COMPLETED` is
not the same as `target_acquired`; preserve the existing `GotoResult` fields.

Proposed ownership policy:

- `IDLE` accepts GoTo or manual movement;
- `GOTO_ACTIVE` owns that axis and rejects a second GoTo by default;
- manual movement during GoTo requests cancellation, then is accepted only
  after the old job has executed its safe STOP and released ownership;
- manual STOP during GoTo requests cooperative cancellation and is never
  inferred from an AUX opcode direction;
- `FAILED` and `CANCELLED` remain observable until an explicit cleanup/new
  operation policy transitions them to `IDLE`;
- an axis job never silently takes ownership from another job.

Rejecting a conflicting new GoTo is the safest first policy. Queuing or
replacing targets needs client evidence and is not justified yet.

## Cancellation semantics

The current controller has no cancellation hook. Add a cooperative
`CancellationToken`/`Event` at bounded loop points only in a later implementation:

```text
STOP request -> set cancel event -> sample loop observes it
             -> enter STOPPING -> existing same-prefix double STOP
             -> job becomes CANCELLED/IDLE
```

The maximum response delay is bounded by one sample interval plus a position
transaction timeout and the controller's existing STOP path. Do not terminate
threads asynchronously. Do not add arbitrary emergency commands.

Manual movement during GoTo should use the same cancellation path, then wait
for ownership release before starting. A new GoTo is rejected while active.
TCP disconnect must not automatically imply physical STOP without an explicit
policy: session ownership and physical motion ownership are separate. The first
safe design keeps a job alive and observable, while an explicit later policy can
request cancellation.

## SLEW_DONE state mapping

Reference evidence establishes `0xFF` as the done byte. It does not yet provide
a sufficiently authoritative not-done byte for this project; do not invent one
in production replies.

Internal mapping should remain richer than AUX:

| Internal state | `MC_SLEW_DONE` policy before wire capture |
| --- | --- |
| `IDLE` / never started | Do not pretend this is a completed GoTo; retain internal distinction. |
| `GOTO_ACTIVE` | Must report not-done only after the exact client-compatible byte is evidenced. |
| `COMPLETED` and target acquired | Candidate `0xFF`, pending real client capture. |
| `FAILED` | Keep failure in telemetry/job state; never silently map it to success. |
| `CANCELLED` | Keep cancellation visible; wire response needs explicit policy/evidence. |

The reference client polls both axes and ANDs their done results. It also may
start a slow GoTo after a fast completion. These are client/reference facts,
not permission to implement replies before capture.

## GET_POSITION during active GoTo

Two options were considered:

1. **Serialized fresh query:** issue a normal J1 query through the shared UART
   executor. This is freshest and matches current backend semantics, but can
   consume transaction time and must not steal another response.
2. **Published job sample:** return the last trusted sample already collected by
   the active controller. This avoids extra UART traffic but needs an explicit
   freshness timestamp and may be stale between samples.

Preferred first design: allow a serialized fresh query when the executor is
   available; otherwise return the last trusted sample only with explicit age
   telemetry. Do not silently introduce an unbounded cache. The controller's
   own sample should be published as `(raw, timestamp, validity)` so the choice
   can be made without duplicate conversion logic.

## Two-axis execution

Run one job state machine per axis, but serialize all shared UART transactions.
Two jobs may therefore make progress concurrently in logical time while J1
access remains strictly ordered. The existing controller has no shared mutable
algorithm state between instances, but the transport and backend direction maps
are shared and require the owner/executor boundary above.

The first implementation should use one coordinator with two axis records and
one UART executor rather than two unmanaged Python threads. This makes
cancellation, operation ownership, STOP ordering and deterministic Fake tests
explicit. If workers are later used, they must submit only short transaction
jobs to that executor.

## FAST vs SLOW semantic mapping

`MC_GOTO_FAST=0x02` and `MC_GOTO_SLOW=0x17` are AUX protocol variants. They are
not the internal NXW436 `FAST -> MEDIUM -> FINE -> SLOW` stages. The reference
client sends a fast 3-byte GoTo, polls both axes, then may issue a slow GoTo
after both report done. The existing NXW436 controller remains the authority
for staged feedback and must not be forced into one stage for the whole AUX
request.

Initial semantic proposal: both AUX variants create the same neutral target job
with a policy field recording the requested AUX variant; the existing relative
controller chooses its frozen internal stages. This proposal requires real
SkyPortal capture and an explicit response contract before implementation.

## Coordinate conversion

`AUXCoordinateAdapter` already supports both `to_aux()` and `from_aux()` with
explicit modulus, zero, sign, wrap and limits. No second conversion path is
needed. A GoTo adapter would call `from_aux(axis, target24)` and pass the
resulting neutral target to `MountController.goto_*()`; it must not import
`POSITION_MODULUS` or pass raw AUX bytes into NXW436.

Session-local calibration remains relative display calibration only. No
alignment, celestial frame or permanent physical reference can be inferred.

## Fake validation plan

Before hardware, add an application-owned coordinator over the existing
`MountController(FakeMountBackend)` with an injected clock and deterministic
job scheduler. Tests must prove:

1. GoTo request returns without blocking TCP handling.
2. GET_POSITION works while a job is active.
3. active/done status state is tracked internally; wire status waits for proof.
4. AZ and ALT jobs can be active concurrently.
5. fake UART transaction recorder cannot interleave request/response pairs.
6. cooperative STOP cancellation reaches existing backend STOP.
7. failed/cancelled jobs remain observable and never become success.
8. conflicting GoTo rejects deterministically.
9. manual movement conflict follows cancel-then-release policy.
10. target conversion handles AUX wrap through the existing adapter.
11. existing manual tests and frozen controller tests remain unchanged.

No instant teleport should be used: the deterministic fake clock must expose
changing positions during an active job and stable position after STOP.

## Proposed files for later implementation

Do not add these files in this research milestone. A minimal later change would
likely add:

- `celestron_aux/goto_coordinator.py`: axis ownership, jobs, cancellation and
  internal status;
- `celestron_aux/uart_executor.py` or an application-owned serialized transport
  wrapper: atomic J1 transactions;
- small dispatcher/TCP integration changes to submit jobs and expose status;
- Fake coordinator tests with injected clock and transaction recorder.

Existing `RelativePositionController`, `NXW436MountBackend` GoTo thresholds,
payloads, stop margins and recovery should remain unchanged unless a concrete
concurrency seam proves otherwise.

## Open questions

- What exact `MC_SLEW_DONE` not-done byte does real SkyPortal accept?
- Does SkyPortal send `0x17`, or only `0x02`, in this target workflow?
- Does a GoTo request receive an ACK, no response, or a device-originated frame?
- Does disconnect cancel, preserve or abandon physical job ownership?
- What is the exact manual/STOP behavior during client-visible GoTo?
- Are two near-simultaneous axis jobs expected and how does SkyPortal poll them?
- Should fresh position queries or published controller samples serve active jobs?

These questions require Fake/client capture or source evidence before any
production GoTo implementation.

## Implementation status of the fake milestone

The isolated `celestron_aux.goto_coordinator.GoToCoordinator` is implemented
for an explicit fake/test profile only. It starts one background job per
AZ/ALT axis, rejects a second GoTo on an active axis, stores target, variant,
timestamps, result/error, cancellation event and thread identity, and keeps the
TCP dispatcher free to process position/manual frames.

`FakeMountBackend` has an optional gated GoTo mode for deterministic tests. The
gate exposes intermediate position reads while a job is active and completes
the target only when the test releases it; default fake behavior remains
unchanged. Cooperative cancellation uses the existing controller/backend
boundary and never kills a thread.

The real NXW436 launcher does **not** inject a coordinator and therefore does
not enable hardware GoTo. `MC_GOTO_FAST`, `MC_GOTO_SLOW` and wire
`MC_SLEW_DONE` replies remain disabled there. This is
`IMPLEMENTED_AND_FAKE_VALIDATED`, not hardware validation.
