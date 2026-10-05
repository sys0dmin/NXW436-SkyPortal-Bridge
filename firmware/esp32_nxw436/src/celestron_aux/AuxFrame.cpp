#include "celestron_aux/AuxFrame.h"

namespace nxw436::celestron_aux {

bool AuxFrame::operator==(const AuxFrame& other) const {
    if (source != other.source || destination != other.destination ||
        command != other.command || payload_length != other.payload_length) {
        return false;
    }
    for (std::size_t i = 0; i < payload_length; ++i) {
        if (payload[i] != other.payload[i]) {
            return false;
        }
    }
    return true;
}

}  // namespace nxw436::celestron_aux
