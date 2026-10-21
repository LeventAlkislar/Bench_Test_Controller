"""Recovery records and cursor rules; never opens a serial port or operates UI."""
import copy
import json
import queue
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.recipe.models import Recipe, RecipeStep, StepLoop
from bench_test.recipe.recovery import (
    RecoveryJournal, expanded_step_indices, plan_recovery, recipe_from_snapshot,
)
from bench_test.recipe.runner import RecipeRunner


def sample_record():
    recipe = Recipe("Two week PAD", [RecipeStep(0, 90) for _ in range(10)], 5)
    return {
        "schema_version": 1, "measurement_mode": "script_pad", "status": "running",
        "phase": "waiting", "recipe_loop": 2, "sequence_index": 5, "step_index": 5,
        "recipe": asdict(recipe), "active_step": asdict(recipe.steps[4]),
        "measurement_running": True, "active_measurement_kind": "discrete_pad",
        "paused": False, "stop_requested": False, "remaining_seconds": 2220,
    }


class RecoveryPlanTests(unittest.TestCase):
    def test_measurement_wait_advances_without_repeating_completed_loop(self):
        record = sample_record()
        plan = plan_recovery(record)
        self.assertEqual((plan.recipe_loop, plan.sequence_index, plan.step_index), (2, 6, 6))
        self.assertTrue(plan.skipped_measurement_wait)
        self.assertEqual(record["remaining_seconds"], 2220)

    def test_measurement_at_end_of_loop_advances_to_next_loop(self):
        record = sample_record()
        record.update(sequence_index=10, step_index=10)
        plan = plan_recovery(record)
        self.assertEqual((plan.recipe_loop, plan.step_index), (3, 1))

    def test_last_measurement_finishes_without_restarting_recipe(self):
        record = sample_record()
        record.update(recipe_loop=5, sequence_index=10, step_index=10)
        self.assertTrue(plan_recovery(record).finished)

    def test_control_actions_are_not_skipped_even_during_wait(self):
        for action in ("start_dropview", "stop_discrete_pad", "stop_measure", "exit_dropview"):
            record = sample_record()
            record["active_step"]["dropview_action"] = action
            with self.subTest(action=action):
                plan = plan_recovery(record)
                self.assertEqual(plan.sequence_index, 5)
                self.assertFalse(plan.skipped_measurement_wait)

    def test_pending_device_operations_are_not_skipped(self):
        for phase in ("step_pending", "valve_a", "valve_b", "pump", "dropview_start",
                      "measurement_start", "measurement_stop", "dropview_exit"):
            record = sample_record()
            record["phase"] = phase
            with self.subTest(phase=phase):
                self.assertEqual(plan_recovery(record).sequence_index, 5)

    def test_wait_without_running_measurement_is_not_skipped(self):
        record = sample_record()
        record["measurement_running"] = False
        self.assertEqual(plan_recovery(record).sequence_index, 5)

    def test_start_measurement_can_only_be_skipped_after_successful_start(self):
        record = sample_record()
        record["active_step"]["dropview_action"] = "start_discrete_pad"
        self.assertEqual(plan_recovery(record).sequence_index, 6)
        record["phase"] = "measurement_start"
        self.assertEqual(plan_recovery(record).sequence_index, 5)

    def test_live_edit_does_not_redefine_the_already_running_step(self):
        record = sample_record()
        record["recipe"]["steps"][4]["dropview_action"] = "stop_discrete_pad"
        self.assertEqual(plan_recovery(record).sequence_index, 6)
        record["active_step"]["dropview_action"] = "stop_discrete_pad"
        record["recipe"]["steps"][4]["dropview_action"] = "none"
        self.assertEqual(plan_recovery(record).sequence_index, 5)

    def test_inner_loop_uses_execution_sequence_not_visible_step_number(self):
        recipe = Recipe("Inner", [RecipeStep(0, 1) for _ in range(4)], 2, [StepLoop(2, 3, 3)])
        self.assertEqual(expanded_step_indices(recipe), [0, 1, 2, 1, 2, 1, 2, 3])
        record = sample_record()
        record.update(recipe=asdict(recipe), recipe_loop=1, sequence_index=4, step_index=2)
        plan = plan_recovery(record)
        self.assertEqual((plan.recipe_loop, plan.sequence_index, plan.step_index), (1, 5, 3))

    def test_completed_and_disabled_steps_advance_exactly_once(self):
        for phase in ("step_completed", "step_skipped"):
            record = sample_record()
            record.update(phase=phase, measurement_running=False)
            with self.subTest(phase=phase):
                self.assertEqual(plan_recovery(record).sequence_index, 6)

    def test_user_stop_pause_and_normal_finish_are_not_recovery_candidates(self):
        for changes in ({"status": "user_stopped"}, {"status": "completed"},
                        {"status": "prepared"}, {"stop_requested": True}, {"paused": True},
                        {"closed_by_user": True}):
            record = sample_record()
            record.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan_recovery(record)

    def test_live_edit_to_cv_disables_automatic_recovery(self):
        record = sample_record()
        record["recipe"]["steps"][8]["dropview_action"] = "run_cv"
        with self.assertRaises(ValueError):
            plan_recovery(record)

    def test_other_measurement_modes_and_unknown_phase_are_rejected(self):
        for changes in ({"measurement_mode": "continuous_pad"}, {"measurement_mode": "cv"},
                        {"phase": "unknown"}, {"schema_version": 2}):
            record = sample_record()
            record.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan_recovery(record)

    def test_invalid_cursor_is_rejected(self):
        for changes in ({"step_index": 7}, {"sequence_index": 0}, {"recipe_loop": 6},
                        {"recipe_loop": True}, {"sequence_index": 3.5}):
            record = sample_record()
            record.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan_recovery(record)

    def test_invalid_inner_loops_are_rejected_before_expansion(self):
        for loops in ([StepLoop(1, 11, 2)], [StepLoop(1, 3, 2), StepLoop(3, 5, 2)],
                      [StepLoop(1, 3, 0)]):
            record = sample_record()
            record["recipe"]["step_loops"] = [asdict(loop) for loop in loops]
            with self.subTest(loops=loops), self.assertRaises(ValueError):
                plan_recovery(record)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "recipe_recovery.json"
        self.journal = RecoveryJournal(str(self.path), sample_record())
        self.journal.update()

    def test_roundtrip_keeps_unicode_and_detaches_mutable_values(self):
        settings = {"valve_a": 3}
        self.journal.update(requested_settings=settings, note="Ölçüm reçetesi")
        settings["valve_a"] = 8
        saved = RecoveryJournal.read(str(self.path))
        self.assertEqual(saved["requested_settings"]["valve_a"], 3)
        self.assertEqual(saved["note"], "Ölçüm reçetesi")

    def test_failed_replace_preserves_previous_record_and_revision(self):
        before = self.path.read_bytes()
        with patch("bench_test.recipe.recovery.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.journal.update(phase="measurement_stop")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.journal.update(phase="measurement_start")
        self.assertEqual(RecoveryJournal.read(str(self.path))["revision"], 2)

    def test_invalid_json_value_does_not_truncate_record(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.journal.update(remaining_seconds=float("nan"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_updates_remain_complete_and_serialized(self):
        errors = []
        def update(index):
            try:
                self.journal.update(**{f"writer_{index}": index})
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=update, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        saved = RecoveryJournal.read(str(self.path))
        self.assertEqual(saved["revision"], 13)
        self.assertTrue(all(saved[f"writer_{index}"] == index for index in range(12)))

    def test_truncated_or_unsupported_record_is_not_silently_reset(self):
        self.path.write_text('{"schema_version":', encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            RecoveryJournal.read(str(self.path))
        self.path.write_text('{"schema_version": 99}', encoding="utf-8")
        with self.assertRaises(ValueError):
            RecoveryJournal.read(str(self.path))


class RunnerRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "recipe_recovery.json"
        self.journal = RecoveryJournal(str(self.path), sample_record())
        self.journal.update()
        self.recipe = Recipe("PAD", [RecipeStep(0, 0) for _ in range(3)], 2)
        self.runner = RecipeRunner(None, None, self.recipe, queue.Queue(), threading.Event(),
                                   recovery_journal=self.journal)
        logger = patch("bench_test.recipe.runner.debug_log")
        logger.start()
        self.addCleanup(logger.stop)

    def read(self):
        return RecoveryJournal.read(str(self.path))

    def test_normal_finish_records_completion_and_all_loop_positions(self):
        visited = []
        def step(*args):
            saved = self.read()
            visited.append((saved["recipe_loop"], saved["sequence_index"], saved["phase"]))
            return True
        self.runner._execute_step = step
        self.runner.run()
        self.assertEqual(visited, [(loop, step, "step_pending")
                                   for loop in (1, 2) for step in (1, 2, 3)])
        self.assertEqual(self.read()["status"], "completed")

    def test_hard_exit_preserves_running_measurement_cursor(self):
        count = [0]
        def step(*args):
            count[0] += 1
            if count[0] == 5:
                self.runner.measurement_running = True
                self.runner.active_measurement_kind = "discrete_pad"
                self.runner.checkpoint("waiting", remaining_seconds=99)
                raise SystemExit("simulated hard exit")
            return True
        self.runner._execute_step = step
        with self.assertRaises(SystemExit):
            self.runner.run()
        plan = plan_recovery(self.read())
        self.assertEqual((plan.recipe_loop, plan.sequence_index), (2, 3))

    def test_unexpected_failure_reports_error_and_preserves_operation_phase(self):
        def step(*args):
            self.runner.checkpoint("dropview_start")
            raise RuntimeError("simulated launch failure")
        self.runner._execute_step = step
        self.runner.run()
        saved = self.read()
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "dropview_start")
        self.assertEqual(plan_recovery(saved).sequence_index, 1)
        messages = list(self.runner.status_queue.queue)
        self.assertTrue(any(kind == "error" and "launch failure" in text for kind, text in messages))

    def test_user_stop_is_durable_before_worker_returns(self):
        self.runner.request_stop()
        saved = self.read()
        self.assertTrue(saved["stop_requested"])
        self.assertEqual(saved["status"], "user_stopped")

    def test_pause_and_resume_are_recorded(self):
        self.runner.pause()
        self.assertTrue(self.read()["paused"])
        self.runner.resume()
        self.assertFalse(self.read()["paused"])

    def test_live_recipe_edit_keeps_active_step_copy(self):
        self.runner._active_step = copy.copy(self.recipe.steps[0])
        self.recipe.steps[0].duration_minutes = 42
        self.runner.checkpoint()
        saved = self.read()
        self.assertEqual(saved["recipe"]["steps"][0]["duration_minutes"], 42)
        self.assertEqual(saved["active_step"]["duration_minutes"], 0)
        restored = recipe_from_snapshot(saved["recipe"])
        self.assertEqual(restored.steps[0].duration_minutes, 42)

    def test_recording_failure_warns_once_without_changing_hardware(self):
        pump = Mock()
        self.runner.pump_ctrl = pump
        with patch.object(self.journal, "update", side_effect=OSError("disk full")):
            self.runner.checkpoint()
            self.runner.checkpoint()
        messages = list(self.runner.status_queue.queue)
        self.assertEqual(sum(kind == "warning" for kind, _ in messages), 1)
        self.assertEqual(pump.mock_calls, [])

    def test_pending_control_phase_is_written_before_dropview_call(self):
        controller = Mock()
        phases = []
        controller.do_start_dropview.side_effect = lambda **kw: phases.append(self.read()["phase"]) or True
        self.runner.dropview_ctrl = controller
        step = RecipeStep(0, 0, dropview_action="start_dropview")
        self.runner._active_step = step
        self.assertTrue(self.runner._execute_step(step, 1, 1))
        self.assertEqual(phases, ["dropview_start"])

    def test_completed_measurement_wait_is_recorded_with_active_measurement(self):
        step = RecipeStep(0, 0.01, dropview_action="start_discrete_pad")
        self.runner.dropview_ctrl = Mock()
        self.runner.dropview_ctrl.do_start_measure.return_value = True
        self.runner.session_scr_path = "unused_test.scr"
        self.runner._active_step = step
        with patch.object(self.runner, "_check_dropview_connected", return_value=True), \
                patch("bench_test.recipe.runner.time.sleep"), \
                patch("bench_test.recipe.runner.time.time", side_effect=[0, 0, 0, 0, 1]):
            self.assertTrue(self.runner._execute_step(step, 1, 1))
        saved = self.read()
        self.assertEqual(saved["phase"], "waiting")
        self.assertTrue(saved["measurement_running"])
        self.assertEqual(saved["active_measurement_kind"], "discrete_pad")


class StartupRecordingTests(unittest.TestCase):
    def test_ui_start_registers_record_only_for_real_discrete_pad(self):
        from bench_test.measurement.session import MeasurementSession
        from bench_test.ui.tabs.recipe_tab import RecipeTab
        for mode, simulation, action, expected in (
            ("script_pad", False, "start_discrete_pad", True),
            ("script_pad", True, "start_discrete_pad", False),
            ("continuous_pad", False, "start_continuous_pad", False),
            ("cv", False, "run_cv", False),
            ("script_pad", False, "run_cv", False),
        ):
            with self.subTest(mode=mode, simulation=simulation, action=action), \
                    tempfile.TemporaryDirectory() as directory:
                session = MeasurementSession.create(directory, "Test Sensor")
                session.measurement_mode = mode
                tab = Mock()
                tab.recipe_steps = [RecipeStep(0, 1, dropview_action=action)]
                tab.step_loops = []
                tab._simulation_mode = simulation
                tab._current_recipe_path = ""
                tab._main_window = None
                tab.package_tab.get_session.return_value = session
                tab.package_tab.build_package.return_value = True
                tab.name_edit.text.return_value = "Test recipe"
                tab.loop_spin.value.return_value = 5
                tab.stop_event = threading.Event()
                tab.status_queue = queue.Queue()
                tab.ctrl_a.serial_port = SimpleNamespace(port="COM7", baudrate=9600)
                tab.ctrl_b.serial_port = SimpleNamespace(port="COM8", baudrate=19200)
                tab.ctrl_a.address = 0
                tab.ctrl_b.address = 1
                with patch("bench_test.ui.tabs.recipe_tab.RecipeRunner") as runner_class, \
                        patch("bench_test.ui.tabs.recipe_tab.get_value", return_value=None):
                    RecipeTab._start_recipe(tab)
                runner_class.return_value.start.assert_called_once()
                journal = runner_class.call_args.kwargs["recovery_journal"]
                self.assertEqual(journal is not None, expected)
                self.assertEqual("recipe_recovery" in session.files, expected)
                if expected:
                    saved = RecoveryJournal.read(journal.path)
                    self.assertEqual(saved["recipe"]["loop_count"], 5)
                    self.assertEqual(saved["connections"]["valve_b"]["port"], "COM8")
                    self.assertEqual(saved["status"], "prepared")


if __name__ == "__main__":
    unittest.main()
