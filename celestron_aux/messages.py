"""AUX message model and diagnostically recognized command identifiers."""

from __future__ import annotations

from dataclasses import dataclass


SOM = 0x3B
MIN_LENGTH = 3  # source, destination, command
MAX_LENGTH = 255

MC_GET_POSITION = 0x01
MC_GOTO_FAST = 0x02
MC_SET_POSITION = 0x04
MC_GET_MODEL = 0x05
MC_SET_POS_GUIDERATE = 0x06
MC_SET_NEG_GUIDERATE = 0x07
MC_SLEW_DONE = 0x13
MC_GET_MAX_SLEW_RATE = 0x21
MC_GET_MAX_RATE = 0x23
MC_GOTO_SLOW = 0x17
MC_MOVE_POS = 0x24
MC_MOVE_NEG = 0x25
MC_GET_POS_BACKLASH = 0x40
MC_GET_NEG_BACKLASH = 0x41
MC_SET_AUTOGUIDE_RATE = 0x46
MC_GET_AUTOGUIDE_RATE = 0x47
MC_GET_APPROACH = 0xFC
MC_SET_APPROACH = 0xFD
MC_GET_VER = 0xFE

KNOWN_DIAGNOSTIC_COMMANDS = frozenset({
    MC_GET_POSITION, MC_GOTO_FAST, MC_SET_POSITION, MC_GET_MODEL, MC_SET_POS_GUIDERATE,
    MC_SET_NEG_GUIDERATE, MC_SET_AUTOGUIDE_RATE, MC_GET_AUTOGUIDE_RATE, MC_SLEW_DONE, MC_GET_MAX_SLEW_RATE, MC_GET_MAX_RATE, MC_GOTO_SLOW, MC_MOVE_POS, MC_GET_POS_BACKLASH, MC_GET_APPROACH,
    MC_MOVE_NEG, MC_GET_VER,
})


@dataclass(frozen=True)
class AUXFrame:
    """Application-level AUX frame without transport/session dependencies."""

    source: int
    destination: int
    command: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        for name, value in (("source", self.source), ("destination", self.destination), ("command", self.command)):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"{name} must be a byte")
        if len(self.payload) > MAX_LENGTH - MIN_LENGTH:
            raise ValueError("payload is too large for an AUX frame")
