# Сетевой протокол Celestron: исследовательская спецификация

## Статус и границы

Этот документ описывает только подтверждённые и явно классифицированные данные
для будущего PC-hosted frontend над `MountController` и `FakeMountBackend`.
Он не разрешает реализацию frontend, работу с COM, реальным телескопом или ESP32.

Уровни доказательности:

| Метка | Значение |
| --- | --- |
| `CONFIRMED_PRIMARY` | Официальный источник Celestron или первичная спецификация. |
| `CONFIRMED_MULTIPLE_IMPLEMENTATIONS` | Несколько независимых зрелых реализаций согласуются. |
| `STRONG_REVERSE_ENGINEERING_EVIDENCE` | Исходный код/анализ реализации с конкретным техническим следствием. |
| `WEAK/ANECDOTAL` | Единственный неофициальный источник или форум. |
| `UNKNOWN` | Недостаточно данных; необходим capture или эксперимент. |

## Главный вывод

Для `Celestron WiFi` нельзя безопасно предполагать ASCII `NexStar Serial
Protocol` непосредственно поверх TCP. Две внешние реализации совместимого
WiFi bridge принимают TCP-клиента на `1.2.3.4:2000` и передают бинарные AUX
frames с `SOM=0x3B`. Поэтому рабочая гипотеза первого исследования: это
бинарный AUX transport/tunnel поверх TCP.

Статус: `STRONG_REVERSE_ENGINEERING_EVIDENCE` для реализации bridge и framing;
`UNKNOWN` для полного фактического начального exchange именно SkyPortal и
SkySafari. Внешние реализации не заменяют capture целевых приложений,
официальную wire specification или доказательство их независимости. Следовательно
данный вывод не является обещанием совместимости первой реализации с обоими
приложениями.

## Источники

| ID | Источник | Тип |
| --- | --- | --- |
| S1 | https://www.celestron.com/products/skyportal-wifi-module | `CONFIRMED_PRIMARY`: модуль соединяется через AUX, поддерживает SkyPortal и Direct Connect/Access Point. Wire format не опубликован. |
| S2 | https://raw.githubusercontent.com/alex-vg/esp-skyportal-module/master/ESPSkyPortalModule/ESPSkyPortalModule.ino | `STRONG_REVERSE_ENGINEERING_EVIDENCE`: `WiFiServer(2000)`, `1.2.3.4`, UDP `55555`, буферизация `0x3B`, length. |
| S3 | https://raw.githubusercontent.com/Wixely/CelestronESPWifi/main/CelestronESPWifi.ino | `STRONG_REVERSE_ENGINEERING_EVIDENCE`: `WiFiServer(2000)` на `1.2.3.4`; TCP байты передаются в AUX-oriented UART bridge. |
| S4 | https://raw.githubusercontent.com/rpineau/SkyPortalWiFi/master/SkyPortalWiFi.h | `STRONG_REVERSE_ENGINEERING_EVIDENCE`: AUX addresses, `MC_*` command identifiers и 24-bit motor position у стандартного AUX mount. |
| S5 | https://raw.githubusercontent.com/rpineau/SkyPortalWiFi/master/SkyPortalWiFi.cpp | `STRONG_REVERSE_ENGINEERING_EVIDENCE`: parsing `SOM`, length, checksum, запросы `MC_GET_VER`, `MC_GET_POSITION`, `MC_SLEW_DONE`, движение и GoTo. |
| S6 | http://www.celestron.com/c3/images/files/downloads/1154108406_nexstarcommprot.pdf | `CONFIRMED_PRIMARY` для historical NexStar Serial Protocol; это отдельный протокол, не доказанный WiFi transport. |
| S7 | `research/KILO_HANDOFF.md`, `research/MOUNT_CONTROL_ARCHITECTURE.md`, `AGENTS.md` | `CONFIRMED_PRIMARY` для локальных границ, семантики API и NXW436. |

## Transport, discovery и session

| Факт | Статус | Последствие |
| --- | --- | --- |
| Официальный SkyPortal WiFi Module подключается к AUX и имеет Direct Connect/Access Point. | `CONFIRMED_PRIMARY`, S1 | Он не является доказательством доступности AUX на нашей NXW436 J1. |
| Внешние bridge используют TCP server на `1.2.3.4:2000`; приложение предполагается TCP client. | `STRONG_REVERSE_ENGINEERING_EVIDENCE`, S2, S3 | Это кандидат transport для offline emulator, а не подтверждённый contract целевых приложений. |
| UDP broadcast с JSON на `255.255.255.255:55555`. | `STRONG_REVERSE_ENGINEERING_EVIDENCE`, S2 | Наблюдается в одном проекте; необходимость для SkyPortal/SkySafari не доказана. |
| TCP reconnect, idle timeout, keepalive, множественные клиенты и точная роль discovery. | `UNKNOWN` | Не фиксировать контракт без capture. Первый стенд должен иметь один явно ограниченный client session. |

## Framing

Наблюдаемый AUX frame:

```text
0x3B | length | source | destination | command | payload | checksum
```

- `length` покрывает `source`, `destination`, `command` и `payload`.
- Полный размер кадра: `length + 3` байта.
- `checksum` является аддитивным дополнением до нуля суммы байтов начиная с
  `length` и заканчивая последним байтом payload.
- Это binary stream, не ASCII и не протокол с terminator `#`.

Статус: `STRONG_REVERSE_ENGINEERING_EVIDENCE` для структуры и длины, S2/S5;
`STRONG_REVERSE_ENGINEERING_EVIDENCE` для алгоритма checksum, S5.

TCP не сохраняет message boundaries. Будущий parser обязан принимать
fragmented frame, несколько frames из одного read, мусор до `0x3B`, malformed
length и checksum. Нельзя сопоставлять один `recv()` одному frame.

## AUX и NexStar Serial Protocol

`NexStar Serial Protocol` из S6 использует ASCII-команды с ответами, обычно
оканчивающимися `#`: например `V`, `m`, `E`/`e`, `Z`/`z`, `R`/`r`, `B`/`b`,
`L`, `M`, `T`/`t`, `W`/`w`, `H`/`h`. Его normal/precise coordinate formats
кодируют долю полного круга как hex ASCII.

Статус этой спецификации: `CONFIRMED_PRIMARY` для serial protocol, S6.
Статус переноса этих ASCII команд в Celestron WiFi: `UNKNOWN`.

Будущий frontend не должен реализовывать ASCII serial protocol как замену
AUX framing без capture, подтверждающего такой режим у целевого клиента.

## Наблюдаемые AUX command candidates

Идентификаторы ниже взяты из S4/S5 и относятся к стандартному AUX mount,
не к NXW436 J1:

| AUX command | Смысл в источнике | Статус |
| --- | --- | --- |
| `MC_GET_VER` (`0xFE`) | Версия motor controller. | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_GET_POSITION` (`0x01`) | 24-bit position стандартной AUX оси. | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_SLEW_DONE` (`0x13`) | Опрос завершения slew. | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_MOVE_POS`/`MC_MOVE_NEG` (`0x24`/`0x25`) | Open-loop move; в реализации rate `0` используется для stop. | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_GOTO_FAST`/`MC_GOTO_SLOW` (`0x02`/`0x17`) | GoTo стандартного AUX motor controller. | `STRONG_REVERSE_ENGINEERING_EVIDENCE` |
| `MC_SET_POSITION` (`0x04`) | Меняет position стандартной AUX оси. | `STRONG_REVERSE_ENGINEERING_EVIDENCE`; исключить из первого milestone. |

Неизвестно, какие из этих кадров SkyPortal либо SkySafari отправляют сразу
после connection, какие responses обязательны и как они обрабатывают error.
Статус: `UNKNOWN`; требуются captures обоих клиентов.

## Координаты и локальная граница

S4/S5 предполагают 24-bit AUX motor coordinates с modulus `2^24`. Наша
NXW436 использует raw encoder modulus `0x102A00`, подтверждённый локально
(S7). Raw count NXW436 не является сетевой AUX coordinate и не должен
выдаваться клиенту.

До GoTo или осмысленного position response нужны явные, протестированные
преобразования:

```text
NXW436 raw count <-> механический угол <-> virtual client coordinate
```

Нулевые точки, знаки, пределы ALT, политика AZ wrap и alignment пока
`UNKNOWN`. RA/DEC дополнительно требует времени, location и alignment;
в этот milestone не входит.

В частности, преобразование вида `raw * 2^24 / POSITION_MODULUS` не является
допустимым generic transform. Его нельзя применять без отдельной спецификации
reference zero, orientation, limits, wrap policy и offline tests.

## Минимальный будущий PC frontend

Ниже только proposal для первого offline implementation, не подтверждённая
совместимость с SkyPortal/SkySafari.

| Возможность | Запрос/ответ | MountController | Статус и безопасное поведение |
| --- | --- | --- | --- |
| TCP session | Frontend слушает `1.2.3.4:2000`; телефонный client подключается к нему. Один client session. | Нет. | `STRONG_REVERSE_ENGINEERING_EVIDENCE` для endpoint; single-client - явное ограничение offline emulator. |
| Frame parser/serializer | AUX frame `0x3B ... checksum`. | Нет. | Framing `STRONG_REVERSE_ENGINEERING_EVIDENCE`; reply address mapping, payload и обязательные responses `UNKNOWN`. |
| Version probe | `MC_GET_VER` к AZ/ALT; возможен только сформированный frontend test-only response. | Нет. | Command meaning `STRONG_REVERSE_ENGINEERING_EVIDENCE`; address mapping и response bytes `UNKNOWN`. Не выдавать это за mount response или compatibility behavior. |
| Read position | `MC_GET_POSITION` к AZ/ALT; только virtual coordinate после отдельного transform. | `get_az_position()` / `get_alt_position()`. | Не включать, пока transform, address mapping и response bytes не специфицированы и не протестированы. |
| Slew status | `MC_SLEW_DONE`; только frontend-owned operation state. | `get_axis_status(axis)` и сохранённый frontend operation state. | `UNKNOWN` вне операции, начатой текущим frontend session; не отражает физическое отсутствие движения, acquisition или состояние после restart/disconnect. |

Manual move, STOP и GoTo исключены из первого минимального subset до capture и
отдельных contracts mapping rate/coordinate. Если их добавят позднее:

- `MC_MOVE_POS`/`MC_MOVE_NEG` могут вызывать только named `SpeedTier`, а не
  payload или NXW436 details;
- network STOP при `safe_stop_available=False` обязан вернуться как явный
  unsupported/failure policy. Frontend не угадывает direction, не посылает оба
  STOP prefix и не обещает emergency stop;
- GoTo обязан различать bounded completion и target acquisition, используя
  `target_acquired`, `final_position`, `final_error_counts`, `settled_outcome`;
- tracking, `00001E`, time/location и alignment исключены: их network contract
  либо backend implementation не доказаны.

## Необходимые captures перед compatibility claim

1. Первый TCP stream SkyPortal и SkySafari до и после выбора `Celestron WiFi`.
2. Version/model/address probes и точные replies.
3. Alignment flow, position poll, manual slew/stop, GoTo/cancel и completion poll.
4. Реакция на malformed/unknown frame, backend error, disconnect/reconnect.
5. UDP `55555` в Direct Connect и Access Point режимах.
6. Time/location/tracking traffic, только если оно фактически возникает.

До этих captures результатом milestone является документированный offline
transport experiment, а не заявленная совместимость с приложениями.

## План offline tests

Все следующие тесты относятся только к test-only TCP AUX emulator над
`MountController(FakeMountBackend)` или injected stub. Они не являются
compatibility tests SkyPortal/SkySafari: до отдельных captures обоих приложений
command addresses, payload, replies, timeouts, discovery и session sequence
остаются `UNKNOWN`.

| Группа | Stimulus | Ожидаемый offline outcome |
| --- | --- | --- |
| F-01 | Один полный валидный synthetic frame. | Ровно один dispatch; reply имеет корректные `length` и checksum. |
| F-02/F-03 | Разрезать валидный frame на каждой границе и побайтно. | Нет dispatch до последнего байта; затем ровно тот же результат, что F-01. |
| F-04/F-05 | Несколько frames в одном read; полный frame плюс начало следующего. | Frames обрабатываются по порядку; неполный tail буферизуется без потери и дублирования. |
| F-06--F-11 | Мусор, invalid/oversized `length`, bad checksum, malformed или unknown command. | Backend не вызывается для невалидного input; parser восстанавливается или session закрывается по заранее документированной policy; следующий валидный frame не блокируется. |
| F-12 | Повтор одного synthetic request с разной fragmentation. | Serializer выдаёт байтово идентичный synthetic reply; он не объявляется mount reply. |
| S-01--S-04 | EOF с неполным frame, reconnect, второй client, освобождение ownership. | Очищаются session buffer/state; один client policy детерминирована; после cleanup новый client может подключиться. |
| B-01--B-03 | `MountError`, неожидаемый `RuntimeError`, injected timeout. | Listener не падает и не блокируется; traceback не уходит в TCP; outcome и logging следуют локальной policy. |
| M-01 | `safe_stop_available=False`. | STOP не вызывает `stop_*`, не выбирает `Direction`, не посылает оба prefix; результат - явный unsupported/failure outcome. |
| M-02 | Известное направление в `FakeMountBackend`. | Будущий adapter вызывает только neutral `stop_*`; frontend не знает prefix или direction. |
| C-01--C-04 | Position request без transform; затем raw wrap в fake backend. | Raw NXW436 count, `POSITION_MODULUS`, `nxw436_*`, serial/COM/J1 details не появляются в frontend output/imports; virtual coordinate запрещена до approved transform. |
| G-01--G-03 | Stub GoTo возвращает `completed=True`, `target_acquired=False`; fake exact GoTo; session status после reconnect. | `completed` не выдается за acquisition; status ограничен frontend-owned operation state и не утверждает physical stillness. |
| H-01/H-02 | Fail-fast guards на `serial.Serial`, COM-like `open`, hardware transport constructors и import frontend. | Не открывается COM, не создаётся `NXW436MountBackend`, не запускается hardware CLI; применяются только fake/stub backends. |

Перед реализацией необходимо определить как локальные resource/safety contracts,
не приписывая их Celestron клиентам: minimum/maximum `length`, maximum buffer,
resync policy, checksum/error policy, idle/backend/write timeout, reply queue
limit, slow-client policy, освобождение ownership при EOF/error/timeout и
structured logging без неограниченного hex dump.

`FakeMountBackend` не моделирует UART faults, coast, механику или timing.
Timeout/error tests используют injected stub, а не расширяют fake до ложной
модели NXW436. Move, STOP, GoTo и tracking остаются отключёнными до captures,
фиксирующих соответствующие client frames и expected responses.
