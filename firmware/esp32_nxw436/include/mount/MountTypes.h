#pragma once

#include <cstdint>

namespace nxw436::mount {

enum class Axis : std::uint8_t { Az, Alt };
enum class Direction : std::int8_t { Minus = -1, Plus = 1 };
enum class SpeedTier : std::uint8_t {
    Fast,
    Medium,
    Fine,
    Slow,
    ManualConservative,
    ManualHigh,
};

struct AxisStatus {
    Axis axis;
    std::uint32_t position;
    bool motion_commanded;
    bool has_last_direction;
    Direction last_direction;
    bool safe_stop_available;
};

struct GotoResult {
    Axis axis;
    std::uint32_t start_position;
    std::uint32_t target_position;
    std::int32_t requested_delta;
    bool completed;
    bool target_acquired;
    std::uint32_t final_position;
    std::int32_t final_error_counts;
};

}  // namespace nxw436::mount
