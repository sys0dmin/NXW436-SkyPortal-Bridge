"""Generate immutable Milestone-1 fixtures from the Python reference."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from celestron_aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from celestron_aux.dispatcher import (
    AUXCapabilities, AUXDispatcher, SyntheticAUXProfile, VirtualMountIdentity,
)
from celestron_aux.framing import deserialize, serialize
from celestron_aux.goto_coordinator import GotoState
from celestron_aux.messages import *
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from fake_mount_backend import FakeMountBackend
from mount_api import Axis, MountController, normalize_position, signed_modular_delta
from mount_model import POSITION_MODULUS

FIXTURES = Path(__file__).resolve().parents[1] / "test" / "fixtures"


class FixtureCoordinator:
    def __init__(self) -> None:
        self.states = {Axis.AZ: GotoState.IDLE, Axis.ALT: GotoState.IDLE}
        self.targets = {Axis.AZ: 0, Axis.ALT: 0}

    def start(self, axis: Axis, target: int, *, variant: int):
        if self.is_active(axis):
            raise RuntimeError("already active")
        self.states[axis] = GotoState.GOTO_ACTIVE
        self.targets[axis] = target
        return SimpleNamespace(axis=axis, target=target, variant=variant)

    def state(self, axis: Axis) -> GotoState:
        return self.states[axis]

    def job(self, axis: Axis):
        return None

    def is_active(self, axis: Axis) -> bool:
        return self.states[axis] in {GotoState.GOTO_ACTIVE, GotoState.STOPPING}

    def cancel(self, axis: Axis, *, timeout: float = 2.0) -> bool:
        self.states[axis] = GotoState.CANCELLED
        return True

    def complete(self, axis: Axis) -> None:
        self.states[axis] = GotoState.COMPLETED


def frame(source: int, destination: int, command: int, payload: bytes = b"") -> AUXFrame:
    return AUXFrame(source, destination, command, payload)


def make_system():
    backend = FakeMountBackend(az_position=16, alt_position=32)
    controller = MountController(backend)
    adapter = AUXCoordinateAdapter(
        az=AxisCoordinateConfig(POSITION_MODULUS, 0, 0, 1),
        alt=AxisCoordinateConfig(POSITION_MODULUS, 0, 0, 1),
    )
    allowed = frozenset({
        (0x20, 0x10, MC_GET_VER, 0), (0x20, 0x11, MC_GET_VER, 0),
        (0x20, 0x10, MC_GET_MODEL, 0),
        (0x20, 0x10, MC_GET_POSITION, 0), (0x20, 0x11, MC_GET_POSITION, 0),
        (0x20, 0x10, MC_GET_POS_BACKLASH, 0), (0x20, 0x11, MC_GET_POS_BACKLASH, 0),
        (0x20, 0x10, MC_GET_APPROACH, 0), (0x20, 0x11, MC_GET_APPROACH, 0),
        (0x20, 0x10, MC_GET_MAX_SLEW_RATE, 0), (0x20, 0x10, MC_GET_MAX_RATE, 0),
        (0x20, 0x10, MC_GET_AUTOGUIDE_RATE, 0), (0x20, 0x11, MC_GET_AUTOGUIDE_RATE, 0),
        (0x20, 0x10, MC_SET_POS_GUIDERATE, 3),
        (0x20, 0x10, MC_SET_AUTOGUIDE_RATE, 1), (0x20, 0x11, MC_SET_AUTOGUIDE_RATE, 1),
        (0x20, 0x10, MC_MOVE_POS, 1), (0x20, 0x11, MC_MOVE_POS, 1),
        (0x20, 0x10, MC_MOVE_NEG, 1), (0x20, 0x11, MC_MOVE_NEG, 1),
        (0x20, 0x10, MC_GOTO_FAST, 3), (0x20, 0x11, MC_GOTO_FAST, 3),
        (0x20, 0x10, MC_GOTO_SLOW, 3), (0x20, 0x11, MC_GOTO_SLOW, 3),
        (0x20, 0x10, MC_SLEW_DONE, 0), (0x20, 0x11, MC_SLEW_DONE, 0),
    })
    profile = SyntheticAUXProfile(
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
        experimental_goto_ack_shapes=frozenset({
            (0x20, 0x10, MC_GOTO_FAST, 3), (0x20, 0x11, MC_GOTO_FAST, 3),
            (0x20, 0x10, MC_GOTO_SLOW, 3), (0x20, 0x11, MC_GOTO_SLOW, 3),
        }),
        hbg_optional_device_emulation=True,
        hbg_evwifi_shim=True,
    )
    coordinator = FixtureCoordinator()
    dispatcher = AUXDispatcher(
        controller, AUXCapabilities(position_translation_enabled=True), adapter,
        identity=VirtualMountIdentity((3, 8)),
        synthetic_profile=profile, virtual_mcs=VirtualCelestronMotorControllers(),
        goto_coordinator=coordinator,
    )
    return backend, dispatcher, coordinator


def write_transcript(name: str, operations: list[tuple]) -> None:
    backend, dispatcher, coordinator = make_system()
    lines = [f"# generated_by=tools/generate_golden.py", f"# fixture={name}"]
    for operation in operations:
        if operation[0] == "COMPLETE":
            axis = Axis.AZ if operation[1] == "AZ" else Axis.ALT
            coordinator.complete(axis)
            backend._positions[axis] = coordinator.targets[axis]  # fixture-only deterministic completion
            lines.append(f"COMPLETE;{operation[1]}")
            continue
        request = operation[1]
        outcome = dispatcher.dispatch(request)
        request_hex = serialize(request).hex().upper()
        response_hex = serialize(outcome.reply).hex().upper() if outcome.reply else "-"
        lines.append(f"RX;{request_hex};{response_hex}")
    (FIXTURES / f"{name}.fixture").write_text("\n".join(lines) + "\n", encoding="ascii")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    math_rows = [
        ("N", -1, "", normalize_position(-1)),
        ("N", POSITION_MODULUS, "", 0),
        ("N", POSITION_MODULUS + 7, "", 7),
        ("D", POSITION_MODULUS - 5, 5, signed_modular_delta(POSITION_MODULUS - 5, 5)),
        ("D", 5, POSITION_MODULUS - 5, signed_modular_delta(5, POSITION_MODULUS - 5)),
    ]
    with (FIXTURES / "position_math.csv").open("w", newline="", encoding="ascii") as file:
        writer = csv.writer(file); writer.writerow(("kind", "a", "b", "expected")); writer.writerows(math_rows)

    coordinate_adapter = AUXCoordinateAdapter(
        az=AxisCoordinateConfig(POSITION_MODULUS, 1000, 0x123456, 1),
        alt=AxisCoordinateConfig(POSITION_MODULUS, 2000, 0x654321, -1),
    )
    coordinate_rows = []
    for axis, config, values in (
        ("AZ", coordinate_adapter._axes["az"], (0, 999, 1000, 1001, POSITION_MODULUS - 1)),
        ("ALT", coordinate_adapter._axes["alt"], (0, 1999, 2000, 2001, POSITION_MODULUS - 1)),
    ):
        key = axis.lower()
        for native in values:
            aux = coordinate_adapter.to_aux(key, native)
            coordinate_rows.append((axis, config.direction, config.neutral_modulus,
                                    config.neutral_zero, config.aux_zero,
                                    "TO", native, aux))
            coordinate_rows.append((axis, config.direction, config.neutral_modulus,
                                    config.neutral_zero, config.aux_zero,
                                    "FROM", aux, coordinate_adapter.from_aux(key, aux)))
    with (FIXTURES / "coordinate.csv").open("w", newline="", encoding="ascii") as file:
        writer = csv.writer(file)
        writer.writerow(("axis", "direction", "native_modulus", "native_zero",
                         "aux_zero", "operation", "input", "expected"))
        writer.writerows(coordinate_rows)

    write_transcript("startup_manual", [
        ("RX", frame(0x20, 0xB5, 0x15, bytes.fromhex("58EE1107DB181B308A04"))),
        ("RX", frame(0x20, 0xBD, MC_GET_VER)),
        ("RX", frame(0x20, 0xB9, MC_GET_VER)),
        ("RX", frame(0x20, 0xB4, MC_GET_VER)),
        ("RX", frame(0x20, 0x12, MC_GET_VER)),
        ("RX", frame(0x20, 0xB9, 0x49)),
        ("RX", frame(0x20, 0xB9, 0x32, bytes.fromhex("32529D82"))),
        ("RX", frame(0x20, 0xB4, 0x3F, b"\x00")),
        ("RX", frame(0x20, 0x12, 0x2B)),
        ("RX", frame(0x20, 0x10, MC_GET_VER)),
        ("RX", frame(0x20, 0x10, MC_GET_MODEL)),
        ("RX", frame(0x20, 0x10, MC_MOVE_POS, b"\x00")),
        ("RX", frame(0x20, 0x11, MC_MOVE_POS, b"\x00")),
        ("RX", frame(0x20, 0x10, MC_GET_POS_BACKLASH)),
        ("RX", frame(0x20, 0x10, MC_GET_APPROACH)),
        ("RX", frame(0x20, 0x10, MC_GET_MAX_SLEW_RATE)),
        ("RX", frame(0x20, 0x10, MC_GET_MAX_RATE)),
        ("RX", frame(0x20, 0x10, MC_GET_AUTOGUIDE_RATE)),
        ("RX", frame(0x20, 0x10, MC_GET_POSITION)),
        ("RX", frame(0x20, 0x10, 0x06, b"\x00\x00\x00")),
        ("RX", frame(0x20, 0x11, MC_GET_POSITION)),
        ("RX", frame(0x20, 0x10, MC_MOVE_NEG, b"\x09")),
        ("RX", frame(0x20, 0x10, MC_MOVE_POS, b"\x00")),
        ("RX", frame(0x20, 0x10, 0x99)),
    ])
    write_transcript("goto", [
        ("RX", frame(0x20, 0x10, MC_GOTO_FAST, bytes.fromhex("0FCBF1"))),
        ("RX", frame(0x20, 0x11, MC_GOTO_FAST, bytes.fromhex("1F87CE"))),
        ("RX", frame(0x20, 0x10, MC_SLEW_DONE)),
        ("RX", frame(0x20, 0x11, MC_SLEW_DONE)),
        ("COMPLETE", "AZ"), ("COMPLETE", "ALT"),
        ("RX", frame(0x20, 0x10, MC_SLEW_DONE)),
        ("RX", frame(0x20, 0x11, MC_SLEW_DONE)),
        ("RX", frame(0x20, 0x10, MC_GOTO_SLOW, bytes.fromhex("10112A"))),
        ("RX", frame(0x20, 0x11, MC_GOTO_SLOW, bytes.fromhex("1FF1B8"))),
    ])
    write_transcript("cancel", [
        ("RX", frame(0x20, 0x10, MC_GOTO_FAST, bytes.fromhex("0FCBF1"))),
        ("RX", frame(0x20, 0x11, MC_GOTO_FAST, bytes.fromhex("1F87CE"))),
        ("RX", frame(0x20, 0x10, MC_MOVE_POS, b"\x00")),
        ("RX", frame(0x20, 0x11, MC_MOVE_POS, b"\x00")),
        ("RX", frame(0x20, 0x10, MC_SLEW_DONE)),
    ])


if __name__ == "__main__":
    main()
