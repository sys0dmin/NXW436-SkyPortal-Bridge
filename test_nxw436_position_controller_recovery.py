from __future__ import annotations

import unittest
from unittest.mock import patch

import nxw436_position_controller as controller_module
from nxw436_driver import RAW_MODULO
from nxw436_position_controller import ControllerAbort, PROFILES, STOP_MARGINS, RelativePositionController


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def perf_counter(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_700_000_000.0 + self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


class FakeMount:
    def __init__(self, positions: list[int | None]) -> None:
        self.positions = positions
        self.index = 0
        self.commands: list[tuple[str, str, str, bytes]] = []

    def query_position_raw(self, axis: str):
        if self.index >= len(self.positions):
            value = self.positions[-1]
        else:
            value = self.positions[self.index]
            self.index += 1
        if value is None:
            return None
        return value.to_bytes(3, "big")

    def move(self, axis: str, direction: str, payload: bytes) -> None:
        self.commands.append(("move", axis, direction, payload))

    def stop(self, axis: str, direction: str) -> None:
        self.commands.extend((
            ("stop", axis, direction, b"\0\0\0"),
            ("stop", axis, direction, b"\0\0\0"),
        ))


def run_with_fake_mount(positions: list[int | None], *, target: int = 6000):
    clock = FakeClock()
    mount = FakeMount(positions)
    with (
        patch.object(controller_module.time, "perf_counter", clock.perf_counter),
        patch.object(controller_module.time, "time", clock.time),
        patch.object(controller_module.time, "sleep", clock.sleep),
    ):
        controller = RelativePositionController(mount, "az", target)
        result = controller.run(max_seconds=20.0, settle_seconds=0.3)
    return controller, mount, result


def normal_finish_positions() -> list[int]:
    return [100000, 101200, 102400, 103600, 104800, 105225, 105800, 105800, 105800]


def normal_finish_positions_negative() -> list[int]:
    return [100000, 98800, 97600, 96400, 95200, 94775, 94200, 94200, 94200]


class ProductionAzMediumRecoveryTests(unittest.TestCase):
    def test_az_stop200_configuration_preserves_alt_and_stage_thresholds(self):
        self.assertEqual(STOP_MARGINS["az"], 200)
        self.assertEqual(STOP_MARGINS["alt"], 350)
        self.assertEqual(PROFILES["az"][-1].name, "SLOW")
        self.assertEqual(PROFILES["az"][-1].minimum_remaining_counts, 300)
        _, _, result = run_with_fake_mount(normal_finish_positions())
        self.assertEqual(result["stop_margin_counts"], 200)

    def test_az_stop300_override_is_explicit_and_does_not_change_profile(self):
        clock = FakeClock()
        mount = FakeMount(normal_finish_positions())
        with (
            patch.object(controller_module.time, "perf_counter", clock.perf_counter),
            patch.object(controller_module.time, "time", clock.time),
            patch.object(controller_module.time, "sleep", clock.sleep),
        ):
            result = RelativePositionController(mount, "az", 6000, stop_margin_counts=300).run(
                max_seconds=20.0, settle_seconds=0.3
            )
        self.assertEqual(result["stop_margin_counts"], 300)
        self.assertEqual(PROFILES["az"][-1].minimum_remaining_counts, 300)

    def test_normal_medium_motion_has_no_resend(self):
        controller, mount, result = run_with_fake_mount(normal_finish_positions())
        medium_moves = [command for command in mount.commands if command[0] == "move" and command[3] == bytes.fromhex("0072F1")]
        self.assertEqual(len(medium_moves), 1)
        self.assertFalse(result["no_progress_detected"])
        self.assertFalse(result["recovery_attempted"])
        self.assertEqual(result["recovery_count"], 0)
        self.assertEqual(result["recovery_success"], None)
        self.assertTrue(any(row["state"] == "MEDIUM" for row in controller.rows))

    def test_negative_normal_medium_motion_has_no_resend(self):
        _, mount, result = run_with_fake_mount(normal_finish_positions_negative(), target=-6000)
        medium_moves = [command for command in mount.commands if command[0] == "move" and command[3] == bytes.fromhex("0072F1")]
        self.assertEqual(len(medium_moves), 1)
        self.assertFalse(result["no_progress_detected"])
        self.assertFalse(result["recovery_attempted"])

    def test_plateau_resends_once_then_continues_to_target(self):
        positions = [100000, 101200] + [101205] * 12 + [101225, 102425, 103625, 104825, 105225, 105800, 105800, 105800]
        _, mount, result = run_with_fake_mount(positions)
        medium_moves = [command for command in mount.commands if command[0] == "move" and command[3] == bytes.fromhex("0072F1")]
        self.assertEqual(len(medium_moves), 2)
        self.assertTrue(result["no_progress_detected"])
        self.assertTrue(result["recovery_attempted"])
        self.assertEqual(result["recovery_count"], 1)
        self.assertEqual(result["recovery_payload"], "0072F1")
        self.assertEqual(result["recovery_progress_counts"], 20)
        self.assertTrue(result["recovery_success"])
        self.assertEqual(result["recovery_failure_reason"], "")

    def test_plateau_with_no_recovery_aborts_after_double_stop(self):
        positions = [100000, 101200] + [101205] * 30
        clock = FakeClock()
        mount = FakeMount(positions)
        with (
            patch.object(controller_module.time, "perf_counter", clock.perf_counter),
            patch.object(controller_module.time, "time", clock.time),
            patch.object(controller_module.time, "sleep", clock.sleep),
        ):
            controller = RelativePositionController(mount, "az", 6000)
            with self.assertRaisesRegex(ControllerAbort, "az_medium_recovery_failed"):
                controller.run(max_seconds=20.0, settle_seconds=0.3)
        medium_moves = [command for command in mount.commands if command[0] == "move" and command[3] == bytes.fromhex("0072F1")]
        self.assertEqual(len(medium_moves), 2)
        self.assertEqual(mount.commands[-2:], [
            ("stop", "az", "+", b"\0\0\0"),
            ("stop", "az", "+", b"\0\0\0"),
        ])
        self.assertEqual(controller.az_medium_recovery_failure_reason, "no-valid-commanded-progress-in-recovery-window")

    def test_invalid_replies_are_not_stationary_evidence(self):
        positions = [100000, 101200, RAW_MODULO + 1, RAW_MODULO + 2, RAW_MODULO + 3]
        clock = FakeClock()
        mount = FakeMount(positions)
        with (
            patch.object(controller_module.time, "perf_counter", clock.perf_counter),
            patch.object(controller_module.time, "time", clock.time),
            patch.object(controller_module.time, "sleep", clock.sleep),
        ):
            controller = RelativePositionController(mount, "az", 6000)
            with self.assertRaisesRegex(ControllerAbort, "three consecutive timeout/malformed"):
                controller.run(max_seconds=20.0, settle_seconds=0.3)
        self.assertFalse(controller.az_medium_no_progress_detected)
        self.assertFalse(controller.az_medium_recovery_attempted)

    def test_near_medium_to_fine_transition_has_no_resend(self):
        positions = [100000, 101350, 102700, 103900, 104900, 105300, 105800, 105800, 105800]
        _, mount, result = run_with_fake_mount(positions, target=5200)
        medium_moves = [command for command in mount.commands if command[0] == "move" and command[3] == bytes.fromhex("0072F1")]
        self.assertEqual(len(medium_moves), 1)
        self.assertFalse(result["no_progress_detected"])
        self.assertFalse(result["recovery_attempted"])

    def test_single_reverse_outlier_is_logged_but_not_applied(self):
        positions = [100000, 101200, 100944, 102400, 103600, 104800, 105225, 105800, 105800, 105800]
        controller, _, result = run_with_fake_mount(positions)
        pending = next(row for row in controller.rows if row["reply_kind"] == "valid-opposite-pending")
        self.assertEqual(pending["raw_position"], 100944)
        self.assertEqual(pending["unwrapped_position"], 101200)
        self.assertIn("trusted position unchanged", pending["note"])
        resumed = next(row for row in controller.rows if "rejected pending opposite raw" in row["note"])
        self.assertEqual(resumed["raw_position"], 102400)
        self.assertEqual(result["settled_outcome"], "undershoot")
        self.assertEqual(controller.invalid_total, 0)

    def test_two_reverse_samples_confirm_abort_and_double_stop(self):
        positions = [100000, 101200, 100944, 100688]
        clock = FakeClock()
        mount = FakeMount(positions)
        with (
            patch.object(controller_module.time, "perf_counter", clock.perf_counter),
            patch.object(controller_module.time, "time", clock.time),
            patch.object(controller_module.time, "sleep", clock.sleep),
        ):
            controller = RelativePositionController(mount, "az", 6000)
            with self.assertRaisesRegex(ControllerAbort, "confirmed opposite-direction"):
                controller.run(max_seconds=20.0, settle_seconds=0.3)
        self.assertTrue(any(row["reply_kind"] == "valid-opposite-pending" for row in controller.rows))
        self.assertTrue(any(row["reply_kind"] == "valid-opposite-confirmed" for row in controller.rows))
        self.assertEqual(mount.commands[-2:], [
            ("stop", "az", "+", b"\0\0\0"),
            ("stop", "az", "+", b"\0\0\0"),
        ])

    def test_implausible_jump_is_still_skipped_without_position_update(self):
        positions = [100000, 101200, 105000, 102400, 103600, 104800, 105225, 105800, 105800, 105800]
        controller, _, _ = run_with_fake_mount(positions)
        invalid = next(row for row in controller.rows if row["reply_kind"] == "implausible-jump")
        self.assertEqual(invalid["raw_position"], 105000)
        self.assertEqual(invalid["unwrapped_position"], 101200)


if __name__ == "__main__":
    unittest.main()
