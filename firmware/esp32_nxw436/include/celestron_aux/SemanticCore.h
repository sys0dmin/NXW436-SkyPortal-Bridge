#pragma once

#include <array>
#include <cstdint>

#include "celestron_aux/AuxCoordinateAdapter.h"
#include "celestron_aux/AuxFrame.h"
#include "celestron_aux/VirtualMotorControllers.h"
#include "mount/IMountBackend.h"

namespace nxw436::celestron_aux {

enum class DispatchStatus : std::uint8_t {
    Unsupported,
    Rejected,
    LocalReply,
    PositionReply,
    ManualAccepted,
    StopAccepted,
    GotoStarted,
    SlewActive,
    SlewDone,
    ConfigUpdated,
};

enum class GotoState : std::uint8_t {
    Idle,
    Active,
    Completed,
    Failed,
    Cancelled,
};

struct DispatchOutcome {
    DispatchStatus status{DispatchStatus::Unsupported};
    bool has_reply{};
    AuxFrame reply{};
};

struct SemanticProfile {
    bool hbg_optional_device_emulation{false};
    bool hbg_evwifi_shim{false};
};

class SemanticCore {
public:
    SemanticCore(mount::IMountBackend& backend, AuxCoordinateAdapter adapter,
                 SemanticProfile profile = {});
    DispatchOutcome dispatch(const AuxFrame& request);
    void completeGoto(mount::Axis axis);
    void failGoto(mount::Axis axis);
    GotoState gotoState(mount::Axis axis) const;
    std::uint32_t gotoTarget(mount::Axis axis) const;

private:
    static std::size_t axisIndex(mount::Axis axis);
    static bool destinationAxis(std::uint8_t destination, mount::Axis& axis);
    static AuxFrame reply(const AuxFrame& request, const std::uint8_t* payload,
                          std::size_t length);
    static mount::SpeedTier simulatedSpeed(std::uint8_t rate);

    mount::IMountBackend& backend_;
    AuxCoordinateAdapter adapter_;
    VirtualMotorControllers virtual_mcs_{};
    SemanticProfile profile_{};
    std::array<GotoState, 2> goto_states_{GotoState::Idle, GotoState::Idle};
    std::array<std::uint32_t, 2> goto_targets_{};
};

}  // namespace nxw436::celestron_aux
