#include "domain/PositionMath.h"

namespace nxw436::domain {

std::uint32_t normalizePosition(const std::int64_t value,
                                const std::uint32_t modulus) {
    const auto normalized = ((value % modulus) + modulus) % modulus;
    return static_cast<std::uint32_t>(normalized);
}

std::int32_t signedModularDelta(const std::uint32_t start,
                                const std::uint32_t target,
                                const std::uint32_t modulus) {
    auto delta = static_cast<std::int64_t>(target) - start;
    const auto half = static_cast<std::int64_t>(modulus / 2);
    if (delta > half) {
        delta -= modulus;
    } else if (delta < -half) {
        delta += modulus;
    }
    return static_cast<std::int32_t>(delta);
}

}  // namespace nxw436::domain
