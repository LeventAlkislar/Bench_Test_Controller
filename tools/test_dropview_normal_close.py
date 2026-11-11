"""Verified normal-close recovery tests; no real process or window operations."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.dropview import automator
from bench_test.dropview.controller import DropViewController
from bench_test.recipe.runner import RecipeRunner


class NormalCloseTests(unittest.TestCase):
    def setUp(self):
        self.owner = {"pid": 42, "created": 100}
        logger = patch.object(automator, "debug_log")
        logger.start()
        self.addCleanup(logger.stop)

    def test_responsive_dropview_closes_with_late_warning_and_verified_process_exit(self):
        now, closing, acknowledged = [0], [False], [False]
        process, order = Mock(), Mock()
        def request_close(*args):
            closing[0] = True
            order.close()
        def warning(owner):
            self.assertEqual(owner, self.owner)
            if closing[0] and now[0] >= 0.25 and not acknowledged[0]:
                acknowledged[0] = True
                order.warning_closed()
                return True
            return False
        def wait(timeout):
            now[0] += timeout
            if not acknowledged[0]:
                raise automator.psutil.TimeoutExpired(timeout)
            order.process_exited()
        process.wait.side_effect = wait
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21) as find, \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "dropview_responding", return_value=True) as responding, \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", side_effect=warning), \
                patch.object(automator.win32gui, "PostMessage", side_effect=request_close) as close, \
                patch.object(automator.time, "monotonic", side_effect=lambda: now[0]):
            automator.close_dropview_for_recovery(self.owner, cancelled=lambda: False)
        self.assertEqual(order.mock_calls, [call.close(), call.warning_closed(), call.process_exited()])
        find.assert_called_once_with(automator.DROPVIEW_WINDOW_NAME, timeout=0.5, poll_interval=0.1, pid=42)
        close.assert_called_once_with(21, automator.win32con.WM_CLOSE, 0, 0)
        responding.assert_not_called()
        self.assertEqual(process.wait.call_count, 2)
        process.terminate.assert_not_called()

    def test_closed_warning_does_not_prove_exit_and_normal_only_policy_blocks_termination(self):
        now, process = [0], Mock()
        def wait(timeout):
            now[0] += timeout
            raise automator.psutil.TimeoutExpired(timeout)
        process.wait.side_effect = wait
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=True), \
                patch.object(automator.win32gui, "PostMessage"), \
                patch.object(automator.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(RuntimeError, "did not close normally"):
                automator._close_verified_dropview(self.owner, allow_terminate=False,
                                                   service_dialogs=True, cancelled=lambda: False)
        self.assertEqual(now[0], 8)
        process.terminate.assert_not_called()

    def test_stop_during_warning_handling_prevents_close_request(self):
        cancelled, process = [False], Mock()
        def warning(owner):
            cancelled[0] = True
            return True
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", side_effect=warning), \
                patch.object(automator.win32gui, "PostMessage") as close:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                automator.close_dropview_for_recovery(self.owner, cancelled=lambda: cancelled[0])
        close.assert_not_called()
        process.terminate.assert_not_called()

    def test_normal_close_timeout_forcibly_terminates_original_even_with_responsive_ui(self):
        process, order = Mock(), Mock()
        order.attach_mock(process.terminate, "terminate")
        order.attach_mock(process.wait, "exit_wait")
        order.normal_wait.side_effect = automator.psutil.TimeoutExpired(8)
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "dropview_responding", return_value=True) as responding, \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                patch.object(automator, "_wait_for_dropview_close", order.normal_wait), \
                patch.object(automator.win32gui, "PostMessage", order.close):
            automator.close_dropview_for_recovery(self.owner, cancelled=lambda: False)
        self.assertEqual([entry[0] for entry in order.mock_calls],
                         ["close", "normal_wait", "terminate", "exit_wait"])
        process.wait.assert_called_once_with(timeout=5)
        responding.assert_not_called()

    def test_pid_reuse_after_normal_close_timeout_never_terminates_replacement(self):
        original, replacement = Mock(), Mock()
        original.create_time.return_value = 100
        original.is_running.return_value = True
        replacement.create_time.return_value = 101
        with patch.object(automator.psutil, "Process", side_effect=[original, replacement]), \
                patch.object(automator, "_is_dropview_process", return_value=True), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                patch.object(automator, "_wait_for_dropview_close", side_effect=automator.psutil.TimeoutExpired(8)), \
                patch.object(automator.win32gui, "PostMessage"):
            automator.close_dropview_for_recovery(self.owner, cancelled=lambda: False)
        original.terminate.assert_not_called()
        replacement.terminate.assert_not_called()

    def test_stop_after_normal_close_timeout_prevents_forced_termination(self):
        cancelled, process = [False], Mock()
        def timeout(*args, **kwargs):
            cancelled[0] = True
            raise automator.psutil.TimeoutExpired(8)
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                patch.object(automator, "_wait_for_dropview_close", side_effect=timeout), \
                patch.object(automator.win32gui, "PostMessage"):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                automator.close_dropview_for_recovery(self.owner, cancelled=lambda: cancelled[0])
        process.terminate.assert_not_called()

    def test_failed_forced_exit_verification_is_reported_as_failed_close(self):
        process = Mock()
        process.wait.side_effect = automator.psutil.TimeoutExpired(5)
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                patch.object(automator, "_wait_for_dropview_close", side_effect=automator.psutil.TimeoutExpired(8)), \
                patch.object(automator.win32gui, "PostMessage"):
            self.assertFalse(DropViewController.do_close_dropview_for_recovery(
                Mock(), self.owner, cancelled=lambda: False))
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_reused_pid_is_not_closed(self):
        process = Mock()
        process.create_time.return_value = 101
        with patch.object(automator.psutil, "Process", return_value=process), \
                patch.object(automator, "find_window") as find, \
                patch.object(automator.win32gui, "PostMessage") as close:
            automator.close_dropview_for_recovery(self.owner, cancelled=lambda: False)
        find.assert_not_called()
        close.assert_not_called()
        process.terminate.assert_not_called()

    def test_new_window_owner_during_warning_prevents_close_request(self):
        process = Mock()
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", side_effect=[self.owner, {"pid": 43, "created": 101}]), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=True), \
                patch.object(automator.win32gui, "PostMessage") as close:
            with self.assertRaisesRegex(RuntimeError, "window changed"):
                automator.close_dropview_for_recovery(self.owner, cancelled=lambda: False)
        close.assert_not_called()
        process.terminate.assert_not_called()

    def test_controller_reports_failed_close_without_claiming_exit(self):
        controller, log = Mock(), Mock()
        with patch.object(automator, "close_dropview_for_recovery", side_effect=RuntimeError("still running")):
            self.assertFalse(DropViewController.do_close_dropview_for_recovery(
                controller, self.owner, cancelled=lambda: False, log_fn=log))
        self.assertEqual(log.call_args.args[0], "DropView recovery close error: still running")

    def test_verified_exit_is_saved_before_relaunch_and_failed_close_blocks_relaunch(self):
        for closed in (True, False):
            with self.subTest(closed=closed):
                dv, journal, order = Mock(), Mock(), Mock()
                dv.do_reconnect_dropsens.return_value = False
                dv.do_close_dropview_for_recovery.return_value = closed
                order.attach_mock(dv.do_reconnect_dropsens, "reconnect")
                order.attach_mock(dv.do_close_dropview_for_recovery, "close")
                order.attach_mock(journal.update, "save")
                runner = SimpleNamespace(_recovery_source_journal=journal, dropview_ctrl=dv, _log=Mock(),
                                         _interrupted=Mock(return_value=False), _start_dropview_for_recovery=Mock())
                order.attach_mock(runner._start_dropview_for_recovery, "start")
                record = {"phase": "waiting", "measurement_running": True, "dropview_owner": self.owner,
                          "error": "device_usb_disconnected"}
                with patch("bench_test.recipe.runner.process_exited", return_value=False):
                    if closed:
                        self.assertTrue(RecipeRunner._reconcile_recovery_dropview(runner, record))
                        self.assertEqual([entry[0] for entry in order.mock_calls], ["reconnect", "close", "save", "start"])
                        journal.update.assert_called_once_with(recovery_resume_basis="process_exited")
                    else:
                        with self.assertRaises(RuntimeError):
                            RecipeRunner._reconcile_recovery_dropview(runner, record)
                        journal.update.assert_not_called()
                        runner._start_dropview_for_recovery.assert_not_called()
                dv.do_stop_unresponsive_dropview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
