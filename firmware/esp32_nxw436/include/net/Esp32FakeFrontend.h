#pragma once

#ifdef ARDUINO

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include <array>
#include <cstdint>
#include <memory>

#include "celestron_aux/AuxCoordinateAdapter.h"
#include "celestron_aux/AuxStreamParser.h"
#include "celestron_aux/SemanticCore.h"
#include "mount/FakeMountBackend.h"
#include "mount/FakeMountRuntime.h"
#include "net/Hbg3Advertisement.h"

#ifdef NXW436_M3_READONLY
#include "nxw436/Esp32J1ReadOnlyUart.h"
#include "nxw436/Nxw436ReadOnlyBackend.h"
#endif

namespace nxw436::net {

class Esp32FakeFrontend {
public:
    Esp32FakeFrontend();
    void begin();
    void poll();

private:
    static constexpr std::uint16_t kAuxPort = 2000;
    static constexpr std::uint16_t kAdvertisementPort = 55555;
    static constexpr std::uint32_t kAdvertisementPeriodMs = 1000;
    static constexpr std::uint32_t kClientIdleTimeoutMs = 15000;
    static constexpr std::size_t kNetworkReadBuffer = 128;
    static constexpr std::size_t kMaxLoggedFrameBytes = 32;

    void acceptClient();
    void closeClient(const char* reason);
    void receiveClient();
    void processEvent(const celestron_aux::ParserEvent& event,
                      std::uint32_t epoch, std::uint32_t request_id);
    void advertise();
    void logFrame(const char* prefix, const std::uint8_t* data,
                  std::size_t length, std::uint32_t epoch = 0,
                  std::uint32_t request_id = 0);
    void logGotoTransitions();
    void logLifecycle(const char* event, const char* reason = nullptr);
    void logEvent(const char* event, const char* format, ...);

#ifdef NXW436_M3_READONLY
    j1::Esp32J1ReadOnlyUart uart_{};
    j1::Nxw436ReadOnlyBackend backend_{uart_};
    std::unique_ptr<celestron_aux::AuxCoordinateAdapter> adapter_;
    std::unique_ptr<celestron_aux::SemanticCore> core_;
#else
    mount::FakeMountBackend backend_;
    celestron_aux::AuxCoordinateAdapter adapter_;
    celestron_aux::SemanticCore core_;
    mount::FakeMountRuntime runtime_;
#endif
    celestron_aux::AuxStreamParser parser_;
    WiFiServer server_{kAuxPort};
    WiFiUDP udp_;
    WiFiClient client_;
    std::uint32_t connection_epoch_{};
    std::uint32_t client_last_rx_ms_{};
    std::uint32_t client_connected_ms_{};
    std::uint32_t next_advertisement_ms_{};
    std::uint32_t advertisement_count_{};
    std::uint32_t rx_bytes_{};
    std::uint32_t tx_complete_{};
    std::uint32_t tx_short_{};
    std::uint32_t parser_errors_{};
    std::uint32_t event_sequence_{};
    std::uint32_t request_sequence_{};
    std::array<std::uint8_t, kNetworkReadBuffer> rx_buffer_{};
    std::array<celestron_aux::GotoState, 2> last_goto_states_{};
    Hbg3Identity identity_{};
};

}  // namespace nxw436::net

#endif
