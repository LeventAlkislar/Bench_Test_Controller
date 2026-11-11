"""Unsaved-curves modal handling during disconnect; no real UI or hardware."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.dropview import automator


class WarningTests(unittest.TestCase):
    def setUp(self):
        self.owner = {"pid": 42, "created": 100}
        logger = patch.object(automator, "debug_log")
        logger.start()
        self.addCleanup(logger.stop)

    def test_enter_requires_confirmed_closure_and_retries_only_the_original_warning(self):
        for results, expected, presses in (([None], True, 1),
                                           ([TimeoutError(), None], True, 2),
                                           ([TimeoutError(), TimeoutError()], False, 2)):
            with self.subTest(results=results), \
                    patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                    patch.object(automator, "_focus_window"), \
                    patch.object(automator.win32gui, "IsWindowVisible", return_value=True), \
                    patch.object(automator.win32gui, "GetWindowText", return_value=automator.WARNING_UNSAVED), \
                    patch.object(automator.win32gui, "GetForegroundWindow", return_value=20), \
                    patch.object(automator, "wait_for_window_close", side_effect=results) as closed, \
                    patch.object(automator.pyautogui, "press") as press:
                self.assertEqual(automator._auto_dismiss_dialog(20, automator.WARNING_UNSAVED), expected)
                self.assertEqual(press.call_count, presses)
                self.assertEqual(closed.call_args.kwargs["hwnd"], 20)

    def test_warning_retry_never_sends_enter_to_a_replacement_process(self):
        replacement = {**self.owner, "created": 101}
        with patch.object(automator, "dropview_process_identity", side_effect=[
                self.owner, self.owner, self.owner, replacement]), \
                patch.object(automator, "_focus_window"), \
                patch.object(automator.win32gui, "IsWindowVisible", return_value=True), \
                patch.object(automator.win32gui, "GetWindowText", return_value=automator.WARNING_UNSAVED), \
                patch.object(automator.win32gui, "GetForegroundWindow", return_value=20), \
                patch.object(automator, "wait_for_window_close", side_effect=TimeoutError()), \
                patch.object(automator.pyautogui, "press") as press:
            self.assertFalse(automator._auto_dismiss_dialog(20, automator.WARNING_UNSAVED))
        press.assert_called_once_with("enter")

    def test_startup_warning_uses_the_same_verified_result(self):
        with patch.object(automator, "window_exists", return_value=True), \
                patch.object(automator, "find_window", return_value=20), \
                patch.object(automator, "_auto_dismiss_dialog", return_value=False) as dismiss:
            self.assertFalse(automator._dismiss_warning_if_present(automator.WARNING_UNSAVED))
        dismiss.assert_called_once_with(20, automator.WARNING_UNSAVED)

    def test_disconnect_closes_modal_then_rechecks_link_before_trying_com(self):
        now, warning, disconnected = [0], [False], [False]
        order = Mock()
        def sequence(*args, **kwargs):
            warning[0] = True
            order.ctrl_d()
        def acknowledge(owner):
            self.assertEqual(owner, self.owner)
            if warning[0]:
                warning[0] = False
                disconnected[0] = True
                order.warning_closed()
                return True
            return False
        def snapshot(**kwargs):
            self.assertEqual(kwargs, {"hwnd": 21})
            order.read_link()
            return {"state": "disconnected" if disconnected[0] else "connected"}
        def manual(*args, **kwargs):
            self.assertTrue(disconnected[0])
            self.assertFalse(warning[0])
            order.manual()
            return "connected"
        with patch.object(automator, "_focus_window"), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", side_effect=acknowledge), \
                patch.object(automator, "window_exists", side_effect=lambda *args, **kwargs: warning[0]), \
                patch.object(automator, "_connection_snapshot", side_effect=snapshot), \
                patch.object(automator, "safe_sequence", side_effect=sequence), \
                patch.object(automator, "_resolve_target_dropsens_ports", return_value=["COM10"]), \
                patch.object(automator, "_connect_manual", side_effect=manual), \
                patch.object(automator, "_verify_dropview_connection"), \
                patch.object(automator, "remember_value"), \
                patch.object(automator.time, "monotonic", side_effect=lambda: now[0]):
            automator.step_connect_dropsens(hwnd=21, force_reconnect=True)
        self.assertEqual(order.mock_calls, [call.read_link(), call.ctrl_d(), call.warning_closed(),
                                            call.read_link(), call.manual()])

    def test_open_warning_blocks_even_a_disconnected_indicator(self):
        now = [0]
        def sleep(seconds):
            now[0] += seconds
        with patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                patch.object(automator, "window_exists", return_value=True), \
                patch.object(automator, "_connection_snapshot", return_value={"state": "disconnected"}) as read, \
                patch.object(automator.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(automator.time, "sleep", side_effect=sleep):
            with self.assertRaisesRegex(TimeoutError, "blocking warning"):
                automator.wait_until_disconnected(hwnd=21)
        read.assert_not_called()
        self.assertEqual(now[0], 10)

    def test_warning_closure_alone_does_not_prove_disconnection(self):
        now = [0]
        def sleep(seconds):
            now[0] += seconds
        with patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=True), \
                patch.object(automator, "window_exists", return_value=False), \
                patch.object(automator, "_connection_snapshot", return_value={"state": "connected"}), \
                patch.object(automator.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(automator.time, "sleep", side_effect=sleep):
            with self.assertRaises(TimeoutError):
                automator.wait_until_disconnected(hwnd=21)
        self.assertLessEqual(now[0], 14)

    def test_late_warning_acknowledgement_still_allows_one_fresh_link_read(self):
        now = [0]
        def acknowledge(owner):
            now[0] = 11
            return True
        with patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", side_effect=acknowledge), \
                patch.object(automator, "window_exists", return_value=False), \
                patch.object(automator, "_connection_snapshot", return_value={"state": "disconnected"}) as read, \
                patch.object(automator.time, "monotonic", side_effect=lambda: now[0]):
            automator.wait_until_disconnected(hwnd=21)
        read.assert_called_once_with(hwnd=21)

    def test_dismissing_modal_does_not_count_as_process_exit_or_trigger_forced_close(self):
        process = Mock()
        with patch.object(automator, "_verified_dropview_process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=self.owner), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=True), \
                patch.object(automator, "dropview_responding", return_value=True), \
                patch.object(automator.win32gui, "PostMessage") as close:
            with self.assertRaisesRegex(RuntimeError, "responding again"):
                automator.stop_unresponsive_dropview(self.owner, cancelled=lambda: False)
        process.wait.assert_not_called()
        process.terminate.assert_not_called()
        close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
