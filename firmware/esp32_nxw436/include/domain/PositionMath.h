#pragma once

#include <cstdint>

namespace nxw436::domain {

constexpr std::uint32_t kPositionModulus = 0x102A00;

std::uint32_t normalizePosition(std::int64_t value,
                                std::uint32_t modulus = kPositionModulus);
std::int32_t signedModularDelta(std::uint32_t start, std::uint32_t target,
                                std::uint32_t modulus = kPositionModulus);

}  // namespace nxw436::domain
