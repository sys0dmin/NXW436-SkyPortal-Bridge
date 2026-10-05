#pragma once

#include <cstdint>

namespace nxw436::celestron_aux {

constexpr std::uint32_t kAuxFullTurn = 1U << 24;

struct AxisCoordinateConfig {
    std::uint32_t native_modulus;
    std::uint32_t native_zero;
    std::uint32_t aux_zero;
    std::int8_t direction;
    bool wraps{true};
    std::uint32_t native_min{};
    std::uint32_t native_max{};
};

class AuxCoordinateAdapter {
public:
    AuxCoordinateAdapter(AxisCoordinateConfig az, AxisCoordinateConfig alt);
    bool toAux(bool az_axis, std::uint32_t native, std::uint32_t& aux) const;
    bool fromAux(bool az_axis, std::uint32_t aux, std::uint32_t& native) const;
    const AxisCoordinateConfig& config(bool az_axis) const;

private:
    static std::int64_t roundDiv(std::int64_t numerator,
                                 std::int64_t denominator);
    static std::int64_t signedDelta(std::uint32_t value,
                                    std::uint32_t modulus);
    AxisCoordinateConfig az_;
    AxisCoordinateConfig alt_;
};

}  // namespace nxw436::celestron_aux
