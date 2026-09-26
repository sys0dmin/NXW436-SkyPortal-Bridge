"""Explicit, controlled NXW436 backend composition for SkyPortal experiments.

This launcher is intentionally outside ``celestron_aux``: it owns serial construction,
session-local calibration and hardware logging.  It is never selected by the
Fake backend launcher.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable

from celestron_aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from celestron_aux.dispatcher import AUXCapabilities, AUXDispatcher, SyntheticAUXProfile, VirtualMountIdentity
from celestron_aux.hbg3_infrastructure_experiment import HBG3InfrastructureExperiment
from celestron_aux.messages import (
    MC_GET_APPROACH, MC_GET_AUTOGUIDE_RATE, MC_GET_MAX_RATE, MC_GET_MAX_SLEW_RATE,
    MC_GET_MODEL, MC_GET_POS_BACKLASH, MC_GET_POSITION, MC_GET_VER, MC_MOVE_NEG,
    MC_MOVE_POS, MC_SET_AUTOGUIDE_RATE,
)
from celestron_aux.tcp_server import AUXTCPServer
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from mount_api import Axis, MountController, SpeedTier
from mount_model import POSITION_MODULUS
from nxw436_driver import MOVE_PREFIXES, POSITION_COMMANDS
from nxw436_mount_backend import NXW436MountBackend, NXW436Transport


HARDWARE_MANUAL_RATE = 0x02


def hardware_speed_policy(rate: int) -> SpeedTier | None:
    """Explicit evidence-backed manual policy; never interpolate payloads."""
    return {
        0x02: SpeedTier.MANUAL_CONSERVATIVE,
        0x05: SpeedTier.FINE,
        0x07: SpeedTier.MEDIUM,
        0x09: SpeedTier.MANUAL_HIGH,
    }.get(rate)


def build_hardware_profile() -> SyntheticAUXProfile:
    """Known startup tuples plus hardware-gated manual motion only."""
    allowed = frozenset({
        (0x20, 0x10, MC_GET_VER, 0), (0x20, 0x11, MC_GET_VER, 0),
        (0x20, 0x10, MC_GET_MODEL, 0),
        (0x20, 0x10, MC_GET_POS_BACKLASH, 0), (0x20, 0x11, MC_GET_POS_BACKLASH, 0),
        (0x20, 0x10, MC_GET_APPROACH, 0), (0x20, 0x11, MC_GET_APPROACH, 0),
        (0x20, 0x10, MC_GET_MAX_SLEW_RATE, 0), (0x20, 0x10, MC_GET_MAX_RATE, 0),
        (0x20, 0x10, MC_GET_AUTOGUIDE_RATE, 0), (0x20, 0x11, MC_GET_AUTOGUIDE_RATE, 0),
        (0x20, 0x10, MC_SET_AUTOGUIDE_RATE, 1), (0x20, 0x11, MC_SET_AUTOGUIDE_RATE, 1),
        (0x20, 0x10, MC_GET_POSITION, 0), (0x20, 0x11, MC_GET_POSITION, 0),
        (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x11, MC_MOVE_POS, 1),
        (0x20, 0x10, MC_MOVE_NEG, 1), (0x20, 0x11, MC_MOVE_NEG, 1),
    })
    return SyntheticAUXProfile(
        allowed,
        experimental_zero_backlash_requests=frozenset({
            (0x20, 0x10, MC_GET_POS_BACKLASH, b""), (0x20, 0x11, MC_GET_POS_BACKLASH, b""),
        }),
        experimental_approach_value_00_requests=frozenset({
            (0x20, 0x10, MC_GET_APPROACH, b""), (0x20, 0x11, MC_GET_APPROACH, b""),
        }),
        experimental_hbg3_v38_max_slew_rate_requests=frozenset({(0x20, 0x10, MC_GET_MAX_SLEW_RATE, b"")}),
        experimental_hbg3_v38_max_rate_requests=frozenset({(0x20, 0x10, MC_GET_MAX_RATE, b"")}),
        simulated_manual_motion_requests=frozenset({
            (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x11, MC_MOVE_POS, 1),
            (0x20, 0x10, MC_MOVE_NEG, 1), (0x20, 0x11, MC_MOVE_NEG, 1),
        }),
    )


class LoggingTransport:
    """Logs actual NXW436 prefix/payload while delegating all UART behavior."""

    def __init__(self, transport: NXW436Transport, log: Callable[[str], None]) -> None:
        self.transport = transport
        self.log = log

    def open(self) -> None:
        getattr(self.transport, "open")()

    def close(self) -> None:
        closer = getattr(self.transport, "close", None)
        if closer is not None:
            closer()

    def query_position_raw(self, axis: str) -> bytes | None:
        try:
            frame = self.transport.query_position_raw(axis)
        except Exception as error:
            self.log(f"nxw_position axis={axis} nxw_cmd={POSITION_COMMANDS[axis]:02X} error={type(error).__name__}")
            raise
        self.log(
            f"nxw_position axis={axis} nxw_cmd={POSITION_COMMANDS[axis]:02X} "
            f"raw={None if frame is None else frame.hex().upper()}"
        )
        return frame

    def move(self, axis: str, direction: str, payload: bytes) -> None:
        self.log(f"nxw_move axis={axis} direction={direction} prefix={MOVE_PREFIXES[(axis, direction)]:02X} payload={payload.hex().upper()}")
        self.transport.move(axis, direction, payload)

    def stop(self, axis: str, direction: str | None = None) -> None:
        if direction is None:
            raise RuntimeError("hardware STOP requested without backend direction")
        self.log(f"nxw_stop_double axis={axis} direction={direction} prefix={MOVE_PREFIXES[(axis, direction)]:02X} payload=000000")
        self.transport.stop(axis, direction)


def session_zero_adapter(backend: NXW436MountBackend, *, az_direction: int, alt_direction: int) -> AUXCoordinateAdapter:
    """Read session-local zeroes only; not physical alignment/calibration."""
    az_zero = backend.get_position(Axis.AZ)
    alt_zero = backend.get_position(Axis.ALT)
    return AUXCoordinateAdapter(
        az=AxisCoordinateConfig(POSITION_MODULUS, az_zero, 0, az_direction),
        alt=AxisCoordinateConfig(POSITION_MODULUS, alt_zero, 0, alt_direction),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EXPLICIT NXW436 hardware SkyPortal experiment.")
    parser.add_argument("--backend", choices=("nxw436",), required=True)
    parser.add_argument("--serial", required=True, help="Explicit COM port; no default is selected.")
    parser.add_argument("--bind", required=True)
    parser.add_argument("--broadcast", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--capture-root", type=Path, default=Path("captures"))
    parser.add_argument("--az-direction", choices=("+", "-"), required=True)
    parser.add_argument("--alt-direction", choices=("+", "-"), required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raw_backend = NXW436MountBackend.from_port(args.serial)
    process_log = logging.getLogger("nxw436_hardware_experiment").info
    server_holder: list[AUXTCPServer] = []

    def telemetry(message: str) -> None:
        process_log(message)
        if server_holder and server_holder[0].capture is not None:
            server_holder[0].capture.log(message)

    transport = LoggingTransport(raw_backend.transport, telemetry)
    backend = NXW436MountBackend(transport)
    backend.open()
    try:
        adapter = session_zero_adapter(
            backend, az_direction=1 if args.az_direction == "+" else -1,
            alt_direction=1 if args.alt_direction == "+" else -1,
        )
        controller = MountController(backend)
        def position_telemetry(destination: int, raw: int, aux: int) -> None:
            axis = "az" if destination == 0x10 else "alt"
            config = adapter.configuration(axis)
            telemetry(
                f"POSITION axis={axis.upper()} nxw_cmd={POSITION_COMMANDS[axis]:02X} "
                f"raw={raw:06X} raw_int={raw} zero={config.neutral_zero:06X} "
                f"direction={'+' if config.direction > 0 else '-'} aux={aux:06X}"
            )

        def motion_telemetry(event: str, destination: int, direction: object,
                             rate: int | None, speed: SpeedTier | None, raw: int) -> None:
            axis = "az" if destination == 0x10 else "alt"
            telemetry(
                f"MOTION event={event} axis={axis.upper()} aux_rate={rate} "
                f"direction={direction} speed={None if speed is None else speed.value} "
                f"raw={'unknown' if raw < 0 else f'{raw:06X}'}"
            )

        virtual_mcs = VirtualCelestronMotorControllers(
            manual_rate_to_speed=hardware_speed_policy,
            position_observer=position_telemetry,
            motion_observer=motion_telemetry,
            log_both_axes_on_position_query=True,
        )
        dispatcher = AUXDispatcher(
            controller, AUXCapabilities(position_translation_enabled=True),
            coordinate_adapter=adapter, identity=VirtualMountIdentity((3, 8)),
            synthetic_profile=build_hardware_profile(),
            virtual_mcs=virtual_mcs,
        )
        server = AUXTCPServer(
            dispatcher, bind=args.bind, port=2000, capture_root=args.capture_root,
            client_label="SkyPortal-NXW436-Hardware", allow_synthetic_replies=True,
            synthetic_profile_name="nxw436-hardware-rates-02-05-07-09-only",
            metadata_extra={
                "mode": "explicit-hardware-experiment",
                "serial_port": args.serial,
                "manual_rate_policy": "EXPLICIT_MANUAL_POLICY: AUX 0x02 -> MANUAL_CONSERVATIVE -> 0000F5; 0x05 -> FINE -> 003978; 0x07 -> MEDIUM -> 0072F1; 0x09 -> MANUAL_HIGH -> 00E5E3",
                "calibration": "session-local encoder zero; not pointing/alignment calibration",
                "az_session_zero": f"{adapter.configuration('az').neutral_zero:06X}",
                "alt_session_zero": f"{adapter.configuration('alt').neutral_zero:06X}",
                "az_session_direction": args.az_direction,
                "alt_session_direction": args.alt_direction,
            },
        )
        server_holder.append(server)
        experiment = HBG3InfrastructureExperiment(server, bind=args.bind, broadcast=args.broadcast, mac=args.mac)
        server.record_observer = experiment.observe_record
        experiment.run()
    finally:
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
