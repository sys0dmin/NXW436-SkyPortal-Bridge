#pragma once

#include "mount/IMountBackend.h"
#include "nxw436/INxw436ReadOnlyUart.h"

namespace nxw436::j1 {

class Nxw436ReadOnlyBackend final : public mount::IMountBackend {
public:
    explicit Nxw436ReadOnlyBackend(INxw436ReadOnlyUart& uart);
    bool begin();
    bool getPosition(mount::Axis axis, std::uint32_t& position) override;
    bool move(mount::Axis axis, mount::Direction direction,
              mount::SpeedTier speed) override;
    bool stop(mount::Axis axis) override;
    mount::AxisStatus getAxisStatus(mount::Axis axis) override;
    const PositionRead& lastRead(mount::Axis axis) const;

private:
    static std::size_t index(mount::Axis axis);
    INxw436ReadOnlyUart& uart_;
    PositionRead last_[2]{};
    std::uint32_t positions_[2]{};
    bool valid_[2]{};
};

}  // namespace nxw436::j1
