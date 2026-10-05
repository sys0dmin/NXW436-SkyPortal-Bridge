#pragma once

#include <array>
#include <cstdint>

#include "celestron_aux/SemanticCore.h"
#include "mount/FakeMountBackend.h"

namespace nxw436::mount {

class FakeMountRuntime {
public:
    FakeMountRuntime(FakeMountBackend& backend,
                     celestron_aux::SemanticCore& core,
                     std::uint32_t goto_duration_ms = 6000);
    void tick(std::uint32_t now_ms);

private:
    struct AxisRuntime {
        bool goto_active{};
        std::uint32_t started_ms{};
        std::uint32_t start_position{};
        std::uint32_t target_position{};
    };

    void tickAxis(Axis axis, std::uint32_t now_ms);
    FakeMountBackend& backend_;
    celestron_aux::SemanticCore& core_;
    std::uint32_t goto_duration_ms_;
    std::uint32_t last_tick_ms_{};
    bool initialized_{};
    std::array<AxisRuntime, 2> axes_{};
};

}  // namespace nxw436::mount
