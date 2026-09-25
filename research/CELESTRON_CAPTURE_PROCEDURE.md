# Пассивный capture SkyPortal/SkySafari

## Назначение и безопасность

`celestron_network_probe.py` записывает только PC network traffic. Это не
Celestron frontend: по умолчанию он не отправляет TCP или UDP ответы, не
импортирует NXW436/serial модули, не открывает COM и не управляет телескопом.

Raw `tcp_raw.bin` является первичным evidence. Поля `candidate_aux_interpretation`
в JSONL - лишь диагностическая гипотеза, не подтверждение AUX protocol.

## A. Запуск probe

На PC выберите IPv4-адрес, доступный телефону. Не используйте `1.2.3.4` как
обязательное значение: это только исследованный кандидат из внешних bridge.

```powershell
python .\celestron_network_probe.py --bind 0.0.0.0 --tcp-port 2000 --udp-port 55555 --client SkyPortal --scenario CAP-01 --network-mode "Windows hotspot" --phone-os "Android <version>" --app-version "<version>" --notes "passive initial connection" --passive
```

Для конкретного IP PC замените `0.0.0.0`, например на `192.168.137.1`.
Остановите после сценария через `Ctrl+C`. Каждый запуск создаёт отдельный
каталог `captures/<timestamp>-<client>-<scenario>/` с:

- `metadata.json` - ввод оператора и git revision;
- `tcp_raw.bin` - точная конкатенация bytes из TCP `recv()`;
- `tcp_events.jsonl` - connect/disconnect и bounded hex;
- `udp_events.jsonl` - source/local address, полный `payload_base64`, bounded hex;
- `probe.log` - журнал probe.

Для защиты PC используйте `--max-session-bytes` и `--max-parser-buffer` при
неизвестном источнике; default limits записывают событие превышения и закрывают
только TCP session, не отправляя response.

`--help` не создаёт сокеты:

```powershell
python .\celestron_network_probe.py --help
```

## B. Сеть Android и PC

1. Создайте общую изолированную Wi-Fi сеть: Windows Mobile Hotspot, отдельный
   travel router или обычная Wi-Fi LAN.
2. Подключите Android-телефон и PC к этой сети.
3. Узнайте IPv4 PC и убедитесь, что firewall допускает inbound TCP `2000` и
   UDP `55555` на выбранном private network profile.
4. Probe не эмулирует Wi-Fi access point Celestron, discovery или mount. Если
   приложение требует конкретный SSID/IP, это и есть полезный результат CAP-01.

## C. SkyPortal

1. Запустите probe с `--client SkyPortal --scenario CAP-01`.
2. В SkyPortal выберите тип подключения `Celestron WiFi` только в рамках
   экранного сценария приложения; не подключайте mount и не включайте motor board.
3. Попробуйте указать/выбрать доступный PC endpoint, если приложение позволяет.
4. Не выполняйте alignment, movement, GoTo, tracking или STOP.
5. Остановите probe и сохраните созданный capture directory без редактирования.

## D. SkySafari

1. Выполните отдельный запуск с `--client SkySafari --scenario CAP-01`.
2. В SkySafari выберите Celestron WiFi connection workflow.
3. Повторите только попытку connection; не запускайте telescope controls.
4. Сохраните отдельный capture directory.

## E. Сценарии

| ID | Действия | Ожидаемый evidence |
| --- | --- | --- |
| CAP-01 | Запуск приложения и попытка connection к PC probe. | TCP bytes, UDP datagrams либо доказательство, что connection не доходит до socket. |
| CAP-02 | После disconnect повторить connection в новом запуске probe. | Reconnect/lifecycle bytes и отличие от CAP-01. |
| CAP-03 | Только после подтверждённого initial handshake: оставить connection idle 60 секунд. | Keepalive/poll/timeout evidence. |
| CAP-04 | Только после подтверждённого initial handshake: открыть telescope/control screen. | Дополнительные probes без движения. |

Position poll, manual slew, STOP, GoTo и tracking запрещены на этом этапе. Они
возможны только после отдельной доказательной спецификации безопасных replies.

## Если probe ничего не получает

Не добавляйте synthetic AUX replies и не меняйте protocol по догадке. Зафиксируйте
это в notes. Для UDP broadcast/discovery, multicast, Wi-Fi traffic до PC socket
или поведения приложения, которое не достигает listener, может понадобиться
отдельный Wireshark/pcap capture на PC network adapter. Wireshark не требуется,
если собственный probe уже получает нужные application bytes.
