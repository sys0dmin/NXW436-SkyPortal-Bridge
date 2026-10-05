#pragma once

#include "nxw436/INxw436ReadOnlyUart.h"

#ifdef ARDUINO
#include <Arduino.h>
#include <HardwareSerial.h>

namespace nxw436::j1 {

struct J1ReadOnlyPins {
    int rx_gpio{16};
    int tx_gpio{17};
    int oe_gpio{27};
};

class Esp32J1ReadOnlyUart final : public INxw436ReadOnlyUart {
public:
    explicit Esp32J1ReadOnlyUart(J1ReadOnlyPins pins = {});
    bool begin() override;
    PositionRead queryPosition(bool az_axis) override;
    bool desynced() const override { return desynced_; }
    void disableTx() override;

private:
    void markDesync();
    J1ReadOnlyPins pins_;
    HardwareSerial serial_{1};
    bool started_{};
    bool desynced_{};
};

}  // namespace nxw436::j1
#endif
