from __future__ import annotations

import unittest
import tempfile
import threading
from pathlib import Path

from celestron_aux.dispatcher import AUXDispatcher, SyntheticAUXProfile
from celestron_aux.goto_coordinator import GoToCoordinator, GotoState
from celestron_aux.hbg3_infrastructure_experiment import (
    HBG3InfrastructureExperiment, hbg3_v38_advertisement, resolved_gate_path,
    build_fake_coordinate_adapter, configure_two_stage_gate,
)
from celestron_aux.messages import AUXFrame, MC_GET_VER, MC_SLEW_DONE
from fake_mount_backend import FakeMountBackend
from mount_api import Axis, MountController


class FakeServer:
    active_connection = False
    capture = None


class RecordingUDP:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, destination: tuple[str, int]) -> None:
        self.calls.append((payload, destination))


class HBG3InfrastructureExperimentTests(unittest.TestCase):
    def test_advertisement_matches_hbg3_version_template(self) -> None:
        payload = hbg3_v38_advertisement("01:02:03:04:05:06")
        self.assertEqual(payload, (
            b'{"mac":"01:02:03:04:05:06",\n"version":"'
            b'HomeBrew-AMW007-9.0.0.0, 2021-10-18T12:00:00Z, ESP32-3.8"\n}'
        ))

    def test_fake_adapter_uses_backend_native_modulus_without_rapid_wrap(self) -> None:
        adapter = build_fake_coordinate_adapter()
        self.assertEqual(adapter.configuration("az").neutral_modulus, FakeMountBackend.POSITION_MODULUS)
        self.assertEqual(adapter.to_aux("az", 0), 0)
        self.assertLess(adapter.to_aux("az", 1), 32)

    def test_advertises_only_without_active_tcp_client(self) -> None:
        server = FakeServer()
        experiment = HBG3InfrastructureExperiment(server, bind="127.0.0.1", broadcast="127.0.0.1", mac="00:00:00:00:00:00")
        udp = RecordingUDP()
        self.assertTrue(experiment.advertise_once(udp))
        self.assertEqual(udp.calls[0][1], ("127.0.0.1", 55555))
        server.active_connection = True
        self.assertFalse(experiment.advertise_once(udp))
        self.assertEqual(len(udp.calls), 1)

    def test_unmatched_get_ver_is_recorded_without_stopping_experiment(self) -> None:
        server = FakeServer()
        experiment = HBG3InfrastructureExperiment(server, bind="127.0.0.1", broadcast="127.0.0.1", mac="00:00:00:00:00:00")
        experiment.observe_record({"command": MC_GET_VER, "dispatch_result": "recognized_synthetic_profile_unconfigured"})
        self.assertTrue(experiment.unmatched_get_ver_observed)
        self.assertFalse(experiment.stop_event.is_set())
        udp = RecordingUDP()
        server.active_connection = True
        self.assertFalse(experiment.advertise_once(udp))
        server.active_connection = False
        self.assertTrue(experiment.advertise_once(udp))

    def test_gate_file_releases_fast_then_rearms_and_releases_slow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            gate_path = resolved_gate_path(Path(temporary) / "release-goto")
            self.assertTrue(gate_path.is_absolute())
            self.assertFalse(gate_path.exists())
            gate = threading.Event()
            controller = MountController(FakeMountBackend(goto_gate=gate))
            coordinator = GoToCoordinator(controller, enabled=True)
            logs: list[str] = []
            coordinator.state_observer = configure_two_stage_gate(
                coordinator, gate_path, gate, logs.append, poll_seconds=0.01,
            )
            dispatcher = AUXDispatcher(
                controller,
                synthetic_profile=SyntheticAUXProfile(frozenset({
                    (0x20, 0x10, MC_SLEW_DONE, 0),
                    (0x20, 0x11, MC_SLEW_DONE, 0),
                })),
                goto_coordinator=coordinator,
            )
            az = coordinator.start(Axis.AZ, 100, variant=0x02)
            alt = coordinator.start(Axis.ALT, 200, variant=0x02)
            self.assertEqual(coordinator.state(Axis.AZ), GotoState.GOTO_ACTIVE)
            self.assertEqual(coordinator.state(Axis.ALT), GotoState.GOTO_ACTIVE)
            self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SLEW_DONE)).reply.payload, b"\x00")
            self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_SLEW_DONE)).reply.payload, b"\x00")
            gate_path.touch()
            az.thread.join(1)
            alt.thread.join(1)
            self.assertEqual(coordinator.state(Axis.AZ), GotoState.COMPLETED)
            self.assertEqual(coordinator.state(Axis.ALT), GotoState.COMPLETED)
            self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SLEW_DONE)).reply.payload, b"\xff")
            self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_SLEW_DONE)).reply.payload, b"\xff")
            deadline = threading.Event()
            for _ in range(100):
                if not gate.is_set() and not gate_path.exists():
                    break
                deadline.wait(0.01)
            self.assertFalse(gate.is_set())
            self.assertFalse(gate_path.exists())
            slow_az = coordinator.start(Axis.AZ, 110, variant=0x17)
            slow_alt = coordinator.start(Axis.ALT, 210, variant=0x17)
            self.assertEqual(coordinator.state(Axis.AZ), GotoState.GOTO_ACTIVE)
            self.assertEqual(coordinator.state(Axis.ALT), GotoState.GOTO_ACTIVE)
            gate_path.touch()
            slow_az.thread.join(1)
            slow_alt.thread.join(1)
            self.assertEqual(coordinator.state(Axis.AZ), GotoState.COMPLETED)
            self.assertEqual(coordinator.state(Axis.ALT), GotoState.COMPLETED)
            self.assertTrue(any("goto_gate_rearmed stage=SLOW" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
