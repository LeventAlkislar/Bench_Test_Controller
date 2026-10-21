"""Connection regression checks; no hardware or application UI is operated."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.dropview import automator, vision
from bench_test.dropview.controller import DropViewController


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.logging = patch.object(automator, "debug_log")
        self.logging.start()
        self.addCleanup(self.logging.stop)

    def capture_status(self, name, covered=False, minimized=False, scale=1.0):
        width, height = round(400 * scale), round(80 * scale)
        image = Image.new("RGB", (width, height), "#eeeeee")
        with Image.open(automator._IMG[name]) as template:
            image.paste(template, (2, height - template.height))
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
        gui.GetAncestor.return_value = 99 if covered else 42
        with patch.object(vision, "win32gui", gui), \
                patch.object(vision.ctypes.windll.user32, "GetDpiForWindow", return_value=int(96 * scale)), \
                patch.object(vision.mss, "mss") as capture:
            capture.return_value.__enter__.return_value = screen
            result = vision.connection_scores_on_window(42, {
                key: automator._IMG[key] for key in ("connected", "disconnected")
            })
        return result, screen

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

    def test_polling_does_not_report_unknown_as_disconnected(self):
        controller = Mock()
        controller._last_connected = True
        for poll in (DropViewController._poll_connection, DropViewController.force_poll):
            with self.subTest(poll=poll.__name__), \
                    patch.object(automator, "get_dropview_connection_scores", return_value={"state": "unknown"}), \
                    patch("bench_test.dropview.controller.threading.Thread") as thread:
                thread.side_effect = lambda target, **kw: SimpleNamespace(start=target)
                poll(controller)
                controller.status_changed.emit.assert_not_called()
                self.assertTrue(controller._last_connected)


if __name__ == "__main__":
    unittest.main()
