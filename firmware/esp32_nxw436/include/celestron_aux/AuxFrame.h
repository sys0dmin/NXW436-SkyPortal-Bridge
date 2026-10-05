#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace nxw436::celestron_aux {

constexpr std::uint8_t kAuxSom = 0x3B;
constexpr std::size_t kAuxMaxPayload = 252;
constexpr std::size_t kAuxMaxWire = 258;

struct AuxFrame {
    std::uint8_t source{};
    std::uint8_t destination{};
    std::uint8_t command{};
    std::array<std::uint8_t, kAuxMaxPayload> payload{};
    std::size_t payload_length{};

    bool operator==(const AuxFrame& other) const;
};

}  // namespace nxw436::celestron_aux
