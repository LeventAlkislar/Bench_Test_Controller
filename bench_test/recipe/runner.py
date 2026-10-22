# bench_test/recipe/runner.py
import copy
import json
import os
import queue
import threading
import time
import traceback
from dataclasses import asdict
from typing import Optional

from bench_test.recipe.models import Recipe, RecipeStep
from bench_test.recipe.recovery import (
    RecoveryJournal, expanded_step_indices, plan_recovery, recipe_from_snapshot,
)
from bench_test.recipe.recovery_executor import (
    RecipeExecutionLock, reconnect_recovery_pump, verify_recovery_valves,
)
from bench_test.dropview.controller import DropViewController
from bench_test.pump import PumpController
from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController
from bench_test.measurement.session import (
    MEASUREMENT_MODE_CONTINUOUS_PAD,
    MEASUREMENT_MODE_CV,
    MEASUREMENT_MODE_SCRIPT_PAD,
)
from bench_test.utils.debug_log import debug_log

# DropView aksiyon sabitleri
DROPVIEW_ACTIONS = [
    "none",
    "start_dropview",
    "start_discrete_pad",
    "stop_discrete_pad",
    "start_continuous_pad",
    "stop_continuous_pad",
    "run_cv",
    "exit_dropview",
    # Legacy actions kept for older recipe files.
    "start_measure",
    "stop_measure",
]
DROPVIEW_LABELS  = {
    "none":                 "None",
    "start_dropview":       "Start DropView",
    "start_discrete_pad":   "Start Discrete PAD",
    "stop_discrete_pad":    "Stop Discrete PAD",
    "start_continuous_pad": "Start Continuous PAD",
    "stop_continuous_pad":  "Stop Continuous PAD",
    "run_cv":               "Run CV",
    "exit_dropview":        "Exit DropView",
    "start_measure":        "Start Measure",
    "stop_measure":         "Stop Measure",
}
DROPVIEW_ZERO_DURATION_OK = {
    "start_dropview",
    "start_discrete_pad",
    "stop_discrete_pad",
    "start_continuous_pad",
    "stop_continuous_pad",
    "run_cv",
    "start_measure",
    "stop_measure",
    "exit_dropview",
}


class RecipeRunner(threading.Thread):
    def __init__(self, controller_a, controller_b, recipe: Recipe,
                 status_queue: queue.Queue, stop_event: threading.Event,
                  dropview_ctrl: Optional[DropViewController] = None,
                  pump_ctrl: Optional[PumpController] = None,
                  session_scr_path: str = "",
                  session_tp_path: str = "",
                  session_method_paths: dict = None,
                  session_measurements_dir: str = "",
                 part_number: str = "",
                 measurement_mode: str = MEASUREMENT_MODE_SCRIPT_PAD,
                 simulation_mode: bool = False,
                 recovery_journal=None,
                 recovery_record=None,
                 recovery_source_path=""):
        super().__init__(daemon=True)
        self.controller_a     = controller_a
        self.controller_b     = controller_b
        self.recipe           = recipe
        self.status_queue     = status_queue
        self.stop_event       = stop_event
        self.dropview_ctrl    = dropview_ctrl
        self.pump_ctrl        = pump_ctrl
        self.session_scr_path = session_scr_path
        self.session_tp_path  = session_tp_path
        self.session_method_paths = session_method_paths or {}
        self.session_measurements_dir = session_measurements_dir
        self.part_number = part_number
        self.measurement_mode = measurement_mode or MEASUREMENT_MODE_SCRIPT_PAD
        self.simulation_mode  = simulation_mode
        self.pause_event      = threading.Event()
        self.pause_event.set()
        self.recipe_start_t   = None
        self.current_loop_index = 0
        self.current_loop_count = 0
        self.current_step_index = 0
        self.current_sequence_index = 0
        self.current_sequence_total = 0
        self.current_expanded_indices = []
        self.measurement_running = False
        self.active_measurement_kind = ""
        self._continuous_pause_requested = False
        self._continuous_resume_requested = False
        self._continuous_stopped_for_pause = False
        self.recovery_journal = recovery_journal
        self._recovery_phase = "prepared"
        self._active_step = None
        self._requested_settings = {"valve_a": None, "valve_b": None, "pump_rpm": None}
        self._journal_failed = False
        self._last_checkpoint_t = 0.0
        self.worker_heartbeat = time.monotonic()
        self.outcome = "prepared"
        self.measurement_epoch = 0
        self._last_measurement_running = False
        self._watchdog_interrupt = threading.Event()
        self._watchdog_reason = ""
        self._checkpoint_lock = threading.RLock()
        self.recovery_record = copy.deepcopy(recovery_record)
        self.recovery_source_path = recovery_source_path
        self._recovery_plan = plan_recovery(recovery_record) if recovery_record else None
        self._recovery_source_journal = None
        self._recovery_prepared = False
        self._recovery_observed_valves = {}
        if recovery_record:
            if asdict(recipe) != asdict(recipe_from_snapshot(recovery_record["recipe"])):
                raise ValueError("Recovery recipe differs from its saved snapshot.")
            if recovery_journal is None or not recovery_source_path:
                raise ValueError("Recovery requires both source and destination records.")
            self._requested_settings.update(recovery_record.get("requested_settings", {}))

    def checkpoint(self, phase=None, **changes):
        """Record execution boundaries without sending any additional device commands."""
        with self._checkpoint_lock:
            self._write_checkpoint(phase, **changes)

    def _write_checkpoint(self, phase=None, **changes):
        if self.measurement_running and not self._last_measurement_running:
            self.measurement_epoch = time.monotonic()
        self._last_measurement_running = self.measurement_running
        if threading.current_thread() is self:
            self.worker_heartbeat = time.monotonic()
        if "status" in changes:
            self.outcome = changes["status"]
        if self.recovery_journal is None or self._journal_failed:
            return
        if phase is not None:
            self._recovery_phase = phase
        try:
            self.recovery_journal.update(
                phase=self._recovery_phase,
                recipe=asdict(self.recipe),
                recipe_loop=self.current_loop_index,
                sequence_index=self.current_sequence_index,
                sequence_total=self.current_sequence_total,
                step_index=self.current_step_index,
                active_step=asdict(self._active_step) if self._active_step else None,
                requested_settings=self._requested_settings,
                measurement_running=self.measurement_running,
                active_measurement_kind=self.active_measurement_kind,
                paused=not self.pause_event.is_set(),
                stop_requested=self.stop_event.is_set(),
                **changes,
            )
            self._last_checkpoint_t = time.monotonic()
        except (OSError, ValueError, TypeError) as exc:
            self._journal_failed = True
            self.status_queue.put(("warning", f"Recovery recording failed; automatic recovery is unavailable: {exc}"))
            debug_log(f"Recovery recording failed: {exc}", level="error")

    def request_stop(self):
        self.stop_event.set()
        self.checkpoint(status="user_stopped")

    def request_recovery(self, reason):
        """Cooperative interrupt, deliberately distinct from the user's Stop."""
        self._watchdog_reason = str(reason)
        self._watchdog_interrupt.set()

    def _interrupted(self):
        return self.stop_event.is_set() or self._watchdog_interrupt.is_set()

    def _pump_connected(self) -> bool:
        return self.pump_ctrl is not None and self.pump_ctrl.is_connected()

    def _log_pump_lines(self, label: str, lines: list[str]):
        for line in lines:
            self._log(f"Pump [{label}]: {line}")

    def _safe_stop_pump(self, label: str = "STOP"):
        if not self._pump_connected():
            return
        try:
            self._log_pump_lines(label, self.pump_ctrl.stop())
            self._log_pump_lines("SPEED", self.pump_ctrl.set_speed_rpm(0))
            self._requested_settings["pump_rpm"] = 0.0
            self.checkpoint()
        except Exception as e:
            self.status_queue.put(("warning", f"Pump stop failed: {e}"))

    def _apply_pump_rpm(self, step: RecipeStep, step_num: int, loop_info: str = "") -> bool:
        if step.pump_rpm is None:
            return True

        if not self._pump_connected():
            self.status_queue.put(("error", f"Step {step_num}: Pump is not connected."))
            return False

        try:
            rpm = float(step.pump_rpm)
            if rpm <= 0:
                self.status_queue.put(("switching", f"{loop_info}Pump -> Stop"))
                self._log_pump_lines("STOP", self.pump_ctrl.stop())
                self._log_pump_lines("SPEED", self.pump_ctrl.set_speed_rpm(0))
                self._log(f"{loop_info}Pump: stopped")
            else:
                self.status_queue.put(("switching", f"{loop_info}Pump -> {rpm:g} RPM"))
                self._log_pump_lines("SPEED", self.pump_ctrl.set_speed_rpm(rpm))
                self._log_pump_lines("RUN FWD", self.pump_ctrl.run("FWD"))
                self._log(f"{loop_info}Pump: running FWD at {rpm:g} RPM")
        except Exception as e:
            self.status_queue.put(("error", f"Step {step_num}: Pump RPM change failed: {e}"))
            return False
        return True

    def pause(self):
        if self.active_measurement_kind == "continuous_pad":
            self._continuous_pause_requested = True
        self.pause_event.clear()
        self.checkpoint()

    def resume(self):
        if (
            self.active_measurement_kind == "continuous_pad"
            and self._continuous_stopped_for_pause
        ):
            self._continuous_resume_requested = True
        self.pause_event.set()
        self.checkpoint()

    def _cleanup_dropview(self, step_num: int):
        """Best-effort cleanup after a failed DropView step."""
        if self.dropview_ctrl is None:
            return
        try:
            from bench_test.dropview import automator
            if automator.window_exists(automator.DROPVIEW_WINDOW_NAME):
                self._debug_log(f"| Cleaning up DropView after step {step_num} error...")
                ok = self.dropview_ctrl.do_exit_dropview(log_fn=self._debug_log)
                if ok:
                    self._debug_log("| DropView cleanup completed.")
                else:
                    self._debug_log("| WARNING: DropView cleanup was attempted but returned failure.")
        except Exception as e:
            self._debug_log(f"| WARNING: DropView cleanup failed: {e}")

    def _log(self, msg):
        self.status_queue.put(("log", msg))

    def _debug_log(self, msg):
        debug_log(msg)

    def next_arrival_context(self, step_index: int):
        target_index = step_index - 1
        expanded = self.current_expanded_indices
        start_at = self.current_sequence_index
        for position in range(start_at, len(expanded)):
            if expanded[position] == target_index:
                return {
                    "recipe_loop": self.current_loop_index,
                    "sequence_index": position + 1,
                    "sequence_total": len(expanded),
                }
        if self.current_loop_index < self.current_loop_count:
            for position, index in enumerate(expanded):
                if index == target_index:
                    return {
                        "recipe_loop": self.current_loop_index + 1,
                        "sequence_index": position + 1,
                        "sequence_total": len(expanded),
                    }
        return None

    def _start_continuous_pad_segment(self, log_prefix: str = "", log_fn=None) -> bool:
        if self.dropview_ctrl is None:
            return True

        log = log_fn or self._log
        tp_path = self.session_method_paths.get("continuous_pad") or self.session_tp_path
        if tp_path:
            debug_log(f"Continuous PAD method: {tp_path}")
        ok = self.dropview_ctrl.do_start_continuous_pad(
            tp_path,
            measurements_dir=self.session_measurements_dir,
            part_number=self.part_number,
            log_fn=log,
        )
        if ok:
            self.measurement_running = True
            self.active_measurement_kind = "continuous_pad"
            if log_prefix:
                log(log_prefix)
            return True
        return False

    def _run_cv_measurement(self) -> bool:
        if self.dropview_ctrl is None:
            return True

        method_path = self.session_method_paths.get("cv") or self.session_tp_path
        if method_path:
            debug_log(f"CV method: {method_path}")
        ok = self.dropview_ctrl.do_run_cv(
            method_path,
            measurements_dir=self.session_measurements_dir,
            part_number=self.part_number,
            log_fn=self._log,
        )
        self.measurement_running = False
        self.active_measurement_kind = ""
        return ok

    def _handle_continuous_pause_request(self) -> bool:
        if self.active_measurement_kind != "continuous_pad":
            return True
        if not self._continuous_pause_requested:
            return True

        self._continuous_pause_requested = False
        if not self.measurement_running or self.dropview_ctrl is None:
            return True

        self._debug_log("Continuous PAD pause requested; stopping active segment...")
        ok = self.dropview_ctrl.do_stop_continuous_pad(log_fn=self._debug_log)
        if not ok:
            self.status_queue.put(("error", "Continuous PAD pause failed: segment could not be stopped."))
            self.pause_event.set()
            return False

        self.measurement_running = False
        self.active_measurement_kind = ""
        self._continuous_stopped_for_pause = True
        self._log("Recipe paused")
        return True

    def _handle_continuous_resume_request(self) -> bool:
        if (
            self.active_measurement_kind
            and self.active_measurement_kind != "continuous_pad"
        ):
            return True
        if not self._continuous_resume_requested:
            return True

        self._continuous_resume_requested = False
        if not self._continuous_stopped_for_pause:
            return True

        self._debug_log("Continuous PAD resume requested; starting a new segment...")
        ok = self._start_continuous_pad_segment(
            log_fn=self._debug_log,
        )
        if not ok:
            self.status_queue.put(("error", "Continuous PAD resume failed: new segment could not be started."))
            return False

        self._continuous_stopped_for_pause = False
        self._log("Recipe resumed")
        return True

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

        if self.active_measurement_kind == "continuous_pad":
            ok = self.dropview_ctrl.do_stop_continuous_pad(log_fn=self._log)
        else:
            ok = self.dropview_ctrl.do_stop_measure(log_fn=self._log)
        if ok:
            self.measurement_running = False
            self.active_measurement_kind = ""
            return True

        self.status_queue.put((
            "stop_failed",
            "Recipe stop requested, but DropSens measurement could not be stopped. "
            "Session was not finalized; stop the measurement manually or retry Stop Recipe."
        ))
        return False

    def _execute_step(self, step: RecipeStep, step_num, total_steps, loop_info="", recovery_options=None):
        if self._interrupted():
            return False

        action = step.dropview_action
        dv     = self.dropview_ctrl
        options = recovery_options or {}
        if self.recovery_record:
            if step.port > 0 and not (self.controller_a and self.controller_a.is_connected()):
                raise RuntimeError("Recovery cannot skip a disconnected Valve A.")
            if step.valve_b_state > 0 and not (self.controller_b and self.controller_b.is_connected()):
                raise RuntimeError("Recovery cannot skip a disconnected Valve B.")

        # ── Valf A ────────────────────────────────────────────
        if self._interrupted():
            return False

        if (step.port > 0 and not options.get("skip_valve_a")
                and self.controller_a and self.controller_a.is_connected()):
            self._requested_settings["valve_a"] = step.port
            self.checkpoint("valve_a")
            self.status_queue.put(("switching", f"{loop_info}Valve A -> Port {step.port}"))
            r = self.controller_a.switch_port(step.port)
            if not r.get("success"):
                err = r.get("error", "")
                if "timeout" not in err.lower():
                    self.status_queue.put(("error", f"Valve A port switch failed: {err}"))
                    return False
            time.sleep(0.3)
            completed = self.controller_a.wait_for_completion(timeout=20.0)
            if recovery_options and not completed:
                raise RuntimeError("Recovery Valve A movement did not complete.")
            if self.recovery_record:
                actual = self.controller_a.get_current_port()
                if not actual.get("success") or actual.get("status") != 0 or actual.get("param1") != step.port:
                    raise RuntimeError(f"Recovery Valve A target was not verified: {actual}")
            self._log(f"{loop_info}Valve A: At port {step.port}")
        elif step.port > 0 and self.simulation_mode:
            self._log(f"SIMULATED: Valve A -> Port {step.port} (not connected, skipped)")

        # ── Valf B ────────────────────────────────────────────
        if (step.valve_b_state > 0 and not options.get("skip_valve_b")
                and self.controller_b and self.controller_b.is_connected()):
            self._requested_settings["valve_b"] = step.valve_b_state
            self.checkpoint("valve_b")
            state_name = InjectorValveController.STATE_NAMES.get(step.valve_b_state, "")
            self.status_queue.put(("switching", f"{loop_info}Valve B -> {state_name}"))
            r = self.controller_b.switch_state(step.valve_b_state)
            if not r.get("success"):
                err = r.get("error", "")
                if "timeout" not in err.lower():
                    self.status_queue.put(("error", f"Valve B switch failed: {err}"))
                    return False
            time.sleep(0.3)
            completed = self.controller_b.wait_for_completion(timeout=20.0)
            if recovery_options and not completed:
                raise RuntimeError("Recovery Valve B movement did not complete.")
            if self.recovery_record:
                actual = self.controller_b.get_current_state()
                if not actual.get("success") or actual.get("status") != 0 or actual.get("param1") != step.valve_b_state:
                    raise RuntimeError(f"Recovery Valve B target was not verified: {actual}")
            self._log(f"{loop_info}Valve B: {state_name} position")
        elif step.valve_b_state > 0 and self.simulation_mode:
            state_name = InjectorValveController.STATE_NAMES.get(step.valve_b_state, "")
            self._log(f"SIMULATED: Valve B -> {state_name} (not connected, skipped)")

        # ── Pompa ─────────────────────────────────────────────
        if step.pump_rpm is not None and not options.get("skip_pump"):
            self._requested_settings["pump_rpm"] = step.pump_rpm
            self.checkpoint("pump")
        if not options.get("skip_pump") and not self._apply_pump_rpm(step, step_num, loop_info):
            return False

        if self._interrupted():
            return False

        if action == "start_dropview" and dv and not options.get("skip_start"):
            self.checkpoint("dropview_start")
            self._debug_log("Launching DropView and connecting DropSens...")
            ok = dv.do_start_dropview(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Start DropView failed."))
                self._cleanup_dropview(step_num)
                return False

        elif action in (
            "start_measure",
            "start_discrete_pad",
            "start_continuous_pad",
            "run_cv",
        ) and dv and not options.get("skip_start"):
            self.checkpoint("measurement_start")
            if not self._check_dropview_connected():
                self.status_queue.put(("error",
                    f"Step {step_num}: DropView is not connected - measurement could not be started. "
                    "Please add a Start DropView step first."))
                self._cleanup_dropview(step_num)
                return False
            if action == "run_cv" or (
                action == "start_measure"
                and self.measurement_mode == MEASUREMENT_MODE_CV
            ):
                ok = self._run_cv_measurement()
                if not ok:
                    self.status_queue.put(("error", f"Step {step_num}: Run CV failed."))
                    self._cleanup_dropview(step_num)
                    return False
            elif action == "start_continuous_pad" or (
                action == "start_measure"
                and self.measurement_mode == MEASUREMENT_MODE_CONTINUOUS_PAD
            ):
                if self.measurement_running:
                    self._debug_log(
                        "Continuous PAD start skipped: measurement is already running. "
                        "Continuing current segment for this step."
                    )
                else:
                    ok = self._start_continuous_pad_segment()
                    if not ok:
                        self.status_queue.put(("error", f"Step {step_num}: Continuous PAD Start failed."))
                        self._cleanup_dropview(step_num)
                        return False
            else:
                scr_path = self.session_scr_path or step.dropview_scr
                if scr_path:
                    self._debug_log(f"Script: {scr_path}")
                ok = dv.do_start_measure(scr_path, log_fn=self._log)
                if not ok:
                    self.status_queue.put(("error", f"Step {step_num}: Start Measure failed."))
                    self._cleanup_dropview(step_num)
                    return False
                self.measurement_running = True
                self.active_measurement_kind = "discrete_pad"

        if self._interrupted():
            return False

        # ── Süre bekleme döngüsü ──────────────────────────────
        cv_run_action = action == "run_cv" or (
            action == "start_measure"
            and self.measurement_mode == MEASUREMENT_MODE_CV
        )
        if step.duration_minutes > 0 and not cv_run_action and not options.get("skip_wait"):
            parts = []
            if step.port > 0:
                parts.append(f"A:Port {step.port}")
            if step.valve_b_state > 0:
                parts.append(f"B:{'Load' if step.valve_b_state==1 else 'Inject'}")
            if step.pump_rpm is not None:
                pump_rpm = float(step.pump_rpm)
                pump_text = "Stop" if pump_rpm <= 0 else f"{pump_rpm:g} RPM"
                parts.append(f"Pump:{pump_text}")
            if action != "none":
                parts.append(f"DV:{DROPVIEW_LABELS.get(action, action)}")
            valve_str = ", ".join(parts) if parts else "No valve change"
    
            running_msg = f"{loop_info}Step {step_num}/{total_steps}: {valve_str} for {step.duration_minutes:.1f} min"
            if step.description:
                running_msg += f"  |  {step.description}"
            self.status_queue.put(("running", running_msg))
    
            duration_sec = options.get("remaining_seconds", step.duration_minutes * 60)
            self.checkpoint("waiting", remaining_seconds=duration_sec)
            start_t = time.time()
            while time.time() - start_t < duration_sec:
                if self._interrupted():
                    return False
                pause_t = time.time()
                if not self._handle_continuous_pause_request():
                    return False
                if not self.pause_event.is_set():
                    self.pause_event.wait()
                    if self._interrupted():
                        return False
                    if not self._handle_continuous_resume_request():
                        return False
                    paused_sec = time.time() - pause_t
                    start_t += paused_sec
                    if self.recipe_start_t:
                        self.recipe_start_t += paused_sec
                elapsed   = time.time() - start_t
                remaining = (duration_sec - elapsed) / 60
                if time.monotonic() - self._last_checkpoint_t >= 5.0:
                    self.checkpoint(remaining_seconds=max(0.0, remaining * 60))
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
                    "pump_rpm": step.pump_rpm,
                    "description": step.description,
                    "loop_info": loop_info,
                    "loop_index": self.current_loop_index,
                    "loop_count": self.current_loop_count,
                }))
                time.sleep(1.0)

        if self._interrupted():
            return False

        if action in ("stop_measure", "stop_discrete_pad", "stop_continuous_pad") and dv:
            self.checkpoint("measurement_stop")
            self._debug_log("Stopping measurement...")
            if action == "stop_continuous_pad" or (
                action == "stop_measure"
                and self.active_measurement_kind == "continuous_pad"
            ):
                ok = dv.do_stop_continuous_pad(log_fn=self._log)
            else:
                ok = dv.do_stop_measure(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Stop Measure failed."))
                return False
            self.measurement_running = False
            self.active_measurement_kind = ""

        elif action == "exit_dropview" and dv:
            self.checkpoint("dropview_exit")
            self._debug_log("Closing DropView...")
            ok = dv.do_exit_dropview(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Exit DropView failed."))
                return False
            self.measurement_running = False

        return True

    def run(self):
        try:
            with RecipeExecutionLock():
                self._run_recipe()
        except Exception as exc:
            status = "interrupted" if not self.recovery_record or self._recovery_prepared else "recovery_blocked"
            self.checkpoint(status="user_stopped" if self.stop_event.is_set() else status, error=str(exc))
            if self._recovery_source_journal is not None:
                try:
                    source = RecoveryJournal.read(self.recovery_source_path)
                    if source.get("status") == "recovering":
                        self._recovery_source_journal.update(
                            status="user_stopped" if self.stop_event.is_set() else "interrupted",
                            stop_requested=self.stop_event.is_set(), recovery_error=str(exc))
                except (OSError, ValueError) as record_error:
                    self._log(f"Could not update recovery source: {record_error}")
            debug_log("Recipe runner failed:\n" + traceback.format_exc(), level="error")
            self.status_queue.put(("error", f"Recipe runner failed: {exc}"))

    def _prepare_recovery(self):
        current = RecoveryJournal.read(self.recovery_source_path)
        if current.get("revision") != self.recovery_record.get("revision"):
            raise RuntimeError("The source record changed; reload it before recovery.")
        plan_recovery(current)
        self._recovery_source_journal = RecoveryJournal(self.recovery_source_path, current)
        self._recovery_source_journal.update(
            status="recovering", recovery_attempts=current.get("recovery_attempts", 0) + 1,
            recovery_session=os.path.dirname(self.session_measurements_dir))
        self.checkpoint("reconciling", status="recovering")
        if self._journal_failed:
            raise RuntimeError("The new recovery record is not writable.")
        self._recovery_observed_valves = verify_recovery_valves(
            current, self.controller_a, self.controller_b)
        reconnect_recovery_pump(current, self.recipe, self.pump_ctrl)
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        if self.dropview_ctrl is None:
            raise RuntimeError("Recovery requires the DropView controller.")
        may_be_running = (
            (current.get("measurement_running", False) or current["phase"] == "measurement_start")
            and not current.get("recovery_previous_stopped", False)
        )
        if not self.dropview_ctrl.do_reconcile_discrete_recovery(may_be_running, log_fn=self._log):
            raise RuntimeError("The previous Discrete PAD measurement could not be reconciled.")
        self._recovery_source_journal.update(recovery_previous_stopped=True)
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        plan = self._recovery_plan
        if not plan.finished:
            target = self.recipe.steps[plan.step_index - 1]
            same_step = (plan.recipe_loop == current["recipe_loop"]
                         and plan.sequence_index == current["sequence_index"])
            if same_step:
                target = RecipeStep(**current["active_step"])
            needs_measurement = current.get("measurement_running", False) and (
                target.dropview_action in ("none", "start_dropview")
                or (same_step and current["phase"] == "waiting"
                    and target.dropview_action in ("stop_measure", "stop_discrete_pad", "exit_dropview")))
            if (needs_measurement or target.dropview_action in ("none", "start_measure", "start_discrete_pad")
                    or (same_step and current["phase"] == "waiting" and target.dropview_action == "start_dropview")):
                if not self.dropview_ctrl.do_start_dropview(log_fn=self._log):
                    raise RuntimeError("DropView could not be restored.")
            if self._interrupted():
                raise RuntimeError("Recovery interrupted.")
            if needs_measurement:
                self._recovery_source_journal.update(recovery_previous_stopped=False)
                if not self.dropview_ctrl.do_start_measure(self.session_scr_path, log_fn=self._log):
                    raise RuntimeError("Discrete PAD could not restart in the new package.")
                self.measurement_running = True
                self.active_measurement_kind = "discrete_pad"
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        if not plan.finished:
            with self._checkpoint_lock:
                self.current_loop_index = plan.recipe_loop
                self.current_sequence_index = plan.sequence_index
                self.current_step_index = plan.step_index
                self.current_sequence_total = len(expanded_step_indices(self.recipe))
                self._active_step = copy.copy(target)
                self.checkpoint(
                    current["phase"] if same_step else "step_pending", status="running",
                    remaining_seconds=current.get("remaining_seconds") if same_step else None)
            if self._journal_failed:
                raise RuntimeError("The recovery cursor could not be saved.")
        self._recovery_source_journal.update(status="superseded")
        self._recovery_prepared = True
        self._log(f"Recovery: loop {plan.recipe_loop}, sequence {plan.sequence_index}, step {plan.step_index}.")

    def _recovery_options(self, step):
        phase = self.recovery_record["phase"]
        rank = {"step_pending": 0, "valve_a": 1, "valve_b": 2, "pump": 3,
                "dropview_start": 4, "measurement_start": 4, "waiting": 5,
                "measurement_stop": 6, "dropview_exit": 6}[phase]
        options = {
            "skip_valve_a": rank > 1 or self._recovery_observed_valves.get("valve_a") == step.port,
            "skip_valve_b": rank > 2 or self._recovery_observed_valves.get("valve_b") == step.valve_b_state,
            "skip_pump": rank > 3,
            "skip_start": rank >= 5,
            "skip_wait": rank >= 6,
        }
        if phase == "waiting":
            remaining = self.recovery_record.get("remaining_seconds")
            if not isinstance(remaining, (int, float)) or not 0 <= remaining <= step.duration_minutes * 60:
                raise ValueError("The saved remaining duration is invalid.")
            options["remaining_seconds"] = remaining
        return options

    def _run_recipe(self):
        recipe     = self.recipe
        steps      = recipe.steps
        loop_count = recipe.loop_count

        self.recipe_start_t = time.time()
        self.current_loop_count = loop_count
        self.status_queue.put(("started", f"Recipe '{recipe.name}' started."))
        if self.recovery_record:
            self._prepare_recovery()
            if self._recovery_plan.finished:
                self.checkpoint(status="completed_after_interruption")
                self.status_queue.put(("finished", "Recipe ended after interruption of its final measurement."))
                return
        else:
            self.checkpoint(status="running")
            self._safe_stop_pump("RECIPE START")

        first_loop = self._recovery_plan.recipe_loop - 1 if self.recovery_record else 0
        for loop_i in range(first_loop, loop_count):
            loop_info = f"[Loop {loop_i+1}/{loop_count}] " if loop_count > 1 else ""

            expanded = expanded_step_indices(recipe)

            total = len(expanded)
            self.current_expanded_indices = expanded
            for step_num, idx in enumerate(expanded, 1):
                if (self.recovery_record and loop_i == first_loop
                        and step_num < self._recovery_plan.sequence_index):
                    continue
                if self._watchdog_interrupt.is_set() and not self.stop_event.is_set():
                    self.checkpoint(status="interrupted", error=self._watchdog_reason)
                    self.status_queue.put(("watchdog_interrupted", self._watchdog_reason))
                    return
                if self.stop_event.is_set():
                    self.checkpoint(status="user_stopped")
                    self._safe_stop_pump("RECIPE STOP")
                    if self._stop_measurement_for_recipe_stop():
                        self.status_queue.put(("stopped", "Recipe stopped."))
                    return
                with self._checkpoint_lock:
                    self.current_loop_index = loop_i + 1
                    self.current_step_index = idx + 1
                    self.current_sequence_index = step_num
                    self.current_sequence_total = total
                    step = copy.copy(steps[idx])
                    same_interrupted_step = (
                        self.recovery_record is not None
                        and loop_i + 1 == self.recovery_record["recipe_loop"]
                        and step_num == self.recovery_record["sequence_index"]
                    )
                    if same_interrupted_step:
                        step = RecipeStep(**self.recovery_record["active_step"])
                    self._active_step = step
                    self.checkpoint(
                        self.recovery_record["phase"] if same_interrupted_step else "step_pending",
                        status="running", remaining_seconds=(
                            self.recovery_record.get("remaining_seconds") if same_interrupted_step else None))
                if not step.enabled:
                    self.checkpoint("step_skipped")
                    description = f" ({step.description})" if step.description else ""
                    self.status_queue.put((
                        "skipped",
                        f"{loop_info}Step {idx + 1} skipped{description} "
                        f"(sequence {step_num}/{total}).",
                    ))
                    continue
                if same_interrupted_step:
                    ok = self._execute_step(step, step_num, total, loop_info, self._recovery_options(step))
                else:
                    ok = self._execute_step(step, step_num, total, loop_info)
                if not ok:
                    self.checkpoint(status="user_stopped" if self.stop_event.is_set() else "interrupted")
                    if self._watchdog_interrupt.is_set() and not self.stop_event.is_set():
                        self.status_queue.put(("watchdog_interrupted", self._watchdog_reason))
                        return
                    if self.stop_event.is_set() or self.recovery_journal is None:
                        self._safe_stop_pump("RECIPE STOP")
                    if self.stop_event.is_set():
                        if self._stop_measurement_for_recipe_stop():
                            self.status_queue.put(("stopped", "Recipe stopped."))
                        return
                    self.status_queue.put(("stopped", "Recipe stopped."))
                    return
                self.checkpoint("step_completed", remaining_seconds=0.0)

        self._safe_stop_pump("RECIPE FINISH")
        self.checkpoint(status="completed")
        self.status_queue.put(("finished", f"Recipe '{recipe.name}' completed."))
