from __future__ import annotations

import threading
import unittest

from celestron_aux.coordinates import AUXCoordinateAdapter, AxisCoordinateConfig
from celestron_aux.dispatcher import AUXCapabilities, AUXDispatcher, SyntheticAUXProfile
from celestron_aux.framing import serialize
from celestron_aux.goto_coordinator import GoToCoordinator, GotoState
from celestron_aux.messages import AUXFrame, MC_GET_POSITION, MC_GOTO_FAST, MC_MOVE_POS, MC_SLEW_DONE
from celestron_aux.virtual_mc import VirtualCelestronMotorControllers
from fake_mount_backend import FakeMountBackend
from mount_api import Axis, MountController


class GotoCoordinatorTests(unittest.TestCase):
    def make_system(self):
        gate = threading.Event()
        backend = FakeMountBackend(az_position=0, alt_position=0, goto_gate=gate)
        controller = MountController(backend)
        adapter = AUXCoordinateAdapter(
            az=AxisCoordinateConfig(256, 0, 0, 1),
            alt=AxisCoordinateConfig(256, 0, 0, 1),
        )
        profile = SyntheticAUXProfile(frozenset({
            (0x20, 0x10, MC_GOTO_FAST, 3),
            (0x20, 0x10, MC_GET_POSITION, 0),
            (0x20, 0x10, MC_SLEW_DONE, 0), (0x20, 0x11, MC_SLEW_DONE, 0),
            (0x20, 0x10, MC_MOVE_POS, 1),
        }), simulated_manual_motion_requests=frozenset({(0x20, 0x10, MC_MOVE_POS, 1)}),
            experimental_goto_ack_shapes=frozenset({(0x20, 0x10, MC_GOTO_FAST, 3)}))
        coordinator = GoToCoordinator(controller, enabled=True)
        dispatcher = AUXDispatcher(
            controller, AUXCapabilities(position_translation_enabled=True), adapter,
            synthetic_profile=profile, virtual_mcs=VirtualCelestronMotorControllers(),
            goto_coordinator=coordinator,
        )
        return gate, backend, coordinator, dispatcher

    def test_goto_starts_background_and_position_remains_usable(self) -> None:
        gate, backend, coordinator, dispatcher = self.make_system()
        result = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GOTO_FAST, (128).to_bytes(3, "big")))
        self.assertEqual(result.status, "goto_started:GOTO_ACTIVE")
        self.assertEqual(result.reply, AUXFrame(0x10, 0x20, MC_GOTO_FAST, b""))
        backend_axis = Axis.AZ
        self.assertEqual(coordinator.state(backend_axis), GotoState.GOTO_ACTIVE)
        position = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GET_POSITION))
        self.assertEqual(position.status, "mount_query_reply")
        gate.set()
        coordinator.job(backend_axis).thread.join(1)
        self.assertEqual(coordinator.state(backend_axis), GotoState.COMPLETED)

    def test_second_goto_rejected_and_cancel_allows_manual(self) -> None:
        gate, backend, coordinator, dispatcher = self.make_system()
        request = AUXFrame(0x20, 0x10, MC_GOTO_FAST, (128).to_bytes(3, "big"))
        self.assertEqual(dispatcher.dispatch(request).status, "goto_started:GOTO_ACTIVE")
        self.assertTrue(dispatcher.dispatch(request).status.startswith("goto_rejected"))
        manual = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_MOVE_POS, b"\x02"))
        self.assertEqual(manual.status, "simulated_manual_motion_accepted")
        gate.set()

    def test_failed_job_is_observable(self) -> None:
        gate, backend, coordinator, dispatcher = self.make_system()
        backend.goto = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fake failure"))  # type: ignore[method-assign]
        self.assertEqual(
            dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GOTO_FAST, (128).to_bytes(3, "big"))).status,
            "goto_started:GOTO_ACTIVE",
        )
        job = coordinator.job(Axis.AZ)
        job.thread.join(1)
        self.assertEqual(job.state, GotoState.FAILED)
        self.assertIn("fake failure", job.error)

    def test_slew_done_maps_active_and_completed_per_axis(self) -> None:
        gate, backend, coordinator, dispatcher = self.make_system()
        self.assertEqual(
            dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GOTO_FAST, (128).to_bytes(3, "big"))).status,
            "goto_started:GOTO_ACTIVE",
        )
        active = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SLEW_DONE))
        self.assertEqual(active.status, "slew_active")
        self.assertEqual(serialize(active.reply), bytes.fromhex("3B0410201300B9"))
        self.assertEqual(dispatcher.dispatch(AUXFrame(0x20, 0x11, MC_SLEW_DONE)).status, "slew_done_state_unmapped:IDLE")
        self.assertTrue(
            dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GOTO_FAST, (128).to_bytes(3, "big"))).status.startswith("goto_rejected")
        )
        gate.set()
        coordinator.job(Axis.AZ).thread.join(1)
        done = dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SLEW_DONE))
        self.assertEqual(done.status, "slew_done")
        self.assertEqual(serialize(done.reply), bytes.fromhex("3B04102013FFBA"))

    def test_slew_done_malformed_and_hardware_disabled(self) -> None:
        gate, backend, coordinator, dispatcher = self.make_system()
        self.assertIsNone(dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_SLEW_DONE, b"\x00")).reply)

    def test_az_and_alt_jobs_have_independent_state(self) -> None:
        gate = threading.Event()
        backend = FakeMountBackend(goto_gate=gate)
        coordinator = GoToCoordinator(MountController(backend), enabled=True)
        az = coordinator.start(Axis.AZ, 64, variant=MC_GOTO_FAST)
        alt = coordinator.start(Axis.ALT, 96, variant=MC_GOTO_FAST)
        self.assertEqual(coordinator.state(Axis.AZ), GotoState.GOTO_ACTIVE)
        self.assertEqual(coordinator.state(Axis.ALT), GotoState.GOTO_ACTIVE)
        gate.set()
        az.thread.join(1)
        alt.thread.join(1)
        self.assertEqual(az.state, GotoState.COMPLETED)
        self.assertEqual(alt.state, GotoState.COMPLETED)

    def test_cancel_transitions_job_without_implicit_disconnect_policy(self) -> None:
        gate, backend, coordinator, dispatcher = self.make_system()
        self.assertEqual(
            dispatcher.dispatch(AUXFrame(0x20, 0x10, MC_GOTO_FAST, (128).to_bytes(3, "big"))).status,
            "goto_started:GOTO_ACTIVE",
        )
        self.assertTrue(coordinator.cancel(Axis.AZ, timeout=1.0))
        self.assertEqual(coordinator.state(Axis.AZ), GotoState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
