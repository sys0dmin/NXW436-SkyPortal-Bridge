#include "mount/FakeMountRuntime.h"

#include "domain/PositionMath.h"

namespace nxw436::mount {

FakeMountRuntime::FakeMountRuntime(FakeMountBackend& backend,
                                   celestron_aux::SemanticCore& core,
                                   const std::uint32_t goto_duration_ms)
    : backend_(backend), core_(core), goto_duration_ms_(goto_duration_ms) {}

void FakeMountRuntime::tick(const std::uint32_t now_ms) {
    if (!initialized_) {
        initialized_ = true;
        last_tick_ms_ = now_ms;
    }
    backend_.tick(now_ms - last_tick_ms_);
    last_tick_ms_ = now_ms;
    tickAxis(Axis::Az, now_ms);
    tickAxis(Axis::Alt, now_ms);
}

void FakeMountRuntime::tickAxis(const Axis axis, const std::uint32_t now_ms) {
    const auto i = axis == Axis::Az ? 0U : 1U;
    auto& runtime = axes_[i];
    const auto state = core_.gotoState(axis);
    if (state != celestron_aux::GotoState::Active) {
        runtime.goto_active = false;
        return;
    }
    if (!runtime.goto_active) {
        runtime.goto_active = true;
        runtime.started_ms = now_ms;
        backend_.getPosition(axis, runtime.start_position);
        runtime.target_position = core_.gotoTarget(axis);
        const auto delta = domain::signedModularDelta(
            runtime.start_position, runtime.target_position);
        backend_.move(axis, delta >= 0 ? Direction::Plus : Direction::Minus,
                      SpeedTier::Medium);
    }
    const auto elapsed = now_ms - runtime.started_ms;
    const auto delta = domain::signedModularDelta(
        runtime.start_position, runtime.target_position);
    if (elapsed >= goto_duration_ms_) {
        backend_.setPosition(axis, runtime.target_position);
        backend_.stop(axis);
        core_.completeGoto(axis);
        runtime.goto_active = false;
        return;
    }
    const auto travelled = static_cast<std::int64_t>(delta) * elapsed
                         / goto_duration_ms_;
    backend_.setPosition(axis, domain::normalizePosition(
        static_cast<std::int64_t>(runtime.start_position) + travelled));
}

}  // namespace nxw436::mount
