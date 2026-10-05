#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace nxw436::net {

struct Hbg3Identity {
    std::array<char, 32> ssid{};
    std::array<char, 192> advertisement{};
    std::size_t advertisement_length{};
};

Hbg3Identity buildHbg3Identity(const std::array<std::uint8_t, 6>& mac);

}  // namespace nxw436::net
