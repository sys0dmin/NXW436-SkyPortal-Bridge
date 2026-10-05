#include "celestron_aux/AuxCoordinateAdapter.h"

namespace nxw436::celestron_aux {

AuxCoordinateAdapter::AuxCoordinateAdapter(const AxisCoordinateConfig az,
                                           const AxisCoordinateConfig alt)
    : az_(az), alt_(alt) {}

const AxisCoordinateConfig& AuxCoordinateAdapter::config(
    const bool az_axis) const {
    return az_axis ? az_ : alt_;
}

std::int64_t AuxCoordinateAdapter::roundDiv(const std::int64_t numerator,
                                            const std::int64_t denominator) {
    if (numerator >= 0) {
        return (numerator + denominator / 2) / denominator;
    }
    return -((-numerator + denominator / 2) / denominator);
}

std::int64_t AuxCoordinateAdapter::signedDelta(const std::uint32_t value,
                                               const std::uint32_t modulus) {
    return value > modulus / 2
        ? static_cast<std::int64_t>(value) - modulus
        : value;
}

bool AuxCoordinateAdapter::toAux(const bool az_axis,
                                 const std::uint32_t native,
                                 std::uint32_t& aux) const {
    const auto& cfg = config(az_axis);
    if (cfg.native_modulus == 0 || (cfg.direction != 1 && cfg.direction != -1)) {
        return false;
    }
    if (!cfg.wraps && (native < cfg.native_min || native > cfg.native_max)) {
        return false;
    }
    const auto offset = (static_cast<std::int64_t>(native) - cfg.native_zero)
                      * cfg.direction;
    const auto scaled = roundDiv(offset * kAuxFullTurn, cfg.native_modulus);
    auto encoded = (static_cast<std::int64_t>(cfg.aux_zero) + scaled)
                 % kAuxFullTurn;
    if (encoded < 0) {
        encoded += kAuxFullTurn;
    }
    aux = static_cast<std::uint32_t>(encoded);
    return true;
}

bool AuxCoordinateAdapter::fromAux(const bool az_axis, const std::uint32_t aux,
                                   std::uint32_t& native) const {
    const auto& cfg = config(az_axis);
    if (cfg.native_modulus == 0 || aux >= kAuxFullTurn) {
        return false;
    }
    const auto wrapped = (aux + kAuxFullTurn - cfg.aux_zero) % kAuxFullTurn;
    const auto delta = signedDelta(wrapped, kAuxFullTurn);
    auto decoded = static_cast<std::int64_t>(cfg.native_zero)
                 + roundDiv(delta * cfg.native_modulus, kAuxFullTurn)
                 * cfg.direction;
    if (cfg.wraps) {
        decoded %= cfg.native_modulus;
        if (decoded < 0) {
            decoded += cfg.native_modulus;
        }
    } else if (decoded < cfg.native_min || decoded > cfg.native_max) {
        return false;
    }
    native = static_cast<std::uint32_t>(decoded);
    return true;
}

}  // namespace nxw436::celestron_aux
