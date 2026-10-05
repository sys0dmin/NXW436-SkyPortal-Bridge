#pragma once

#include <cstdint>

namespace nxw436::j1 {

enum class UartReadStatus : std::uint8_t {
    Ok,
    Timeout,
    Partial,
    Desync,
    NotReady,
    TransportError,
};

struct PositionRead {
    UartReadStatus status{UartReadStatus::NotReady};
    std::uint8_t query{};
    std::uint8_t raw[3]{};
    std::uint32_t duration_us{};
};

class INxw436ReadOnlyUart {
public:
    virtual ~INxw436ReadOnlyUart() = default;
    virtual bool begin() = 0;
    virtual PositionRead queryPosition(bool az_axis) = 0;
    virtual bool desynced() const = 0;
    virtual void disableTx() = 0;
};

}  // namespace nxw436::j1
