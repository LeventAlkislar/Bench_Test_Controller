"""Pump simulation consent and execution tests; no device commands or UI dialogs."""
import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QMessageBox
from bench_test.recipe.models import Recipe, RecipeStep
from bench_test.recipe.runner import RecipeRunner
from bench_test.ui.tabs.recipe_tab import RecipeTab


class PumpSimulationTests(unittest.TestCase):
    def make_runner(self, pump=None, **kwargs):
        return RecipeRunner(
            None, None, Recipe("Simulation", []), queue.Queue(), threading.Event(),
            pump_ctrl=pump, **kwargs,
        )

    def make_tab(self, rpm=50, pump_connected=False, valves=(True, True)):
        tab = Mock()
        tab.recipe_runner = None
        tab.recipe_steps = [RecipeStep(0, 0, pump_rpm=rpm)]
        tab.step_loops = []
        tab.ctrl_a.is_connected.return_value = valves[0]
        tab.ctrl_b.is_connected.return_value = valves[1]
        tab.pump_ctrl.is_connected.return_value = pump_connected
        tab.package_tab = None
        tab.watchdog = None
        tab.name_edit.text.return_value = "Simulation"
        tab.loop_spin.value.return_value = 1
        tab._main_window.manual_tab.port_dv.currentText.return_value = ""
        return tab

    def test_disconnected_pump_fails_in_normal_mode(self):
        runner = self.make_runner()
        self.assertFalse(runner._apply_pump_rpm(RecipeStep(0, 0, pump_rpm=50), 1))
        self.assertEqual(runner.status_queue.get()[0], "error")

    def test_approved_pump_commands_are_logged_without_device_calls(self):
        pump = Mock()
        runner = self.make_runner(pump, simulation_mode=True)
        for rpm, target in ((50, "50 RPM"), (0, "Stop")):
            with self.subTest(rpm=rpm):
                self.assertTrue(runner._apply_pump_rpm(RecipeStep(0, 0, pump_rpm=rpm), 1))
                kind, message = runner.status_queue.get()
                self.assertEqual(kind, "log")
                self.assertIn(f"SIMULATED: Pump -> {target}", message)
        runner._safe_stop_pump()
        self.assertEqual(pump.mock_calls, [])

    def test_connected_pump_still_runs_and_stops(self):
        pump = Mock()
        pump.is_connected.return_value = True
        pump.set_speed_rpm.return_value = []
        pump.run.return_value = []
        pump.stop.return_value = []
        runner = self.make_runner(pump)
        self.assertTrue(runner._apply_pump_rpm(RecipeStep(0, 0, pump_rpm=50), 1))
        pump.set_speed_rpm.assert_called_with(50)
        pump.run.assert_called_once_with("FWD")
        self.assertTrue(runner._apply_pump_rpm(RecipeStep(0, 0, pump_rpm=0), 2))
        pump.stop.assert_called_once_with()
        pump.set_speed_rpm.assert_called_with(0)

    def test_declining_simulation_does_not_start_recipe(self):
        tab = self.make_tab()
        with patch("bench_test.ui.tabs.recipe_tab.QMessageBox.warning",
                   return_value=QMessageBox.StandardButton.No) as warning, \
                patch("bench_test.ui.tabs.recipe_tab.RecipeRunner") as runner:
            RecipeTab._start_recipe(tab)
        runner.assert_not_called()
        self.assertEqual(warning.call_args.args[-1], QMessageBox.StandardButton.No)

    def test_approved_start_handles_pump_and_valve_combinations(self):
        for pump_connected, valves, missing in (
            (False, (True, True), ["Pump"]),
            (True, (False, True), ["Valve A"]),
            (True, (True, False), ["Valve B"]),
            (False, (False, False), ["Pump", "Valve A", "Valve B"]),
        ):
            with self.subTest(missing=missing):
                tab = self.make_tab(pump_connected=pump_connected, valves=valves)
                with patch("bench_test.ui.tabs.recipe_tab.QMessageBox.warning",
                           return_value=QMessageBox.StandardButton.Yes) as warning, \
                        patch("bench_test.ui.tabs.recipe_tab.RecipeRunner") as runner:
                    RecipeTab._start_recipe(tab)
                warning.assert_called_once()
                self.assertEqual(warning.call_args.args[2].split("\n\n", 1)[0].splitlines()[1:], missing)
                self.assertTrue(runner.call_args.kwargs["simulation_mode"])
                runner.return_value.start.assert_called_once_with()
                tab._main_window.set_simulation_mode.assert_called_once_with(True)

    def test_missing_pump_requires_consent_even_without_a_pump_step(self):
        tab = self.make_tab(rpm=None)
        with patch("bench_test.ui.tabs.recipe_tab.QMessageBox.warning", return_value=QMessageBox.StandardButton.No) as warning, \
                patch("bench_test.ui.tabs.recipe_tab.RecipeRunner") as runner:
            RecipeTab._start_recipe(tab)
        warning.assert_called_once()
        self.assertIn("Pump", warning.call_args.args[2])
        runner.assert_not_called()

    def test_all_devices_connected_starts_normal_mode_without_consent(self):
        tab = self.make_tab(pump_connected=True)
        with patch("bench_test.ui.tabs.recipe_tab.QMessageBox.warning") as warning, \
                patch("bench_test.ui.tabs.recipe_tab.RecipeRunner") as runner:
            RecipeTab._start_recipe(tab)
        warning.assert_not_called()
        self.assertFalse(runner.call_args.kwargs["simulation_mode"])

    def test_simulation_skips_all_hardware_but_executes_dropview_action(self):
        valve_a, valve_b, pump, dropview = Mock(), Mock(), Mock(), Mock()
        dropview.do_start_dropview.return_value = True
        step = RecipeStep(1, 0, valve_b_state=1, pump_rpm=20, dropview_action="start_dropview")
        runner = RecipeRunner(valve_a, valve_b, Recipe("Simulation", [step]), queue.Queue(), threading.Event(),
                              dropview_ctrl=dropview, pump_ctrl=pump, simulation_mode=True)

        self.assertTrue(runner._execute_step(step, 1, 1))
        self.assertEqual(valve_a.mock_calls + valve_b.mock_calls + pump.mock_calls, [])
        dropview.do_start_dropview.assert_called_once()


if __name__ == "__main__":
    unittest.main()
