# bench_test/recipe/runner.py
import json
import queue
import threading
import time
from typing import Optional

from bench_test.recipe.models import Recipe, RecipeStep
from bench_test.dropview.controller import DropViewController
from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController

# DropView aksiyon sabitleri
DROPVIEW_ACTIONS = ["none", "start_dropview", "start_measure", "stop_measure", "exit_dropview"]
DROPVIEW_LABELS  = {
    "none":           "None",
    "start_dropview": "Start DropView",
    "start_measure":  "Start Measure",
    "stop_measure":   "Stop Measure",
    "exit_dropview":  "Exit DropView",
}
DROPVIEW_ZERO_DURATION_OK = {"start_dropview", "start_measure", "stop_measure", "exit_dropview"}


class RecipeRunner(threading.Thread):
    def __init__(self, controller_a, controller_b, recipe: Recipe,
                 status_queue: queue.Queue, stop_event: threading.Event,
                 dropview_ctrl: Optional[DropViewController] = None,
                 session_scr_path: str = "",
                 simulation_mode: bool = False):
        super().__init__(daemon=True)
        self.controller_a     = controller_a
        self.controller_b     = controller_b
        self.recipe           = recipe
        self.status_queue     = status_queue
        self.stop_event       = stop_event
        self.dropview_ctrl    = dropview_ctrl
        self.session_scr_path = session_scr_path
        self.simulation_mode  = simulation_mode
        self.pause_event      = threading.Event()
        self.pause_event.set()
        self.recipe_start_t   = None
        self.current_loop_index = 0
        self.current_loop_count = 0
        self.measurement_running = False

    def pause(self):
        self.pause_event.clear()

    def resume(self):
        self.pause_event.set()

    def _cleanup_dropview(self, step_num: int):
        """Best-effort cleanup after a failed DropView step."""
        if self.dropview_ctrl is None:
            return
        try:
            from bench_test.dropview import automator
            if automator.window_exists(automator.DROPVIEW_WINDOW_NAME):
                self._log(f"| Cleaning up DropView after step {step_num} error...")
                ok = self.dropview_ctrl.do_exit_dropview(log_fn=self._log)
                if ok:
                    self._log("| DropView cleanup completed.")
                else:
                    self._log("| WARNING: DropView cleanup was attempted but returned failure.")
        except Exception as e:
            self._log(f"| WARNING: DropView cleanup failed: {e}")

    def _log(self, msg):
        self.status_queue.put(("log", msg))

    def _check_dropview_connected(self) -> bool:
        if self.dropview_ctrl is None:
            return True
        try:
            from bench_test.dropview import automator
            return automator._is_dropview_connected()
        except Exception:
            return False

    def _is_measurement_window_open(self) -> bool:
        try:
            from bench_test.dropview import automator
            return automator.window_exists(automator.MULTISCRIPT_WINDOW)
        except Exception:
            return False

    def _stop_measurement_for_recipe_stop(self) -> bool:
        if self.dropview_ctrl is None:
            return True
        if not self.measurement_running and not self._is_measurement_window_open():
            return True

        ok = self.dropview_ctrl.do_stop_measure(log_fn=self._log)
        if ok:
            self.measurement_running = False
            return True

        self.status_queue.put((
            "stop_failed",
            "Recipe stop requested, but DropSens measurement could not be stopped. "
            "Session was not finalized; stop the measurement manually or retry Stop Recipe."
        ))
        return False

    def _execute_step(self, step: RecipeStep, step_num, total_steps, loop_info=""):
        if self.stop_event.is_set():
            return False

        action = step.dropview_action
        dv     = self.dropview_ctrl

        # ── Valf A ────────────────────────────────────────────
        if self.stop_event.is_set():
            return False

        if step.port > 0 and self.controller_a and self.controller_a.is_connected():
            self.status_queue.put(("switching", f"{loop_info}Valve A -> Port {step.port}"))
            r = self.controller_a.switch_port(step.port)
            if not r.get("success"):
                err = r.get("error", "")
                if "timeout" not in err.lower():
                    self.status_queue.put(("error", f"Valve A port switch failed: {err}"))
                    return False
            time.sleep(0.3)
            self.controller_a.wait_for_completion(timeout=20.0)
        elif step.port > 0 and self.simulation_mode:
            self._log(f"SIMULATED: Valve A -> Port {step.port} (not connected, skipped)")

        # ── Valf B ────────────────────────────────────────────
        if step.valve_b_state > 0 and self.controller_b and self.controller_b.is_connected():
            state_name = InjectorValveController.STATE_NAMES.get(step.valve_b_state, "")
            self.status_queue.put(("switching", f"{loop_info}Valve B -> {state_name}"))
            r = self.controller_b.switch_state(step.valve_b_state)
            if not r.get("success"):
                err = r.get("error", "")
                if "timeout" not in err.lower():
                    self.status_queue.put(("error", f"Valve B switch failed: {err}"))
                    return False
            time.sleep(0.3)
            self.controller_b.wait_for_completion(timeout=20.0)
        elif step.valve_b_state > 0 and self.simulation_mode:
            state_name = InjectorValveController.STATE_NAMES.get(step.valve_b_state, "")
            self._log(f"SIMULATED: Valve B -> {state_name} (not connected, skipped)")

        if self.stop_event.is_set():
            return False

        if action == "start_dropview" and dv:
            self._log("Launching DropView and connecting DropSens...")
            ok = dv.do_start_dropview(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Start DropView failed."))
                self._cleanup_dropview(step_num)
                return False

        elif action == "start_measure" and dv:
            if not self._check_dropview_connected():
                self.status_queue.put(("error",
                    f"Step {step_num}: DropView is not connected - measurement could not be started. "
                    "Please add a Start DropView step first."))
                self._cleanup_dropview(step_num)
                return False
            scr_path = self.session_scr_path or step.dropview_scr
            if scr_path:
                self._log(f"Script: {scr_path}")
            ok = dv.do_start_measure(scr_path, log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Start Measure failed."))
                self._cleanup_dropview(step_num)
                return False
            self.measurement_running = True

        if self.stop_event.is_set():
            return False

        # ── Süre bekleme döngüsü ──────────────────────────────
        if step.duration_minutes > 0:
            parts = []
            if step.port > 0:
                parts.append(f"A:Port {step.port}")
            if step.valve_b_state > 0:
                parts.append(f"B:{'Load' if step.valve_b_state==1 else 'Inject'}")
            if action != "none":
                parts.append(f"DV:{DROPVIEW_LABELS.get(action, action)}")
            valve_str = ", ".join(parts) if parts else "No valve change"
    
            running_msg = f"{loop_info}Step {step_num}/{total_steps}: {valve_str} for {step.duration_minutes:.1f} min"
            if step.description:
                running_msg += f"  |  {step.description}"
            self.status_queue.put(("running", running_msg))
    
            duration_sec = step.duration_minutes * 60
            start_t = time.time()
            while time.time() - start_t < duration_sec:
                if self.stop_event.is_set():
                    return False
                self.pause_event.wait()
                elapsed   = time.time() - start_t
                remaining = (duration_sec - elapsed) / 60
                self.status_queue.put(("progress", {
                    "step": step_num,
                    "total_steps": total_steps,
                    "elapsed_min": elapsed / 60,
                    "overall_elapsed_min": (time.time() - self.recipe_start_t) / 60 if self.recipe_start_t else elapsed / 60,
                    "remaining_min": remaining,
                    "remaining_minutes": remaining,
                    "duration_min": step.duration_minutes,
                    "total_minutes": step.duration_minutes,
                    "port": step.port,
                    "valve_b_state": step.valve_b_state,
                    "description": step.description,
                    "loop_info": loop_info,
                    "loop_index": self.current_loop_index,
                    "loop_count": self.current_loop_count,
                }))
                time.sleep(1.0)

        if self.stop_event.is_set():
            return False

        if action == "stop_measure" and dv:
            self._log("Stopping measurement...")
            ok = dv.do_stop_measure(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Stop Measure failed."))
                return False
            self.measurement_running = False

        elif action == "exit_dropview" and dv:
            self._log("Closing DropView...")
            ok = dv.do_exit_dropview(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Exit DropView failed."))
                return False
            self.measurement_running = False

        return True

    def run(self):
        recipe     = self.recipe
        steps      = recipe.steps
        loop_count = recipe.loop_count
        step_loops = recipe.step_loops

        self.recipe_start_t = time.time()
        self.current_loop_count = loop_count
        self.status_queue.put(("started", f"Recipe '{recipe.name}' started."))

        for loop_i in range(loop_count):
            self.current_loop_index = loop_i + 1
            loop_info = f"[Loop {loop_i+1}/{loop_count}] " if loop_count > 1 else ""

            step_indices = list(range(len(steps)))

            # step_loops genişletme
            expanded = []
            i = 0
            while i < len(step_indices):
                matched = False
                for sl in step_loops:
                    if sl.start_step - 1 == i:
                        for _ in range(sl.loop_count):
                            expanded.extend(step_indices[sl.start_step-1:sl.end_step])
                        i = sl.end_step
                        matched = True
                        break
                if not matched:
                    expanded.append(step_indices[i])
                    i += 1

            total = len(expanded)
            for step_num, idx in enumerate(expanded, 1):
                if self.stop_event.is_set():
                    if self._stop_measurement_for_recipe_stop():
                        self.status_queue.put(("stopped", "Recipe stopped."))
                    return
                step = steps[idx]
                ok = self._execute_step(step, step_num, total, loop_info)
                if not ok:
                    if self.stop_event.is_set():
                        if self._stop_measurement_for_recipe_stop():
                            self.status_queue.put(("stopped", "Recipe stopped."))
                        return
                    self.status_queue.put(("stopped", "Recipe stopped."))
                    return

        self.status_queue.put(("finished", f"Recipe '{recipe.name}' completed."))
