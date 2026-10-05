#pragma once

#include <cstdint>

namespace nxw436::net {

inline bool elapsedAtLeast(const std::uint32_t now, const std::uint32_t then,
                           const std::uint32_t interval) {
    return static_cast<std::int32_t>(now - then) >= static_cast<std::int32_t>(interval);
}

}  // namespace nxw436::net
