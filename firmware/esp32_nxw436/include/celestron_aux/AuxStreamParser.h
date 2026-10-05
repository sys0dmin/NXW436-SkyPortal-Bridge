#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "celestron_aux/AuxCodec.h"

namespace nxw436::celestron_aux {

struct ParserEvent {
    bool valid{};
    AuxFrame frame{};
    AuxError error{AuxError::None};
    AuxWireBuffer raw{};
};

constexpr std::size_t kParserEventCapacity = 8;

struct ParserEventBatch {
    std::array<ParserEvent, kParserEventCapacity> events{};
    std::size_t count{};
    bool overflow{};
};

class AuxStreamParser {
public:
    explicit AuxStreamParser(std::size_t max_buffer = 512);
    bool feedByte(std::uint8_t byte, ParserEvent& event);
    ParserEventBatch feedBounded(const std::uint8_t* data, std::size_t length);
    std::vector<ParserEvent> feed(const std::uint8_t* data, std::size_t length);
    void reset();
    std::size_t buffered() const { return length_; }

private:
    void eraseFront(std::size_t count);
    std::array<std::uint8_t, 512> buffer_{};
    std::size_t length_{};
    std::size_t max_buffer_{};
};

}  // namespace nxw436::celestron_aux
