#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "celestron_aux/AuxFrame.h"

namespace nxw436::celestron_aux {

enum class AuxError : std::uint8_t {
    None,
    TooShort,
    MissingSom,
    ImpossibleLength,
    LengthMismatch,
    ChecksumMismatch,
    PayloadTooLarge,
};

struct AuxWireBuffer {
    std::array<std::uint8_t, kAuxMaxWire> bytes{};
    std::size_t length{};
};

std::uint8_t auxChecksum(const std::uint8_t* body, std::size_t length);
bool serializeAux(const AuxFrame& frame, AuxWireBuffer& output,
                  AuxError& error);
bool deserializeAux(const std::uint8_t* wire, std::size_t length,
                    AuxFrame& frame, AuxError& error);

}  // namespace nxw436::celestron_aux
