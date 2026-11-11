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
from bench_test.pump.flexicon_pump import FlexiconPumpController
from bench_test.recipe.models import Recipe, RecipeStep
from bench_test.recipe.recovery import RecoveryJournal, plan_recovery
from bench_test.recipe.recovery_executor import (
    RecipeExecutionLock, create_recovery_package, reconnect_recovery_pump, verify_recovery_valves,
)
from bench_test.recipe.runner import RecipeRunner
from bench_test.watchdog.state import read_json, recovery_source
from bench_test.watchdog.supervisor import Supervisor


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        dialogs = patch("bench_test.dropview.automator.dismiss_connection_error_dialogs")
        dialogs.start()
        self.addCleanup(dialogs.stop)
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
        csv_gate = patch.object(RecipeRunner, "_wait_for_recovery_csv", return_value=True)
        self.csv_gate = csv_gate.start()
        self.addCleanup(csv_gate.stop)
        self.dv = Mock()
        self.dv.do_reconcile_discrete_recovery.return_value = True
        self.dv.do_start_dropview.return_value = True
        self.dv.do_start_measure.return_value = True
        self.pump = Mock()
        self.pump.is_connected.return_value = True
        self.pump.active_type = "flexicon"
        self.pump.port = "COM9"
        self.pump.stop.return_value = []
        self.pump.stop_and_confirm.return_value = True
        self.pump.set_speed_rpm.return_value = []
        logger = patch("bench_test.recipe.runner.debug_log")
        logger.start()
        self.addCleanup(logger.stop)
        measurement = patch("bench_test.dropview.automator.get_discrete_measurement_state",
                            return_value={"state": "running"})
        measurement.start()
        self.addCleanup(measurement.stop)

    def record(self):
        return RecoveryJournal.read(self.original.path)

    def make_runner(self, source_path=None):
        source_path = source_path or self.original.path
        record = RecoveryJournal.read(source_path)
        session, recipe, journal, _ = create_recovery_package(record, source_path)
        runner = RecipeRunner(self.a, self.b, recipe, queue.Queue(), threading.Event(), self.dv,
                              pump_ctrl=self.pump, session_scr_path=session.get_file_path("script"),
                              session_measurements_dir=session.measurements_dir,
                              recovery_journal=journal, recovery_record=record,
                              recovery_source_path=source_path,
                              simulation_recovery=record.get("simulation_recovery", False))
        return runner, journal, session

    def test_simulation_recovery_restores_cursor_without_touching_pump_or_valves(self):
        self.original.update(simulation_recovery=True, requested_settings={
            "valve_a": 3, "valve_b": 2, "pump_rpm": 50,
        })
        runner, journal, session = self.make_runner()
        runner._prepare_recovery()

        self.assertTrue(runner.simulation_mode)
        self.assertTrue(RecoveryJournal.read(journal.path)["simulation_recovery"])
        self.assertEqual((runner.current_loop_index, runner.current_step_index), (1, 3))
        self.assertTrue(runner._resume_measurement)
        self.assertTrue(runner._restore_pump_at_step)
        self.dv.do_reconcile_discrete_recovery.assert_called_once()
        self.dv.do_start_dropview.assert_called_once()
        self.assertEqual(self.a.mock_calls + self.b.mock_calls + self.pump.mock_calls, [])
        self.assertNotEqual(session.session_dir, self.source.session_dir)
        runner._resume_measurement = False
        self.assertTrue(runner._execute_step(RecipeStep(0, 0), 3, len(runner.recipe.steps)))
        self.assertEqual(self.pump.mock_calls, [])

    def test_simulation_recovery_checks_dropview_before_resuming_idle_wait(self):
        self.original.update(simulation_recovery=True, phase="waiting", sequence_index=3, step_index=3,
                             active_step=asdict(self.recipe.steps[2]), measurement_running=False,
                             active_measurement_kind="", remaining_seconds=20)
        runner, _, _ = self.make_runner()
        runner._prepare_recovery()

        self.assertEqual(runner.current_step_index, 3)
        self.assertFalse(runner._resume_measurement)
        self.dv.do_reconcile_discrete_recovery.assert_called_once_with(False, log_fn=runner._log)
        self.dv.do_start_dropview.assert_called_once_with(
            log_fn=runner._log, allow_cleanup=False, cancelled=runner._interrupted)

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
                self.assertTrue(runner._resume_measurement)
            visited.append((runner.current_loop_index, sequence))
            return True
        runner._execute_step = execute
        runner.run()
        self.assertEqual(visited, [(1, 3), (1, 4), (1, 5)] + [(2, n) for n in range(1, 6)])
        self.assertEqual(self.record()["status"], "superseded")
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "completed")
        self.dv.do_start_measure.assert_not_called()  # Step execution was replaced above.
        self.a.switch_port.assert_not_called()
        self.b.switch_state.assert_not_called()

    def test_verified_external_stop_does_not_restart_when_the_next_step_is_stop(self):
        self.recipe.steps[2] = RecipeStep(0, 0, dropview_action="stop_discrete_pad")
        self.original.update(recipe=asdict(self.recipe), status="interrupted", observed_measurement_state="stopped")
        runner, _, _ = self.make_runner()
        runner._prepare_recovery()

        self.assertEqual(runner.current_step_index, 3)
        self.assertFalse(runner._resume_measurement)
        self.dv.do_start_measure.assert_not_called()

    def test_stale_source_record_blocks_before_any_device_command(self):
        runner, journal, _ = self.make_runner()
        self.original.update(note="changed after package was prepared")
        runner.run()
        self.assertEqual(self.dv.mock_calls, [])
        self.assertEqual(self.a.mock_calls, [])
        self.pump.stop.assert_not_called()
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "recovery_blocked")

    def test_restarted_dropview_does_not_prove_old_measurement_stopped(self):
        self.original.update(requested_settings={"valve_a": 3, "valve_b": 2, "pump_rpm": 12})
        runner, journal, _ = self.make_runner()
        self.dv.do_reconcile_discrete_recovery.return_value = False
        self.dv.do_restore_discrete_control.return_value = False
        runner._execute_step = Mock()
        runner.run()
        runner._execute_step.assert_not_called()
        self.pump.stop_and_confirm.assert_called_once()
        self.pump.run.assert_not_called()
        self.dv.do_restore_discrete_control.assert_called_once_with(
            None, log_fn=runner._log, allow_terminate=False, cancelled=runner._interrupted)
        self.dv.do_start_measure.assert_not_called()
        self.assertEqual(self.record()["status"], "interrupted")
        self.assertTrue(self.record()["recovery_connection_restored"])
        self.assertNotIn("recovery_resume_basis", self.record())
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "operator_required")

    def test_verified_exit_automatically_uses_normal_start_and_resumes_measurement(self):
        previous = {"pid": 42, "created": 100}
        self.original.update(status="interrupted", error="dropview_process_exited",
                             dropview_owner=previous)
        runner, _, _ = self.make_runner()
        with patch("bench_test.recipe.runner.process_exited", return_value=True) as exited:
            runner._prepare_recovery()
        exited.assert_called_once_with(previous)
        self.dv.do_start_dropview.assert_called_once()
        self.dv.do_restore_discrete_control.assert_not_called()
        self.dv.do_reconcile_discrete_recovery.assert_not_called()
        self.assertEqual(self.record()["recovery_resume_basis"], "process_exited")
        self.assertTrue(self.record()["recovery_connection_restored"])
        self.assertEqual(runner.current_step_index, 3)
        self.assertTrue(runner._resume_measurement)
        self.dv.do_start_measure.assert_not_called()

    def test_exit_recovery_does_not_accept_a_still_running_original_process(self):
        self.original.update(status="interrupted", error="dropview_process_exited",
                             dropview_owner={"pid": 42, "created": 100})
        self.dv.do_reconcile_discrete_recovery.return_value = False
        self.dv.do_restore_discrete_control.return_value = False
        runner, journal, _ = self.make_runner()
        with patch("bench_test.recipe.runner.process_exited", return_value=False):
            runner.run()
        self.dv.do_start_dropview.assert_not_called()
        self.dv.do_start_measure.assert_not_called()
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "operator_required")

    def test_confirmed_dropview_hang_restarts_before_resuming_the_recipe(self):
        previous = {"pid": 42, "created": 100}
        self.original.update(status="interrupted", error="dropview_not_responding", dropview_owner=previous)
        self.dv.do_reconcile_discrete_recovery.return_value = False
        self.dv.do_stop_unresponsive_dropview.return_value = True
        runner, _, _ = self.make_runner()
        with patch("bench_test.recipe.runner.process_exited", return_value=False):
            runner._prepare_recovery()

        self.dv.do_stop_unresponsive_dropview.assert_called_once_with(
            previous, cancelled=runner._interrupted, log_fn=runner._log)
        self.dv.do_start_dropview.assert_called_once_with(
            log_fn=runner._log, allow_cleanup=False, cancelled=runner._interrupted)
        self.assertEqual(self.record()["recovery_resume_basis"], "process_exited")
        self.assertTrue(self.record()["recovery_connection_restored"])
        self.assertEqual(runner.current_step_index, 3)
        self.assertTrue(runner._resume_measurement)

    def test_verified_process_exit_is_saved_even_if_relaunch_fails(self):
        previous = {"pid": 42, "created": 100}
        self.original.update(status="interrupted", error="dropview_process_exited", dropview_owner=previous)
        self.dv.do_start_dropview.return_value = False
        runner, _, _ = self.make_runner()
        with patch("bench_test.recipe.runner.process_exited", return_value=True):
            runner.run()

        self.assertEqual(self.record()["recovery_resume_basis"], "process_exited")
        self.assertFalse(self.record()["recovery_connection_restored"])
        self.dv.do_start_measure.assert_not_called()

    def test_disconnection_does_not_restart_measurement_until_reconnection_is_verified(self):
        previous = {"pid": 42, "created": 100}
        self.original.update(status="interrupted", error="dropsens_disconnected", dropview_owner=previous)
        self.dv.do_reconnect_dropsens.return_value = False
        runner, journal, _ = self.make_runner()
        with patch("bench_test.recipe.runner.process_exited", return_value=False):
            runner.run()
        self.dv.do_reconnect_dropsens.assert_called_once_with(previous, log_fn=runner._log)
        self.dv.do_start_dropview.assert_not_called()
        self.dv.do_start_measure.assert_not_called()
        self.assertEqual(RecoveryJournal.read(journal.path)["status"], "recovery_blocked")
        self.assertNotIn("recovery_resume_basis", self.record())
        self.assertFalse(self.record()["recovery_connection_restored"])

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
        self.assertEqual(self.record()["recovery_resume_basis"], "editor_idle")
        self.assertFalse(self.record()["recovery_connection_restored"])
        self.assertEqual(plan_recovery(self.record()).sequence_index, 3)
        self.dv.do_start_dropview.return_value = True
        retry, _, _ = self.make_runner()
        retry._execute_step = Mock(return_value=True)
        retry.run()
        self.assertEqual(self.dv.do_reconcile_discrete_recovery.call_args_list[0].args, (True,))
        self.assertEqual(self.dv.do_reconcile_discrete_recovery.call_args_list[1].args, (False,))
        self.assertEqual(self.record()["status"], "superseded")
        self.assertEqual(self.record()["recovery_attempts"], 2)

    def test_legacy_stop_record_remains_eligible_without_claiming_editor_idle(self):
        self.original.update(recovery_previous_stopped=True)
        runner, _, _ = self.make_runner()
        runner._prepare_recovery()

        self.dv.do_reconcile_discrete_recovery.assert_called_once_with(False, log_fn=runner._log)
        self.assertEqual(self.record()["recovery_resume_basis"], "legacy_reconciled")

    def test_process_exit_during_recovery_preparation_reclaims_the_linked_parent(self):
        runner, child, _ = self.make_runner()
        self.dv.do_start_dropview.side_effect = SystemExit("simulated process exit")
        with self.assertRaises(SystemExit):
            runner.run()
        self.assertEqual(self.record()["status"], "recovering")
        self.assertEqual(self.record()["recovery_resume_basis"], "editor_idle")
        path, record = recovery_source({"record_path": child.path,
                                        "recovery_source": self.original.path}, reclaim=True)
        self.assertEqual(path, self.original.path)
        self.assertEqual(record["status"], "interrupted")
        self.assertEqual(plan_recovery(record).sequence_index, 3)
        self.dv.do_start_measure.assert_not_called()

    def test_process_exit_during_new_start_does_not_replay_it_without_stop_evidence(self):
        self.recipe.steps[2] = RecipeStep(0, 0)
        self.recipe.loop_count = 1
        self.original.update(recipe=asdict(self.recipe))
        runner, child, _ = self.make_runner()
        self.dv.do_start_measure.side_effect = SystemExit("simulated exit during Start")
        with self.assertRaises(SystemExit):
            runner.run()
        path, record = recovery_source({"record_path": child.path,
                                        "recovery_source": self.original.path}, reclaim=True)
        self.assertEqual(path, child.path)
        self.assertEqual(self.record()["status"], "superseded")
        self.assertTrue(record["measurement_restart_pending"])
        self.assertFalse(record["measurement_running"])
        self.assertEqual(plan_recovery(record).sequence_index, 3)
        retry, destination, _ = self.make_runner(path)
        self.dv.reset_mock(side_effect=True)
        self.dv.do_reconcile_discrete_recovery.return_value = False
        self.dv.do_restore_discrete_control.return_value = False
        retry.run()
        self.assertEqual(RecoveryJournal.read(destination.path)["status"], "operator_required")
        self.dv.do_reconcile_discrete_recovery.assert_called_once_with(True, log_fn=retry._log)
        self.dv.do_start_measure.assert_not_called()

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
                self.assertTrue(runner._resume_measurement)
                self.assertTrue(runner._restore_pump_at_step)
            seen.append(sequence)
            return True
        runner._execute_step = execute
        runner.run()
        self.assertTrue(seen)
        self.pump.stop_and_confirm.assert_called_once()

    def test_recovery_applies_target_or_inherited_speed_before_new_measurement(self):
        for target_rpm, expected in ((None, 12), (0, 0), (8, 8)):
            with self.subTest(target_rpm=target_rpm):
                self.recipe.steps[2] = RecipeStep(0, 0, pump_rpm=target_rpm)
                self.original.update(recipe=asdict(self.recipe), status="running",
                                     recovery_previous_stopped=False,
                                     requested_settings={"valve_a": 3, "valve_b": 2, "pump_rpm": 12})
                runner, journal, session = self.make_runner()
                order = Mock()
                order.hold.return_value = True
                order.reconcile.return_value = True
                order.speed.return_value = []
                order.run.return_value = []
                order.running_state.return_value = expected > 0
                order.start.return_value = True
                with patch.object(self.pump, "stop_and_confirm", order.hold), \
                        patch.object(self.dv, "do_reconcile_discrete_recovery", order.reconcile), \
                        patch.object(self.pump, "set_speed_rpm", order.speed), \
                        patch.object(self.pump, "run", order.run), \
                        patch.object(self.pump, "running_state", order.running_state), \
                        patch.object(self.dv, "do_start_measure", order.start):
                    runner._prepare_recovery()
                    self.assertEqual(RecoveryJournal.read(journal.path)["requested_settings"]["pump_rpm"], 12)
                    self.assertTrue(runner._execute_step(self.recipe.steps[2], 3, 5))
                names = [call[0] for call in order.mock_calls]
                self.assertLess(names.index("hold"), names.index("reconcile"))
                self.assertLess(names.index("speed"), names.index("start"))
                order.speed.assert_called_once_with(expected)
                if expected:
                    order.run.assert_called_once_with("FWD")
                else:
                    order.run.assert_not_called()
                order.start.assert_called_once_with(session.get_file_path("script"), log_fn=runner._log)

    def test_unconfirmed_pump_stop_blocks_before_any_dropview_action(self):
        self.original.update(requested_settings={"valve_a": 3, "valve_b": 2, "pump_rpm": 12})
        self.pump.stop_and_confirm.return_value = False
        runner, journal, _ = self.make_runner()
        runner.run()
        self.dv.do_start_measure.assert_not_called()
        self.dv.do_reconcile_discrete_recovery.assert_not_called()
        self.assertEqual(RecoveryJournal.read(journal.path)["pump_recovery_stop"], "unverified")

    def test_disconnection_recovers_through_supervisor_client_and_new_package(self):
        from PyQt6.QtWidgets import QApplication
        from bench_test.watchdog.client import WatchdogClient
        import time

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication(["recovery-integration"])
        self.recipe.steps[2] = RecipeStep(0, 0)
        self.recipe.loop_count = 1
        self.original.update(recipe=asdict(self.recipe),
                             requested_settings={"valve_a": 3, "valve_b": 2, "pump_rpm": 12})
        old = RecipeRunner(self.a, self.b, self.recipe, queue.Queue(), threading.Event(), self.dv,
                           pump_ctrl=self.pump, recovery_journal=self.original)
        old.current_loop_index, old.current_sequence_index, old.current_step_index = 1, 2, 2
        old._active_step = self.recipe.steps[1]
        old._requested_settings.update(self.record()["requested_settings"])
        old.measurement_running = True
        old.active_measurement_kind = "discrete_pad"
        old._measurement_owner = {"pid": 42, "created": 100}
        old.is_alive = Mock(return_value=True)
        old.checkpoint("waiting", status="running", remaining_seconds=120)

        state_machine = SimpleNamespace(state="running")
        tab = SimpleNamespace(package_tab=SimpleNamespace(sm=state_machine), recover_from_record=Mock())
        health = SimpleNamespace(last_health={})
        runtime = Path(self.temp.name) / "watchdog-integration"
        client = WatchdogClient(tab, health, directory=runtime)
        client._timer.stop()
        now = [time.monotonic()]
        launch, terminate = Mock(), Mock()
        supervisor = Supervisor(runtime, clock=lambda: now[0], is_alive=lambda _: True,
                                start_app=launch, terminate=terminate)
        recovered = []

        def adopt(path):
            self.assertEqual(path, self.original.path)
            runner, journal, session = self.make_runner()
            recovered.append((runner, journal, session))
            client.arm(runner)

        tab.recover_from_record.side_effect = adopt
        self.pump.running_state.return_value = True
        self.pump.run.return_value = []
        self.dv.do_reconnect_dropsens.return_value = True
        self.dv.discrete_measurement_owner = old._measurement_owner
        with patch.object(client, "_ensure_supervisor"), \
                patch("bench_test.recipe.runner.process_exited", return_value=False), \
                patch("bench_test.watchdog.client.WindowsUpdateGuard") as guard_factory, \
                patch("bench_test.watchdog.client.threading.Thread",
                      side_effect=lambda target, **_: SimpleNamespace(start=target)), \
                patch("bench_test.watchdog.client.time.monotonic", side_effect=lambda: now[0]):
            guard = guard_factory.return_value
            guard.protect.return_value = guard.status.return_value = "test-only"
            guard.restore.return_value = "test-only"
            try:
                client.arm(old)
                for delta in (0, 15):
                    now[0] += delta
                    health.last_health = {"dv_open": True, "state": "disconnected", "sampled_at": now[0],
                                          "process_identity": old._measurement_owner}
                    old.worker_heartbeat = now[0]
                    client._publish()
                    supervisor.tick()
                self.assertEqual(read_json(runtime / "command.json")["reason"], "dropsens_disconnected")
                client._tick()
                self.assertTrue(old._interrupted())
                tab.recover_from_record.assert_not_called()
                old._finish_watchdog_interrupt()
                old.is_alive.return_value = False
                client._tick()
                tab.recover_from_record.assert_not_called()  # Old package is still being finalized.
                state_machine.state = "idle"
                client._tick()
                runner, journal, session = recovered[0]
                runner.run()
                result = RecoveryJournal.read(journal.path)
                self.assertEqual(result["status"], "completed", result.get("error"))
                self.assertTrue(runner.recovery_continued)
                self.assertEqual(self.record()["status"], "superseded")
                self.assertEqual(self.record()["recovery_resume_basis"], "link_restored")
                self.assertTrue(self.record()["recovery_connection_restored"])
                self.dv.do_reconnect_dropsens.assert_called_once_with(old._measurement_owner, log_fn=runner._log)
                self.dv.do_start_dropview.assert_not_called()
                self.dv.do_reconcile_discrete_recovery.assert_not_called()
                self.pump.set_speed_rpm.assert_any_call(12)
                self.dv.do_start_measure.assert_called_once_with(session.get_file_path("script"), log_fn=runner._log)
                self.assertEqual(self.pump.stop_and_confirm.call_count, 2)
                launch.assert_not_called()
                terminate.assert_not_called()
            finally:
                client.close()
        app.processEvents()

    def test_flexicon_stop_requires_valid_fresh_running_status(self):
        for replies, expected in (([[], ["1 !"], ["0 !"]], True),
                                  ([[], [], ["ERR NO_RESPONSE"], ["OK STOP"]], False)):
            with self.subTest(replies=replies):
                pump = FlexiconPumpController()
                pump.serial_port = Mock(is_open=True)
                with patch.object(pump, "send_command", side_effect=replies) as command:
                    self.assertIs(pump.stop_and_confirm(), expected)
                self.assertEqual(command.call_args_list[0].args, ("ST",))
                self.assertTrue(all(call.args == ("ZY",) for call in command.call_args_list[1:]))

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
    def test_recovery_can_reopen_the_editor_in_the_verified_original_session(self):
        owner = {"pid": 42, "created": 100}
        for stopped in (True, False):
            with self.subTest(stopped=stopped):
                controller = Mock()
                controller.do_stop_measure.return_value = stopped
                with patch("bench_test.dropview.controller.automator.verify_discrete_session_connection") as verify, \
                        patch("bench_test.dropview.controller.automator.window_exists", return_value=False):
                    result = DropViewController.do_reconcile_discrete_recovery(
                        controller, True, expected_owner=owner)
                self.assertEqual(result, stopped)
                verify.assert_called_once_with(owner)
                controller.do_stop_measure.assert_called_once_with(
                    log_fn=None, require_editor=True, expected_owner=owner, reopen_if_needed=True)

    def test_recovery_does_not_touch_the_editor_when_the_original_session_cannot_be_verified(self):
        owner = {"pid": 42, "created": 100}
        controller = Mock()
        with patch("bench_test.dropview.controller.automator.verify_discrete_session_connection",
                   side_effect=RuntimeError("The original DropView process could not be verified.")):
            self.assertFalse(DropViewController.do_reconcile_discrete_recovery(
                controller, True, expected_owner=owner))
        controller.do_stop_measure.assert_not_called()

    def test_absent_editor_is_not_treated_as_stop_confirmation(self):
        controller = Mock()
        with patch("bench_test.dropview.controller.automator.window_exists", return_value=False):
            self.assertFalse(DropViewController.do_reconcile_discrete_recovery(controller, True))
            self.assertTrue(DropViewController.do_reconcile_discrete_recovery(controller, False))
        controller.do_stop_measure.assert_not_called()

    def test_present_editor_requires_successful_verified_stop(self):
        controller = Mock()
        controller.do_stop_measure.return_value = True
        with patch("bench_test.dropview.controller.automator.window_exists", return_value=True):
            self.assertTrue(DropViewController.do_reconcile_discrete_recovery(controller, True))
        controller.do_stop_measure.return_value = False
        with patch("bench_test.dropview.controller.automator.window_exists", return_value=True):
            self.assertFalse(DropViewController.do_reconcile_discrete_recovery(controller, True))


if __name__ == "__main__":
    unittest.main()
