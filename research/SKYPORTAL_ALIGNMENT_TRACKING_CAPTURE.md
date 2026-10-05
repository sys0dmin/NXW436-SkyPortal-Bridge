# SkyPortal alignment and tracking capture plan

## Scope

This document defines passive client-behavior capture against the ESP32
`AzmAltOnly` fake mount. It does not implement alignment, tracking, accessory
devices, J1 UART or physical movement.

Evidence labels:

- `PROVEN`: exact capture or exact-board experiment;
- `STRONG_EVIDENCE`: source-backed behavior not yet client-confirmed here;
- `HYPOTHESIS`: plausible but unverified interpretation;
- `UNKNOWN`: no accepted evidence.

## Current proven transport behavior

`PROVEN` for the PC fake reference: SkyPortal uses a persistent TCP session
through startup, manual movement and position polling. `PROVEN` for real
SkyPortal manual control: a short tap can send a nonzero manual command and
direction-neutral stop closely together:

```text
AZ 0x09 start: 2026-09-26T17:47:21.520Z
AZ 0x00 stop:  2026-09-26T17:47:21.589Z
delta: 69 ms
```

An ALT example has a 3 ms command-to-stop interval. This is
`REAL_SKYPORTAL_CAPTURE_OBSERVED` client behavior, not a transport timeout.
The observed multi-second gap between separate manual actions happened while
the same TCP session continued position polling, so it is an intentional UI
action gap, not server latency.

ESP32 diagnostic capture currently provides bounded `t_ms`, `seq`, epoch,
RX/TX raw AUX hex, parser errors, semantic begin/end, TX completion/short
write, and lifecycle close details. This is sufficient to identify protocol
traffic/order/replies. UI causality requires an external timestamped journal.

## Baseline

Run only `esp32dev` fake profile, with NXW436/J1 disconnected.

1. Boot ESP32.
2. Connect SkyPortal.
3. Open Telescope Controls.
4. Do nothing for 15 seconds.
5. Short tap ALT+.
6. Short tap AZ+.
7. Do nothing for 15 seconds.
8. Disconnect.

Classify frames as `KNOWN_STARTUP`, `BACKGROUND`, `POSITION_POLL`,
`MANUAL_MOVE`, `RETRY` or `UNKNOWN`.

## Alignment workflow capture

No firmware marker is used. Begin an external screen recording that includes a
UTC-synchronized clock. Keep an append-only external journal:

```text
UTC ISO-8601 | marker | exact visible UI text | action | video offset
```

Use `tools/capture_esp32_serial.py` rather than piping `pio device monitor`:

```powershell
python .\tools\capture_esp32_serial.py --port COM4 --baud 115200 --output .\captures\esp32_alignment_serial.txt
```

It reads only, prints UTC-prefixed lines and writes the same lines to the
specified file. Stop it with `Ctrl+C` after the capture.

Required markers:

```text
ALIGN_BEGIN
ALIGN_POINT_1_SELECT
ALIGN_POINT_1_CONFIRM
ALIGN_POINT_2_SELECT
ALIGN_POINT_2_CONFIRM
ALIGN_POINT_3_SELECT
ALIGN_POINT_3_CONFIRM
ALIGN_COMPLETE
UI_ERROR
```

Use only the exact alignment path shown by the installed SkyPortal UI. Do not
invent a three-star sequence if the UI presents a different one. Do not press
manual movement, STOP, GoTo or tracking during the alignment capture.

If SkyPortal requires physical centering/movement, stop immediately and mark:

```text
blocked_by_physical_centering
```

No synthetic alignment response, `MC_SET_POSITION`, tracking or accessory reply
may be added to bypass this block.

## Alignment packet diff

Compare the baseline and alignment windows (±10 s around each external marker).
For every frame not classified as known background/manual/poll, record:

```text
timestamp
raw AUX
src/dst/cmd/payload
RX/TX/no response
preceding UI marker
PROVEN / STRONG_EVIDENCE / HYPOTHESIS / UNKNOWN
```

Particular candidates:

- new destination IDs;
- `0x04`, `0x06`, `0x07`, `0x46`, `0x47`;
- GPS/time/location devices;
- position reads before confirmation;
- post-confirm model/state writes.

### First ALIGN_BEGIN capture: raw serial analysis

`captures/esp32_alignment_serial.txt` is a real SkyPortal-to-ESP32 serial
capture. It began at `2026-10-05T18:20:21.411+00:00`; the server was ready at
`t_ms=153`. The separately supplied operator marker is:

```text
ALIGN_BEGIN | 21:22:22.670 local
```

The journal does not retain a date or timezone. The following correlation is
therefore conditional on the stated working hypothesis `local = UTC+03:00`:

```text
ALIGN_BEGIN = 2026-10-05T18:22:22.670+00:00
ESP t_ms approximately 120925
comparison window (-10 s .. +15 s): t_ms=110925..135925
UTC window: 2026-10-05T18:22:12.670+00:00 .. 2026-10-05T18:22:37.670+00:00
```

This is `STRONG_EVIDENCE` for a bounded temporal correlation, not `PROVEN` UI
causality: the marker was not emitted by the ESP32 and there is no synchronized
screen recording.

The full comparison window contains 94 AUX RX frames and 94 corresponding TX
frames: 74 `MC_GET_POSITION` polls, 18 `MC_SLEW_DONE` polls, and two
`GOTO_SLOW` requests. Its sequence is:

1. The first `cmd=13` pair returns `FF` at `t_ms=111065` and `111111`, ending
   a prior fake GoTo.
2. A new `GOTO_SLOW` begins inside the window: `20 -> 10 cmd=17 payload=029D74`
   at `111129`, followed by `20 -> 11 cmd=17 payload=1210E2` at `111175`.
   Both receive empty ACKs.
3. The next seven `cmd=13` pairs return `00` while that SLOW GoTo is active.
4. The log records the fake jobs entering `Completed` at
   `2026-10-05T18:22:18.951Z` (AZ) and `.954Z` (ALT); the final `cmd=13` pair
   then returns `FF` at `117401` and `117462`.

In this fake profile, the source defines `00` as active and `FF` as completed;
neither value proves target acquisition of a physical or astronomical mount.
This is pre-marker GoTo traffic, not an alignment command. Do not equate the
two AUX target payloads with separately logged native fake targets (`002A45`
and `012405`) without an adapter/logging audit.

After those final `cmd=13` completion replies, including the marker
neighborhood, every observed request is a normal `MC_GET_POSITION` poll:

```text
20 -> 10 cmd=01, response 10 -> 20 cmd=01 payload=02 9D 73
20 -> 11 cmd=01, response 11 -> 20 cmd=01 payload=12 10 E4
```

At the marker's nearest polls, AZ was received at `t_ms=120708` and ALT at
`t_ms=120755`; the next pair occurred at `121407` and `121453`. No AUX frame
in this post-GoTo marker neighborhood changes destination ID or carries a
candidate alignment/configuration payload. There is no observed `cmd=02`,
`cmd=17`, `cmd=24`, or `cmd=25` after `t_ms=117462` through the end of the
comparison window.

The recorded position trajectory across the preceding fake GoTo is real capture
evidence, but it is not attributable to opening alignment: across the full
comparison window AZ changed monotonically from `0x023DFD` to `0x029D73`
(`+0x5F76`, 24438 AUX counts), and ALT from `0x11B89B` to `0x1210E4`
(`+0x5849`, 22601 AUX counts). Both had reached their final values before
`ALIGN_BEGIN` under the conditional correlation above. Thus it is
incorrect to claim that the entire +/- window contained no coordinate movement;
it did, before the marker, as part of the preceding GoTo.

`UI_EVIDENCE`: a visible map-area/telescope-cursor change was reported after
entering alignment. This remains `UI_EVIDENCE_AMBIGUOUS`: without synchronized
video it cannot distinguish viewport pan/zoom/orientation from a telescope
cursor change. The capture proves neither an alignment command nor fake-state
mutation, a client-side transform, or completed alignment.

The next experiment is one controlled repeat on the ESP32 fake mount only:
record the complete screen with a visible UTC clock, write UTC markers for every
alignment action, and retain the matching UTC-prefixed serial log. Do not issue
manual movement, STOP, GoTo, or tracking commands during that run.

### Controlled ALIGN_BEGIN capture: 2026-10-05

The controlled repeat is preserved at:

```text
captures/20261005T214145-SkyPortal-ALIGN-BEGIN/esp32_alignment_serial.txt
research/video_2026-10-05_21-47-12.mp4
```

The externally recorded markers include explicit offsets, so their UTC mapping
is `PROVEN` rather than conditional:

```text
CONTROLS_IDLE_10S = 2026-10-05T21:43:40.378+03:00
                  = 2026-10-05T18:43:40.378Z
ALIGN_BEGIN       = 2026-10-05T21:44:04.491+03:00
                  = 2026-10-05T18:44:04.491Z
interval          = 24.113 s
```

`UI_EVIDENCE` from the saved video, operator account, and screenshot confirms
the intended interaction sequence included a Merak alignment confirmation. The
MP4 decodes (`720x1280`, `02:09`), but its visible clock has minutes only
(`21:42`, `21:43`, `21:44`), not seconds. The `21:44` screenshot shows Merak
above the telescope reticle, while `Перейти к Выравниванию` remains available.
It is therefore evidence of visible reticle/star offset, but does not establish
that the alignment transaction completed or that SkyPortal entered a terminal
aligned state. Consequently, the external second-precision marker supports
serial correlation, while the screen recording alone cannot independently
supply the marker's second-level time. File-name time `21-47-12` is not evidence
because the video already displays `21:42` at its start.

`ALIGN_BEGIN` coincides exactly with `t_ms=135742`, the `semantic_end` event
for an ALT `MC_GET_POSITION` request; it is not a firmware marker or a distinct
AUX frame. The surrounding observed traffic is:

```text
18:44:04.474Z  RX 20 -> 11, cmd=01, empty payload
18:44:04.491Z  semantic_end req=204, dst=11, cmd=01
18:44:04.510Z  TX 11 -> 20, cmd=01, payload=00 01 FB
```

The requested +/-10 s window is `18:43:54.491Z..18:44:14.491Z`. Capture ends at
`18:44:13.689Z`, leaving its final 0.802 s unrecorded. In the recorded portion
there are exactly 60 AUX RX and 60 matching AUX TX frames:

```text
30 x 20 -> 10 cmd=01  -> 10 -> 20 cmd=01 payload=00 00 FD
30 x 20 -> 11 cmd=01  -> 11 -> 20 cmd=01 payload=00 01 FB
```

All position replies are constant. There are no non-`GET_POSITION` frames, new
destination IDs, GoTo, manual-control, `MC_SLEW_DONE`, configuration, or
alignment-candidate AUX frames in the recorded portion. This is `PROVEN` only
for the observed frontend wire traffic and replies. It does not prove that
SkyPortal has no client-side alignment UI/state. The missing final 0.802 s and
UI ambiguity prevent an absolute claim about the whole requested window or any
later alignment step.

### Controlled Tau Ophiuchi alignment attempt: 2026-10-05

This follow-up capture deliberately used manual controls before alignment:

```text
captures/20261005T220744-SkyPortal-ALIGN-BEGIN/esp32_alignment_serial.txt
research/video_2026-10-05_22-21-42.mp4
CONTROLS_IDLE_10S = 2026-10-05T19:09:30.431Z
Ophiuchi_CENTERED = 2026-10-05T19:11:32.744Z
ALIGN_BEGIN       = 2026-10-05T19:12:33.986Z
```

The capture proves manual movement before the external centering marker. AZ
`MC_MOVE_POS` (`cmd=24`, rate `05`) and ACKed stops changed the returned AZ
position; ALT `MC_MOVE_NEG` (`cmd=25`, rate `05`) and an ACKed stop likewise
changed ALT. At `Ophiuchi_CENTERED`, the nearby position samples were AZ
`FE 91 A2` and ALT `11 48 F1`; this is evidence of the displayed fake
coordinates only, not astrometric or physical centering.

The video shows Tau Ophiuchi near the reticle but not provably at its geometric
crosshair intersection. It then shows the dialog asking to adjust the telescope
and naming Tau Ophiuchi, first as `1 из 10`; the dialog closes to the map. Near
`03:40` the same dialog appears as `2 из 10` and closes again. No terminal
alignment-success UI state is shown, and the action to proceed remains visible.

The `ALIGN_BEGIN` marker falls between an ALT position reply at
`19:12:33.509Z` and the next AZ request at `19:12:34.008Z`. From the marker
through the final AUX TX at `19:12:40.837Z`, the capture records only 31 AZ and
31 ALT `MC_GET_POSITION` request/reply pairs, with constant positions
`FE A1 39` and `11 42 C1`. There is no manual command, GoTo, `MC_SLEW_DONE`,
configuration, or alignment-candidate AUX frame. AUX traffic then stops even
though the serial capture continues through `19:14:26.870Z`.

This is `PROVEN` evidence that the observed pre-terminal Tau Ophiuchi dialog
flow has no recorded frontend AUX transaction beyond position polling. It does
not establish exact stellar centering, accepted alignment-point semantics,
terminal alignment completion, client-side transform ownership, or pointing
accuracy.

### Alignment completion blocker

The fake-mount experiment must stop before the second alignment point. After
the first point, SkyPortal no longer provides the map reticle needed to center a
subsequent star visually. The operator must center the real star through the
eyepiece and confirm that the telescope is physically pointing at it. Attempts
to confirm an approximate map-only position are rejected because SkyPortal
checks it against the real axis position.

Therefore a terminal multi-point alignment cannot be honestly completed with
the ESP32 fake mount alone. Do not fabricate positions, add synthetic alignment
replies, or infer later alignment behavior from approximate map placement. The
next valid completion experiment requires a safe, physically operating mount
backend with optical centering available; it is outside this fake-only capture
scope.

## Alignment ownership experiments

Only after a UI state claims alignment complete:

1. Select one object and initiate fake GoTo; record AZ/ALT FAST/SLOW targets,
   SLEW_DONE and final position.
2. Repeat alignment with materially different fake coordinates/actions, select
   the same object and compare resulting mount targets.
3. Disconnect/reconnect without closing SkyPortal, then repeat GoTo.
4. Restart ESP32 with SkyPortal alive, reconnect and repeat GoTo.

Different target coordinates for the same celestial object after changed
alignment are `STRONG_EVIDENCE` for client-side celestial-to-mount transform;
one observation is not final proof. No distinct alignment frame has yet been
captured, so client-side ownership remains `HYPOTHESIS`.

## Tracking

After successful UI alignment and GoTo completion, capture 30 seconds of idle
traffic. Compare it with a 30-second pre-alignment idle baseline. If the UI
offers tracking controls, capture only available `OFF` and default/ON states.

Do not classify position polling as tracking. The exact-board `00001E` sidereal
feed-forward observation is backend evidence only and does not establish a
SkyPortal AUX tracking command.

## Cancel

On active fake two-axis GoTo, press the client UI stop/cancel. Existing client
evidence confirms:

```text
20 -> 10 24 00
20 -> 11 24 00
```

Capture another 10 seconds afterwards. `cancel -> nonzero manual MOVE` is still
unknown until observed.

## HBG3 and source comparison

Only after a real capture, compare candidate commands to pinned HBG3 v3.8
commit `8c3a3c50e6797b77b3cdfedc258a1bed66e55f23` and primary documentation.
Do not enable B4/B9/12/B5 or tracking replies from newer HBG emulators without
new client evidence.

## Current non-conclusions

- no wire-level alignment command is proven;
- no aligned state response is proven;
- no celestial alignment model is implemented in ESP32;
- no tracking intent/byte/rate/lifetime is proven;
- no physical movement/J1 code participates in this capture;
- no claim about target acquisition or pointing accuracy follows from fake
  alignment UI behavior.
