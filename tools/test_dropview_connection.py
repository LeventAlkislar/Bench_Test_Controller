"""Connection regression checks; no hardware or application UI is operated."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.dropview import automator, vision, window_actions
from bench_test.dropview.controller import DropViewController


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.logging = patch.object(automator, "debug_log")
        self.logging.start()
        self.addCleanup(self.logging.stop)

    def test_connection_error_poll_retries_exact_dropview_dialog_and_preserves_attempt_result(self):
        owner = {"pid": 42, "created": 100}
        other_owner = {"pid": 43, "created": 200}
        with patch.object(automator, "win32gui") as gui, \
                patch.object(automator, "dropview_process_identity", side_effect={
                    10: owner, 11: other_owner, 20: owner, 21: owner, 22: None,
                }.get), \
                patch.object(automator, "_read_error_window_text", side_effect={
                    20: "The potentiostat  is null", 21: "Potentiostat not found",
                }.__getitem__), \
                patch.object(automator, "_find_child_by_text", return_value=30):
            gui.IsWindowVisible.return_value = True
            gui.IsWindowEnabled.return_value = True
            gui.GetClassName.return_value = "Button"
            gui.GetAncestor.return_value = 20
            gui.GetForegroundWindow.return_value = 20
            gui.GetWindowText.side_effect = lambda hwnd: "OK" if hwnd == 30 else "Error"
            gui.EnumWindows.side_effect = lambda callback, _: [callback(hwnd, None) for hwnd in (20, 21, 22)]
            with automator._track_connection_error(10) as seen, \
                    automator._track_connection_error(11) as unrelated:
                automator.dismiss_connection_error_dialogs()
                automator.dismiss_connection_error_dialogs()
                self.assertEqual(automator._wait_for_connection_result(connection_error=seen), "potentiostat_null")
                self.assertFalse(unrelated.is_set())
            with automator._track_connection_error(10) as next_attempt:
                self.assertFalse(next_attempt.is_set())

        self.assertEqual(gui.PostMessage.call_args_list, [call(30, automator.win32con.BM_CLICK, 0, 0)] * 2)

    def test_connection_error_uses_ocr_ok_only_when_point_belongs_to_the_dialog(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "win32gui") as gui, \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "_read_error_window_text", return_value=""), \
                patch.object(automator, "_find_child_by_text", return_value=None), \
                patch.object(automator, "connection_scores_on_window", return_value={
                    "error": "", "region": (90, 180, 240, 80), "image": object(),
                }), \
                patch.object(automator, "read_label_positions", return_value=[
                    ("The potentiostat is null", 10, 5), ("0K", 10, 20),
                ]), \
                patch.object(automator.pyautogui, "click") as click:
            gui.IsWindowVisible.return_value = True
            gui.GetWindowText.return_value = "Error"
            gui.GetForegroundWindow.return_value = 20
            gui.GetAncestor.side_effect = [99, 20]
            self.assertTrue(automator._dismiss_connection_error_dialog(20))
            click.assert_not_called()
            self.assertTrue(automator._dismiss_connection_error_dialog(20))
            click.assert_called_once_with(100, 200)

    def test_active_measurement_accepts_only_the_known_warning_from_its_dropview_process(self):
        owner = {"pid": 42, "created": 100}
        for title, dialog_owner, expected in (
            (automator.WARNING_UNSAVED, owner, True),
            ("Some curves have not been saved elsewhere", owner, False),
            (automator.WARNING_UNSAVED, {"pid": 42, "created": 200}, False),
        ):
            with self.subTest(title=title, dialog_owner=dialog_owner), \
                    patch.object(automator, "window_exists", return_value=True), \
                    patch.object(automator, "find_window", return_value=20) as find, \
                    patch.object(automator.win32gui, "GetWindowText", return_value=title), \
                    patch.object(automator, "dropview_process_identity", return_value=dialog_owner), \
                    patch.object(automator, "_auto_dismiss_dialog", return_value=True) as dismiss:
                self.assertEqual(automator.acknowledge_discrete_unsaved_warning(owner), expected)
                self.assertEqual(find.call_args.kwargs["pid"], 42)
                self.assertEqual(dismiss.call_count, int(expected))

    def test_automatic_warning_acknowledgement_requires_verified_foreground(self):
        for foreground in (20, 30):
            with self.subTest(foreground=foreground), \
                    patch.object(automator, "_focus_window"), \
                    patch.object(automator, "dropview_process_identity", return_value={"pid": 42, "created": 100}), \
                    patch.object(automator.win32gui, "IsWindowVisible", return_value=True), \
                    patch.object(automator, "wait_for_window_close"), \
                    patch.object(automator, "_get_dialog_owner_info", return_value=("DropView", 10)), \
                    patch.object(automator.win32gui, "GetForegroundWindow", return_value=foreground), \
                    patch.object(automator.win32gui, "GetWindowText", return_value=automator.WARNING_UNSAVED), \
                    patch.object(automator.pyautogui, "press") as press:
                self.assertEqual(automator._auto_dismiss_dialog(20, automator.WARNING_UNSAVED), foreground == 20)
                self.assertEqual(press.call_count, int(foreground == 20))

    def test_dropview_identity_accepts_only_the_bundled_java_running_dropview_jar(self):
        install = Path(automator.DROPVIEW_EXE_CANDIDATES[0]).parent
        bundled = install / "jre" / "bin" / "javaw.exe"
        process = Mock()
        process.name.return_value = "javaw.exe"
        process.create_time.return_value = 100
        with patch.object(automator, "get_window_pid", return_value=42), \
                patch.object(automator.psutil, "Process", return_value=process):
            for executable, cwd, jar, expected in (
                (bundled, install, "dropview.jar", {"pid": 42, "created": 100}),
                (bundled, install, "dropviewremoteserver.jar", None),
                (bundled, install / "other", "dropview.jar", None),
                (bundled, install, str(install / "other" / "dropview.jar"), None),
                (install / "other" / "javaw.exe", install, "dropview.jar", None),
            ):
                with self.subTest(executable=executable, cwd=cwd, jar=jar):
                    process.exe.return_value = str(executable)
                    process.cwd.return_value = str(cwd)
                    process.cmdline.return_value = [str(executable), "-Xmx8192m", "-jar", jar]
                    self.assertEqual(automator.dropview_process_identity(42), expected)

    def capture_status(self, name, covered=False, minimized=False, scale=1.0, measurement=False,
                       pad_activity=None, pad_covered=False):
        width, height = (1000, 800) if measurement else (round(400 * scale), round(80 * scale))
        image = Image.new("RGB", (width, height), "#eeeeee")
        with Image.open(automator._IMG[name]) as template:
            image.paste(template, (2, height - template.height))
        if pad_activity:
            with Image.open(automator._IMG[pad_activity]) as template:
                image.paste(template, (110, height - template.height))
        pixels = np.array(image)
        bgra = np.concatenate((pixels[:, :, ::-1],
                               np.full((height, width, 1), 255, dtype=np.uint8)), axis=2)
        raw = SimpleNamespace(size=image.size, bgra=bgra.tobytes())
        screen = Mock()
        screen.monitors = [{"left": 0, "top": 0, "width": 3000, "height": 2000}]
        screen.grab.return_value = raw
        gui = Mock()
        gui.IsWindowVisible.return_value = True
        gui.IsIconic.return_value = minimized
        gui.GetClientRect.return_value = (0, 0, 1000, 800)
        gui.ClientToScreen.side_effect = lambda hwnd, point: (point[0] + 100, point[1] + 100)
        gui.WindowFromPoint.side_effect = lambda point: 99 if covered or (pad_covered and point[0] == 210) else 42
        gui.GetAncestor.side_effect = lambda hwnd, _: hwnd
        with patch.object(vision, "win32gui", gui), \
                patch.object(vision.ctypes.windll.user32, "GetDpiForWindow", return_value=int(96 * scale)), \
                patch.object(vision.mss, "mss") as capture:
            capture.return_value.__enter__.return_value = screen
            keys = ("yellow_dot_selected", "green_dot_selected") if measurement else ("connected", "disconnected")
            if pad_activity:
                keys += ("pad_status_run", "pad_status_stopped")
            result = vision.connection_scores_on_window(
                42, {key: automator._IMG[key] for key in keys}, full_client=measurement,
                optional_templates=("pad_status_run", "pad_status_stopped"))
        return result, screen

    def test_pad_activity_uses_status_row_and_original_process_without_focus(self):
        owner = {"pid": 42, "created": 100}
        for template, expected in (("pad_status_run", "running"), ("pad_status_stopped", "stopped")):
            with self.subTest(template=template):
                scores, screen = self.capture_status("connected", pad_activity=template)
                self.assertFalse(scores["error"])
                self.assertEqual(screen.grab.call_count, 1)
                scores.update(state="connected", dv_open=True)
                with patch.object(automator, "find_window", return_value=42) as find, \
                        patch.object(automator, "dropview_process_identity", return_value=owner), \
                        patch.object(automator, "dropview_responding", return_value=True), \
                        patch.object(automator, "_connection_snapshot", return_value=scores) as snapshot:
                    health = automator.observe_dropview(expected_owner=owner)
                    self.assertEqual((health["state"], health["pad_activity"]), ("connected", expected))
                    self.assertEqual(health["process_identity"], owner)
                    self.assertNotIn("image", health)
                    self.assertEqual(find.call_args.kwargs["pid"], 42)
                    snapshot.assert_called_once_with(hwnd=42, measurement_status=True)
                    scores[f"{template}_center_y"] -= 30
                    self.assertEqual(automator.observe_dropview(expected_owner=owner)["pad_activity"], "unknown")

    def test_covered_pad_indicator_keeps_connection_readable(self):
        scores, screen = self.capture_status("connected", pad_activity="pad_status_run", pad_covered=True)
        self.assertEqual(scores["error"], "")
        self.assertIn("covered", scores["optional_error"])
        self.assertGreater(scores["connected_score"], 0.9)
        self.assertEqual(screen.grab.call_count, 1)

    def test_measurement_templates_are_distinguishable_in_one_client_capture(self):
        for name in ("yellow_dot_selected", "green_dot_selected"):
            with self.subTest(name=name):
                result, screen = self.capture_status(name, measurement=True)
                self.assertFalse(result["error"])
                other = "green_dot_selected" if name == "yellow_dot_selected" else "yellow_dot_selected"
                self.assertGreater(result[f"{name}_score"] - result[f"{other}_score"], 0.08)
                screen.grab.assert_called_once_with({"left": 100, "top": 100, "width": 1000, "height": 800})

    def test_measurement_capture_rejects_covered_and_minimized_windows(self):
        for options in ({"covered": True}, {"minimized": True}):
            result, _ = self.capture_status("green_dot_selected", measurement=True, **options)
            self.assertTrue(result["error"])

    def test_measurement_state_requires_exclusive_visible_indicator(self):
        for running, stopped, error, expected in (
                (0.99, 0.5, "", "running"), (0.5, 0.99, "", "stopped"),
                (0.95, 0.94, "", "unknown"), (0.2, 0.5, "", "unknown"),
                (0.1, 0.99, "covered", "unknown")):
            with self.subTest(expected=expected), \
                    patch.object(automator, "window_exists", return_value=True), \
                    patch.object(automator, "find_window", return_value=42), \
                    patch.object(automator, "_focus_window"), \
                    patch.object(automator, "connection_scores_on_window", return_value={
                        "running_score": running, "stopped_score": stopped, "error": error}):
                self.assertEqual(automator.get_discrete_measurement_state()["state"], expected)

    def test_measurement_visibility_failure_focuses_once_then_reads_fresh_capture(self):
        for error in ("Measurement indicator is covered by another window.", "DropView is hidden or minimized."):
            with self.subTest(error=error), \
                    patch.object(automator, "window_exists", return_value=True), \
                    patch.object(automator, "find_window", return_value=42), \
                    patch.object(automator, "_focus_window") as focus, \
                    patch.object(automator, "connection_scores_on_window", side_effect=[
                        {"running_score": 0.1, "stopped_score": 0.99, "error": error},
                        {"running_score": 0.99, "stopped_score": 0.1, "error": ""}]) as capture:
                result = automator.get_discrete_measurement_state()
            focus.assert_called_once_with(42, sleep_after=automator.SLEEP_AFTER_FOCUS, require_foreground=False)
            self.assertEqual(capture.call_count, 2)
            self.assertEqual(result["state"], "running")
            self.assertEqual(result["error"], "")

    def test_readable_or_ambiguous_measurement_does_not_change_focus(self):
        for running, stopped, error in ((0.99, 0.1, ""), (0.94, 0.94, ""), (0, 0, "capture failed")):
            with self.subTest(error=error, running=running), \
                    patch.object(automator, "window_exists", return_value=True), \
                    patch.object(automator, "find_window", return_value=42), \
                    patch.object(automator, "_focus_window") as focus, \
                    patch.object(automator, "connection_scores_on_window", return_value={
                        "running_score": running, "stopped_score": stopped, "error": error}) as capture:
                automator.get_discrete_measurement_state()
            focus.assert_not_called()
            capture.assert_called_once()

    def test_failed_focus_or_still_covered_editor_remains_unknown(self):
        for focus_error in (None, RuntimeError("foreground denied")):
            with self.subTest(focus_error=focus_error), \
                    patch.object(automator, "window_exists", return_value=True), \
                    patch.object(automator, "find_window", return_value=42), \
                    patch.object(automator, "_focus_window", side_effect=focus_error) as focus, \
                    patch.object(automator, "connection_scores_on_window", return_value={
                        "running_score": 0.1, "stopped_score": 0.99, "error": "covered"}) as capture:
                result = automator.get_discrete_measurement_state()
            focus.assert_called_once()
            self.assertEqual(capture.call_count, 1 if focus_error else 2)
            self.assertEqual(result["state"], "unknown")
            self.assertIn("covered", result["error"])

    def test_focus_restores_minimized_editor_without_clicking(self):
        with patch.object(window_actions, "win32gui") as gui, \
                patch.object(window_actions, "_log_action"), \
                patch.object(automator.time, "sleep"), \
                patch.object(automator.pyautogui, "click") as click:
            gui.IsIconic.return_value = True
            gui.GetForegroundWindow.return_value = 42
            automator._focus_window(42, click_title=False, restore_if_iconic=True)
            gui.ShowWindow.assert_called_once_with(42, automator.win32con.SW_RESTORE)
            gui.SetForegroundWindow.assert_not_called()
            click.assert_not_called()

    def test_focus_exception_is_checked_against_the_actual_foreground_window(self):
        with patch.object(window_actions, "win32gui") as gui, \
                patch.object(window_actions, "_log_action"), \
                patch.object(window_actions.time, "sleep"), \
                patch.object(window_actions.pyautogui, "click") as click:
            gui.IsWindow.return_value = True
            gui.IsIconic.return_value = False
            gui.GetForegroundWindow.side_effect = [99, 42, 42]
            gui.SetForegroundWindow.side_effect = RuntimeError("foreground denied")
            self.assertTrue(window_actions.bring_to_front(42, retries=1))
            click.assert_not_called()

    def test_visible_stop_point_is_clickable_while_another_monitor_has_keyboard_focus(self):
        with patch.object(window_actions, "win32gui") as gui, \
                patch.object(window_actions, "_log_action"), \
                patch.object(window_actions, "bring_to_front") as focus, \
                patch.object(window_actions.pyautogui, "click") as click:
            gui.IsWindowEnabled.return_value = True
            gui.GetForegroundWindow.return_value = 99
            gui.WindowFromPoint.return_value = 41
            gui.GetAncestor.side_effect = [42, 99]
            window_actions.safe_click(42, -900, 450, retries=1, focus=False)
            with self.assertRaisesRegex(RuntimeError, "Click failed"):
                window_actions.safe_click(42, -900, 450, retries=1, focus=False)
            gui.WindowFromPoint.assert_called_with((-900, 450))
            click.assert_called_once_with(-900, 450)
            focus.assert_not_called()

    def test_keyboard_sequence_stops_when_target_loses_foreground(self):
        steps = [{"type": "hotkey", "keys": ("alt", "s")}, {"type": "press", "key": "s"}]
        with patch.object(window_actions, "bring_to_front", return_value=True), \
                patch.object(window_actions, "is_foreground", side_effect=[True, False]), \
                patch.object(window_actions, "_log_action"), \
                patch.object(window_actions.time, "sleep"), \
                patch.object(window_actions.pyautogui, "hotkey") as hotkey, \
                patch.object(window_actions.pyautogui, "press") as press:
            with self.assertRaisesRegex(RuntimeError, "lost keyboard focus"):
                window_actions.safe_sequence(42, steps, retries=1)
            hotkey.assert_called_once_with("alt", "s")
            press.assert_not_called()

    def test_invalid_window_cannot_supply_a_process_identity(self):
        for thread_id, pid in ((0, 123), (0, -123), (5, -123)):
            with self.subTest(thread_id=thread_id, pid=pid), \
                    patch.object(automator.win32process, "GetWindowThreadProcessId", return_value=(thread_id, pid)), \
                    patch.object(automator.psutil, "Process") as process:
                self.assertIsNone(automator.dropview_process_identity(42))
                self.assertIsNone(window_actions._get_window_pid(42))
                process.assert_not_called()

    def test_absent_editor_is_unknown_not_proof_of_stopped_device(self):
        with patch.object(automator, "window_exists", return_value=False):
            state = automator.get_discrete_measurement_state()
        self.assertEqual(state["state"], "unknown")
        self.assertTrue(state["editor_missing"])

    def test_hidden_editor_is_not_classified_as_missing(self):
        with patch.object(automator, "window_exists", side_effect=[False, True]):
            state = automator.get_discrete_measurement_state()
        self.assertEqual(state["state"], "unknown")
        self.assertFalse(state["editor_missing"])

    def test_reopen_discrete_editor_only_sends_menu_shortcut_to_original_process(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "find_window", return_value=10) as find, \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "dropview_responding", return_value=True), \
                patch.object(automator, "_connection_snapshot", return_value={"state": "connected"}), \
                patch.object(automator, "_find_ready_discrete_editor", side_effect=[(None, "unknown"), (20, "running")]), \
                patch.object(automator.win32gui, "IsWindowEnabled", return_value=True), \
                patch.object(automator.win32gui, "IsWindowVisible", return_value=True), \
                patch.object(automator, "find_owned_dialogs", return_value=[]), \
                patch.object(automator, "safe_wait_for_image"), \
                patch.object(automator, "safe_sequence") as keys, \
                patch.object(automator, "safe_click") as click, \
                patch.object(automator.time, "sleep"), \
                patch.object(automator, "bring_to_front", return_value=True) as focus:
            automator.reopen_discrete_editor(owner)
        self.assertEqual([call.kwargs["pid"] for call in find.call_args_list], [42])
        hwnd, steps = keys.call_args.args
        self.assertEqual(hwnd, 10)
        self.assertEqual([step["type"] for step in steps], ["hotkey", "press"])
        self.assertEqual(steps[0]["keys"], ("alt", "s"))
        self.assertEqual(steps[1]["key"], "s")
        click.assert_not_called()
        focus.assert_not_called()

    def test_hidden_editor_is_reopened_from_dropview_menu(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "verify_discrete_session_connection", return_value=10), \
                patch.object(automator, "_find_ready_discrete_editor", side_effect=[(None, "unknown"), (20, "running")]), \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator.win32gui, "IsWindowEnabled", return_value=True), \
                patch.object(automator, "find_owned_dialogs", return_value=[]), \
                patch.object(automator, "safe_wait_for_image"), \
                patch.object(automator.time, "sleep"), \
                patch.object(automator, "safe_sequence") as keys, \
                patch.object(automator, "bring_to_front", return_value=False):
            self.assertEqual(automator.reopen_discrete_editor(owner), 20)
        self.assertEqual(keys.call_args.args[0], 10)
        self.assertEqual(keys.call_args.args[1][0]["keys"], ("alt", "s"))

    def test_visible_editor_is_reused_without_sending_shortcut(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "verify_discrete_session_connection", return_value=10), \
                patch.object(automator, "_find_ready_discrete_editor", return_value=(20, "running")), \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "safe_sequence") as keys, \
                patch.object(automator, "bring_to_front", return_value=True):
            automator.reopen_discrete_editor(owner)
        keys.assert_not_called()

    def test_stop_ignores_blank_editor_and_verifies_only_the_selected_window(self):
        owner = {"pid": 42, "created": 100}
        observed = {20: ["unknown"], 21: ["running", "stopped"]}
        with patch.object(automator.win32gui, "EnumWindows", side_effect=lambda callback, _: [callback(h, None) for h in (20, 21)]), \
                patch.object(automator.win32gui, "IsWindowVisible", return_value=True), \
                patch.object(automator.win32gui, "GetWindowText", return_value=automator.MULTISCRIPT_WINDOW), \
                patch.object(automator, "get_window_pid", return_value=42), \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "get_discrete_measurement_state",
                             side_effect=lambda **kw: {"state": observed[kw["hwnd"]].pop(0)}), \
                patch.object(automator, "ensure_dialog_watchdog"), \
                patch.object(automator, "safe_find_on_screen", return_value=(10, 20)), \
                patch.object(automator, "safe_click") as click, \
                patch.object(automator, "safe_wait_for_image"), \
                patch.object(automator, "safe_wait_for_image_gone"), \
                patch.object(automator, "wait_for_window_close") as close:
            automator.step_stop_measure(require_editor=True, expected_owner=owner)
        self.assertEqual([call.args[0] for call in click.call_args_list], [21, 21])
        self.assertEqual(close.call_args.kwargs["hwnd"], 21)

    def test_planned_stop_reopens_editor_and_stops_the_same_window(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "_find_ready_discrete_editor", side_effect=[(None, "unknown"), (21, "running")]), \
                patch.object(automator, "verify_discrete_session_connection", return_value=10), \
                patch.object(automator, "_open_multiscript_menu") as reopen, \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "get_discrete_measurement_state",
                             return_value={"state": "stopped"}) as observe, \
                patch.object(automator, "ensure_dialog_watchdog"), \
                patch.object(automator, "safe_find_on_screen", return_value=(10, 20)) as find, \
                patch.object(automator, "safe_click") as click, \
                patch.object(automator, "safe_wait_for_image"), \
                patch.object(automator, "safe_wait_for_image_gone"), \
                patch.object(automator, "wait_for_window_close"):
            automator.step_stop_measure(require_editor=True, expected_owner=owner, reopen_if_needed=True)
        reopen.assert_called_once_with(10, log_fn=None, timeout=3, expected_owner=owner)
        observe.assert_called_once_with(expected_owner=owner, hwnd=21, refocus_if_covered=False)
        self.assertEqual(find.call_args_list[0].args[:2], (21, automator._IMG["stop_btn"]))
        self.assertEqual([call.args[0] for call in click.call_args_list], [21, 21])

    def test_planned_stop_reads_visible_editor_without_focus_or_opening_menu(self):
        owner = {"pid": 42, "created": 100}

        with patch.object(automator.win32gui, "EnumWindows", side_effect=lambda callback, _: callback(21, None)), \
                patch.object(automator.win32gui, "IsWindowVisible", return_value=True), \
                patch.object(automator.win32gui, "GetWindowText", return_value=automator.MULTISCRIPT_WINDOW), \
                patch.object(automator, "get_window_pid", return_value=42), \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "connection_scores_on_window", side_effect=[
                    {"running_score": 0.99, "stopped_score": 0.1, "error": ""},
                    {"running_score": 0.1, "stopped_score": 0.99, "error": ""},
                ]) as capture, \
                patch.object(automator, "_focus_window") as focus, \
                patch.object(automator, "_open_multiscript_menu") as reopen, \
                patch.object(automator, "ensure_dialog_watchdog"), \
                patch.object(automator, "safe_find_on_screen", return_value=(10, 20)) as find, \
                patch.object(automator, "safe_click") as click, \
                patch.object(automator, "safe_wait_for_image"), \
                patch.object(automator, "safe_wait_for_image_gone"), \
                patch.object(automator, "wait_for_window_close"):
            automator.step_stop_measure(require_editor=True, expected_owner=owner, reopen_if_needed=True)

        focus.assert_not_called()
        self.assertEqual([read.args[0] for read in capture.call_args_list], [21, 21])
        self.assertTrue(all(not action.kwargs["focus"] for action in click.call_args_list))
        self.assertEqual(find.call_args_list[0].args[:2], (21, automator._IMG["stop_btn"]))
        reopen.assert_not_called()

    def test_reopen_rejects_reused_dropview_pid_before_sending_keys(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "find_window", return_value=10), \
                patch.object(automator, "dropview_process_identity", return_value={"pid": 42, "created": 200}), \
                patch.object(automator, "safe_sequence") as keys:
            with self.assertRaisesRegex(RuntimeError, "original DropView process"):
                automator.reopen_discrete_editor(owner)
        keys.assert_not_called()

    def test_recovery_relaunch_does_not_use_reused_pid(self):
        previous = {"pid": 42, "created": 100}
        process = Mock()
        process.create_time.return_value = 200
        with patch.object(automator, "window_exists", return_value=False), \
                patch.object(automator.psutil, "Process", return_value=process), \
                patch.object(automator, "_get_dropview_pid", return_value=None), \
                patch.object(automator, "step_launch_dropview") as launch, \
                patch.object(automator, "step_connect_dropsens") as connect, \
                patch.object(automator, "_verify_dropview_connection") as verify:
            self.assertFalse(automator.restore_dropview_control(previous))
        process.terminate.assert_not_called()
        launch.assert_called_once()
        connect.assert_called_once()
        verify.assert_called_once()

    def test_hung_bundled_java_dropview_is_closed_before_forced_termination(self):
        owner = {"pid": 42, "created": 100}
        install = Path(automator.DROPVIEW_EXE_CANDIDATES[0]).parent
        process = Mock()
        process.name.return_value = "javaw.exe"
        process.exe.return_value = str(install / "jre" / "bin" / "javaw.exe")
        process.cwd.return_value = str(install)
        process.cmdline.return_value = ["javaw.exe", "-jar", "dropview.jar"]
        process.create_time.return_value = owner["created"]
        process.is_running.return_value = True
        process.wait.side_effect = [automator.psutil.TimeoutExpired(5), None]
        order = Mock()
        order.attach_mock(process.wait, "wait")
        order.attach_mock(process.terminate, "terminate")
        with patch.object(automator.psutil, "Process", return_value=process), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "get_window_pid", return_value=42), \
                patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                patch.object(automator, "dropview_responding", return_value=False), \
                patch.object(automator.win32gui, "PostMessage", order.close):
            automator.stop_unresponsive_dropview(owner, cancelled=lambda: False)

        self.assertEqual(order.mock_calls, [call.close(21, automator.win32con.WM_CLOSE, 0, 0),
                                            call.wait(timeout=5), call.terminate(), call.wait(timeout=5)])

    def test_cleanup_rechecks_creation_time_before_forced_termination(self):
        owner = {"pid": 42, "created": 100}
        original, replacement = Mock(), Mock()
        original.name.return_value = "DropView.exe"
        original.create_time.return_value = 100
        original.wait.side_effect = automator.psutil.TimeoutExpired(5)
        replacement.create_time.return_value = 200
        with patch.object(automator.psutil, "Process", side_effect=[original, replacement]), \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator.win32gui, "PostMessage") as close:
            automator._close_verified_dropview(owner, 21, allow_terminate=True)
        close.assert_called_once_with(21, automator.win32con.WM_CLOSE, 0, 0)
        original.terminate.assert_not_called()
        replacement.terminate.assert_not_called()

    def test_recovery_startup_leaves_a_blocked_existing_process_untouched(self):
        owner = {"pid": 42, "created": 100}
        with patch.object(automator, "ensure_dialog_watchdog"), \
                patch.object(automator, "window_exists", return_value=True), \
                patch.object(automator, "find_window", return_value=21), \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator.win32gui, "IsWindowEnabled", return_value=False), \
                patch.object(automator, "_close_verified_dropview") as close, \
                patch.object(automator, "step_launch_dropview") as launch:
            with self.assertRaisesRegex(RuntimeError, "blocked during recovery startup"):
                automator.step_start_dropview({}, allow_cleanup=False)
        close.assert_not_called()
        launch.assert_not_called()

    def test_hung_dropview_is_not_terminated_if_it_responds_again_or_stop_is_requested(self):
        owner = {"pid": 42, "created": 100}
        for responses, cancelled, error in (
            ([False, True], [False, False, False], "no longer confirmed unresponsive"),
            ([False], [False, False, True], "cancelled"),
        ):
            with self.subTest(error=error):
                process = Mock()
                process.name.return_value = "DropView.exe"
                process.create_time.return_value = owner["created"]
                process.wait.side_effect = automator.psutil.TimeoutExpired(5)
                with patch.object(automator.psutil, "Process", return_value=process), \
                        patch.object(automator, "find_window", return_value=21), \
                        patch.object(automator, "dropview_process_identity", return_value=owner), \
                        patch.object(automator, "acknowledge_discrete_unsaved_warning", return_value=False), \
                        patch.object(automator, "dropview_responding", side_effect=responses), \
                        patch.object(automator.win32gui, "PostMessage"):
                    with self.assertRaisesRegex(RuntimeError, error):
                        automator.stop_unresponsive_dropview(owner, cancelled=Mock(side_effect=cancelled))
                process.terminate.assert_not_called()

    def test_readable_editor_capture_does_not_require_foreground(self):
        calls = Mock()
        calls.capture.side_effect = [
            {"connected_score": 0.1, "disconnected_score": 0.99, "error": ""},
            {"running_score": 0.1, "stopped_score": 0.99, "error": ""},
        ]
        with patch.object(automator, "window_exists", return_value=True), \
                patch.object(automator, "find_window", side_effect=[42, 43]), \
                patch.object(automator, "_focus_window", calls.focus), \
                patch.object(automator, "connection_scores_on_window", calls.capture):
            self.assertEqual(automator.get_dropview_connection_scores(lambda: True)["state"], "disconnected")
            self.assertEqual(automator.get_discrete_measurement_state()["state"], "stopped")
        self.assertEqual([(call[0], call.args[0]) for call in calls.mock_calls],
                         [("focus", 42), ("capture", 42), ("capture", 43)])

    def test_connection_read_does_not_focus_outside_active_measurement(self):
        with patch.object(automator, "window_exists", return_value=True), \
                patch.object(automator, "find_window", return_value=42), \
                patch.object(automator, "_focus_window") as focus, \
                patch.object(automator, "connection_scores_on_window", return_value={
                    "connected_score": 0.99, "disconnected_score": 0.1, "error": ""}):
            self.assertEqual(automator.get_dropview_connection_scores(lambda: False)["state"], "connected")
        focus.assert_not_called()

    def test_error_dialog_visual_fallback_reads_after_focus_and_before_dismissal(self):
        actions = Mock()
        actions.classify.return_value = "no_device_connected"
        with patch.object(automator, "window_exists", side_effect=[False, True]), \
                patch.object(automator, "find_window", return_value=42), \
                patch.object(automator, "_read_error_window_text", return_value=""), \
                patch.object(automator, "_focus_window", actions.focus), \
                patch.object(automator, "_classify_error_dialog_by_template", actions.classify), \
                patch.object(automator.win32gui, "GetForegroundWindow", return_value=42), \
                patch.object(automator.pyautogui, "press", actions.press), \
                patch.object(automator.time, "sleep"):
            self.assertEqual(automator._wait_for_connection_result(), "no_device_connected")
        self.assertEqual([call[0] for call in actions.mock_calls], ["focus", "classify", "press"])

    def test_already_stopped_editor_is_verified_and_closed_without_disabled_stop_click(self):
        with patch.object(automator, "ensure_dialog_watchdog"), \
                patch.object(automator, "window_exists", return_value=True), \
                patch.object(automator, "find_window", return_value=42), \
                patch.object(automator, "_focus_window"), \
                patch.object(automator, "get_discrete_measurement_state", return_value={"state": "stopped"}), \
                patch.object(automator, "safe_find_on_screen", return_value=(10, 20)) as find, \
                patch.object(automator, "safe_click") as click, \
                patch.object(automator, "safe_wait_for_image_gone") as yellow_gone, \
                patch.object(automator, "safe_wait_for_image") as green, \
                patch.object(automator, "wait_for_window_close") as closed:
            automator.step_stop_measure()
        self.assertEqual(find.call_count, 1)
        self.assertEqual(find.call_args.args[1], automator._IMG["exit_btn"])
        click.assert_called_once()
        yellow_gone.assert_called_once()
        green.assert_called_once()
        closed.assert_called_once()

    def test_status_templates_use_one_bottom_left_capture(self):
        for name in ("connected", "disconnected"):
            with self.subTest(name=name):
                result, screen = self.capture_status(name)
                self.assertFalse(result["error"])
                self.assertGreater(result[f"{name}_score"], 0.99)
                other = "disconnected" if name == "connected" else "connected"
                self.assertGreater(result[f"{name}_score"] - result[f"{other}_score"], 0.08)
                screen.grab.assert_called_once_with({
                    "left": 100, "top": 820, "width": 400, "height": 80})

    def test_dpi_scaled_region_stays_inside_client(self):
        result, screen = self.capture_status("connected", scale=1.5)
        self.assertFalse(result["error"])
        screen.grab.assert_called_once_with({
            "left": 100, "top": 780, "width": 600, "height": 120})

    def test_covered_region_keeps_diagnostic_image(self):
        result, _ = self.capture_status("connected", covered=True)
        self.assertIn("covered", result["error"])
        self.assertIsNotNone(result["image"])

    def test_minimized_window_is_not_captured(self):
        result, screen = self.capture_status("connected", minimized=True)
        self.assertIn("minimized", result["error"])
        screen.grab.assert_not_called()

    def test_three_states_and_capture_errors(self):
        for conn, disc, error, expected in (
                (0.95, 0.6, "", "connected"),
                (0.6, 0.95, "", "disconnected"),
                (0.95, 0.94, "", "unknown"),
                (0.2, 0.1, "", "unknown"),
                (0.95, 0.1, "capture failed", "unknown")):
            with self.subTest(expected=expected, conn=conn, error=error), \
                    patch.object(automator, "window_exists", return_value=True), \
                    patch.object(automator, "find_window", return_value=42), \
                    patch.object(automator, "connection_scores_on_window", return_value={
                        "connected_score": conn, "disconnected_score": disc, "error": error}):
                self.assertEqual(automator._connection_snapshot()["state"], expected)

    def verify_sequence(self, states, connecting=False, cost=0):
        clock = [0.0]
        states = iter(states)
        last = ["unknown"]

        def snapshot():
            clock[0] += cost
            last[0] = next(states, last[0])
            return {"state": last[0], "connected_score": 0.9,
                    "disconnected_score": 0.5}

        def sleep(seconds):
            clock[0] += seconds

        with patch.object(automator.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(automator.time, "sleep", side_effect=sleep), \
                patch.object(automator, "window_exists", return_value=connecting), \
                patch.object(automator, "_connection_snapshot", side_effect=snapshot), \
                patch.object(automator, "_save_connection_failure") as save:
            try:
                automator._verify_dropview_connection()
                return clock[0], save.call_count, None
            except RuntimeError as exc:
                return clock[0], save.call_count, str(exc)

    def test_stable_connection_finishes_early(self):
        elapsed, saves, error = self.verify_sequence(["connected", "connected"])
        self.assertAlmostEqual(elapsed, 0.2)
        self.assertEqual(saves, 0)
        self.assertIsNone(error)

    def test_unknown_resets_stability(self):
        elapsed, _, error = self.verify_sequence([
            "connected", "unknown", "connected", "connected"])
        self.assertAlmostEqual(elapsed, 0.6)
        self.assertIsNone(error)

    def test_unknown_or_disconnected_times_out_at_five_seconds(self):
        for state in ("unknown", "disconnected"):
            elapsed, saves, error = self.verify_sequence([state])
            self.assertAlmostEqual(elapsed, 5.0)
            self.assertEqual(saves, 1)
            self.assertIn("within 5.0s", error)

    def test_connecting_dialog_prevents_success(self):
        _, saves, error = self.verify_sequence(["connected"], connecting=True)
        self.assertEqual(saves, 1)
        self.assertIsNotNone(error)

    def test_slow_capture_cannot_pass_after_deadline(self):
        _, saves, error = self.verify_sequence(["connected"], cost=3.0)
        self.assertEqual(saves, 1)
        self.assertIsNotNone(error)

    def test_failure_image_saved_beside_debug_log(self):
        image = Mock()
        with patch.object(automator, "_debug_log_path", return_value="logs/bench_test_debug.log"), \
                patch.object(automator.os, "makedirs"), \
                patch.object(automator.time, "time_ns", return_value=123):
            automator._save_connection_failure({"image": image})
        saved = Path(image.save.call_args.args[0])
        self.assertEqual(saved, Path("logs/dropview_diagnostics/connection_failure_123.png"))

    def test_missing_windows_ports_are_skipped(self):
        with patch.object(automator.list_ports, "comports", return_value=[SimpleNamespace(device="COM4")]), \
                patch.object(automator, "get_value", return_value="COM10"):
            self.assertEqual(automator._resolve_target_dropsens_ports("COM3"), ["COM4"])

    def test_disappeared_manual_port_never_opens_a_dialog(self):
        with patch.object(automator.list_ports, "comports", return_value=[]), \
                patch.object(automator, "safe_sequence") as sequence:
            self.assertEqual(automator._connect_manual(42, "COM3"), "port_unavailable")
            sequence.assert_not_called()

    def test_named_connection_failure_never_falls_back_to_another_device(self):
        with patch.object(automator, "_connection_snapshot", return_value={"state": "disconnected"}), \
                patch.object(automator, "_focus_window"), \
                patch.object(automator, "get_window_pid", return_value=0), \
                patch.object(automator, "_connect_manual", return_value="no_device_connected") as connect, \
                patch.object(automator, "_resolve_target_dropsens_ports") as other_ports, \
                patch.object(automator, "safe_sequence") as automatic:
            with self.assertRaisesRegex(RuntimeError, "COM9.*no_device_connected"):
                automator.step_connect_dropsens(target_com="COM9", hwnd=42, exact_port=True)
        connect.assert_called_once_with(42, "COM9", log_fn=None, exact_port=True)
        other_ports.assert_not_called()
        automatic.assert_not_called()

    def test_named_connection_verifies_the_collapsed_port_before_connecting(self):
        for selected, expected in (("COM7", "port_not_selectable"), ("COM9", "connected")):
            with self.subTest(selected=selected), \
                    patch.object(automator.list_ports, "comports", return_value=[SimpleNamespace(device="COM9")]), \
                    patch.object(automator, "safe_sequence"), \
                    patch.object(automator, "safe_click"), \
                    patch.object(automator, "find_window", return_value=20), \
                    patch.object(automator, "get_window_pid", return_value=42), \
                    patch.object(automator, "find_on_screen", side_effect=[(100, 100), (150, 180)]), \
                    patch.object(automator.win32gui, "WindowFromPoint", side_effect=[20, 21, 21, 20, 20]), \
                    patch.object(automator.win32gui, "GetAncestor", side_effect=lambda hwnd, _: hwnd), \
                    patch.object(automator.win32gui, "GetWindow", return_value=20), \
                    patch.object(automator.win32gui, "GetWindowRect", return_value=(40, 115, 110, 155)), \
                    patch.object(automator.win32gui, "IsWindowVisible", side_effect=[True, False]):
                self._check_selected_port(selected, expected)

    def _check_selected_port(self, selected, expected):
        with patch.object(automator.ctypes.windll.user32, "GetDpiForWindow", return_value=96), \
                    patch.object(automator, "_grab_region", return_value=Image.new("RGB", (80, 18))), \
                    patch.object(automator, "_images_equal", return_value=True), \
                    patch.object(automator, "read_label_positions",
                                 side_effect=[[("cotv17", 20, 9), ("cotv19", 20, 23)], [(selected, 15, 9)]]), \
                    patch.object(automator.pyautogui, "moveTo"), \
                    patch.object(automator.pyautogui, "click") as click, \
                    patch.object(automator.time, "sleep"), \
                    patch.object(automator, "wait_for_window_close"), \
                    patch.object(automator, "_wait_for_connection_result", return_value="connected"):
            self.assertEqual(automator._connect_manual(42, "COM9", exact_port=True), expected)
            expected_clicks = [call(60, 138)]
            if expected == "connected":
                expected_clicks.append(call(150, 180))
            self.assertEqual(click.call_args_list, expected_clicks)

    def test_named_connection_resolves_the_current_port_from_device_identity(self):
        controller = Mock()
        with patch("bench_test.dropview.controller.list_dropsens_devices",
                   return_value=[("A", "UST1", "COM10"), ("B", "UST2", "COM9")]), \
                patch("bench_test.dropview.controller.threading.Thread",
                      side_effect=lambda target, daemon: SimpleNamespace(start=target)), \
                patch.object(automator, "step_connect_dropsens") as connect:
            DropViewController.do_connect_dropsens(controller, com_port="COM3", device_address="B")
        connect.assert_called_once_with(target_com="COM9", log_fn=None, exact_port=True)
        controller.action_done.emit.assert_called_once_with("connect_named", True)

    def test_saved_automatic_route_still_tries_last_manual_port_first(self):
        settings = {"dropsens_connection_route": {"kind": "usb_auto"},
                    "dropsens_last_successful_port": "COM10", "dropsens_com": "COM3"}
        ports = [SimpleNamespace(device="COM3"), SimpleNamespace(device="COM10")]
        order = Mock()
        order.manual.return_value = "no_device_connected"
        with patch.object(automator, "get_value", side_effect=settings.get), \
                patch.object(automator.list_ports, "comports", return_value=ports), \
                patch.object(automator, "window_exists", side_effect=lambda title, **_: title == automator.DROPVIEW_WINDOW_NAME), \
                patch.object(automator, "find_window", return_value=42), \
                patch.object(automator, "_is_dropview_connected", return_value=False), \
                patch.object(automator, "_focus_window"), \
                patch.object(automator, "_connect_manual", order.manual), \
                patch.object(automator, "safe_sequence", order.automatic), \
                patch.object(automator, "_wait_for_connection_result", return_value="connected"), \
                patch.object(automator, "_verify_dropview_connection", side_effect=RuntimeError("unverified")), \
                patch.object(automator, "remember_value") as remember:
            with self.assertRaisesRegex(RuntimeError, "unverified"):
                automator.step_connect_dropsens()
        self.assertEqual(order.mock_calls[:2], [call.manual(42, "COM10", log_fn=None),
                                                call.manual(42, "COM3", log_fn=None)])
        self.assertEqual(order.automatic.call_args.args[1][0]["keys"], ("ctrl", "c"))
        remember.assert_not_called()

    def test_reconnect_targets_the_original_dropview_window(self):
        owner = {"pid": 42, "created": 100}
        controller = Mock()
        with patch.object(automator, "find_window", return_value=21) as find, \
                patch.object(automator, "dropview_process_identity", return_value=owner), \
                patch.object(automator, "dropview_responding", return_value=True), \
                patch.object(automator, "dismiss_connection_error_dialogs"), \
                patch.object(automator, "step_stop_measure") as stop, \
                patch.object(automator, "step_connect_dropsens") as connect:
            self.assertTrue(DropViewController.do_reconnect_dropsens(controller, owner))
        find.assert_called_once_with(automator.DROPVIEW_WINDOW_NAME, timeout=3, pid=42)
        connect.assert_called_once_with(log_fn=None, hwnd=21, force_reconnect=True)
        stop.assert_called_once_with(log_fn=None, require_editor=True, expected_owner=owner, reopen_if_needed=True)
        controller.status_changed.emit.assert_called_once_with(True)

    def test_polling_does_not_report_unknown_as_disconnected(self):
        controller = DropViewController()
        controller._last_connected = True
        changed = Mock()
        controller.status_changed.connect(changed)
        for poll in (controller._poll_connection, controller.force_poll):
            with self.subTest(poll=poll.__name__), \
                    patch.object(automator, "dismiss_connection_error_dialogs") as dismiss, \
                    patch.object(automator, "observe_dropview", return_value={
                        "state": "unknown", "dv_open": True, "process_identity": None}), \
                    patch("bench_test.dropview.controller.threading.Thread") as thread:
                thread.side_effect = lambda target, **kw: SimpleNamespace(start=target)
                poll()
                dismiss.assert_called_once_with()
                changed.assert_not_called()
                self.assertTrue(controller._last_connected)

    def test_runner_reuses_only_fresh_pad_sample_for_original_process(self):
        controller = DropViewController()
        owner = {"pid": 42, "created": 100}
        controller.discrete_measurement_owner = owner
        controller.health_focus_requested = lambda: True
        sample = {"state": "connected", "dv_open": True, "process_identity": owner,
                  "sampled_at": 100, "pad_observed": True, "pad_activity": "running"}
        with patch.object(automator, "dismiss_connection_error_dialogs"), \
                patch.object(automator, "observe_dropview", side_effect=[sample, {**sample, "sampled_at": 102}]) as read, \
                patch("bench_test.dropview.controller.time.monotonic", return_value=102), \
                patch("bench_test.dropview.controller.threading.Thread",
                      side_effect=lambda target, **_: SimpleNamespace(start=target)):
            controller._poll_connection()
            self.assertEqual(controller.observe_discrete_pad_activity(owner, since=99, max_age=3), "running")
            self.assertEqual(read.call_count, 1)
            self.assertEqual(controller.observe_discrete_pad_activity(owner, since=101, max_age=3), "running")
            self.assertEqual(read.call_count, 2)
            read.assert_called_with(expected_owner=owner, should_focus=None)


if __name__ == "__main__":
    unittest.main()
