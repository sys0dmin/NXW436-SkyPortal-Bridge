#include "net/Hbg3Advertisement.h"

#include <cstdio>
#include <cstring>

namespace nxw436::net {

Hbg3Identity buildHbg3Identity(const std::array<std::uint8_t, 6>& mac) {
    Hbg3Identity identity{};
    std::snprintf(identity.ssid.data(), identity.ssid.size(),
                  "Celestron-%02X%02X%02X", mac[3], mac[4], mac[5]);
    std::snprintf(
        identity.advertisement.data(), identity.advertisement.size(),
        "{\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\",\n"
        "\"version\":\"HomeBrew-AMW007-9.0.0.0, 2021-10-18T12:00:00Z, ESP32-3.8\"\n}",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    identity.advertisement_length = std::strlen(identity.advertisement.data());
    return identity;
}

}  // namespace nxw436::net
