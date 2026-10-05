#include "celestron_aux/AuxStreamParser.h"

#include <algorithm>

namespace nxw436::celestron_aux {

AuxStreamParser::AuxStreamParser(const std::size_t max_buffer)
    : max_buffer_(std::min(max_buffer, buffer_.size())) {}

void AuxStreamParser::reset() { length_ = 0; }

void AuxStreamParser::eraseFront(const std::size_t count) {
    if (count >= length_) {
        length_ = 0;
        return;
    }
    std::move(buffer_.begin() + count, buffer_.begin() + length_,
              buffer_.begin());
    length_ -= count;
}

bool AuxStreamParser::feedByte(const std::uint8_t byte, ParserEvent& event) {
    event = ParserEvent{};
    if (length_ >= max_buffer_) {
        event.error = AuxError::PayloadTooLarge;
        event.raw.length = std::min(length_, event.raw.bytes.size());
        std::copy_n(buffer_.begin(), event.raw.length, event.raw.bytes.begin());
        length_ = 0;
        return true;
    }
    buffer_[length_++] = byte;
    while (true) {
        const auto som = std::find(buffer_.begin(), buffer_.begin() + length_,
                                   kAuxSom);
        if (som == buffer_.begin() + length_) {
            length_ = 0;
            return false;
        }
        const auto prefix = static_cast<std::size_t>(som - buffer_.begin());
        if (prefix > 0) {
            eraseFront(prefix);
        }
        if (length_ < 2) {
            return false;
        }
        if (buffer_[1] < 3) {
            event.error = AuxError::ImpossibleLength;
            event.raw.length = 2;
            event.raw.bytes[0] = buffer_[0];
            event.raw.bytes[1] = buffer_[1];
            eraseFront(1);
            return true;
        }
        const auto total = static_cast<std::size_t>(buffer_[1]) + 3;
        if (total > max_buffer_) {
            event.error = AuxError::PayloadTooLarge;
            event.raw.length = 2;
            event.raw.bytes[0] = buffer_[0];
            event.raw.bytes[1] = buffer_[1];
            eraseFront(1);
            return true;
        }
        if (length_ < total) {
            return false;
        }
        event.raw.length = total;
        std::copy_n(buffer_.begin(), total, event.raw.bytes.begin());
        event.valid = deserializeAux(event.raw.bytes.data(), total, event.frame,
                                     event.error);
        eraseFront(total);
        return true;
    }
}

ParserEventBatch AuxStreamParser::feedBounded(const std::uint8_t* data,
                                               const std::size_t length) {
    ParserEventBatch batch{};
    for (std::size_t i = 0; i < length; ++i) {
        ParserEvent event{};
        if (!feedByte(data[i], event)) continue;
        if (batch.count < batch.events.size()) {
            batch.events[batch.count++] = event;
        } else {
            batch.overflow = true;
        }
    }
    return batch;
}

std::vector<ParserEvent> AuxStreamParser::feed(const std::uint8_t* data,
                                                const std::size_t length) {
    std::vector<ParserEvent> events;
    for (std::size_t i = 0; i < length; ++i) {
        ParserEvent event{};
        if (feedByte(data[i], event)) events.push_back(event);
    }
    return events;
}

}  // namespace nxw436::celestron_aux
