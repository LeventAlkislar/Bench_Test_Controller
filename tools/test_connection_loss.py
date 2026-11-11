"""Cable-loss recovery regression checks, without operating hardware or windows."""
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.dropview import automator
from bench_test.recipe.models import RecipeStep
from bench_test.recipe.runner import RecipeRunner


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class ConnectionLossTests(unittest.TestCase):
    def test_usb_error_uses_the_shared_ocr_handler_and_rejects_unknown_messages(self):
        owner = {"pid": 424243, "created": 100}
        self.addCleanup(automator._connection_faults.pop, owner["pid"], None)
        with patch.object(automator, "win32gui") as gui, \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "_read_error_window_text", return_value=""), \
                patch.object(automator, "_find_child_by_text", return_value=None), \
                patch.object(automator, "_read_connection_error_labels", side_effect=[
                    ("Device USB cable disconnected", (100, 200)), ("An unrelated error", (100, 200))]), \
                patch.object(automator, "debug_log"), patch.object(automator.pyautogui, "click") as click:
            gui.IsWindowVisible.return_value = True
            gui.GetWindowText.return_value = "Error"
            gui.GetForegroundWindow.return_value = 20
            gui.GetAncestor.return_value = 20
            self.assertTrue(automator._dismiss_connection_error_dialog(20))
            self.assertFalse(automator._dismiss_connection_error_dialog(20))
            click.assert_called_once_with(100, 200)

    def test_saved_bluetooth_address_uses_its_current_port_before_other_ports(self):
        settings = {"dropsens_named_selection": {"address": "A"}, "dropsens_last_successful_port": "COM4"}
        with patch.object(automator, "get_value", side_effect=lambda key, default=None: settings.get(key, default)), \
                patch.object(automator.list_ports, "comports", return_value=[
                    SimpleNamespace(device="COM99"), SimpleNamespace(device="COM4")]), \
                patch("bench_test.dropview.windows_devices.list_dropsens_devices",
                      return_value=[("A", "UST1", "COM99")]):
            self.assertEqual(automator._resolve_target_dropsens_ports(), ["COM99", "COM4"])

    def test_usb_dialog_is_latched_before_failed_dismissal_and_scoped_to_process_creation(self):
        owner = {"pid": 424242, "created": 100}
        self.addCleanup(automator._connection_faults.pop, owner["pid"], None)
        with patch.object(automator, "win32gui") as gui, \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "_read_error_window_text", return_value="Device USB cable disconnected."), \
                patch.object(automator, "_find_child_by_text", return_value=30), \
                patch.object(automator, "debug_log"), \
                patch.object(automator, "_focus_window", side_effect=RuntimeError("focus denied")):
            gui.IsWindowVisible.return_value = True
            gui.GetWindowText.return_value = "Error"
            gui.GetClassName.return_value = "Button"
            gui.GetForegroundWindow.return_value = 99
            since = time.monotonic()
            with automator._track_connection_error(10) as seen:
                self.assertTrue(automator._dismiss_connection_error_dialog(20))
                self.assertEqual(automator._wait_for_connection_result(connection_error=seen), "device_usb_disconnected")
            fault = automator.connection_fault(owner, since=since)
            self.assertEqual(fault["reason"], "device_usb_disconnected")
            self.assertIsNone(automator.connection_fault({**owner, "created": 101}))
            self.assertIsNone(automator.connection_fault(owner, since=fault["sampled_at"] + 1))

    def test_cable_loss_blocks_next_recipe_command_even_with_stale_running_state(self):
        runner = SimpleNamespace(
            measurement_running=True, active_measurement_kind="discrete_pad",
            _measurement_owner={"pid": 42, "created": 100}, _pad_activity_since=0,
            _interrupted=Mock(return_value=False), _log=Mock(), checkpoint=Mock(), request_recovery=Mock())
        runner._check_dropview_connection_fault = lambda: RecipeRunner._check_dropview_connection_fault(runner)
        fault = {"reason": "device_usb_disconnected"}
        with patch.object(automator, "dismiss_connection_error_dialogs"), \
                patch.object(automator, "connection_fault", return_value=fault):
            self.assertFalse(RecipeRunner._execute_step(runner, RecipeStep(3, 10), 1, 2))
        runner.request_recovery.assert_called_once_with("device_usb_disconnected")
        runner.checkpoint.assert_called_once_with(connection_error=fault)

    def test_reconnect_ignores_old_green_indicator_and_tries_manual_then_automatic(self):
        for ports in (["COM4", "COM3"], []):
            with self.subTest(ports=ports), \
                    patch.object(automator, "_connection_snapshot", return_value={"state": "connected"}), \
                    patch.object(automator, "_focus_window"), \
                    patch.object(automator, "safe_sequence") as sequence, \
                    patch.object(automator, "wait_until_disconnected") as disconnected, \
                    patch.object(automator, "_resolve_target_dropsens_ports", return_value=ports), \
                    patch.object(automator, "_connect_manual", return_value="timeout") as manual, \
                    patch.object(automator, "get_window_pid", return_value=42), \
                    patch.object(automator, "window_exists", return_value=False), \
                    patch.object(automator, "_track_connection_error", return_value=nullcontext(threading.Event())), \
                    patch.object(automator, "_wait_for_connection_result", return_value="connected"), \
                    patch.object(automator, "_verify_dropview_connection") as verified, \
                    patch.object(automator, "remember_value"), patch.object(automator, "debug_log"):
                automator.step_connect_dropsens(hwnd=21, force_reconnect=True)
                disconnected.assert_called_once_with(timeout=automator.TIMEOUT_CLOSE_WINDOW, hwnd=21)
                self.assertEqual([args.args[1] for args in manual.call_args_list], ports)
                self.assertEqual([args.args[1][0]["keys"] for args in sequence.call_args_list],
                                 [("ctrl", "d"), ("ctrl", "c")])
                verified.assert_called_once_with(hwnd=21)

    def test_failed_reconnect_restarts_only_after_verified_normal_process_exit(self):
        for closed in (True, False):
            with self.subTest(closed=closed):
                dv = Mock()
                dv.do_reconnect_dropsens.return_value = False
                dv.do_close_dropview_for_recovery.return_value = closed
                runner = SimpleNamespace(_recovery_source_journal=Mock(), dropview_ctrl=dv,
                                         _log=Mock(), _interrupted=Mock(return_value=False),
                                         _start_dropview_for_recovery=Mock())
                current = {"phase": "waiting", "measurement_running": True,
                           "dropview_owner": {"pid": 42, "created": 100}, "error": "device_usb_disconnected"}
                with patch("bench_test.recipe.runner.process_exited", return_value=False):
                    if closed:
                        self.assertTrue(RecipeRunner._reconcile_recovery_dropview(runner, current))
                        runner._start_dropview_for_recovery.assert_called_once()
                    else:
                        with self.assertRaises(RuntimeError):
                            RecipeRunner._reconcile_recovery_dropview(runner, current)
                        runner._start_dropview_for_recovery.assert_not_called()

    def csv_runner(self, directory):
        return SimpleNamespace(
            csv_expectation={"first_seconds": 10, "interval_seconds": 60, "exports": 10, "csv_stem": "pad"},
            session_measurements_dir=str(directory), measurement_epoch=100,
            measurement_started_ns=time.time_ns(), _interrupted=Mock(return_value=False),
            _check_discrete_measurement_progress=Mock(return_value=True), checkpoint=Mock(),
            _log=Mock(), _hold_pump_for_recovery=Mock(), request_recovery=Mock(), stop_event=Mock())

    def test_csv_gate_rejects_old_wrong_name_and_invalid_exports_before_accepting_fresh_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self.csv_runner(root)
            valid = '"curve#001";"";""\n""\n"Time (s)";"Current (uA)";""\n"0";"0.1"\n"2.5";"0.2"\n'
            old = root / "PAD-001.csv"
            old.write_text(valid, encoding="utf-8")
            os.utime(old, ns=(runner.measurement_started_ns - 1, runner.measurement_started_ns - 1))
            now, stages = [100], []
            def wait(_):
                stages.append(len(stages))
                now[0] += 3
                if len(stages) == 1:
                    (root / "other.csv").write_text(valid, encoding="utf-8")
                elif len(stages) == 2:
                    (root / "PAD-002.csv").write_text("invalid", encoding="utf-8")
                else:
                    (root / "PAD-003.csv").write_text(valid, encoding="utf-8")
            runner.stop_event.wait.side_effect = wait
            with patch("bench_test.recipe.runner.time.monotonic", side_effect=lambda: now[0]), \
                    patch("bench_test.watchdog.progress.threading.Thread", ImmediateThread):
                self.assertTrue(RecipeRunner._wait_for_recovery_csv(runner))
            self.assertEqual(len(stages), 3)
            runner.request_recovery.assert_not_called()

    def test_csv_gate_timeout_and_user_stop_do_not_allow_continuation(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                runner = self.csv_runner(directory)
                now = [100]
                def wait(_):
                    now[0] += 80
                    runner._interrupted.return_value = cancel
                runner.stop_event.wait.side_effect = wait
                with patch("bench_test.recipe.runner.time.monotonic", side_effect=lambda: now[0]), \
                        patch("bench_test.watchdog.progress.threading.Thread", ImmediateThread):
                    self.assertFalse(RecipeRunner._wait_for_recovery_csv(runner))
                if cancel:
                    runner.request_recovery.assert_not_called()
                else:
                    runner.request_recovery.assert_called_once_with("discrete_first_export_missing")


if __name__ == "__main__":
    unittest.main()
