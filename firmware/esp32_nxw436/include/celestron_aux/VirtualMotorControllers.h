#pragma once

#include <array>
#include <cstdint>

namespace nxw436::celestron_aux {

constexpr std::uint8_t kAzmAddress = 0x10;
constexpr std::uint8_t kAltAddress = 0x11;

struct VirtualMotorConfig {
    std::uint8_t positive_backlash{0x00};
    std::uint8_t approach{0x00};
    std::uint8_t autoguide_rate{0x80};
};

class VirtualMotorControllers {
public:
    const VirtualMotorConfig* get(std::uint8_t destination) const;
    VirtualMotorConfig* get(std::uint8_t destination);

private:
    std::array<VirtualMotorConfig, 2> configs_{};
};

}  // namespace nxw436::celestron_aux
