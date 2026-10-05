#include "celestron_aux/AuxCodec.h"

namespace nxw436::celestron_aux {

std::uint8_t auxChecksum(const std::uint8_t* body,
                         const std::size_t length) {
    std::uint32_t sum = 0;
    for (std::size_t i = 0; i < length; ++i) {
        sum += body[i];
    }
    return static_cast<std::uint8_t>(-sum);
}

bool serializeAux(const AuxFrame& frame, AuxWireBuffer& output,
                  AuxError& error) {
    if (frame.payload_length > kAuxMaxPayload) {
        error = AuxError::PayloadTooLarge;
        return false;
    }
    const auto message_length = static_cast<std::uint8_t>(3 + frame.payload_length);
    output.length = frame.payload_length + 6;
    output.bytes[0] = kAuxSom;
    output.bytes[1] = message_length;
    output.bytes[2] = frame.source;
    output.bytes[3] = frame.destination;
    output.bytes[4] = frame.command;
    for (std::size_t i = 0; i < frame.payload_length; ++i) {
        output.bytes[5 + i] = frame.payload[i];
    }
    output.bytes[output.length - 1] = auxChecksum(&output.bytes[1],
                                                  output.length - 2);
    error = AuxError::None;
    return true;
}

bool deserializeAux(const std::uint8_t* wire, const std::size_t length,
                    AuxFrame& frame, AuxError& error) {
    if (length < 6) {
        error = AuxError::TooShort;
        return false;
    }
    if (wire[0] != kAuxSom) {
        error = AuxError::MissingSom;
        return false;
    }
    if (wire[1] < 3) {
        error = AuxError::ImpossibleLength;
        return false;
    }
    if (length != static_cast<std::size_t>(wire[1]) + 3) {
        error = AuxError::LengthMismatch;
        return false;
    }
    std::uint32_t sum = 0;
    for (std::size_t i = 1; i < length; ++i) {
        sum += wire[i];
    }
    if ((sum & 0xFFU) != 0) {
        error = AuxError::ChecksumMismatch;
        return false;
    }
    frame = AuxFrame{};
    frame.source = wire[2];
    frame.destination = wire[3];
    frame.command = wire[4];
    frame.payload_length = wire[1] - 3;
    for (std::size_t i = 0; i < frame.payload_length; ++i) {
        frame.payload[i] = wire[5 + i];
    }
    error = AuxError::None;
    return true;
}

}  // namespace nxw436::celestron_aux
