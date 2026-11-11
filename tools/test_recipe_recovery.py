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
    EDITOR_CONTROL_UNVERIFIED, RecoveryJournal, expanded_step_indices, plan_recovery, recipe_from_snapshot,
)
from bench_test.recipe.runner import EDITOR_RESTORE_TIMEOUT, PAD_ACTIVITY_CHECK_SECONDS, RecipeRunner


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

    def test_invalid_device_values_and_control_duration_are_rejected(self):
        for changes in ({"port": 9}, {"valve_b_state": 3}, {"pump_rpm": float("nan")},
                        {"duration_minutes": -1}, {"enabled": "yes"}, {"dropview_action": "unknown"}):
            record = sample_record()
            record["active_step"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan_recovery(record)
        record = sample_record()
        record["active_step"]["dropview_action"] = "stop_discrete_pad"
        record["remaining_seconds"] = float("inf")
        with self.assertRaises(ValueError):
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
        dialogs = patch("bench_test.dropview.automator.dismiss_connection_error_dialogs")
        dialogs.start()
        self.addCleanup(dialogs.stop)
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

    def test_watchdog_interrupt_preserves_pump_and_measurement_cursor(self):
        def step(*args):
            self.runner.measurement_running = True
            self.runner.active_measurement_kind = "discrete_pad"
            self.runner.checkpoint("waiting", remaining_seconds=99)
            self.runner.request_recovery("connection lost")
            return False
        self.runner._execute_step = step
        self.runner._safe_stop_pump = Mock()
        self.runner.run()
        self.runner._safe_stop_pump.assert_called_once_with("RECIPE START")
        saved = self.read()
        self.assertEqual(saved["status"], "interrupted")
        self.assertFalse(saved["stop_requested"])
        self.assertEqual(plan_recovery(saved).sequence_index, 2)
        self.assertIn(("watchdog_interrupted", "connection lost"), list(self.runner.status_queue.queue))

    def test_gui_checkpoint_does_not_make_a_hung_worker_look_alive(self):
        original = self.runner.worker_heartbeat
        self.runner.checkpoint()
        self.assertEqual(self.runner.worker_heartbeat, original)

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
                patch.object(self.runner, "_check_discrete_measurement_progress", return_value=True), \
                patch("bench_test.recipe.runner.time.sleep"), \
                patch("bench_test.recipe.runner.time.time", side_effect=[0, 0, 0, 0, 1]):
            self.assertTrue(self.runner._execute_step(step, 1, 1))
        saved = self.read()
        self.assertEqual(saved["phase"], "waiting")
        self.assertTrue(saved["measurement_running"])
        self.assertEqual(saved["active_measurement_kind"], "discrete_pad")

    def test_missing_packaged_script_has_clear_error_before_measurement_start(self):
        self.runner.dropview_ctrl = Mock()
        self.runner.session_scr_path = ""
        step = RecipeStep(0, 1, dropview_action="start_discrete_pad")
        with patch.object(self.runner, "_check_dropview_connected", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "requires a script in the measurement package"):
                self.runner._execute_step(step, 1, 1)
        self.runner.dropview_ctrl.do_start_measure.assert_not_called()

    def test_external_measurement_stop_interrupts_before_next_recipe_step(self):
        self.runner.dropview_ctrl = Mock()
        self.runner.simulation_mode = True
        self.runner._safe_stop_pump = Mock()
        self.recipe.steps = [RecipeStep(0, 2), RecipeStep(0, 0)]
        clock = [1000.0]
        visited = []
        execute = self.runner._execute_step
        def step(item, number, *args):
            visited.append(number)
            self.runner.measurement_running = True
            self.runner.active_measurement_kind = "discrete_pad"
            return execute(item, number, *args)
        def sleep(seconds):
            clock[0] += 3
        self.runner._execute_step = step
        with patch("bench_test.recipe.runner.time.monotonic", side_effect=lambda: clock[0]), \
                patch("bench_test.recipe.runner.time.time", side_effect=lambda: clock[0]), \
                patch("bench_test.recipe.runner.time.sleep", side_effect=sleep), \
                patch("bench_test.dropview.automator.get_discrete_measurement_state", return_value={"state": "stopped"}):
            self.runner.run()
        self.assertEqual(visited, [1])
        self.assertEqual(self.runner.outcome, "interrupted")
        self.assertTrue(any(kind == "error" and "stopped unexpectedly" in text
                            for kind, text in list(self.runner.status_queue.queue)))
        self.runner._safe_stop_pump.assert_called_once_with("RECIPE START")

    def test_unknown_observation_breaks_stop_confirmation(self):
        self.runner.dropview_ctrl = Mock()
        self.runner.measurement_running = True
        self.runner.active_measurement_kind = "discrete_pad"
        observations = ["stopped", "stopped", "unknown", "stopped", "stopped", "running", "stopped"]
        now = 1000
        with patch("bench_test.recipe.runner.time.monotonic", side_effect=lambda: now), \
                patch("bench_test.dropview.automator.get_discrete_measurement_state",
                      side_effect=[{"state": value} for value in observations]):
            for _ in observations:
                self.assertTrue(self.runner._check_discrete_measurement_progress(force=True))
                now += 3
        self.assertFalse(self.runner._watchdog_interrupt.is_set())

    def test_watchdog_pump_hold_preserves_requested_speed(self):
        pump = Mock()
        pump.is_connected.return_value = True
        pump.stop_and_confirm.return_value = True
        self.runner.pump_ctrl = pump
        self.runner._requested_settings["pump_rpm"] = 12
        self.runner.request_recovery("dropview_not_responding")
        self.runner._finish_watchdog_interrupt()
        pump.stop_and_confirm.assert_called_once()
        pump.set_speed_rpm.assert_not_called()
        self.assertEqual(self.read()["requested_settings"]["pump_rpm"], 12)
        self.assertEqual(self.read()["pump_recovery_stop"], "verified")

    def test_real_measurement_stop_preserves_recovery_plan_and_requests_recovery(self):
        self.runner.dropview_ctrl = Mock()
        self.runner.measurement_running = True
        self.runner.active_measurement_kind = "discrete_pad"
        self.runner.current_loop_index = self.runner.current_sequence_index = self.runner.current_step_index = 1
        self.runner._active_step = self.recipe.steps[0]
        self.runner.checkpoint("waiting", status="running")
        with patch("bench_test.recipe.runner.time.monotonic", side_effect=[1000, 1060, 1063, 1066, 1066]), \
                patch("bench_test.dropview.automator.get_discrete_measurement_state", return_value={"state": "stopped"}):
            self.assertTrue(self.runner._check_discrete_measurement_progress())
            self.assertTrue(self.runner._check_discrete_measurement_progress())
            self.assertTrue(self.runner._check_discrete_measurement_progress())
            self.assertFalse(self.runner._check_discrete_measurement_progress())
        self.assertTrue(self.runner._watchdog_interrupt.is_set())
        self.assertEqual(plan_recovery(self.read()).sequence_index, 2)
        self.assertFalse(any(kind == "error" for kind, _ in list(self.runner.status_queue.queue)))

    def test_continuous_pad_is_not_checked_as_discrete(self):
        self.runner.dropview_ctrl = Mock()
        self.runner.measurement_running = True
        self.runner.active_measurement_kind = "continuous_pad"
        with patch("bench_test.dropview.automator.get_discrete_measurement_state") as probe:
            self.assertTrue(self.runner._check_discrete_measurement_progress())
            probe.assert_not_called()


class DiscreteEditorRecoveryTests(unittest.TestCase):
    def setUp(self):
        dialogs = patch("bench_test.dropview.automator.dismiss_connection_error_dialogs")
        dialogs.start()
        self.addCleanup(dialogs.stop)
        self.dv = Mock()
        self.activity = self.dv.observe_discrete_pad_activity
        self.activity.return_value = "unknown"
        self.runner = RecipeRunner(None, None, Recipe("PAD", [RecipeStep(0, 0)], 1),
                                   queue.Queue(), threading.Event(), dropview_ctrl=self.dv,
                                   session_measurements_dir="existing-package")
        self.runner.measurement_running = True
        self.runner.active_measurement_kind = "discrete_pad"
        self.runner.measurement_epoch = 900
        self.runner._last_measurement_running = True
        self.runner._measurement_owner = {"pid": 42, "created": 100}
        self.runner._log = Mock()
        self.clock = 1000
        self.runner._measurement_inactive_since = self.clock - PAD_ACTIVITY_CHECK_SECONDS
        patches = (
            ("bench_test.recipe.runner.time.monotonic", {"side_effect": lambda: self.clock}),
            ("bench_test.dropview.automator.get_discrete_measurement_state", {
                "return_value": {"state": "unknown", "editor_missing": True}}),
            ("bench_test.dropview.automator.reopen_discrete_editor", {}),
            ("bench_test.dropview.automator.verify_discrete_session_connection", {}),
        )
        for attribute, (target, options) in zip(("_monotonic", "observe", "reopen", "verify"), patches):
            patcher = patch(target, **options)
            setattr(self, attribute, patcher.start())
            self.addCleanup(patcher.stop)

    def test_normal_dropview_cycle_never_probes_editor(self):
        self.runner._measurement_inactive_since = None
        for elapsed in range(0, 126, 3):
            self.clock = 1000 + elapsed
            self.activity.return_value = "running" if elapsed % 60 < 15 else "stopped"
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.observe.assert_not_called()
        self.reopen.assert_not_called()

    def test_stopped_status_checks_editor_at_60_seconds_only(self):
        self.runner._measurement_inactive_since = None
        self.activity.return_value = "stopped"
        self.observe.return_value = {"state": "stopped"}
        for elapsed in (0, 45, 57):
            self.clock = 1000 + elapsed
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.observe.assert_not_called()
        self.clock = 1060
        self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.observe.assert_called_once_with(refocus_if_covered=True, expected_owner=self.runner._measurement_owner)
        self.clock = 1063
        self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.assertFalse(self.observe.call_args.kwargs["refocus_if_covered"])
        self.clock = 1066
        self.assertFalse(self.runner._check_discrete_measurement_progress())

    def test_recent_csv_defers_editor_check_while_dropview_shows_stopped(self):
        self.activity.return_value = "stopped"
        written_ns = 1_000_000_000_000
        self.runner._measurement_csv_probe = Mock(content_last_progress_ns=written_ns)
        with patch("bench_test.recipe.runner.time.time_ns", return_value=written_ns + 10_000_000_000):
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.observe.assert_not_called()

    def test_disconnected_status_leaves_detection_to_link_watchdog(self):
        self.activity.return_value = "disconnected"
        self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.observe.assert_not_called()

    def test_unsaved_warning_acknowledgement_waits_for_fresh_measurement_status(self):
        with patch("bench_test.dropview.automator.acknowledge_discrete_unsaved_warning", return_value=True) as ack:
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        ack.assert_called_once_with(self.runner._measurement_owner)
        self.activity.assert_not_called()
        self.observe.assert_not_called()
        self.assertFalse(self.runner._watchdog_interrupt.is_set())

    def test_new_run_clears_editor_suspicion_without_reopening(self):
        self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.assertIsNotNone(self.runner._editor_loss_at)
        self.clock += 3
        self.activity.return_value = "running"
        self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.assertIsNone(self.runner._editor_loss_at)
        self.observe.assert_called_once()
        self.reopen.assert_not_called()

    def test_missing_editor_interrupts_only_after_bounded_restore_attempts(self):
        for elapsed in (0, 3, 6, 9, 12, 15):
            self.clock = 1000 + elapsed
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.assertEqual(self.reopen.call_count, 3)
        self.assertTrue(self.runner.measurement_running)
        self.clock = 1000 + EDITOR_RESTORE_TIMEOUT
        self.assertFalse(self.runner._check_discrete_measurement_progress())
        self.assertEqual(self.runner._watchdog_measurement_state, EDITOR_CONTROL_UNVERIFIED)
        self.runner._finish_watchdog_interrupt()
        self.assertEqual(self.runner.outcome, "operator_required")
        self.assertEqual(len(self.dv.mock_calls), self.activity.call_count)

    def test_control_recovery_waits_for_matching_csv_when_measurement_must_continue(self):
        self.runner.csv_expectation = {"first_seconds": 10, "interval_seconds": 30, "csv_stem": "pad"}
        csv = Mock(content_last_progress=None)
        self.dv.do_stop_measure.return_value = True

        def wait(_seconds):
            self.clock += 3
            self.dv.do_stop_measure.assert_not_called()
            if self.clock == 1009:
                self.observe.return_value = {"state": "running"}
            if self.clock == 1012:
                csv.content_last_progress = self.clock
            return False

        with patch("bench_test.recipe.runner.CsvProgressProbe", return_value=csv), \
                patch.object(self.runner.stop_event, "wait", side_effect=wait):
            self.assertTrue(self.runner._wait_for_discrete_dropview_control())
        self.reopen.assert_called_once_with(self.runner._measurement_owner, log_fn=self.runner._log)
        self.verify.assert_called_once_with(self.runner._measurement_owner)
        self.assertEqual(csv.observe.call_args.args[:2], ("existing-package", 900))
        self.assertIsInstance(csv.observe.call_args.kwargs["started_ns"], int)
        self.dv.do_start_measure.assert_not_called()
        self.dv.do_stop_measure.assert_not_called()
        self.assertIsNone(self.runner._editor_loss_at)

    def test_planned_discrete_stop_uses_editor_without_waiting_for_csv(self):
        self.runner._editor_loss_at = self.clock
        self.runner._watchdog_measurement_state = EDITOR_CONTROL_UNVERIFIED
        self.runner._editor_csv_probe = Mock(content_last_progress=None)
        self.dv.do_stop_measure.return_value = True
        with patch.object(self.runner.stop_event, "wait") as wait:
            self.assertTrue(self.runner._execute_step(RecipeStep(0, 0, dropview_action="stop_discrete_pad"), 1, 1))
        self.dv.do_stop_measure.assert_called_once_with(
            log_fn=self.runner._log, require_editor=True,
            expected_owner=self.runner._measurement_owner, reopen_if_needed=True)
        self.observe.assert_not_called()
        wait.assert_not_called()
        self.assertFalse(self.runner.measurement_running)
        self.assertIsNone(self.runner._watchdog_measurement_state)

    def test_retry_stop_keeps_original_owner_until_stop_is_verified(self):
        from bench_test.ui.tabs.recipe_tab import RecipeTab

        self.dv.do_stop_measure.side_effect = [False, False, True]
        tab = SimpleNamespace(
            start_btn=Mock(), pause_btn=Mock(), stop_btn=Mock(), status_lbl=Mock(),
            status_queue=self.runner.status_queue,
        )
        self.assertFalse(self.runner.stop_measurement_for_recipe_stop())
        self.assertEqual(tab.status_queue.get_nowait()[0], "stop_failed")
        self.dv.discrete_measurement_owner = {"pid": 43, "created": 200}
        with patch("bench_test.ui.tabs.recipe_tab.threading.Thread") as worker:
            for outcome in ("stop_failed", "stopped"):
                RecipeTab._retry_stop_measurement(tab, self.runner)
                worker.call_args.kwargs["target"]()
                self.assertEqual(tab.status_queue.get_nowait()[0], outcome)
                self.assertEqual(self.runner.measurement_running, outcome == "stop_failed")
                self.assertTrue(tab.status_queue.empty())

        self.assertEqual(self.dv.do_stop_measure.call_count, 3)
        for attempt in self.dv.do_stop_measure.call_args_list:
            self.assertEqual(attempt.kwargs, {
                "log_fn": self.runner._log, "require_editor": True,
                "expected_owner": {"pid": 42, "created": 100}, "reopen_if_needed": True,
            })

    def test_idle_reopened_editor_does_not_prove_old_measurement_stopped(self):
        self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.runner._restored_editor_hwnd = 42
        self.observe.return_value = {"state": "stopped"}
        for elapsed in (3, 6, 9):
            self.clock = 1000 + elapsed
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.clock = 1000 + EDITOR_RESTORE_TIMEOUT
        self.assertFalse(self.runner._check_discrete_measurement_progress())
        self.assertEqual(self.runner._watchdog_measurement_state, EDITOR_CONTROL_UNVERIFIED)
        self.assertTrue(self.runner.measurement_running)

    def test_connected_dropview_and_three_idle_reads_on_reopened_editor_confirm_stop(self):
        self.activity.return_value = "stopped"
        self.reopen.return_value = 42
        for elapsed in (0, 3, 6):
            self.clock = 1000 + elapsed
            self.assertTrue(self.runner._check_discrete_measurement_progress())

        self.observe.return_value = {"state": "stopped"}
        for elapsed in (9, 12):
            self.clock = 1000 + elapsed
            self.assertTrue(self.runner._check_discrete_measurement_progress())
        self.verify.assert_not_called()
        self.clock = 1015
        self.assertFalse(self.runner._check_discrete_measurement_progress())
        self.verify.assert_called_once_with(self.runner._measurement_owner)
        self.assertEqual(self.observe.call_args.kwargs["hwnd"], 42)
        self.runner._finish_watchdog_interrupt()
        self.assertEqual(self.runner.outcome, "interrupted")
        self.assertEqual(self.runner._watchdog_measurement_state, "stopped")
        self.assertTrue(self.runner.measurement_running)

    def test_unverified_editor_record_blocks_automatic_package_recovery(self):
        record = sample_record()
        record["observed_measurement_state"] = EDITOR_CONTROL_UNVERIFIED
        with self.assertRaisesRegex(ValueError, "Verify the previous Discrete PAD measurement"):
            plan_recovery(record)


class StartupRecordingTests(unittest.TestCase):
    def test_ui_start_registers_record_for_discrete_pad_in_both_modes(self):
        from PyQt6.QtWidgets import QMessageBox
        from bench_test.measurement.session import MeasurementSession
        from bench_test.ui.tabs.recipe_tab import RecipeTab
        for mode, simulation, action, expected in (
            ("script_pad", False, "start_discrete_pad", True),
            ("script_pad", True, "start_discrete_pad", True),
            ("continuous_pad", False, "start_continuous_pad", False),
            ("continuous_pad", True, "start_continuous_pad", False),
            ("cv", False, "run_cv", False),
            ("script_pad", False, "run_cv", False),
        ):
            with self.subTest(mode=mode, simulation=simulation, action=action), \
                    tempfile.TemporaryDirectory() as directory:
                session = MeasurementSession.create(directory, "Test Sensor")
                session.measurement_mode = mode
                tab = Mock()
                tab.recipe_steps = [RecipeStep(0, 1, dropview_action=action)]
                tab.recipe_runner = None
                tab.step_loops = []
                tab._simulation_mode = simulation
                tab.ctrl_a.is_connected.return_value = not simulation
                tab.ctrl_b.is_connected.return_value = not simulation
                tab.pump_ctrl.is_connected.return_value = True
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
                        patch("bench_test.ui.tabs.recipe_tab.QMessageBox.warning",
                              return_value=QMessageBox.StandardButton.Yes), \
                        patch("bench_test.ui.tabs.recipe_tab.get_value", return_value=None):
                    RecipeTab._start_recipe(tab)
                runner_class.return_value.start.assert_called_once()
                journal = runner_class.call_args.kwargs["recovery_journal"]
                self.assertEqual(runner_class.call_args.kwargs["simulation_mode"], simulation)
                self.assertEqual(runner_class.call_args.kwargs["simulation_recovery"], simulation and expected)
                self.assertIs(runner_class.call_args.args[5], tab.dv_ctrl)
                self.assertEqual(journal is not None, expected)
                self.assertEqual("recipe_recovery" in session.files, expected)
                if expected:
                    saved = RecoveryJournal.read(journal.path)
                    self.assertEqual(saved["simulation_recovery"], simulation)
                    self.assertEqual(saved["recipe"]["loop_count"], 5)
                    if simulation:
                        self.assertNotIn("valve_b", saved["connections"])
                        self.assertNotIn("pump", saved["connections"])
                    else:
                        self.assertEqual(saved["connections"]["valve_b"]["port"], "COM8")
                    self.assertEqual(saved["status"], "prepared")


if __name__ == "__main__":
    unittest.main()
