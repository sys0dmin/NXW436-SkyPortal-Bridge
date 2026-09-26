"""State-only virtual Celestron motor-controller configuration façade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from celestron_aux.coordinates import AUXCoordinateAdapter
from mount_api import Direction, MountController, SpeedTier


AZM_ADDRESS = 0x10
ALT_ADDRESS = 0x11


@dataclass
class VirtualMCConfiguration:
    positive_backlash: int = 0x00
    approach: int = 0x00
    autoguide_rate: int = 0x80

    def __post_init__(self) -> None:
        for value in (self.positive_backlash, self.approach, self.autoguide_rate):
            if not 0 <= value <= 0xFF:
                raise ValueError("virtual MC configuration values must be bytes")


class VirtualCelestronMotorControllers:
    """Independent AZM/ALT configuration state with no mount/backend dependency."""

    def __init__(self, *, manual_rate_to_speed: Callable[[int], SpeedTier | None] | None = None,
                 position_observer: Callable[[int, int, int], None] | None = None,
                 motion_observer: Callable[[str, int, Direction | None, int | None, SpeedTier | None], None] | None = None,
                 log_both_axes_on_position_query: bool = False) -> None:
        self._controllers = {
            AZM_ADDRESS: VirtualMCConfiguration(),
            ALT_ADDRESS: VirtualMCConfiguration(),
        }
        self._manual_rate_to_speed = manual_rate_to_speed or self.simulated_speed_tier
        self._position_observer = position_observer
        self._motion_observer = motion_observer
        self._log_both_axes_on_position_query = log_both_axes_on_position_query
        self._owned_direction: dict[int, Direction] = {}
        self._motion_owned = {AZM_ADDRESS: False, ALT_ADDRESS: False}

    def configuration(self, destination: int) -> VirtualMCConfiguration | None:
        return self._controllers.get(destination)

    def get_positive_backlash(self, destination: int) -> int | None:
        config = self.configuration(destination)
        return None if config is None else config.positive_backlash

    def get_approach(self, destination: int) -> int | None:
        config = self.configuration(destination)
        return None if config is None else config.approach

    def get_autoguide_rate(self, destination: int) -> int | None:
        config = self.configuration(destination)
        return None if config is None else config.autoguide_rate

    def set_autoguide_rate(self, destination: int, value: int) -> bool:
        config = self.configuration(destination)
        if config is None or not 0 <= value <= 0xFF:
            return False
        config.autoguide_rate = value
        return True

    def get_aux_position(self, destination: int, controller: MountController,
                         adapter: AUXCoordinateAdapter) -> int | None:
        aux = self._read_aux_position(destination, controller, adapter)
        if aux is not None and self._log_both_axes_on_position_query:
            other = ALT_ADDRESS if destination == AZM_ADDRESS else AZM_ADDRESS
            self._read_aux_position(other, controller, adapter)
        return aux

    @staticmethod
    def simulated_speed_tier(rate: int) -> SpeedTier | None:
        """Synthetic SkyPortal UI rate mapping, not a physical NXW436 mapping."""
        if not 1 <= rate <= 9:
            return None
        if rate <= 2:
            return SpeedTier.SLOW
        if rate <= 4:
            return SpeedTier.FINE
        if rate <= 7:
            return SpeedTier.MEDIUM
        return SpeedTier.FAST

    def apply_manual_motion(self, destination: int, direction: Direction, rate: int,
                            controller: MountController) -> bool:
        speed = self._manual_rate_to_speed(rate)
        if speed is None:
            return False
        if destination == AZM_ADDRESS:
            raw = controller.get_az_position()
            self._notify_motion("manual_pre", destination, direction, rate, speed, raw)
            controller.move_az(direction, speed)
            self._motion_owned[destination] = True
            self._owned_direction[destination] = direction
            return True
        if destination == ALT_ADDRESS:
            raw = controller.get_alt_position()
            self._notify_motion("manual_pre", destination, direction, rate, speed, raw)
            controller.move_alt(direction, speed)
            self._motion_owned[destination] = True
            self._owned_direction[destination] = direction
            return True
        return False

    def stop_manual_motion(self, destination: int, controller: MountController) -> str | None:
        if destination not in self._controllers:
            return None
        if not self._motion_owned[destination]:
            return "frontend_idle"
        self._notify_motion("manual_stop_attempt", destination, self._owned_direction.get(destination), None, None, -1)
        try:
            if destination == AZM_ADDRESS:
                controller.stop_az()
            else:
                controller.stop_alt()
        except Exception as error:
            self._notify_motion(
                f"manual_stop_error:{type(error).__name__}",
                destination, self._owned_direction.get(destination), None, None, -1,
            )
            raise
        self._motion_owned[destination] = False
        self._owned_direction.pop(destination, None)
        try:
            raw = controller.get_az_position() if destination == AZM_ADDRESS else controller.get_alt_position()
        except Exception as error:
            self._notify_motion(
                f"manual_post_stop_position_unavailable:{type(error).__name__}",
                destination, None, None, None, -1,
            )
        else:
            self._notify_motion("manual_stop", destination, None, None, None, raw)
        return "backend_stopped"

    def motion_owned(self, destination: int) -> bool:
        return self._motion_owned.get(destination, False)

    def owned_direction(self, destination: int) -> Direction | None:
        return self._owned_direction.get(destination)

    def _read_aux_position(self, destination: int, controller: MountController,
                           adapter: AUXCoordinateAdapter) -> int | None:
        if destination == AZM_ADDRESS:
            raw = controller.get_az_position()
            aux = adapter.to_aux("az", raw)
            self._notify_position(destination, raw, aux)
            return aux
        if destination == ALT_ADDRESS:
            raw = controller.get_alt_position()
            aux = adapter.to_aux("alt", raw)
            self._notify_position(destination, raw, aux)
            return aux
        return None

    def _notify_position(self, destination: int, raw: int, aux: int) -> None:
        if self._position_observer is not None:
            self._position_observer(destination, raw, aux)

    def _notify_motion(self, event: str, destination: int, direction: Direction | None,
                       rate: int | None, speed: SpeedTier | None, raw: int) -> None:
        if self._motion_observer is not None:
            self._motion_observer(event, destination, direction, rate, speed, raw)
