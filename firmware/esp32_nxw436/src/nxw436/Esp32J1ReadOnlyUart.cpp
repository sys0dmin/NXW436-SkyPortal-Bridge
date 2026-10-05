#include "nxw436/Esp32J1ReadOnlyUart.h"

#ifdef ARDUINO

namespace nxw436::j1 {

Esp32J1ReadOnlyUart::Esp32J1ReadOnlyUart(const J1ReadOnlyPins pins)
    : pins_(pins) {}

bool Esp32J1ReadOnlyUart::begin() {
    // External pull-up keeps AHCT /OE disabled until firmware explicitly drives
    // its open-drain control gate. INPUT is fail-safe during ESP reset/boot.
    pinMode(pins_.oe_gpio, INPUT);
    serial_.begin(4800, SERIAL_8N1, pins_.rx_gpio, pins_.tx_gpio, false);
    pinMode(pins_.tx_gpio, OUTPUT);
    digitalWrite(pins_.tx_gpio, HIGH);
    delayMicroseconds(10);
    pinMode(pins_.oe_gpio, OUTPUT);
    digitalWrite(pins_.oe_gpio, HIGH);  // 2N7002 on -> /OE low -> TX enabled
    started_ = true;
    return true;
}

void Esp32J1ReadOnlyUart::disableTx() {
    pinMode(pins_.oe_gpio, INPUT);
}

void Esp32J1ReadOnlyUart::markDesync() {
    desynced_ = true;
    disableTx();
}

PositionRead Esp32J1ReadOnlyUart::queryPosition(const bool az_axis) {
    PositionRead result{};
    result.query = az_axis ? 0x01 : 0x15;
    if (!started_) {
        result.status = UartReadStatus::NotReady;
        return result;
    }
    if (desynced_) {
        result.status = UartReadStatus::Desync;
        return result;
    }
    if (serial_.available() != 0) {
        // No automatic drain: pending bytes have unknown request ownership.
        markDesync();
        result.status = UartReadStatus::Desync;
        return result;
    }
    const auto started_us = micros();
    serial_.write(result.query);
    serial_.flush();
    std::size_t count = 0;
    while (static_cast<std::uint32_t>(micros() - started_us) < 150000U) {
        while (serial_.available() > 0 && count < 3) {
            const auto value = serial_.read();
            if (value >= 0) result.raw[count++] = static_cast<std::uint8_t>(value);
        }
        if (count == 3) {
            result.duration_us = micros() - started_us;
            if (serial_.available() != 0) {
                markDesync();
                result.status = UartReadStatus::Desync;
            } else {
                result.status = UartReadStatus::Ok;
            }
            return result;
        }
        delay(1);
    }
    result.duration_us = micros() - started_us;
    result.status = count == 0 ? UartReadStatus::Timeout : UartReadStatus::Partial;
    markDesync();
    return result;
}

}  // namespace nxw436::j1

#endif
