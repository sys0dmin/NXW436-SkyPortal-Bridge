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
