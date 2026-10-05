#include "celestron_aux/VirtualMotorControllers.h"

namespace nxw436::celestron_aux {

const VirtualMotorConfig* VirtualMotorControllers::get(
    const std::uint8_t destination) const {
    if (destination == kAzmAddress) return &configs_[0];
    if (destination == kAltAddress) return &configs_[1];
    return nullptr;
}

VirtualMotorConfig* VirtualMotorControllers::get(
    const std::uint8_t destination) {
    return const_cast<VirtualMotorConfig*>(
        static_cast<const VirtualMotorControllers*>(this)->get(destination));
}

}  // namespace nxw436::celestron_aux
