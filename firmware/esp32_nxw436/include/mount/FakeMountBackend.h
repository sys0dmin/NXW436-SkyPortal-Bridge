#pragma once

#include <array>
#include "domain/PositionMath.h"
#include "mount/IMountBackend.h"

namespace nxw436::mount {

class FakeMountBackend final : public IMountBackend {
public:
    explicit FakeMountBackend(std::uint32_t az = 0, std::uint32_t alt = 0);
    bool getPosition(Axis axis, std::uint32_t& position) override;
    bool move(Axis axis, Direction direction, SpeedTier speed) override;
    bool stop(Axis axis) override;
    AxisStatus getAxisStatus(Axis axis) override;

    void setPosition(Axis axis, std::uint32_t position);
    void advance(Axis axis, std::int32_t counts);
    void tick(std::uint32_t elapsed_ms);

private:
    static std::size_t index(Axis axis);
    std::array<std::uint32_t, 2> positions_{};
    std::array<bool, 2> moving_{};
    std::array<bool, 2> has_direction_{};
    std::array<Direction, 2> directions_{};
    std::array<SpeedTier, 2> speeds_{};
    std::array<std::int64_t, 2> remainder_milli_counts_{};
};

}  // namespace nxw436::mount
