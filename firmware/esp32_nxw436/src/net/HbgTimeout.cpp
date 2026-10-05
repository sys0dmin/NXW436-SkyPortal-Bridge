#include "net/HbgTimeout.h"

namespace nxw436::net {

bool isHbgTimeoutVersionProbe(const celestron_aux::AuxFrame& frame) {
    if (frame.source != 0x20 || frame.command != 0xFE ||
        frame.payload_length != 0) return false;
    switch (frame.destination) {
        case 0xBD:
        case 0xB9:
        case 0xB4:
        case 0x12:
            return true;
        default:
            return false;
    }
}

bool buildHbgTimeoutWire(const celestron_aux::AuxWireBuffer& request,
                         celestron_aux::AuxWireBuffer& timeout_wire) {
    if (request.length < 6 || request.bytes[0] != celestron_aux::kAuxSom ||
        request.length != static_cast<std::size_t>(request.bytes[1]) + 3) return false;
    timeout_wire = request;
    timeout_wire.bytes[timeout_wire.length - 1] = 0x00;
    return true;
}

}  // namespace nxw436::net
