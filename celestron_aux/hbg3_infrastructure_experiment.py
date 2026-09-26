"""Experimental HBG3-informed Infrastructure discovery and TCP evidence tool.

This is intentionally not a production frontend: it uses FakeMountBackend and
permits only one explicitly matched synthetic MC_GET_VER reply.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

from celestron_aux.dispatcher import AUXCapabilities, AUXDispatcher, SyntheticAUXProfile, VirtualMountIdentity
from celestron_aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from celestron_aux.messages import MC_GET_APPROACH, MC_GET_AUTOGUIDE_RATE, MC_GET_MAX_RATE, MC_GET_MAX_SLEW_RATE, MC_GET_MODEL, MC_GET_POS_BACKLASH, MC_GET_VER, MC_MOVE_NEG, MC_MOVE_POS, MC_SET_AUTOGUIDE_RATE
from celestron_aux.tcp_server import AUXTCPServer
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from fake_mount_backend import FakeMountBackend
from mount_api import MountController


HBG3_V38_VERSION = "HomeBrew-AMW007-9.0.0.0, 2021-10-18T12:00:00Z, ESP32-3.8"


def hbg3_v38_advertisement(mac: str) -> bytes:
    """HBG3 v3.8 JSON template; source/destination UDP details remain CLI-selected."""
    return f'{{"mac":"{mac}",\n"version":"{HBG3_V38_VERSION}"\n}}'.encode("ascii")


class HBG3InfrastructureExperiment:
    def __init__(self, server: AUXTCPServer, *, bind: str, broadcast: str, mac: str) -> None:
        self.server = server
        self.bind = bind
        self.broadcast = broadcast
        self.payload = hbg3_v38_advertisement(mac)
        self.stop_event = threading.Event()
        self.unmatched_get_ver_observed = False
        self.exit_reason = "stop_event"

    def observe_record(self, record: dict[str, object]) -> None:
        if record.get("command") == MC_GET_VER and record.get("dispatch_result") == "recognized_synthetic_profile_unconfigured":
            self.unmatched_get_ver_observed = True
            if self.server.capture is not None:
                self.server.capture.log("unmatched_get_ver_recorded_no_reply")

    def advertise_once(self, udp: socket.socket) -> bool:
        if self.server.active_connection:
            return False
        udp.sendto(self.payload, (self.broadcast, 55555))
        if self.server.capture is not None:
            self.server.capture.log(json.dumps({
                "event": "hbg3_udp_advertisement", "timestamp": datetime.now(timezone.utc).isoformat(),
                "destination": f"{self.broadcast}:55555", "payload_hex": self.payload.hex().upper(),
            }, sort_keys=True))
        return True

    def run(self) -> None:
        ready = threading.Event()
        tcp_thread = threading.Thread(target=self.server.serve_until, args=(self.stop_event, ready), daemon=True)
        tcp_thread.start()
        if not ready.wait(5):
            self.exit_reason = "tcp_server_start_failed"
            raise RuntimeError("TCP listener did not become ready")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp.bind((self.bind, 0))
            while not self.stop_event.wait(1.0):
                self.advertise_once(udp)
        tcp_thread.join(2)
        if self.server.capture is not None:
            self.server.capture.log(f"experiment_exit reason={self.exit_reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EXPERIMENTAL HBG3-informed Infrastructure launcher.")
    parser.add_argument("--bind", required=True, help="Selected local LAN IPv4 address.")
    parser.add_argument("--broadcast", required=True, help="Explicit LAN broadcast destination selected by operator.")
    parser.add_argument("--mac", required=True, help="MAC string advertised in HBG3 v3.8 JSON template.")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--capture-root", type=Path, default=Path("captures"))
    parser.add_argument("--mc-version", default="03.08", help="Synthetic test-only MC_GET_VER major.minor bytes.")
    args = parser.parse_args(argv)
    try:
        major, minor = (int(value, 10) for value in args.mc_version.split(".", 1))
    except ValueError as error:
        raise SystemExit("--mc-version must be decimal major.minor") from error
    if not 0 <= major <= 255 or not 0 <= minor <= 255:
        raise SystemExit("--mc-version bytes must be 0..255")
    profile = SyntheticAUXProfile(
        frozenset({
            (0x20, 0x10, MC_GET_VER, 0), (0x20, 0x11, MC_GET_VER, 0),
            (0x20, 0x10, MC_GET_MODEL, 0),
            (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x11, MC_MOVE_POS, 1),
            (0x20, 0x10, MC_MOVE_NEG, 1), (0x20, 0x11, MC_MOVE_NEG, 1),
            (0x20, 0x10, MC_GET_POS_BACKLASH, 0), (0x20, 0x11, MC_GET_POS_BACKLASH, 0),
            (0x20, 0x10, MC_GET_APPROACH, 0), (0x20, 0x11, MC_GET_APPROACH, 0),
            (0x20, 0x10, MC_GET_MAX_SLEW_RATE, 0),
            (0x20, 0x10, MC_GET_MAX_RATE, 0),
            (0x20, 0x10, MC_SET_AUTOGUIDE_RATE, 1), (0x20, 0x11, MC_SET_AUTOGUIDE_RATE, 1),
            (0x20, 0x10, MC_GET_AUTOGUIDE_RATE, 0), (0x20, 0x11, MC_GET_AUTOGUIDE_RATE, 0),
            (0x20, 0x10, 0x01, 0), (0x20, 0x11, 0x01, 0),
        }),
        hypothetical_zero_payload_ack_requests=frozenset({
            (0x20, 0x10, MC_MOVE_POS, b"\x00"),
            (0x20, 0x11, MC_MOVE_POS, b"\x00"),
        }),
        experimental_zero_backlash_requests=frozenset({
            (0x20, 0x10, MC_GET_POS_BACKLASH, b""),
            (0x20, 0x11, MC_GET_POS_BACKLASH, b""),
        }),
        experimental_approach_value_00_requests=frozenset({
            (0x20, 0x10, MC_GET_APPROACH, b""),
            (0x20, 0x11, MC_GET_APPROACH, b""),
        }),
        experimental_hbg3_v38_max_slew_rate_requests=frozenset({(0x20, 0x10, MC_GET_MAX_SLEW_RATE, b"")}),
        experimental_hbg3_v38_max_rate_requests=frozenset({(0x20, 0x10, MC_GET_MAX_RATE, b"")}),
        simulated_manual_motion_requests=frozenset({
            (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x11, MC_MOVE_POS, 1),
            (0x20, 0x10, MC_MOVE_NEG, 1), (0x20, 0x11, MC_MOVE_NEG, 1),
        }),
    )
    # Synthetic-only calibration: these values are not NXW436 physical calibration.
    coordinate_adapter = AUXCoordinateAdapter(
        az=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
        alt=AxisCoordinateConfig(neutral_modulus=256, neutral_zero=0, aux_zero=0, direction=1),
    )
    fake_backend = FakeMountBackend(az_position=0x10, alt_position=0x20)
    dispatcher = AUXDispatcher(
        MountController(fake_backend), AUXCapabilities(position_translation_enabled=True),
        coordinate_adapter=coordinate_adapter, identity=VirtualMountIdentity((major, minor)),
        synthetic_profile=profile, virtual_mcs=VirtualCelestronMotorControllers(),
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = AUXTCPServer(
        dispatcher, bind=args.bind, port=args.port, capture_root=args.capture_root,
        client_label="SkyPortal-HBG3-Infrastructure", allow_synthetic_replies=True,
        synthetic_profile_name="hbg3-experiment-get-ver-model-move-backlash-approach-and-v38-max-rates",
        metadata_extra={
            "mode": "synthetic-test-only",
            "hbg3_reference_commit": "8c3a3c50e6797b77b3cdfedc258a1bed66e55f23",
            "hbg3_advertisement_evidence": "DIRECTLY_IMPLEMENTED port/template; destination/source port NOT_FOUND",
            "advertisement_destination": args.broadcast,
            "experimental_hypothesis": "HYPOTHETICAL_ZERO_PAYLOAD_ACK for exact 20->10 and 20->11 MC_MOVE_POS payload 00; not protocol-proven",
            "experimental_zero_backlash": "EXPERIMENTAL_ZERO_BACKLASH for exact 20->10 and 20->11 MC_GET_POS_BACKLASH empty requests; AZ CLIENT_ACCEPTED_EXPERIMENTALLY, ALT pending; NOT_DEVICE_CAPTURE_PROVEN",
            "experimental_approach": "EXPERIMENTAL_APPROACH_VALUE_00 for exact 20->10 and 20->11 MC_GET_APPROACH empty requests; AZ CLIENT_ACCEPTED_EXPERIMENTALLY, ALT pending; NOT_DEVICE_CAPTURE_PROVEN; VALUE_SEMANTICS_UNKNOWN",
            "experimental_max_slew_rate": "HBG3_V38_COMPATIBILITY_RESPONSE for exact 20->10 MC_GET_MAX_SLEW_RATE empty request; CLIENT_ACCEPTANCE_NOT_YET_PROVEN; NOT_DEVICE_CAPTURE_PROVEN",
            "experimental_max_rate": "HBG3_V38_COMPATIBILITY_RESPONSE for exact 20->10 MC_GET_MAX_RATE empty request; CLIENT_ACCEPTANCE_NOT_YET_PROVEN; NOT_DEVICE_CAPTURE_PROVEN",
        },
    )
    experiment = HBG3InfrastructureExperiment(server, bind=args.bind, broadcast=args.broadcast, mac=args.mac)
    server.record_observer = experiment.observe_record
    try:
        experiment.run()
    except KeyboardInterrupt:
        experiment.exit_reason = "keyboard_interrupt"
        experiment.stop_event.set()
    finally:
        server.close(experiment.exit_reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
