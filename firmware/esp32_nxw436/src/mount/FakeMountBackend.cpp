#include "mount/FakeMountBackend.h"

namespace nxw436::mount {

FakeMountBackend::FakeMountBackend(const std::uint32_t az,
                                   const std::uint32_t alt) {
    positions_[0] = domain::normalizePosition(az);
    positions_[1] = domain::normalizePosition(alt);
}

std::size_t FakeMountBackend::index(const Axis axis) {
    return axis == Axis::Az ? 0U : 1U;
}

bool FakeMountBackend::getPosition(const Axis axis, std::uint32_t& position) {
    position = positions_[index(axis)];
    return true;
}

bool FakeMountBackend::move(const Axis axis, const Direction direction,
                            const SpeedTier speed) {
    const auto i = index(axis);
    moving_[i] = true;
    has_direction_[i] = true;
    directions_[i] = direction;
    speeds_[i] = speed;
    return true;
}

bool FakeMountBackend::stop(const Axis axis) {
    const auto i = index(axis);
    if (!has_direction_[i]) {
        return false;
    }
    moving_[i] = false;
    return true;
}

AxisStatus FakeMountBackend::getAxisStatus(const Axis axis) {
    const auto i = index(axis);
    return AxisStatus{axis, positions_[i], moving_[i], has_direction_[i],
                      directions_[i], has_direction_[i]};
}

void FakeMountBackend::setPosition(const Axis axis,
                                   const std::uint32_t position) {
    positions_[index(axis)] = domain::normalizePosition(position);
}

void FakeMountBackend::advance(const Axis axis, const std::int32_t counts) {
    positions_[index(axis)] = domain::normalizePosition(
        static_cast<std::int64_t>(positions_[index(axis)]) + counts);
}

void FakeMountBackend::tick(const std::uint32_t elapsed_ms) {
    static constexpr std::array<std::int32_t, 6> kCountsPerSecond{
        1000, 200, 50, 10, 10, 1000,
    };
    for (std::size_t i = 0; i < positions_.size(); ++i) {
        if (!moving_[i]) continue;
        const auto speed = kCountsPerSecond[static_cast<std::size_t>(speeds_[i])];
        auto milli_counts = remainder_milli_counts_[i]
                          + static_cast<std::int64_t>(speed) * elapsed_ms;
        auto whole = milli_counts / 1000;
        remainder_milli_counts_[i] = milli_counts % 1000;
        if (directions_[i] == Direction::Minus) whole = -whole;
        positions_[i] = domain::normalizePosition(
            static_cast<std::int64_t>(positions_[i]) + whole);
    }
}

}  // namespace nxw436::mount
