#include "nxw436/Nxw436ReadOnlyBackend.h"

#include "domain/PositionMath.h"

namespace nxw436::j1 {

Nxw436ReadOnlyBackend::Nxw436ReadOnlyBackend(INxw436ReadOnlyUart& uart)
    : uart_(uart) {}

bool Nxw436ReadOnlyBackend::begin() { return uart_.begin(); }

std::size_t Nxw436ReadOnlyBackend::index(const mount::Axis axis) {
    return axis == mount::Axis::Az ? 0U : 1U;
}

bool Nxw436ReadOnlyBackend::getPosition(const mount::Axis axis,
                                        std::uint32_t& position) {
    const auto i = index(axis);
    last_[i] = uart_.queryPosition(axis == mount::Axis::Az);
    if (last_[i].status != UartReadStatus::Ok) {
        valid_[i] = false;
        return false;
    }
    const auto raw = (static_cast<std::uint32_t>(last_[i].raw[0]) << 16)
                   | (static_cast<std::uint32_t>(last_[i].raw[1]) << 8)
                   | last_[i].raw[2];
    if (raw >= domain::kPositionModulus) {
        valid_[i] = false;
        return false;
    }
    positions_[i] = raw;
    valid_[i] = true;
    position = raw;
    return true;
}

bool Nxw436ReadOnlyBackend::move(const mount::Axis, const mount::Direction,
                                 const mount::SpeedTier) {
    return false;
}

bool Nxw436ReadOnlyBackend::stop(const mount::Axis) {
    return false;
}

mount::AxisStatus Nxw436ReadOnlyBackend::getAxisStatus(const mount::Axis axis) {
    const auto i = index(axis);
    return {axis, positions_[i], false, false, mount::Direction::Plus, false};
}

const PositionRead& Nxw436ReadOnlyBackend::lastRead(const mount::Axis axis) const {
    return last_[index(axis)];
}

}  // namespace nxw436::j1
