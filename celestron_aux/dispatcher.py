"""Safe AUX dispatch classification; no synthetic replies or motion execution."""

from __future__ import annotations

from dataclasses import dataclass

from mount_api import Direction, MountController, MountError

from celestron_aux.coordinates import AUXCoordinateAdapter, CoordinateUnavailable
from celestron_aux.messages import (
    AUXFrame, KNOWN_DIAGNOSTIC_COMMANDS, MC_GET_MODEL, MC_GET_POSITION, MC_GET_VER, MC_GOTO_FAST,
    MC_GET_APPROACH, MC_GET_AUTOGUIDE_RATE, MC_GET_MAX_RATE, MC_GET_MAX_SLEW_RATE, MC_GET_POS_BACKLASH, MC_GOTO_SLOW, MC_MOVE_NEG, MC_MOVE_POS, MC_SET_AUTOGUIDE_RATE, MC_SET_POSITION, MC_SLEW_DONE,
)
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers


@dataclass(frozen=True)
class AUXCapabilities:
    position_translation_enabled: bool = False


@dataclass(frozen=True)
class DispatchResult:
    status: str
    reply: AUXFrame | None = None


@dataclass(frozen=True)
class VirtualMountIdentity:
    """Explicit test profile only; no real mount identity is assumed."""

    mc_version: tuple[int, int]
    mc_model: bytes = b"\x11\x89"


@dataclass(frozen=True)
class SyntheticAUXProfile:
    """Explicit test fixture, not a claim about SkyPortal request semantics."""

    allowed_requests: frozenset[tuple[int, int, int, int]]
    hypothetical_zero_payload_ack_requests: frozenset[tuple[int, int, int, bytes]] = frozenset()
    experimental_zero_backlash_requests: frozenset[tuple[int, int, int, bytes]] = frozenset()
    experimental_approach_value_00_requests: frozenset[tuple[int, int, int, bytes]] = frozenset()
    experimental_hbg3_v38_max_slew_rate_requests: frozenset[tuple[int, int, int, bytes]] = frozenset()
    experimental_hbg3_v38_max_rate_requests: frozenset[tuple[int, int, int, bytes]] = frozenset()
    simulated_manual_motion_requests: frozenset[tuple[int, int, int, int]] = frozenset()

    def permits(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, len(frame.payload)) in self.allowed_requests

    def is_hypothetical_zero_payload_ack(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, frame.payload) in self.hypothetical_zero_payload_ack_requests

    def is_experimental_zero_backlash(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, frame.payload) in self.experimental_zero_backlash_requests

    def is_experimental_approach_value_00(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, frame.payload) in self.experimental_approach_value_00_requests

    def is_experimental_hbg3_v38_max_slew_rate(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, frame.payload) in self.experimental_hbg3_v38_max_slew_rate_requests

    def is_experimental_hbg3_v38_max_rate(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, frame.payload) in self.experimental_hbg3_v38_max_rate_requests

    def permits_simulated_manual_motion(self, frame: AUXFrame) -> bool:
        return (frame.source, frame.destination, frame.command, len(frame.payload)) in self.simulated_manual_motion_requests


class AUXDispatcher:
    """Classifies frames at the neutral boundary without making unsafe guesses."""

    def __init__(self, controller: MountController, capabilities: AUXCapabilities | None = None,
                 coordinate_adapter: AUXCoordinateAdapter | None = None,
                 identity: VirtualMountIdentity | None = None,
                 synthetic_profile: SyntheticAUXProfile | None = None,
                 virtual_mcs: VirtualCelestronMotorControllers | None = None) -> None:
        self._controller = controller
        self._capabilities = capabilities or AUXCapabilities()
        self._coordinate_adapter = coordinate_adapter
        self._identity = identity
        self._synthetic_profile = synthetic_profile
        self._virtual_mcs = virtual_mcs

    def dispatch(self, frame: AUXFrame) -> DispatchResult:
        if frame.command not in KNOWN_DIAGNOSTIC_COMMANDS:
            return DispatchResult("unsupported_unknown_command")
        if frame.command == MC_GET_VER:
            if self._synthetic_profile is None or not self._synthetic_profile.permits(frame):
                return DispatchResult("recognized_synthetic_profile_unconfigured")
            if self._identity is None:
                return DispatchResult("recognized_identity_unconfigured")
            return DispatchResult("local_reply", self._reply(frame, bytes(self._identity.mc_version)))
        if frame.command == MC_GET_MODEL:
            if self._synthetic_profile is None or not self._synthetic_profile.permits(frame):
                return DispatchResult("recognized_synthetic_profile_unconfigured")
            if self._identity is None:
                return DispatchResult("recognized_identity_unconfigured")
            return DispatchResult("local_reply", self._reply(frame, self._identity.mc_model))
        if frame.command == MC_GET_POSITION:
            if self._synthetic_profile is None or not self._synthetic_profile.permits(frame):
                return DispatchResult("recognized_synthetic_profile_unconfigured")
            if self._coordinate_adapter is None or not self._capabilities.position_translation_enabled:
                return DispatchResult("recognized_position_translation_disabled")
            if self._virtual_mcs is None:
                return DispatchResult("recognized_position_virtual_mc_unconfigured")
            if self._virtual_mcs.configuration(frame.destination) is None:
                return DispatchResult("recognized_position_unknown_destination")
            try:
                aux = self._virtual_mcs.get_aux_position(frame.destination, self._controller, self._coordinate_adapter)
            except CoordinateUnavailable:
                return DispatchResult("recognized_position_coordinate_unavailable")
            except (MountError, RuntimeError):
                return DispatchResult("mount_query_error")
            if aux is None:
                return DispatchResult("recognized_position_unknown_destination")
            return DispatchResult("mount_query_reply", self._reply(frame, aux.to_bytes(3, "big")))
        if frame.command in {MC_MOVE_POS, MC_MOVE_NEG}:
            if self._virtual_mcs is not None and self._synthetic_profile is not None and self._synthetic_profile.permits_simulated_manual_motion(frame):
                rate = frame.payload[0]
                if rate > 9:
                    return DispatchResult("recognized_manual_rate_out_of_domain")
                if rate == 0:
                    try:
                        stop_outcome = self._virtual_mcs.stop_manual_motion(frame.destination, self._controller)
                        if stop_outcome == "frontend_idle":
                            return DispatchResult("frontend_idle_stop_ack", self._reply(frame, b""))
                        if stop_outcome == "backend_stopped":
                            return DispatchResult("simulated_manual_stop_accepted", self._reply(frame, b""))
                    except (MountError, RuntimeError):
                        return DispatchResult("simulated_manual_stop_rejected")
                else:
                    direction = Direction.PLUS if frame.command == MC_MOVE_POS else Direction.MINUS
                    try:
                        if self._virtual_mcs.apply_manual_motion(frame.destination, direction, rate, self._controller):
                            return DispatchResult("simulated_manual_motion_accepted", self._reply(frame, b""))
                    except (MountError, RuntimeError):
                        return DispatchResult("simulated_manual_motion_rejected")
            if self._synthetic_profile is not None and self._synthetic_profile.is_hypothetical_zero_payload_ack(frame):
                return DispatchResult("experimental_zero_payload_ack", self._reply(frame, b""))
            return DispatchResult("recognized_motion_disabled")
        if frame.command == MC_GET_POS_BACKLASH:
            if self._virtual_mcs is not None and self._synthetic_profile is not None and self._synthetic_profile.permits(frame):
                value = self._virtual_mcs.get_positive_backlash(frame.destination)
                if value is not None:
                    return DispatchResult("experimental_zero_backlash", self._reply(frame, bytes((value,))))
            if self._synthetic_profile is not None and self._synthetic_profile.is_experimental_zero_backlash(frame):
                return DispatchResult("experimental_zero_backlash", self._reply(frame, b"\x00"))
            return DispatchResult("recognized_backlash_disabled")
        if frame.command == MC_GET_APPROACH:
            if self._virtual_mcs is not None and self._synthetic_profile is not None and self._synthetic_profile.permits(frame):
                value = self._virtual_mcs.get_approach(frame.destination)
                if value is not None:
                    return DispatchResult("experimental_approach_value_00", self._reply(frame, bytes((value,))))
            if self._synthetic_profile is not None and self._synthetic_profile.is_experimental_approach_value_00(frame):
                return DispatchResult("experimental_approach_value_00", self._reply(frame, b"\x00"))
            return DispatchResult("recognized_approach_disabled")
        if frame.command == MC_GET_MAX_SLEW_RATE:
            if self._synthetic_profile is not None and self._synthetic_profile.is_experimental_hbg3_v38_max_slew_rate(frame):
                return DispatchResult("experimental_hbg3_v38_max_slew_rate", self._reply(frame, bytes.fromhex("A0119454")))
            return DispatchResult("recognized_max_slew_rate_disabled")
        if frame.command == MC_GET_MAX_RATE:
            if self._synthetic_profile is not None and self._synthetic_profile.is_experimental_hbg3_v38_max_rate(frame):
                return DispatchResult("experimental_hbg3_v38_max_rate", self._reply(frame, b"\x00"))
            return DispatchResult("recognized_max_rate_disabled")
        if frame.command == MC_GET_AUTOGUIDE_RATE:
            if self._virtual_mcs is not None and self._synthetic_profile is not None and self._synthetic_profile.permits(frame):
                value = self._virtual_mcs.get_autoguide_rate(frame.destination)
                if value is not None:
                    return DispatchResult("virtual_autoguide_rate", self._reply(frame, bytes((value,))))
            return DispatchResult("recognized_autoguide_rate_disabled")
        if frame.command == MC_SET_AUTOGUIDE_RATE:
            if self._virtual_mcs is not None and self._synthetic_profile is not None and self._synthetic_profile.permits(frame):
                if self._virtual_mcs.set_autoguide_rate(frame.destination, frame.payload[0]):
                    return DispatchResult("virtual_autoguide_rate_updated", self._reply(frame, b""))
            return DispatchResult("recognized_autoguide_rate_disabled")
        if frame.command in {MC_GOTO_FAST, MC_GOTO_SLOW}:
            return DispatchResult("recognized_goto_disabled")
        if frame.command in {MC_SLEW_DONE, MC_SET_POSITION}:
            return DispatchResult("recognized_semantics_not_implemented")
        return DispatchResult("recognized_unsupported")

    @staticmethod
    def _reply(request: AUXFrame, payload: bytes) -> AUXFrame:
        return AUXFrame(request.destination, request.source, request.command, payload)

    @staticmethod
    def _axis_for_destination(destination: int) -> str | None:
        return {0x10: "az", 0x11: "alt"}.get(destination)
