#pragma once

#include "mount/MountTypes.h"

namespace nxw436::mount {

class IMountBackend {
public:
    virtual ~IMountBackend() = default;
    virtual bool getPosition(Axis axis, std::uint32_t& position) = 0;
    virtual bool move(Axis axis, Direction direction, SpeedTier speed) = 0;
    virtual bool stop(Axis axis) = 0;
    virtual AxisStatus getAxisStatus(Axis axis) = 0;
};

}  // namespace nxw436::mount
