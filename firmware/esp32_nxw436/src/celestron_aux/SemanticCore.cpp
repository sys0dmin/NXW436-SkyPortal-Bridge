#include "celestron_aux/SemanticCore.h"

namespace nxw436::celestron_aux {

namespace {
constexpr std::uint8_t kGetPosition = 0x01;
constexpr std::uint8_t kGotoFast = 0x02;
constexpr std::uint8_t kGetModel = 0x05;
constexpr std::uint8_t kSlewDone = 0x13;
constexpr std::uint8_t kGotoSlow = 0x17;
constexpr std::uint8_t kGetMaxSlewRate = 0x21;
constexpr std::uint8_t kGetMaxRate = 0x23;
constexpr std::uint8_t kMovePos = 0x24;
constexpr std::uint8_t kMoveNeg = 0x25;
constexpr std::uint8_t kGetPosBacklash = 0x40;
constexpr std::uint8_t kSetAutoguide = 0x46;
constexpr std::uint8_t kGetAutoguide = 0x47;
constexpr std::uint8_t kGetApproach = 0xFC;
constexpr std::uint8_t kGetVersion = 0xFE;
constexpr std::uint8_t kSetPosGuiderate = 0x06;
}  // namespace

SemanticCore::SemanticCore(mount::IMountBackend& backend,
                           AuxCoordinateAdapter adapter,
                           const SemanticProfile profile)
    : backend_(backend), adapter_(adapter), profile_(profile) {}

std::size_t SemanticCore::axisIndex(const mount::Axis axis) {
    return axis == mount::Axis::Az ? 0U : 1U;
}

bool SemanticCore::destinationAxis(const std::uint8_t destination,
                                   mount::Axis& axis) {
    if (destination == kAzmAddress) { axis = mount::Axis::Az; return true; }
    if (destination == kAltAddress) { axis = mount::Axis::Alt; return true; }
    return false;
}

AuxFrame SemanticCore::reply(const AuxFrame& request,
                             const std::uint8_t* payload,
                             const std::size_t length) {
    AuxFrame response{};
    response.source = request.destination;
    response.destination = request.source;
    response.command = request.command;
    response.payload_length = length;
    for (std::size_t i = 0; i < length; ++i) response.payload[i] = payload[i];
    return response;
}

mount::SpeedTier SemanticCore::simulatedSpeed(const std::uint8_t rate) {
    if (rate <= 2) return mount::SpeedTier::Slow;
    if (rate <= 4) return mount::SpeedTier::Fine;
    if (rate <= 7) return mount::SpeedTier::Medium;
    return mount::SpeedTier::Fast;
}

DispatchOutcome SemanticCore::dispatch(const AuxFrame& request) {
    DispatchOutcome outcome{};
    if (request.source != 0x20) return outcome;
    mount::Axis axis{};
    const bool has_axis = destinationAxis(request.destination, axis);

    if (request.command == kGetVersion && request.payload_length == 0 && has_axis) {
        const std::uint8_t value[] = {0x03, 0x08};
        return {DispatchStatus::LocalReply, true, reply(request, value, 2)};
    }
    if (profile_.hbg_optional_device_emulation &&
        request.command == kGetVersion && request.payload_length == 0) {
        const std::uint8_t* version = nullptr;
        if (request.destination == 0xB4) {
            static constexpr std::uint8_t kSsaa[] = {0x01, 0x02, 0x34, 0x1F};
            version = kSsaa;
        } else if (request.destination == 0xB9) {
            static constexpr std::uint8_t kSsag[] = {0x23, 0x0B, 0x00, 0x0C};
            version = kSsag;
        } else if (request.destination == 0x12) {
            static constexpr std::uint8_t kFocus[] = {0x07, 0x10, 0x24, 0x54};
            version = kFocus;
        }
        if (version != nullptr) return {DispatchStatus::LocalReply, true, reply(request, version, 4)};
    }
    if (request.command == kGetModel && request.destination == kAzmAddress &&
        request.payload_length == 0) {
        // HBG3 compatibility value accepted by real SkyPortal; synthetic, not
        // a physical NXW436 model claim.
        const std::uint8_t value[] = {0x11, 0x89};
        return {DispatchStatus::LocalReply, true, reply(request, value, 2)};
    }
    if (profile_.hbg_evwifi_shim && request.destination == 0xB5 && request.command == 0x15 &&
        request.payload_length == 10) {
        return {DispatchStatus::LocalReply, true, reply(request, nullptr, 0)};
    }
    if (profile_.hbg_optional_device_emulation && request.destination == 0xB9 && request.command == 0x49 &&
        request.payload_length == 0) {
        const std::uint8_t value[] = {0x01};
        return {DispatchStatus::LocalReply, true, reply(request, value, 1)};
    }
    if (profile_.hbg_optional_device_emulation && request.destination == 0xB9 && request.command == 0x32 &&
        request.payload_length == 4) {
        return {DispatchStatus::LocalReply, true, reply(request, nullptr, 0)};
    }
    if (profile_.hbg_optional_device_emulation && request.destination == 0xB4 && request.command == 0x3F &&
        request.payload_length == 1 && request.payload[0] == 0x00) {
        const std::uint8_t value[] = {0x80, 0x02, 0x00, 0x00, 0xE0, 0x01, 0x00, 0x00};
        return {DispatchStatus::LocalReply, true, reply(request, value, 8)};
    }
    if (profile_.hbg_optional_device_emulation && request.destination == 0x12 && request.command == 0x2B &&
        request.payload_length == 0) {
        const std::uint8_t value[] = {0x00};
        return {DispatchStatus::LocalReply, true, reply(request, value, 1)};
    }
    if (!has_axis) return outcome;

    if (request.command == kSetPosGuiderate && request.payload_length == 3) {
        // HBG v9.11 emulate.mount accepts and ACKs guide-rate configuration
        // without enabling tracking in this fake milestone.
        return {DispatchStatus::ConfigUpdated, true, reply(request, nullptr, 0)};
    }

    if (request.command == kGetPosition && request.payload_length == 0) {
        std::uint32_t native{};
        if (!backend_.getPosition(axis, native)) return outcome;
        std::uint32_t aux{};
        if (!adapter_.toAux(axis == mount::Axis::Az, native, aux)) return outcome;
        const std::uint8_t value[] = {
            static_cast<std::uint8_t>(aux >> 16),
            static_cast<std::uint8_t>(aux >> 8),
            static_cast<std::uint8_t>(aux),
        };
        return {DispatchStatus::PositionReply, true, reply(request, value, 3)};
    }
    if (request.command == kGetPosBacklash && request.payload_length == 0) {
        const auto* config = virtual_mcs_.get(request.destination);
        return {DispatchStatus::LocalReply, true,
                reply(request, &config->positive_backlash, 1)};
    }
    if (request.command == kGetApproach && request.payload_length == 0) {
        const auto* config = virtual_mcs_.get(request.destination);
        return {DispatchStatus::LocalReply, true,
                reply(request, &config->approach, 1)};
    }
    if (request.command == kGetAutoguide && request.payload_length == 0) {
        const auto* config = virtual_mcs_.get(request.destination);
        return {DispatchStatus::LocalReply, true,
                reply(request, &config->autoguide_rate, 1)};
    }
    if (request.command == kSetAutoguide && request.payload_length == 1) {
        auto* config = virtual_mcs_.get(request.destination);
        config->autoguide_rate = request.payload[0];
        return {DispatchStatus::ConfigUpdated, true, reply(request, nullptr, 0)};
    }
    if (request.command == kGetMaxSlewRate && request.destination == kAzmAddress &&
        request.payload_length == 0) {
        // HBG3 v3.8 compatibility response, not physical MC evidence.
        const std::uint8_t value[] = {0xA0, 0x11, 0x94, 0x54};
        return {DispatchStatus::LocalReply, true, reply(request, value, 4)};
    }
    if (request.command == kGetMaxRate && request.destination == kAzmAddress &&
        request.payload_length == 0) {
        const std::uint8_t value[] = {0x00};
        return {DispatchStatus::LocalReply, true, reply(request, value, 1)};
    }
    if ((request.command == kMovePos || request.command == kMoveNeg) &&
        request.payload_length == 1) {
        const auto rate = request.payload[0];
        if (rate == 0) {
            if (goto_states_[axisIndex(axis)] == GotoState::Active) {
                goto_states_[axisIndex(axis)] = GotoState::Cancelled;
            }
            const auto status = backend_.getAxisStatus(axis);
            if (status.motion_commanded && !backend_.stop(axis)) return outcome;
            return {DispatchStatus::StopAccepted, true, reply(request, nullptr, 0)};
        }
        if (rate > 9) return outcome;
        if (goto_states_[axisIndex(axis)] == GotoState::Active) {
            goto_states_[axisIndex(axis)] = GotoState::Cancelled;
        }
        const auto direction = request.command == kMovePos
            ? mount::Direction::Plus : mount::Direction::Minus;
        if (!backend_.move(axis, direction, simulatedSpeed(rate))) return outcome;
        return {DispatchStatus::ManualAccepted, true, reply(request, nullptr, 0)};
    }
    if ((request.command == kGotoFast || request.command == kGotoSlow) &&
        request.payload_length == 3) {
        if (goto_states_[axisIndex(axis)] == GotoState::Active) {
            return {DispatchStatus::Rejected, false, {}};
        }
        const auto target_aux = (static_cast<std::uint32_t>(request.payload[0]) << 16)
                              | (static_cast<std::uint32_t>(request.payload[1]) << 8)
                              | request.payload[2];
        std::uint32_t target_native{};
        if (!adapter_.fromAux(axis == mount::Axis::Az, target_aux,
                              target_native)) return outcome;
        goto_targets_[axisIndex(axis)] = target_native;
        goto_states_[axisIndex(axis)] = GotoState::Active;
        return {DispatchStatus::GotoStarted, true, reply(request, nullptr, 0)};
    }
    if (request.command == kSlewDone && request.payload_length == 0) {
        const auto state = goto_states_[axisIndex(axis)];
        if (state == GotoState::Active) {
            const std::uint8_t value[] = {0x00};
            return {DispatchStatus::SlewActive, true, reply(request, value, 1)};
        }
        if (state == GotoState::Completed) {
            const std::uint8_t value[] = {0xFF};
            return {DispatchStatus::SlewDone, true, reply(request, value, 1)};
        }
    }
    return outcome;
}

void SemanticCore::completeGoto(const mount::Axis axis) {
    const auto i = axisIndex(axis);
    goto_states_[i] = GotoState::Completed;
}

void SemanticCore::failGoto(const mount::Axis axis) {
    goto_states_[axisIndex(axis)] = GotoState::Failed;
}

GotoState SemanticCore::gotoState(const mount::Axis axis) const {
    return goto_states_[axisIndex(axis)];
}

std::uint32_t SemanticCore::gotoTarget(const mount::Axis axis) const {
    return goto_targets_[axisIndex(axis)];
}

}  // namespace nxw436::celestron_aux
