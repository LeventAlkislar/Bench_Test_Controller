"""Package/continuation tests using fake devices only."""
import copy
import json
import os
import queue
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.dropview.controller import DropViewController
from bench_test.dropview.script_generator import generate_dropview_script
from bench_test.measurement.session import MeasurementSession
from bench_test.recipe.models import Recipe, RecipeStep
from bench_test.recipe.recovery import RecoveryJournal, plan_recovery
from bench_test.recipe.recovery_executor import (
    RecipeExecutionLock, create_recovery_package, reconnect_recovery_pump, verify_recovery_valves,
)
from bench_test.recipe.runner import RecipeRunner


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = MeasurementSession.create(self.temp.name, "SİL 1")
        self.source.experiment_params = {"temperature": 25}
        method = Path(self.source.params_dir) / "edited.tp"
        method.write_text("<root><technic><sample>edited sensor</sample></technic></root>", encoding="utf-8")
        script = Path(self.source.params_dir) / "measurement.scr"
        generate_dropview_script(str(method), str(Path(self.source.measurements_dir) / "old.csv"),
                                 1440, 47500, str(script))
        self.source.register_file("tp", str(method))
        self.source.register_file("method_discrete_pad", str(method))
        self.source.register_file("script", str(script))
        self.source.save()
        self.recipe = Recipe("Saved recipe", [
            RecipeStep(0, 0, dropview_action="start_dropview"),
            RecipeStep(0, 90, dropview_action="start_discrete_pad"),
            RecipeStep(0, 90),
            RecipeStep(0, 0, dropview_action="stop_discrete_pad"),
            RecipeStep(0, 0, dropview_action="exit_dropview"),
        ], 2)
        connections = {
            "valve_a": {"port": "COM7", "baudrate": 9600, "address": 0},
            "valve_b": {"port": "COM8", "baudrate": 19200, "address": 1},
            "pump": {"pump_type": "flexicon", "pump_com": "COM9"},
        }
        self.original = RecoveryJournal.create(self.source, self.recipe, connections)
        self.original.update(status="running", phase="waiting", recipe_loop=1,
                             sequence_index=2, step_index=2,
                             active_step=asdict(self.recipe.steps[1]), measurement_running=True,
                             active_measurement_kind="discrete_pad", remaining_seconds=120,
                             requested_settings={"valve_a": 3, "valve_b": 2, "pump_rpm": None})
        self.a, self.b = Mock(), Mock()
        for controller, port, baud, address in ((self.a, "COM7", 9600, 0), (self.b, "COM8", 19200, 1)):
            controller.is_connected.return_value = True
            controller.serial_port = SimpleNamespace(port=port, baudrate=baud)
            controller.address = address
        self.a.get_current_port.return_value = {"success": True, "status": 0, "param1": 3}
        self.b.get_current_state.return_value = {"success": True, "status": 0, "param1": 2}
        self.dv = Mock()
        self.dv.do_reconcile_discrete_recovery.return_value = True
        self.dv.do_start_dropview.return_value = True
        self.dv.do_start_measure.return_value = True
        self.pump = Mock()
        self.pump.is_connected.return_value = True
        self.pump.active_type = "flexicon"
        self.pump.port = "COM9"
        self.pump.stop.return_value = []
        self.pump.set_speed_rpm.return_value = []
        logger = patch("bench_test.recipe.runner.debug_log")
        logger.start()
        self.addCleanup(logger.stop)

    def record(self):
        return RecoveryJournal.read(self.original.path)

    def make_runner(self):
        record = self.record()
        session, recipe, journal, _ = create_recovery_package(record, self.original.path)
        runner = RecipeRunner(self.a, self.b, recipe, queue.Queue(), threading.Event(), self.dv,
                              pump_ctrl=self.pump, session_scr_path=session.get_file_path("script"),
                              session_measurements_dir=session.measurements_dir,
                              recovery_journal=journal, recovery_record=record,
                              recovery_source_path=self.original.path)
        return runner, journal, session

    def test_new_package_preserves_source_and_repoints_method_and_all_exports(self):
        source_method = Path(self.source.get_file_path("tp")).read_bytes()
        source_script = Path(self.source.get_file_path("script")).read_bytes()
        source_record = Path(self.original.path).read_bytes()
        session, recipe, journal, plan = create_recovery_package(self.record(), self.original.path)
        self.assertNotEqual(session.session_dir, self.source.session_dir)
        self.assertEqual(Path(session.get_file_path("tp")).read_bytes(), source_method)
        tree = ET.parse(session.get_file_path("script"))
        self.assertEqual(tree.find("actions/action[@type='LOADMETHOD']/file").text, session.get_file_path("tp"))
        self.assertTrue(all(element.text.startswith(session.measurements_dir)
                            for element in tree.findall("actions/action[@type='EXPORTCURVES']/file")))
        self.assertEqual(tree.find("actions/action[@type='REPEAT']/times").text, "1440")
        self.assertEqual(tree.find("actions/action[@type='WAIT']").get("timeMS"), "47500")
        self.assertEqual(Path(self.source.get_file_path("script")).read_bytes(), source_script)
        self.assertEqual(Path(self.original.path).read_bytes(), source_record)
        self.assertEqual(session.experiment_params, {"temperature": 25})
        self.assertEqual(recipe.loop_count, 2)
        self.assertEqual(plan.step_index, 3)
        self.assertTrue(session.get_file_path("recovery_origin"))
        self.assertTrue(Path(journal.path).is_file())

    def test_new_packages_are_unique_even_when_created_in_same_second(self):
        first = create_recovery_package(self.record(), self.original.path)[0]
        second = create_recovery_package(self.record(), self.original.path)[0]
        self.assertNotEqual(first.session_dir, second.session_dir)

    def test_missing_input_or_unrelated_method_fails_before_new_package(self):
        before = set(Path(self.source.session_dir).parent.iterdir())
        tree = ET.parse(self.source.get_file_path("script"))
        tree.find("actions/action[@type='LOADMETHOD']/file").text = "unrelated.tp"
        tree.write(self.source.get_file_path("script"), encoding="UTF-8")
        with self.assertRaises(ValueError):
            create_recovery_package(self.record(), self.original.path)
        self.assertEqual(set(Path(self.source.session_dir).parent.iterdir()), before)

    def test_valve_readback_does_not_move_or_reset_devices(self):
        positions = verify_recovery_valves(self.record(), self.a, self.b)
        self.assertEqual(positions, {"valve_a": 3, "valve_b": 2})
        self.a.switch_port.assert_not_called()
        self.b.switch_state.assert_not_called()
        self.a.reset.assert_not_called()
        self.b.reset.assert_not_called()

    def test_position_mismatch_blocks_except_for_unfinished_movement(self):
        self.a.get_current_port.return_value["param1"] = 4
        with self.assertRaises(RuntimeError):
            verify_recovery_valves(self.record(), self.a, self.b)
        record = self.record()
        record["phase"] = "valve_a"
        self.assertEqual(verify_recovery_valves(record, self.a, self.b)["valve_a"], 4)

    def test_busy_valve_or_wrong_connected_device_blocks(self):
        self.a.get_current_port.return_value["status"] = 4
        with self.assertRaises(RuntimeError):
            verify_recovery_valves(self.record(), self.a, self.b)
        self.a.get_current_port.return_value["status"] = 0
        self.a.serial_port.port = "COM99"
        with self.assertRaises(RuntimeError):
            verify_recovery_valves(self.record(), self.a, self.b)

    def test_valve_reconnect_uses_saved_settings_without_reset(self):
        self.a.is_connected.return_value = False
        verify_recovery_valves(self.record(), self.a, self.b)
        self.a.connect.assert_called_once_with("COM7", 9600)
        self.a.reset.assert_not_called()

    def test_arduino_is_not_reopened_and_flexicon_connect_does_not_change_output(self):
        record = self.record()
        record["requested_settings"]["pump_rpm"] = 10
        record["connections"]["pump"]["pump_type"] = "arduino"
        self.pump.is_connected.return_value = False
        with self.assertRaises(RuntimeError):
            reconnect_recovery_pump(record, self.recipe, self.pump)
        self.pump.connect.assert_not_called()
        record["connections"]["pump"]["pump_type"] = "flexicon"
        self.pump.connect.return_value = True
        reconnect_recovery_pump(record, self.recipe, self.pump)
        self.pump.connect.assert_called_once_with("COM9")
        self.pump.stop.assert_not_called()
        self.pump.set_speed_rpm.assert_not_called()

    def test_resume_skips_completed_steps_and_keeps_outer_loop_count(self):
        runner, journal, session = self.make_runner()
        visited = []
        def execute(step, sequence, total, loop_info, *options):
            if not visited:
                self.pump.stop.assert_not_called()
                self.pump.set_speed_rpm.assert_not_called()
            visited.append((runner.current_loop_index, sequence))
            return True
        runner._execute_step = execute
        runner.run()
        self.assertEqual(visited, [(1, 3), (1, 4), (1, 5)] + [(2, n) for n in range(1, 6)])
        self.assertEqual(self.record()["status"], "superseded")
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "completed")
        self.dv.do_start_measure.assert_called_once_with(session.get_file_path("script"), log_fn=runner._log)
        self.a.switch_port.assert_not_called()
        self.b.switch_state.assert_not_called()

    def test_stale_source_record_blocks_before_any_device_command(self):
        runner, journal, _ = self.make_runner()
        self.original.update(note="changed after package was prepared")
        runner.run()
        self.assertEqual(self.dv.mock_calls, [])
        self.assertEqual(self.a.mock_calls, [])
        self.pump.stop.assert_not_called()
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "recovery_blocked")

    def test_unverified_old_measurement_blocks_without_stopping_pump(self):
        runner, journal, _ = self.make_runner()
        self.dv.do_reconcile_discrete_recovery.return_value = False
        runner._execute_step = Mock()
        runner.run()
        runner._execute_step.assert_not_called()
        self.pump.stop.assert_not_called()
        self.dv.do_start_measure.assert_not_called()
        self.assertEqual(self.record()["status"], "interrupted")
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "recovery_blocked")

    def test_stop_request_during_reconciliation_does_not_start_new_measurement(self):
        runner, _, _ = self.make_runner()
        def stop_during_reconciliation(*args, **kwargs):
            runner.request_stop()
            return True
        self.dv.do_reconcile_discrete_recovery.side_effect = stop_during_reconciliation
        runner.run()
        self.dv.do_start_dropview.assert_not_called()
        self.dv.do_start_measure.assert_not_called()
        self.assertEqual(self.record()["status"], "user_stopped")

    def test_retry_after_launch_failure_remembers_verified_stop_and_original_cursor(self):
        runner, _, _ = self.make_runner()
        self.dv.do_start_dropview.return_value = False
        runner.run()
        self.assertEqual(self.record()["status"], "interrupted")
        self.assertTrue(self.record()["recovery_previous_stopped"])
        self.assertEqual(plan_recovery(self.record()).sequence_index, 3)
        self.dv.do_start_dropview.return_value = True
        retry, _, _ = self.make_runner()
        retry._execute_step = Mock(return_value=True)
        retry.run()
        self.assertEqual(self.dv.do_reconcile_discrete_recovery.call_args_list[0].args, (True,))
        self.assertEqual(self.dv.do_reconcile_discrete_recovery.call_args_list[1].args, (False,))
        self.assertEqual(self.record()["status"], "superseded")
        self.assertEqual(self.record()["recovery_attempts"], 2)

    def test_unwritable_destination_record_blocks_before_device_queries(self):
        runner, journal, _ = self.make_runner()
        with patch.object(journal, "update", side_effect=OSError("disk full")):
            runner.run()
        self.assertEqual(self.a.mock_calls, [])
        self.assertEqual(self.dv.mock_calls, [])
        self.pump.stop.assert_not_called()
        self.assertEqual(self.record()["status"], "interrupted")

    def test_wait_before_stop_preserves_remaining_time_and_does_not_repeat_settings(self):
        step = RecipeStep(3, 90, valve_b_state=2, pump_rpm=10, dropview_action="stop_discrete_pad")
        self.recipe.steps[3] = step
        self.original.update(recipe=asdict(self.recipe), sequence_index=4, step_index=4,
                             phase="waiting", active_step=asdict(step), remaining_seconds=120)
        runner, _, _ = self.make_runner()
        seen = []
        def execute(current, sequence, total, loop_info, *options):
            if not seen:
                self.assertEqual(sequence, 4)
                self.assertEqual(options[0]["remaining_seconds"], 120)
                self.assertTrue(options[0]["skip_valve_a"])
                self.assertTrue(options[0]["skip_valve_b"])
                self.assertTrue(options[0]["skip_pump"])
                self.assertFalse(options[0]["skip_wait"])
            seen.append(sequence)
            return True
        runner._execute_step = execute
        runner.run()
        self.assertTrue(seen)
        self.dv.do_start_measure.assert_called_once()

    def test_final_measurement_interruption_does_not_start_another_loop(self):
        self.recipe.steps = self.recipe.steps[:3]
        self.original.update(recipe=asdict(self.recipe), recipe_loop=2, sequence_index=3,
                             step_index=3, active_step=asdict(self.recipe.steps[2]))
        runner, journal, _ = self.make_runner()
        runner._execute_step = Mock()
        runner.run()
        runner._execute_step.assert_not_called()
        self.dv.do_start_measure.assert_not_called()
        self.pump.stop.assert_not_called()
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "completed_after_interruption")

    def test_stop_phase_does_not_repeat_valves_pump_or_wait(self):
        step = RecipeStep(3, 90, valve_b_state=2, pump_rpm=10, dropview_action="stop_discrete_pad")
        self.recipe.steps[3] = step
        self.original.update(recipe=asdict(self.recipe), sequence_index=4, step_index=4,
                             phase="measurement_stop", active_step=asdict(step))
        runner, _, _ = self.make_runner()
        runner._recovery_observed_valves = {"valve_a": 3, "valve_b": 2}
        runner._apply_pump_rpm = Mock()
        with patch("bench_test.recipe.runner.time.sleep") as sleep:
            self.assertTrue(runner._execute_step(step, 4, 5, recovery_options=runner._recovery_options(step)))
        self.a.switch_port.assert_not_called()
        self.b.switch_state.assert_not_called()
        runner._apply_pump_rpm.assert_not_called()
        sleep.assert_not_called()
        self.dv.do_stop_measure.assert_called_once()

    def test_second_recipe_lock_is_rejected_then_released(self):
        path = str(Path(self.temp.name) / "execution.lock")
        with RecipeExecutionLock(path):
            with self.assertRaises(RuntimeError):
                with RecipeExecutionLock(path):
                    self.fail("Second owner acquired lock")
        with RecipeExecutionLock(path):
            pass

    def test_recovery_entry_attaches_new_package_and_locks_ui_without_normal_start(self):
        from PyQt6.QtWidgets import QApplication
        from bench_test.ui.tabs.package_panel import PackagePanel
        from bench_test.ui.tabs.recipe_tab import RecipeTab
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication(["recovery-tests"])
        with patch.object(RecipeTab, "_restore_last_recipe"), \
                patch.object(PackagePanel, "_restore_last_part_number"), \
                patch("bench_test.ui.tabs.package_panel.remember_value"), \
                patch("bench_test.ui.tabs.package_panel.remember"), \
                patch("bench_test.measurement.state_machine.debug_log"):
            panel = PackagePanel()
            tab = RecipeTab(self.a, self.b, self.dv, self.pump)
            setup = Mock()
            setup.sm = panel.sm
            setup.package_panel = panel
            setup.get_session.side_effect = panel.get_session
            tab.package_tab = setup
            try:
                with patch.object(RecipeRunner, "start") as start, \
                        patch.object(tab, "_start_recipe") as normal_start:
                    session = tab.recover_from_record(self.original.path)
                start.assert_called_once()
                normal_start.assert_not_called()
                self.assertIs(panel.get_session(), session)
                self.assertIs(tab.recipe_steps, tab.recipe_runner.recipe.steps)
                self.assertFalse(tab.start_btn.isEnabled())
                self.assertFalse(tab.recover_btn.isEnabled())
                self.assertTrue(tab.stop_btn.isEnabled())
                self.assertEqual(panel.sm.state, "running")
                self.assertTrue(tab._recovery_run)
                self.assertEqual(self.record()["status"], "running")
                self.assertEqual(self.dv.mock_calls, [])
                self.pump.stop.assert_not_called()
            finally:
                tab._poll_timer.stop()
                panel.sm.reset()
                panel._close_log_writer()
                tab.close()
                panel.close()
                app.processEvents()


class DiscreteReconciliationTests(unittest.TestCase):
    def test_absent_editor_is_not_treated_as_stop_confirmation(self):
        controller = Mock()
        with patch("bench_test.dropview.controller.automator.window_exists", return_value=False):
            self.assertFalse(DropViewController.do_reconcile_discrete_recovery(controller, True))
            self.assertTrue(DropViewController.do_reconcile_discrete_recovery(controller, False))
        controller.do_stop_measure.assert_not_called()

    def test_present_editor_requires_successful_stop_and_verified_close(self):
        controller = Mock()
        controller.do_stop_measure.return_value = True
        with patch("bench_test.dropview.controller.automator.window_exists", side_effect=[True, False]):
            self.assertTrue(DropViewController.do_reconcile_discrete_recovery(controller, True))
        with patch("bench_test.dropview.controller.automator.window_exists", return_value=True):
            self.assertFalse(DropViewController.do_reconcile_discrete_recovery(controller, True))


if __name__ == "__main__":
    unittest.main()
