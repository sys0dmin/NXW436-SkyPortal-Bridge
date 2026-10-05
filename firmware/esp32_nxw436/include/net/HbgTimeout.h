#pragma once

#include "celestron_aux/AuxCodec.h"
#include "celestron_aux/AuxFrame.h"

namespace nxw436::net {

// HBG3 forwards timed-out GET_VERSION packets with final byte 00. This is not
// a canonical checksum-valid AUX reply.
bool isHbgTimeoutVersionProbe(const celestron_aux::AuxFrame& frame);
bool buildHbgTimeoutWire(const celestron_aux::AuxWireBuffer& request,
                         celestron_aux::AuxWireBuffer& timeout_wire);

}  // namespace nxw436::net
