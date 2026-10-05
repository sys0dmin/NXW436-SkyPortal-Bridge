#include "net/Esp32FakeFrontend.h"

#ifdef ARDUINO

#include "celestron_aux/AuxCodec.h"
#include "domain/PositionMath.h"
#include "net/TcpTiming.h"

extern "C" {
#include "dhcpserver/dhcpserver_options.h"
#include "esp_netif.h"
#include "lwip/sockets.h"
}

#include <cerrno>
#include <cstdarg>

namespace nxw436::net {

namespace {
#ifndef NXW436_M3_READONLY
celestron_aux::AxisCoordinateConfig fakeAxisConfig() {
    return {domain::kPositionModulus, 0, 0, 1, true, 0, 0};
}
#endif

bool configureDirectConnectNetwork() {
    auto* netif = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
    if (netif == nullptr) return false;
    auto error = esp_netif_dhcps_stop(netif);
    if (error != ESP_OK && error != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
        return false;
    }
    esp_netif_ip_info_t info{};
    info.ip.addr = static_cast<std::uint32_t>(IPAddress(1, 2, 3, 4));
    info.gw.addr = static_cast<std::uint32_t>(IPAddress(1, 2, 3, 4));
    info.netmask.addr = static_cast<std::uint32_t>(IPAddress(255, 255, 255, 240));
    if (esp_netif_set_ip_info(netif, &info) != ESP_OK) return false;

    dhcps_lease_t lease{};
    lease.enable = true;
    lease.start_ip.addr = static_cast<std::uint32_t>(IPAddress(1, 2, 3, 5));
    lease.end_ip.addr = static_cast<std::uint32_t>(IPAddress(1, 2, 3, 14));
    if (esp_netif_dhcps_option(netif, ESP_NETIF_OP_SET,
                               ESP_NETIF_REQUESTED_IP_ADDRESS,
                               &lease, sizeof(lease)) != ESP_OK) {
        return false;
    }
    return esp_netif_dhcps_start(netif) == ESP_OK;
}

enum class PeerState : std::uint8_t { Open, Fin, Interrupted, SocketError };

PeerState peerState(WiFiClient& client, int& result, int& error) {
    if (!client || client.fd() < 0) {
        result = 0;
        error = ENOTCONN;
        return PeerState::Fin;
    }
    std::uint8_t byte{};
    result = recv(client.fd(), &byte, 1, MSG_PEEK | MSG_DONTWAIT);
    error = errno;
    if (result == 0) return PeerState::Fin;
    if (result > 0 || error == EWOULDBLOCK || error == EAGAIN) return PeerState::Open;
    if (error == EINTR) return PeerState::Interrupted;
    return PeerState::SocketError;
}
}  // namespace

Esp32FakeFrontend::Esp32FakeFrontend()
#ifdef NXW436_M3_READONLY
    : parser_(512),
      last_goto_states_{celestron_aux::GotoState::Idle,
                        celestron_aux::GotoState::Idle} {}
#else
    : backend_(16, 32),
      adapter_(fakeAxisConfig(), fakeAxisConfig()),
      core_(backend_, adapter_, {false, false}),
      runtime_(backend_, core_),
      parser_(512),
      last_goto_states_{celestron_aux::GotoState::Idle,
                        celestron_aux::GotoState::Idle} {}
#endif

void Esp32FakeFrontend::begin() {
    Serial.begin(115200);
#ifdef NXW436_M3_READONLY
    Serial.println("m3_profile=REAL_NXW436_READ_ONLY rx=GPIO16 tx=GPIO17 oe=GPIO27");
    if (!backend_.begin()) {
        Serial.println("m3_readonly_init_failed stage=uart_begin");
        return;
    }
    std::uint32_t az_zero{};
    std::uint32_t alt_zero{};
    if (!backend_.getPosition(mount::Axis::Az, az_zero) ||
        !backend_.getPosition(mount::Axis::Alt, alt_zero)) {
        Serial.println("m3_readonly_init_failed stage=initial_position");
        uart_.disableTx();
        return;
    }
    celestron_aux::AxisCoordinateConfig az{domain::kPositionModulus, az_zero, 0, 1, true, 0, 0};
    celestron_aux::AxisCoordinateConfig alt{domain::kPositionModulus, alt_zero, 0, 1, true, 0, 0};
    adapter_ = std::make_unique<celestron_aux::AuxCoordinateAdapter>(az, alt);
    core_ = std::make_unique<celestron_aux::SemanticCore>(backend_, *adapter_,
                                                           celestron_aux::SemanticProfile{false, false});
    Serial.printf("m3_readonly_ready az_zero=%06lX alt_zero=%06lX\n",
                  static_cast<unsigned long>(az_zero), static_cast<unsigned long>(alt_zero));
#endif
    std::uint8_t mac[6]{};
    WiFi.mode(WIFI_AP);
    WiFi.softAPmacAddress(mac);
    identity_ = buildHbg3Identity({mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]});
    const bool ap_ok = WiFi.softAP(identity_.ssid.data());
    const bool config_ok = ap_ok && configureDirectConnectNetwork();
    WiFi.softAPmacAddress(mac);
    server_.begin();
    const bool udp_ok = udp_.begin(0);
    next_advertisement_ms_ = millis();
    Serial.printf("wifi_state=SOFTAP_READY ssid=%s ip=1.2.3.4/28\n",
                  identity_.ssid.data());
    Serial.printf("wifi_init softap_config=%u softap=%u udp=%u\n",
                  config_ok, ap_ok, udp_ok);
    Serial.printf("tcp_server_started port=%u\n", kAuxPort);
}

void Esp32FakeFrontend::poll() {
    const auto now = millis();
#ifndef NXW436_M3_READONLY
    runtime_.tick(now);
#endif
    logGotoTransitions();

    int peek_result{};
    int peek_errno{};
    const auto state = peerState(client_, peek_result, peek_errno);
    if (client_ && state == PeerState::Fin) {
        Serial.printf("peer_probe t_ms=%lu epoch=%lu result=%d errno=%d state=FIN\n",
                      static_cast<unsigned long>(now), static_cast<unsigned long>(connection_epoch_),
                      peek_result, peek_errno);
        closeClient("peer_fin");
    } else if (client_ && state == PeerState::SocketError) {
        Serial.printf("peer_probe t_ms=%lu epoch=%lu result=%d errno=%d state=ERROR\n",
                      static_cast<unsigned long>(now), static_cast<unsigned long>(connection_epoch_),
                      peek_result, peek_errno);
        closeClient("peer_socket_error");
    } else if (client_ && !client_.connected()) {
        closeClient("disconnect");
    }
    acceptClient();
    receiveClient();

    const auto idle_now = millis();
    if (client_ && elapsedAtLeast(idle_now, client_last_rx_ms_, kClientIdleTimeoutMs)) {
        closeClient("idle_timeout");
    }
    if (!client_ && static_cast<std::int32_t>(now - next_advertisement_ms_) >= 0) {
        advertise();
        next_advertisement_ms_ = now + kAdvertisementPeriodMs;
    }
    delay(1);
}

void Esp32FakeFrontend::acceptClient() {
    if (client_ && client_.connected()) return;
    auto incoming = server_.available();
    if (!incoming) return;
    client_ = incoming;
    parser_.reset();
    ++connection_epoch_;
    client_last_rx_ms_ = millis();
    client_connected_ms_ = client_last_rx_ms_;
    Serial.printf("client_connected t_ms=%lu epoch=%lu peer=%s:%u\n",
                  static_cast<unsigned long>(client_connected_ms_),
                  static_cast<unsigned long>(connection_epoch_),
                  client_.remoteIP().toString().c_str(), client_.remotePort());
    logLifecycle("tcp_accept");
}

void Esp32FakeFrontend::closeClient(const char* reason) {
    logLifecycle("client_disconnected", reason);
    if (parser_.buffered() != 0) {
        Serial.printf("parser_discard_on_close t_ms=%lu epoch=%lu buffered=%u\n",
                      static_cast<unsigned long>(millis()),
                      static_cast<unsigned long>(connection_epoch_),
                      static_cast<unsigned>(parser_.buffered()));
    }
    client_.stop();
    parser_.reset();
}

void Esp32FakeFrontend::receiveClient() {
    if (!client_ || !client_.connected()) return;
    while (client_.available() > 0) {
        const auto available = static_cast<std::size_t>(client_.available());
        const auto wanted = available < rx_buffer_.size() ? available : rx_buffer_.size();
        const auto received = client_.read(rx_buffer_.data(), wanted);
        if (received <= 0) return;
        client_last_rx_ms_ = millis();
        const auto epoch = connection_epoch_;
        rx_bytes_ += static_cast<std::uint32_t>(received);
        Serial.printf("tcp_rx_bytes t_ms=%lu epoch=%lu count=%d\n",
                      static_cast<unsigned long>(client_last_rx_ms_),
                      static_cast<unsigned long>(epoch), received);
        for (int i = 0; i < received; ++i) {
            celestron_aux::ParserEvent event{};
            if (parser_.feedByte(rx_buffer_[static_cast<std::size_t>(i)], event)) {
                processEvent(event, epoch, ++request_sequence_);
            }
        }
    }
}

void Esp32FakeFrontend::processEvent(const celestron_aux::ParserEvent& event,
                                     const std::uint32_t epoch,
                                     const std::uint32_t request_id) {
    if (!event.valid) {
        ++parser_errors_;
        logEvent("parser_error", "epoch=%lu req=%lu code=%u",
                 static_cast<unsigned long>(epoch),
                 static_cast<unsigned long>(request_id),
                 static_cast<unsigned>(event.error));
        logFrame("aux_rx_invalid", event.raw.bytes.data(), event.raw.length, epoch, request_id);
        return;
    }
    logFrame("aux_rx", event.raw.bytes.data(), event.raw.length, epoch, request_id);
    logEvent("semantic_begin", "epoch=%lu req=%lu src=%02X dst=%02X cmd=%02X payload_len=%u",
             static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
             event.frame.source, event.frame.destination, event.frame.command,
             static_cast<unsigned>(event.frame.payload_length));
#ifdef NXW436_M3_READONLY
    if (!core_) {
        Serial.println("semantic_rejected reason=m3_not_ready");
        return;
    }
    const auto outcome = core_->dispatch(event.frame);
    if (event.frame.command == 0x01 &&
        (event.frame.destination == celestron_aux::kAzmAddress ||
         event.frame.destination == celestron_aux::kAltAddress)) {
        const auto axis = event.frame.destination == celestron_aux::kAzmAddress
            ? mount::Axis::Az : mount::Axis::Alt;
        const auto& read = backend_.lastRead(axis);
        if (read.status == j1::UartReadStatus::Ok) {
            const auto raw = (static_cast<std::uint32_t>(read.raw[0]) << 16)
                           | (static_cast<std::uint32_t>(read.raw[1]) << 8)
                           | read.raw[2];
            logEvent("j1_position", "epoch=%lu req=%lu axis=%s j1_tx=%02X j1_rx=%02X%02X%02X native=%lu aux_pending=true transaction_us=%lu",
                          static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
                          axis == mount::Axis::Az ? "AZ" : "ALT", read.query,
                          read.raw[0], read.raw[1], read.raw[2],
                          static_cast<unsigned long>(raw),
                          static_cast<unsigned long>(read.duration_us));
        } else {
            logEvent("j1_read", "epoch=%lu req=%lu status=%s axis=%s j1_tx=%02X duration_us=%lu",
                          static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
                          read.status == j1::UartReadStatus::Timeout ? "timeout" :
                          read.status == j1::UartReadStatus::Partial ? "partial" :
                          read.status == j1::UartReadStatus::Desync ? "desync" : "error",
                          axis == mount::Axis::Az ? "AZ" : "ALT", read.query,
                          static_cast<unsigned long>(read.duration_us));
        }
    }
#else
    const auto outcome = core_.dispatch(event.frame);
#endif
    const bool config_only = event.frame.command == 0x06 || event.frame.command == 0x07 ||
                             event.frame.command == 0x46 || event.frame.command == 0x47;
    logEvent("semantic_end", "epoch=%lu req=%lu dst=%02X cmd=%02X status=%u motion_applied=%s",
             static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
             event.frame.destination, event.frame.command,
             static_cast<unsigned>(outcome.status), config_only ? "false" : "unknown");
    if (!outcome.has_reply) return;
    celestron_aux::AuxWireBuffer tx{};
    celestron_aux::AuxError error{};
    if (!celestron_aux::serializeAux(outcome.reply, tx, error)) {
        Serial.printf("aux_tx_encode_error code=%u\n",
                      static_cast<unsigned>(error));
        return;
    }
    if (epoch != connection_epoch_ || !client_ || !client_.connected()) {
        Serial.printf("stale_response_drop response_epoch=%lu active_epoch=%lu\n",
                      static_cast<unsigned long>(epoch),
                      static_cast<unsigned long>(connection_epoch_));
        return;
    }
    logEvent("tcp_tx_begin", "epoch=%lu req=%lu bytes=%u",
                  static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
                  static_cast<unsigned>(tx.length));
    const auto written = client_.write(tx.bytes.data(), tx.length);
    if (written != tx.length) {
        ++tx_short_;
        logEvent("tcp_tx_short_write", "epoch=%lu req=%lu expected=%u actual=%u fd=%d connected=%u",
                      static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
                      static_cast<unsigned>(tx.length), static_cast<unsigned>(written),
                      client_.fd(), client_.connected());
    } else {
        ++tx_complete_;
        logEvent("tcp_tx_complete", "epoch=%lu req=%lu bytes=%u",
                      static_cast<unsigned long>(epoch), static_cast<unsigned long>(request_id),
                      static_cast<unsigned>(written));
    }
    logFrame("aux_tx", tx.bytes.data(), tx.length, epoch, request_id);
}

void Esp32FakeFrontend::advertise() {
    const IPAddress destination(1, 2, 3, 15);
    udp_.beginPacket(destination, kAdvertisementPort);
    udp_.write(reinterpret_cast<const std::uint8_t*>(identity_.advertisement.data()),
               identity_.advertisement_length);
    udp_.endPacket();
    ++advertisement_count_;
    if (advertisement_count_ == 1 || advertisement_count_ % 10 == 0) {
        Serial.printf("udp_advertisement t_ms=%lu dst=1.2.3.15:%u bytes=%u count=%lu\n",
                      static_cast<unsigned long>(millis()), kAdvertisementPort,
                      static_cast<unsigned>(identity_.advertisement_length),
                      static_cast<unsigned long>(advertisement_count_));
    }
}

void Esp32FakeFrontend::logLifecycle(const char* event, const char* reason) {
    const auto now = millis();
    const auto idle = client_ ? now - client_last_rx_ms_ : 0;
    const auto age = client_ ? now - client_connected_ms_ : 0;
    Serial.printf("%s t_ms=%lu epoch=%lu reason=%s fd=%d connected=%u available=%d rx_idle_ms=%lu age_ms=%lu parser_buffered=%u rx_bytes=%lu tx_complete=%lu tx_short=%lu parser_errors=%lu\n",
                  event, static_cast<unsigned long>(now),
                  static_cast<unsigned long>(connection_epoch_),
                  reason ? reason : "-", client_ ? client_.fd() : -1,
                  client_ ? client_.connected() : 0,
                  client_ ? client_.available() : 0,
                  static_cast<unsigned long>(idle), static_cast<unsigned long>(age),
                  static_cast<unsigned>(parser_.buffered()),
                  static_cast<unsigned long>(rx_bytes_),
                  static_cast<unsigned long>(tx_complete_),
                  static_cast<unsigned long>(tx_short_),
                  static_cast<unsigned long>(parser_errors_));
}

void Esp32FakeFrontend::logFrame(const char* prefix, const std::uint8_t* data,
                                 const std::size_t length,
                                 const std::uint32_t epoch,
                                 const std::uint32_t request_id) {
    Serial.printf("t_ms=%lu seq=%lu event=%s epoch=%lu req=%lu len=%u hex=",
                  static_cast<unsigned long>(millis()),
                  static_cast<unsigned long>(++event_sequence_), prefix,
                  static_cast<unsigned long>(epoch),
                  static_cast<unsigned long>(request_id),
                  static_cast<unsigned>(length));
    const auto shown = length < kMaxLoggedFrameBytes
        ? length : kMaxLoggedFrameBytes;
    for (std::size_t i = 0; i < shown; ++i) Serial.printf("%02X", data[i]);
    if (shown < length) Serial.print("...");
    Serial.println();
}

void Esp32FakeFrontend::logEvent(const char* event, const char* format, ...) {
    Serial.printf("t_ms=%lu seq=%lu event=%s ",
                  static_cast<unsigned long>(millis()),
                  static_cast<unsigned long>(++event_sequence_), event);
    char message[224]{};
    va_list arguments;
    va_start(arguments, format);
    std::vsnprintf(message, sizeof(message), format, arguments);
    va_end(arguments);
    Serial.println(message);
}

void Esp32FakeFrontend::logGotoTransitions() {
    const mount::Axis axes[] = {mount::Axis::Az, mount::Axis::Alt};
    for (std::size_t i = 0; i < 2; ++i) {
#ifdef NXW436_M3_READONLY
        if (!core_) return;
        const auto state = core_->gotoState(axes[i]);
        const auto target = core_->gotoTarget(axes[i]);
#else
        const auto state = core_.gotoState(axes[i]);
        const auto target = core_.gotoTarget(axes[i]);
#endif
        if (state == last_goto_states_[i]) continue;
        last_goto_states_[i] = state;
        Serial.printf("fake_goto axis=%s state=%u target=%06lX\n",
                      i == 0 ? "AZ" : "ALT", static_cast<unsigned>(state),
                      static_cast<unsigned long>(target));
    }
}

}  // namespace nxw436::net

#endif
