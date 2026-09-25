# HBG3 v3.8: decomposition Celestron-compatible Wi-Fi/AUX frontend

## Статус и источники

Это source-level decomposition **HBG3 / HomeBrew Gen3 v3.8** как primary
reference для будущего Celestron-compatible frontend. Он не разрешает перенос
всей HBG3 firmware, ESP32 work, физический AUX forwarding или изменения
NXW436 backend.

| ID | Source |
| --- | --- |
| H1 | https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino (`8c3a3c50e6797b77b3cdfedc258a1bed66e55f23`) |
| H2 | https://www.rtr.ca/hbg3/ |

Метки: `DIRECTLY_IMPLEMENTED` - исполняемый H1 code; `INFERRED` - вывод из
code structure/comment; `NOT_FOUND` - нет в H1 v3.8 или source не доказывает
wire-level detail.

## Direct Connect

| Поведение | Статус | HBG3 evidence |
| --- | --- | --- |
| Роль ESP32: SoftAP/server. | `DIRECTLY_IMPLEMENTED` | `wifi_begin_server_mode()`: [`H1:865-875`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L865-L875), `WiFi.mode(WIFI_AP)` и `WiFi.softAP`. |
| Default SSID: `HomeBrew-%02X%02X%02X` из последних трёх MAC bytes; stored `softap.ssid` переопределяет. | `DIRECTLY_IMPLEMENTED` | [`H1:149`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L149), [`H1:783-791`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L783-L791). |
| IP/gateway: `1.2.3.4`; netmask: `255.255.255.240` (`/28`). | `DIRECTLY_IMPLEMENTED` | [`H1:865-875`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L865-L875), `WiFi.softAPConfig(ip, ip, netmask)`. |
| DHCP lease range, DNS и explicit DHCP server configuration. | `NOT_FOUND` | H1 не конфигурирует их явно; SDK behavior не подменять предположением. |
| TCP services поднимаются после Wi-Fi. | `DIRECTLY_IMPLEMENTED` | `set_esp32_wifi()`: [`H1:917-935`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L917-L935), `W2000.begin()` и `W3000.begin()`. |

## Access Point / Infrastructure

| Поведение | Статус | HBG3 evidence |
| --- | --- | --- |
| Роль ESP32: station/client существующей WLAN. | `DIRECTLY_IMPLEMENTED` | `wifi_begin_client_mode()`: [`H1:878-904`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L878-L904), `WiFi.mode(WIFI_STA)`, `WiFi.begin(ssid, passkey)`. H2 называет режим Access Point. |
| SSID/passphrase: persisted `wlan.ssid`/`wlan.passkey`; пустой SSID вызывает fallback к SoftAP. | `DIRECTLY_IMPLEMENTED` | [`H1:745-776`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L745-L776), [`H1:878-886`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L878-L886), [`H1:927-932`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L927-L932). |
| DHCP default; static IP/netmask/gateway только при disabled `wlan.dhcp.enabled`. | `DIRECTLY_IMPLEMENTED` | [`H1:767-774`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L767-L774), [`H1:887-899`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L887-L899). |
| Адрес ESP32 на wire после DHCP. | `NOT_FOUND` | Его выдаёт WLAN DHCP server. |
| Отдельный Infrastructure discovery protocol. | `NOT_FOUND` | H1 использует общий UDP advertisement API, но destination IP/source port не раскрыты H1. |
| Reconnect после уже установленного Wi-Fi drop. | `NOT_FOUND` | Есть initial ожидание `WL_CONNECTED`, нет явной post-drop policy: [`H1:899-903`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L899-L903). |

## UDP advertisement

| Поведение | Статус | HBG3 evidence |
| --- | --- | --- |
| Отправляется только пока нет `W2000` client. | `DIRECTLY_IMPLEMENTED` | `poll_for_new_w2000_connection()`: [`H1:1175-1197`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1175-L1197). |
| Интервал - scheduled каждые 1000 ms. | `DIRECTLY_IMPLEMENTED` | `next_bcast = get_timeout(1000)`: [`H1:1190-1192`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1190-L1192). Wire interval under load не измерен. |
| Destination port `55555`. | `DIRECTLY_IMPLEMENTED` | `udp.broadcastTo(..., 55555)`: [`H1:1191`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1191). |
| Payload - JSON без NUL, MAC и `version`: `HomeBrew-AMW007-9.0.0.0, 2021-10-18T12:00:00Z, ESP32-3.8`; literal newlines входят в template. | `DIRECTLY_IMPLEMENTED` | Advertisement construction: [`H1:826-829`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L826-L829). |
| `AMW007` достаточен/обязателен для SkyPortal. | `INFERRED` | Авторский comment говорит «seems to be the critical bit», не wire capture: [`H1:826-827`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L826-L827). |
| Broadcast destination IP и UDP source port. | `NOT_FOUND` | H1 вызывает `broadcastTo`, но не задаёт IP/source port; нужен API source или capture. |

## TCP transport

| Поведение | Статус | HBG3 evidence |
| --- | --- | --- |
| `W2000` - Celestron/SkyPortal application listener на TCP `2000`; `W3000` - management service. | `DIRECTLY_IMPLEMENTED` | [`H1:247-251`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L247-L251), management decoder [`H1:1157-1173`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1157-L1173). |
| Один active `WiFiClient` на port; после disconnect listener снова poll-ит accept. | `DIRECTLY_IMPLEMENTED` | [`H1:1138-1155`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1138-L1155), [`H1:1175-1206`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1175-L1206). |
| Stream parsing по одному byte; fragmentation и coalescing проходят один `packet_decoder`. | `DIRECTLY_IMPLEMENTED` | `service_w2000()` читает `while (w2000.available())`: [`H1:1208-1217`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1208-L1217). |
| `SO_KEEPALIVE`, `TCP_KEEPALIVE=3000 ms`. | `DIRECTLY_IMPLEMENTED` | [`H1:1179-1187`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1179-L1187). Actual wire packets `NOT_FOUND`. |
| Application idle timeout: 15 s without received TCP byte -> `w2000.stop()`. | `DIRECTLY_IMPLEMENTED` | [`H1:1208-1217`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/main/esp32_wifi-V3.8.ino#L1208-L1217). |

## AUX bridge и compatibility behavior

### Physical forwarding boundary

HBG3 is a transparent physical AUX bridge. `packet_decoder()` принимает
`0x3B`, length/checksum, и для TCP RX queue вызывает:

```cpp
if (buf != &auxbus_rxbuf)
    auxbus_tx_enq(true, buf->data, buf->len);
```

Статус: `DIRECTLY_IMPLEMENTED`: [`packet_decoder(), H1:451-516`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L451-L516).
Это точка, которую наш проект **не** будет копировать: вместо неё будущий
frontend должен dispatch AUX intent в `MountController`, а не писать raw frame
на NXW436 UART.

| Поведение | Статус | HBG3 evidence |
| --- | --- | --- |
| AUX parser: SOM `0x3B`, length, checksum; range length `3..26` при `AUXBUS_PKT_MAX=32`. | `DIRECTLY_IMPLEMENTED` | [`packet_decoder(), H1:451-516`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L451-L516); TCP invocation [`service_w2000(), H1:1199-1222`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L1199-L1222). |
| TCP RX -> raw physical AUX TX queue. | `DIRECTLY_IMPLEMENTED` | [`packet_decoder(), H1:451-516`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L451-L516), queue at L513-L514. |
| Queue -> physical AUX UART: `service_auxbus()` -> `auxbus_tx()` -> `auxBus.write(...)`. | `DIRECTLY_IMPLEMENTED` | [`service_auxbus(), H1:725-737`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L725-L737), [`auxbus_tx(), H1:700-723`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L700-L723). Excluded from our frontend. |
| Physical AUX RX -> TCP: `auxbus_receive()` -> `bridge_auxbus_to_others()` -> `w2000_tx()` -> `w2000.write(...)`. | `DIRECTLY_IMPLEMENTED` | [`auxbus_receive(), H1:644-677`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L644-L677), [`bridge_auxbus_to_others(), H1:602-641`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L602-L641), [`w2000_tx(), H1:534-541`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L534-L541). |
| Echo suppression/correlation uses requestor marker, not address whitelist. | `DIRECTLY_IMPLEMENTED` | Requestor/physical echo: [`packet_decoder(), H1:497-503`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L497-L503); Wi-Fi suppression: [`bridge_auxbus_to_others(), H1:620-624`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L620-L624). |
| Valid TCP frame address whitelist. | `NOT_FOUND` | H1 raw-forwards valid frame; only GPS/local and requestor logic are special. |

### Excluded reference only: HBG3 local GPS/compatibility hooks

Этот подраздел исключён из frontend milestone и записан только во избежание
случайного копирования. Он не разрешает GPS emulation или response rewriting.

| Behavior | Status | HBG3 evidence |
| --- | --- | --- |
| GPS target is locally answered by `handle_gps_request()`, including `DEV_GET_VER=0xFE` -> `2.0`. | `DIRECTLY_IMPLEMENTED` | [`handle_gps_request(), H1:350-429`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L350-L429). It is not proof SkyPortal requires it. |
| Mount replies are rewritten by `hack_response_for_compatibility()`: `MC_GET_MODEL`, `MC_GET_MAX_RATE`, `MC_GET_MAX_SLEW_RATE`. | `DIRECTLY_IMPLEMENTED` | [`hack_response_for_compatibility(), H1:554-600`](https://github.com/g7ltt/Celestron-GPS-WiFi-BT-Interface/blob/8c3a3c50e6797b77b3cdfedc258a1bed66e55f23/esp32_wifi-V3.8.ino#L554-L600). Values emulate CPC/Evolution-like behavior. |
| Timed-out TCP `GET_VER` gets null reply. | `NOT_FOUND` in executable v3.8 | Only history comment exists; no pending-request state, command-specific timeout, or null `w2000.write`. |
| Startup-specific client request sequence/replies. | `NOT_FOUND` | H1 supports compatibility, but does not document an observed SkyPortal/SkySafari sequence. |

## Comparison with current PC AUX skeleton

| HBG3 behavior | Наше implementation | Уже есть? | Missing / status | Required for first SkyPortal connection? |
| --- | --- | --- | --- | --- |
| TCP listener `:2000`, one session/reconnect. | `AUXTCPServer` listener/session parser. | Да | HBG3 keepalive/15 s idle policy absent. | `INFERRED`: likely helpful, not yet required. |
| Byte-stream AUX framing. | `AUXStreamParser`, serializer. | Да | HBG3 length max `26`, ours `255`; no reason менять без client capture. | Да, parser already safe. |
| Raw capture before interpretation. | `tcp_raw.bin` then `aux_frames.jsonl`. | Да | None. | Да, for evidence. |
| UDP `:55555` advertisement with `AMW007`. | Нет. | Нет | HBG3 directly implements it; suitability for our PC frontend requires explicit follow-up scope. | `INFERRED`, do not implement now. |
| Direct SoftAP `HomeBrew-*`, `1.2.3.4/28`. | Нет. | Нет | Separate Wi-Fi topology feature. | `INFERRED`, do not implement now. |
| Infrastructure station DHCP/static. | Нет. | Нет | Separate Wi-Fi topology feature. | `INFERRED`, do not implement now. |
| Raw TCP AUX -> physical AUX queue. | Dispatcher produces no reply and invokes no operation. | Intentionally no | Replace with semantic dispatcher -> `MountController`; command/reply contracts still required. | Да, but only after evidence/gates. |
| AUX RX replies -> TCP, GPS emulation, response hacks. | No replies. | Intentionally no | Do not copy until captures and explicit contracts. | `UNKNOWN`. |
| Requestor correlation / echo suppression. | One session, no replies. | Partial | Needed only when outbound replies/async events exist. | `UNKNOWN`. |
| Keepalive, idle disconnect. | No. | No | HBG3 values documented; wire impact not captured. | `INFERRED`. |

## Минимальный план PC implementation

1. Keep current passive capture endpoint unchanged as the evidence baseline.
   Real-client capture is passive only: the server records inbound bytes and
   sends no synthetic replies; it performs no position translation, movement,
   STOP, GoTo, alignment or tracking, and uses `FakeMountBackend` only.
2. Add HBG3-informed Direct Connect transport feature only under separately
   approved scope: SoftAP topology, UDP `:55555` advertisement and TCP
   listener; do not invent Infrastructure behavior. This is not a claim of
   SkyPortal/SkySafari compatibility; UDP destination/source-port behavior and
   client acceptance remain `UNKNOWN` pending capture/API evidence.
3. Capture real client startup frames with raw evidence.
4. For each observed command, specify a synthetic/local reply contract or a
   semantic `MountController` mapping. Never raw-forward to NXW436 UART.
5. Add only the smallest proven initial replies. Position/GoTo remain disabled
   until zero offsets, axis signs, ALT limits, AZ wrap, physical reference and
   AUX-coordinate-to-neutral-coordinate mapping are evidenced; raw NXW436
   counts or direct AUX target -> `goto_*` mapping are prohibited.
6. Later replace the HBG3 physical queue seam with AUX semantic dispatcher ->
   `MountController` -> `FakeMountBackend`, then validate against NXW436 only
   under explicit hardware authorization.

## Excluded HBG3 subsystems

GPS, Bluetooth/BLE, OLED, dew/focuser/Nunchuck/MUSB/relay, OTA, ESP32 hardware
abstraction, physical AUX arbitration and physical AUX forwarding are not part
of this milestone.
