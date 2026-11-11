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
    EDITOR_CONTROL_UNVERIFIED, RecoveryJournal, RecoveryVerificationBlocked,
    expanded_step_indices, plan_recovery, recipe_from_snapshot,
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
from bench_test.watchdog.progress import CSV_SCAN_INTERVAL, CsvProgressProbe, read_csv_expectation
from bench_test.watchdog.state import process_exited

EDITOR_PROBE_INTERVAL = 3.0
PAD_ACTIVITY_CHECK_SECONDS = 60.0
EDITOR_RESTORE_TIMEOUT = 30.0
EDITOR_RESTORE_ATTEMPTS = 3

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
                 recovery_source_path="",
                 pump_simulation_mode: bool = False,
                 simulation_recovery: bool = False):
        super().__init__(daemon=True)
        self.simulation_mode = simulation_mode or simulation_recovery
        self.controller_a     = None if self.simulation_mode else controller_a
        self.controller_b     = None if self.simulation_mode else controller_b
        self.recipe           = recipe
        self.status_queue     = status_queue
        self.stop_event       = stop_event
        self.dropview_ctrl    = dropview_ctrl
        self.pump_ctrl        = None if self.simulation_mode else pump_ctrl
        self.session_scr_path = session_scr_path
        self.session_tp_path  = session_tp_path
        self.session_method_paths = session_method_paths or {}
        self.session_measurements_dir = session_measurements_dir
        self.part_number = part_number
        self.measurement_mode = measurement_mode or MEASUREMENT_MODE_SCRIPT_PAD
        self.simulation_recovery = simulation_recovery
        self.pump_simulation_mode = pump_simulation_mode or self.simulation_mode
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
        self.measurement_started_ns = None
        self.csv_expectation = None
        self._last_measurement_running = False
        self._watchdog_interrupt = threading.Event()
        self._watchdog_reason = ""
        self._wait_deadline = None
        self._recovery_hold = None
        self._watchdog_measurement_state = None
        self._measurement_probe_at = -float("inf")
        self._pad_activity_since = 0.0
        self._measurement_inactive_since = None
        self._measurement_csv_probe = None
        self._measurement_stopped_count = 0
        self._measurement_missing_count = 0
        self._measurement_owner = None
        self._restored_editor_hwnd = None
        self._editor_loss_at = None
        self._editor_loss_ns = None
        self._editor_restore_attempts = 0
        self._editor_csv_probe = None
        self._editor_running_seen = False
        self._resume_measurement = False
        self._restore_pump_at_step = False
        self.recovery_continued = False
        self._measurement_probe_state = None
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
        if self._recovery_hold is not None:
            phase = self._recovery_hold["phase"]
            changes.update(self._recovery_hold)
            changes.pop("phase")
        if self.measurement_running and not self._last_measurement_running:
            self.measurement_epoch = time.monotonic()
        self._last_measurement_running = self.measurement_running
        if threading.current_thread() is self:
            self.worker_heartbeat = time.monotonic()
        if "status" in changes:
            self.outcome = changes["status"]
        if phase is not None:
            self._recovery_phase = phase
        self._last_checkpoint_t = time.monotonic()
        if self.recovery_journal is None or self._journal_failed:
            return
        owner = self._measurement_owner if self.active_measurement_kind == "discrete_pad" else None
        owner = owner or getattr(self.dropview_ctrl, "last_process_identity", None)
        if isinstance(owner, dict):
            changes.setdefault("dropview_owner", owner)
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
        except (OSError, ValueError, TypeError) as exc:
            self._journal_failed = True
            self.status_queue.put(("warning", f"Recovery recording failed; automatic recovery is unavailable: {exc}"))
            debug_log(f"Recovery recording failed: {exc}", level="error")

    def request_stop(self):
        self.stop_event.set()
        self.checkpoint(status="user_stopped")

    def request_recovery(self, reason):
        """Cooperative interrupt, deliberately distinct from the user's Stop."""
        with self._checkpoint_lock:
            if self._watchdog_interrupt.is_set() or self.stop_event.is_set():
                return
            self._watchdog_reason = str(reason)
            self._watchdog_interrupt.set()
            self._recovery_hold = {"phase": self._recovery_phase, "recovery_hold": True}
            if self._recovery_phase == "waiting" and self._wait_deadline is not None:
                self._recovery_hold["remaining_seconds"] = max(0.0, self._wait_deadline - time.monotonic())
            self.checkpoint(status="interrupted", error=self._watchdog_reason)

    def _interrupted(self):
        return self.stop_event.is_set() or self._watchdog_interrupt.is_set()

    def _pump_connected(self) -> bool:
        return self.pump_ctrl is not None and self.pump_ctrl.is_connected()

    def _log_pump_lines(self, label: str, lines: list[str]):
        for line in lines:
            self._log(f"Pump [{label}]: {line}")

    def _safe_stop_pump(self, label: str = "STOP"):
        if self.pump_simulation_mode or not self._pump_connected():
            return
        try:
            self._log_pump_lines(label, self.pump_ctrl.stop())
            self._log_pump_lines("SPEED", self.pump_ctrl.set_speed_rpm(0))
            self._requested_settings["pump_rpm"] = 0.0
            self.checkpoint()
        except Exception as e:
            self.status_queue.put(("warning", f"Pump stop failed: {e}"))

    def _hold_pump_for_recovery(self, required=False):
        """Fence the pump without overwriting the saved recipe speed."""
        if self.simulation_mode or self.pump_simulation_mode:
            return True
        required = required or self._requested_settings.get("pump_rpm") is not None
        if not required:
            return True
        stopped = False
        try:
            stopped = self._pump_connected() and self.pump_ctrl.stop_and_confirm() is True
        except Exception as exc:
            self._log(f"Pump stop verification failed: {exc}")
        self.checkpoint(pump_recovery_stop="verified" if stopped else "unverified")
        if not stopped:
            self.status_queue.put(("warning", "Pump stop could not be verified; automatic continuation is blocked."))
        return stopped

    def _finish_watchdog_interrupt(self):
        pump_stopped = self._hold_pump_for_recovery()
        blocked = (not pump_stopped or (self._watchdog_measurement_state == EDITOR_CONTROL_UNVERIFIED
                   and (not self.simulation_mode or self.simulation_recovery)))
        status = "operator_required" if blocked else "interrupted"
        if self.stop_event.is_set():
            status = "user_stopped"
        self.checkpoint(status=status, error=self._watchdog_reason,
                        observed_measurement_state=self._watchdog_measurement_state)
        self.status_queue.put(("watchdog_interrupted", self._watchdog_reason))

    def _apply_pump_rpm(self, step: RecipeStep, step_num: int, loop_info: str = "") -> bool:
        if step.pump_rpm is None:
            return True

        if self.pump_simulation_mode:
            rpm = float(step.pump_rpm)
            target = "Stop" if rpm <= 0 else f"{rpm:g} RPM"
            self._log(f"{loop_info}SIMULATED: Pump -> {target} (hardware command skipped)")
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
        if self.recovery_journal is not None:
            self._hold_pump_for_recovery()
            if self.measurement_running or self._recovery_phase == "measurement_start":
                # Keep the old editor available for a verified Stop during recovery.
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

    def _start_discrete_measurement(self, script_path):
        self._connection_fault_since = time.monotonic()
        self.csv_expectation = read_csv_expectation(script_path, self.session_measurements_dir)
        self.measurement_started_ns = time.time_ns()
        if self.csv_expectation:
            first = self.csv_expectation["first_seconds"]
            interval = self.csv_expectation["interval_seconds"]
            self._log(f"Discrete PAD CSV expectation: first after {first:g}s, then every {interval:g}s (plus tolerance).")
        if not self.dropview_ctrl.do_start_measure(script_path, log_fn=self._log):
            return False
        owner = getattr(self.dropview_ctrl, "discrete_measurement_owner", None)
        self._measurement_owner = owner if isinstance(owner, dict) else None
        self._restored_editor_hwnd = None
        if self._measurement_owner is None:
            self._log("DropView process identity unavailable; same-session editor recovery cannot be verified.")
        self._measurement_stopped_count = 0
        self._measurement_missing_count = 0
        self._measurement_probe_at = -float("inf")
        self._pad_activity_since = time.monotonic()
        self._measurement_inactive_since = None
        self._measurement_csv_probe = CsvProgressProbe() if self.csv_expectation else None
        return True

    def _wait_for_recovery_csv(self):
        """Verify new-package data before allowing the recovery recipe to advance."""
        if not self.csv_expectation:
            self._hold_pump_for_recovery()
            raise RecoveryVerificationBlocked("The recovery script's first CSV timing could not be determined.")
        first = self.csv_expectation["first_seconds"]
        deadline = time.monotonic() + first + max(CSV_SCAN_INTERVAL, first * 0.25)
        probe = CsvProgressProbe()
        self.checkpoint("measurement_start", measurement_restart_pending=True)
        self._log("Recovery: waiting for the first valid CSV in the new measurement package.")
        while not self._interrupted():
            now = time.monotonic()
            self.worker_heartbeat = now
            if not self._check_discrete_measurement_progress():
                return False
            probe.observe(self.session_measurements_dir, self.measurement_epoch, now,
                          expectation=self.csv_expectation, started_ns=self.measurement_started_ns,
                          scan_interval=EDITOR_PROBE_INTERVAL)
            if probe.content_last_progress is not None:
                self._log("Recovery: fresh CSV measurement data verified.")
                return True
            if now >= deadline:
                self._log("Recovery: no valid first CSV arrived; recipe progression interrupted.")
                self.request_recovery("discrete_first_export_missing")
                return False
            self.stop_event.wait(0.5)
        return False

    def _check_dropview_connection_fault(self):
        if not (self.measurement_running and self.active_measurement_kind == "discrete_pad"
                and self._measurement_owner):
            return True
        from bench_test.dropview import automator
        automator.dismiss_connection_error_dialogs()
        fault = automator.connection_fault(
            self._measurement_owner, since=getattr(self, "_connection_fault_since", self._pad_activity_since))
        if fault:
            self._log("DropView reported Device USB cable disconnected; ignoring stale Run/connection indicators.")
            self._watchdog_measurement_state = "disconnected"
            self.checkpoint(connection_error=fault)
            self.request_recovery(fault["reason"])
            return False
        return True

    def _check_discrete_measurement_progress(self, *, force=False, command_pending=False) -> bool:
        if (not self.measurement_running or self.active_measurement_kind != "discrete_pad"
                or self.dropview_ctrl is None):
            self._measurement_stopped_count = 0
            self._measurement_missing_count = 0
            return True
        now = time.monotonic()
        fresh = now - self._measurement_probe_at >= EDITOR_PROBE_INTERVAL
        if not fresh and not force:
            return True
        if fresh:
            self._measurement_probe_at = now
        from bench_test.dropview import automator
        if not self._check_dropview_connection_fault():
            return False
        scope = {"expected_owner": self._measurement_owner} if self._measurement_owner else {}
        if fresh and not self._interrupted() and automator.acknowledge_discrete_unsaved_warning(self._measurement_owner):
            self._pad_activity_since = time.monotonic()
            self._log("DropView's unsaved-curves warning was acknowledged. Waiting for fresh device status.")
            return True
        if self._restored_editor_hwnd is not None:
            scope["hwnd"] = self._restored_editor_hwnd
        activity = "unknown"
        if not force and not command_pending:
            activity = self.dropview_ctrl.observe_discrete_pad_activity(
                self._measurement_owner, since=self._pad_activity_since, max_age=EDITOR_PROBE_INTERVAL)
            if self._editor_loss_at is not None and activity == "running":
                self._clear_discrete_editor_loss()
                self._log("Discrete PAD is running in the original DropView session.")
            if self._editor_loss_at is None and not self._needs_discrete_editor_check(now, activity):
                return True
        observation = automator.get_discrete_measurement_state(
            refocus_if_covered=force or self._measurement_stopped_count == 0 or self._editor_loss_at is not None,
            **scope)
        state = observation["state"]
        unavailable = (observation.get("editor_missing") or observation.get("editor_hidden")
                       or ((force or command_pending) and state == "unknown"))
        if activity == "disconnected" or (not force and self._editor_loss_at is None
                                          and state != "stopped" and not unavailable):
            self._measurement_inactive_since = now
        if state != "stopped" or (self._editor_loss_at is not None and activity != "stopped"):
            self._measurement_stopped_count = 0
        elif fresh:
            self._measurement_stopped_count += 1
        if not unavailable:
            self._measurement_missing_count = 0
        elif fresh:
            self._measurement_missing_count += 1
        if state != self._measurement_probe_state:
            self._measurement_probe_state = state
            self._log(f"Discrete PAD observed state: {state}. {observation.get('error', '')}")
        # A reopened editor needs corroboration from the original DropView status row.
        if self._measurement_stopped_count >= 3 and self._editor_loss_at is None:
            return self._interrupt_discrete_measurement(
                "Discrete PAD stopped unexpectedly in DropView; recipe progression interrupted.", state)
        if unavailable and self._editor_loss_at is None:
            self._editor_loss_at = now
            self._editor_loss_ns = time.time_ns()
            self._editor_restore_attempts = 0
            self._editor_csv_probe = CsvProgressProbe() if self.csv_expectation else None
            self._editor_running_seen = False
            self._watchdog_measurement_state = EDITOR_CONTROL_UNVERIFIED
            self.checkpoint(observed_measurement_state=EDITOR_CONTROL_UNVERIFIED)
            self._log("Multiscript Editor unavailable; checking the existing Discrete PAD measurement.")
        if self._editor_loss_at is not None:
            return self._recover_discrete_editor(now, state, fresh, activity)
        return True

    def _needs_discrete_editor_check(self, now, activity):
        probe = self._measurement_csv_probe
        if probe:
            probe.observe(self.session_measurements_dir, self.measurement_epoch, now,
                          expectation=self.csv_expectation, started_ns=self.measurement_started_ns)
        if activity in ("running", "disconnected"):
            self._measurement_inactive_since = None
            self._measurement_stopped_count = 0
            return False
        if self._measurement_inactive_since is None:
            self._measurement_inactive_since = now
        if probe and probe.content_last_progress_ns is not None:
            csv_age = (time.time_ns() - probe.content_last_progress_ns) / 1_000_000_000
            if 0 <= csv_age < PAD_ACTIVITY_CHECK_SECONDS:
                self._measurement_inactive_since = max(self._measurement_inactive_since, now - csv_age)
                self._measurement_stopped_count = 0
        return now - self._measurement_inactive_since >= PAD_ACTIVITY_CHECK_SECONDS

    def _clear_discrete_editor_loss(self):
        self._editor_loss_at = None
        self._editor_loss_ns = None
        self._editor_csv_probe = None
        self._measurement_missing_count = 0
        self._watchdog_measurement_state = None
        self.checkpoint(observed_measurement_state=None)

    def _recover_discrete_editor(self, now, state, fresh, activity):
        from bench_test.dropview import automator

        elapsed = now - self._editor_loss_at
        probe = self._editor_csv_probe
        if probe:
            # Only accept a valid export written to this package after the loss was observed.
            probe.observe(self.session_measurements_dir, self.measurement_epoch, now,
                          expectation=self.csv_expectation, started_ns=self._editor_loss_ns)
        if (fresh and self._measurement_owner and self._measurement_missing_count >= 3
                and elapsed < EDITOR_RESTORE_TIMEOUT
                and self._editor_restore_attempts < EDITOR_RESTORE_ATTEMPTS and not self._interrupted()):
            self._editor_restore_attempts += 1
            self._log(f"Reopening Multiscript in the original DropView session (attempt {self._editor_restore_attempts}).")
            try:
                self._restored_editor_hwnd = automator.reopen_discrete_editor(self._measurement_owner, log_fn=self._log)
            except Exception as exc:
                self._log(f"Could not restore Multiscript Editor: {exc}")

        if (fresh and self._restored_editor_hwnd and activity == "stopped"
                and self._measurement_stopped_count >= 3 and self._measurement_inactive_since is not None
                and now - self._measurement_inactive_since >= PAD_ACTIVITY_CHECK_SECONDS
                and (probe is None or probe.content_last_progress is None) and not self._interrupted()):
            try:
                automator.verify_discrete_session_connection(self._measurement_owner)
            except Exception as exc:
                self._log(f"Could not verify the original DropView connection after reading idle: {exc}")
            else:
                return self._interrupt_discrete_measurement(
                    "Discrete PAD stopped unexpectedly; idle was verified in the reopened Multiscript Editor.",
                    "stopped",
                )

        if state == "running" and self._measurement_owner and probe:
            self._editor_running_seen = True
            if probe.content_last_progress is not None and not self._interrupted():
                try:
                    automator.verify_discrete_session_connection(self._measurement_owner)
                except Exception as exc:
                    self._log(f"Could not verify restored Multiscript control: {exc}")
                else:
                    self._clear_discrete_editor_loss()
                    self._log("Multiscript control restored in the original DropView session; CSV export verified.")
                    return True
        deadline = EDITOR_RESTORE_TIMEOUT
        if self._editor_running_seen:
            # A PAD cycle can be longer than the window recovery timeout.
            deadline = max(deadline, self.csv_expectation["first_seconds"],
                           self.csv_expectation["interval_seconds"]) + 2 * CSV_SCAN_INTERVAL
        if elapsed >= deadline:
            problem = ("Multiscript Editor reopened, but the previous Discrete PAD measurement state could not be verified."
                       if self._restored_editor_hwnd else
                       "Multiscript Editor could not be recovered in the original DropView session.")
            return self._interrupt_discrete_measurement(
                f"{problem} Operator verification is required.", EDITOR_CONTROL_UNVERIFIED)
        return True

    def _interrupt_discrete_measurement(self, message, state):
        can_recover = self.recovery_journal is not None and (not self.simulation_mode or self.simulation_recovery)
        self.status_queue.put(("warning" if can_recover else "error", message))
        # Preserve the expected measurement state for the recovery cursor.
        self._watchdog_measurement_state = state
        self.request_recovery(message)
        return False

    def _wait_for_discrete_dropview_control(self):
        if self._interrupted() or not self._check_discrete_measurement_progress(force=True):
            return False
        if self._editor_loss_at is not None:
            self._log("Waiting for verified Multiscript control before the next DropView command.")
        while self._editor_loss_at is not None and not self._interrupted():
            self.worker_heartbeat = time.monotonic()
            if self.stop_event.wait(0.5) or not self._check_discrete_measurement_progress(command_pending=True):
                return False
        return not self._interrupted()

    def _is_measurement_window_open(self) -> bool:
        try:
            from bench_test.dropview import automator
            return automator.window_exists(automator.MULTISCRIPT_WINDOW)
        except Exception:
            return False

    def _stop_discrete_measurement(self) -> bool:
        if not self._measurement_owner:
            self._log("Cannot verify Stop: the original DropView process was not recorded.")
            return False
        return self.dropview_ctrl.do_stop_measure(
            log_fn=self._log, require_editor=True, expected_owner=self._measurement_owner, reopen_if_needed=True)

    def stop_measurement_for_recipe_stop(self) -> bool:
        if self.dropview_ctrl is None:
            return True
        if not self.measurement_running and not self._is_measurement_window_open():
            return True

        if self.active_measurement_kind == "continuous_pad":
            ok = self.dropview_ctrl.do_stop_continuous_pad(log_fn=self._log)
        elif self.active_measurement_kind == "discrete_pad" or self._editor_loss_at is not None:
            ok = self._stop_discrete_measurement()
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
        if not self._check_dropview_connection_fault():
            return False

        action = step.dropview_action
        dv     = self.dropview_ctrl
        options = recovery_options or {}
        if self.recovery_record and not self.simulation_recovery:
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
            if self.simulation_recovery and not options.get("skip_valve_a"):
                self._requested_settings["valve_a"] = step.port
                self.checkpoint("valve_a")
            self._log(f"SIMULATED: Valve A -> Port {step.port} (hardware command skipped)")

        # ── Valf B ────────────────────────────────────────────
        if self._interrupted():
            return False
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
            if self.simulation_recovery and not options.get("skip_valve_b"):
                self._requested_settings["valve_b"] = step.valve_b_state
                self.checkpoint("valve_b")
            state_name = InjectorValveController.STATE_NAMES.get(step.valve_b_state, "")
            self._log(f"SIMULATED: Valve B -> {state_name} (hardware command skipped)")

        # ── Pompa ─────────────────────────────────────────────
        if self._interrupted():
            return False
        pump_step = step
        restore_pump = self._restore_pump_at_step
        if restore_pump and step.pump_rpm is None:
            pump_step = copy.copy(step)
            pump_step.pump_rpm = self._requested_settings.get("pump_rpm")
        apply_pump = restore_pump or not options.get("skip_pump")
        if pump_step.pump_rpm is not None and apply_pump:
            self._requested_settings["pump_rpm"] = pump_step.pump_rpm
            self.checkpoint(None if options.get("skip_pump") else "pump")
        if restore_pump and self._journal_failed:
            raise RuntimeError("The recovered pump setting could not be recorded.")
        if apply_pump and not self._apply_pump_rpm(pump_step, step_num, loop_info):
            return False
        if restore_pump and pump_step.pump_rpm is not None and not self.pump_simulation_mode:
            if self.pump_ctrl.running_state() is not (pump_step.pump_rpm > 0):
                raise RuntimeError("The recovered pump running state could not be verified.")
        self._restore_pump_at_step = False

        if self._interrupted():
            return False

        if action == "start_dropview" and dv and not options.get("skip_start"):
            self.checkpoint("dropview_start")
            if not self._wait_for_discrete_dropview_control():
                return False
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
            if not self._wait_for_discrete_dropview_control():
                return False
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
                scr_path = self.session_scr_path
                if not scr_path:
                    raise RuntimeError("Discrete PAD requires a script in the measurement package.")
                self._debug_log(f"Script: {scr_path}")
                ok = self._start_discrete_measurement(scr_path)
                if not ok:
                    self.status_queue.put(("error", f"Step {step_num}: Start Measure failed."))
                    self._cleanup_dropview(step_num)
                    return False
                self.measurement_running = True
                self.active_measurement_kind = "discrete_pad"
                if self.recovery_record:
                    if not self._wait_for_recovery_csv():
                        return False
                    self.checkpoint(measurement_restart_pending=False, measurement_resume_required=False)

        if self._interrupted():
            return False

        if self._resume_measurement:
            self.checkpoint(measurement_restart_pending=True)
            if self._journal_failed:
                raise RuntimeError("Measurement restart intent could not be saved.")
            if not self._start_discrete_measurement(self.session_scr_path):
                raise RuntimeError("Discrete PAD could not restart in the new package.")
            self.measurement_running = True
            self.active_measurement_kind = "discrete_pad"
            if not self._wait_for_recovery_csv():
                return False
            self._resume_measurement = False
            self.checkpoint(measurement_restart_pending=False, measurement_resume_required=False)

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
            with self._checkpoint_lock:
                if self._interrupted():
                    return False
                self._wait_deadline = time.monotonic() + duration_sec
                self.checkpoint("waiting", remaining_seconds=duration_sec)
            if self._recovery_prepared and not self._journal_failed:
                self.recovery_continued = True
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
                    with self._checkpoint_lock:
                        self._wait_deadline += paused_sec
                    if self.recipe_start_t:
                        self.recipe_start_t += paused_sec
                elapsed   = time.time() - start_t
                if not self._check_discrete_measurement_progress():
                    return False
                remaining = (duration_sec - elapsed) / 60
                if time.monotonic() - self._last_checkpoint_t >= 5.0:
                    self.checkpoint(remaining_seconds=max(0.0, remaining * 60))
                self.status_queue.put(("progress", {
                    "step": step_num,
                    "total_steps": total_steps,
                    "elapsed_min": (step.duration_minutes * 60 - duration_sec + elapsed) / 60,
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
            planned_discrete_stop = (action != "stop_continuous_pad" and self.measurement_running
                                     and self.active_measurement_kind == "discrete_pad")
            if not planned_discrete_stop and not self._wait_for_discrete_dropview_control():
                return False
            self._debug_log("Stopping measurement...")
            if action == "stop_continuous_pad" or (
                action == "stop_measure"
                and self.active_measurement_kind == "continuous_pad"
            ):
                ok = dv.do_stop_continuous_pad(log_fn=self._log)
            elif planned_discrete_stop:
                ok = self._stop_discrete_measurement()
            else:
                ok = dv.do_stop_measure(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error", f"Step {step_num}: Stop Measure failed."))
                return False
            self.measurement_running = False
            self.active_measurement_kind = ""
            if planned_discrete_stop:
                self._restored_editor_hwnd = None
                if self._editor_loss_at is not None:
                    self._clear_discrete_editor_loss()

        elif action == "exit_dropview" and dv:
            self.checkpoint("dropview_exit")
            if not self._wait_for_discrete_dropview_control():
                return False
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
                try:
                    self._run_recipe()
                except Exception:
                    if self._recovery_prepared or not self.recovery_record:
                        if self.recovery_journal is not None and not self.stop_event.is_set():
                            self._hold_pump_for_recovery()
                    raise
        except Exception as exc:
            status = "interrupted" if not self.recovery_record or self._recovery_prepared else "recovery_blocked"
            if isinstance(exc, RecoveryVerificationBlocked):
                status = "operator_required"
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
            recovery_session=os.path.dirname(self.session_measurements_dir), recovery_connection_restored=False)
        self.checkpoint("reconciling", status="recovering")
        if self._journal_failed:
            raise RuntimeError("The new recovery record is not writable.")
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        if self.simulation_recovery:
            self._restore_pump_at_step = self._requested_settings.get("pump_rpm") is not None
            self._log("SIMULATED recovery: pump and valve hardware verification skipped.")
        else:
            pump_required = reconnect_recovery_pump(current, self.recipe, self.pump_ctrl)
            if pump_required and not self._hold_pump_for_recovery(required=True):
                raise RuntimeError("The saved pump could not be confirmed stopped.")
            self._restore_pump_at_step = bool(pump_required)
            self._recovery_observed_valves = verify_recovery_valves(
                current, self.controller_a, self.controller_b)
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        if self.dropview_ctrl is None:
            raise RuntimeError("Recovery requires the DropView controller.")
        dropview_reconnected = self._reconcile_recovery_dropview(current)
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        plan = self._recovery_plan
        if not plan.finished:
            target = self.recipe.steps[plan.step_index - 1]
            same_step = (plan.recipe_loop == current["recipe_loop"]
                         and plan.sequence_index == current["sequence_index"])
            if same_step:
                target = RecipeStep(**current["active_step"])
            needs_measurement = (current.get("measurement_running", False)
                                 or current.get("measurement_restart_pending") is True
                                 or current.get("measurement_resume_required") is True) and (
                target.dropview_action in ("none", "start_dropview")
                or (same_step and current["phase"] == "waiting"
                    and target.dropview_action in ("start_measure", "start_discrete_pad",
                                                   "stop_measure", "stop_discrete_pad", "exit_dropview")))
            if not dropview_reconnected and (needs_measurement
                    or target.dropview_action in ("none", "start_measure", "start_discrete_pad")
                    or (same_step and current["phase"] == "waiting" and target.dropview_action == "start_dropview")):
                self._start_dropview_for_recovery()
            if self._interrupted():
                raise RuntimeError("Recovery interrupted.")
            if needs_measurement:
                self._resume_measurement = True
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
                    measurement_resume_required=needs_measurement,
                    remaining_seconds=current.get("remaining_seconds") if same_step else None)
            if self._journal_failed:
                raise RuntimeError("The recovery cursor could not be saved.")
        self._recovery_source_journal.update(status="superseded")
        self._recovery_prepared = True
        self._log(f"Recovery: loop {plan.recipe_loop}, sequence {plan.sequence_index}, step {plan.step_index}.")

    def _start_dropview_for_recovery(self):
        if self._interrupted():
            raise RuntimeError("Recovery interrupted.")
        if not self.dropview_ctrl.do_start_dropview(
                log_fn=self._log, allow_cleanup=False, cancelled=self._interrupted):
            raise RuntimeError("DropView and DropSens could not be restored.")
        self._recovery_source_journal.update(recovery_connection_restored=True)

    def _reconcile_recovery_dropview(self, current) -> bool:
        """Record the basis for continuation separately from a restored connection."""
        journal = self._recovery_source_journal
        basis = current.get("recovery_resume_basis")
        if basis is None and current.get("recovery_previous_stopped") is True:
            basis = "legacy_reconciled"
        may_be_running = basis is None and (
            current.get("measurement_running", False) or current["phase"] == "measurement_start"
            or current.get("measurement_restart_pending") is True)
        owner = current.get("dropview_owner")
        owner_option = {"expected_owner": owner} if owner and may_be_running else {}
        reason = current.get("error")
        link_loss = may_be_running and owner and reason in ("dropsens_disconnected", "device_usb_disconnected")
        hang = may_be_running and owner and reason == "dropview_not_responding"
        dv = self.dropview_ctrl

        if may_be_running and (reason == "dropview_process_exited" or link_loss or hang) and process_exited(owner):
            self._log("Verified exit of the original DropView process. Restarting DropView and reconnecting DropSens.")
            journal.update(recovery_resume_basis="process_exited")
            self._start_dropview_for_recovery()
            return True
        if link_loss:
            self._log("Verified DropSens disconnection. Reconnecting in the original DropView session.")
            if not dv.do_reconnect_dropsens(owner, log_fn=self._log):
                if self._interrupted():
                    raise RuntimeError("Recovery interrupted.")
                self._log("DropSens reconnect failed. Closing the original DropView session, with forced termination if normal closure times out.")
                if dv.do_close_dropview_for_recovery(owner, cancelled=self._interrupted, log_fn=self._log) is True:
                    journal.update(recovery_resume_basis="process_exited")
                    self._start_dropview_for_recovery()
                    return True
                raise RuntimeError("DropSens reconnect failed and DropView process exit could not be verified.")
            journal.update(recovery_resume_basis="link_restored", recovery_connection_restored=True)
            return True

        if not dv.do_reconcile_discrete_recovery(may_be_running, log_fn=self._log, **owner_option):
            if hang:
                if not dv.do_stop_unresponsive_dropview(owner, cancelled=self._interrupted, log_fn=self._log):
                    raise RuntimeError("DropView control could not be restored after the hang.")
                journal.update(recovery_resume_basis="process_exited")
                self._start_dropview_for_recovery()
                return True
            try:
                same_process = dv.do_restore_discrete_control(
                    owner, log_fn=self._log, allow_terminate=not may_be_running, cancelled=self._interrupted)
            except RuntimeError as exc:
                if may_be_running and not self._interrupted():
                    raise RecoveryVerificationBlocked(str(exc)) from exc
                raise
            journal.update(recovery_connection_restored=True)
            if self._interrupted():
                raise RuntimeError("Recovery interrupted.")
            if may_be_running and (same_process is not True or not dv.do_reconcile_discrete_recovery(
                    True, log_fn=self._log, **owner_option)):
                raise RecoveryVerificationBlocked(
                    "DropView control was restored, but the previous measurement stop could not be verified.")

        journal.update(recovery_resume_basis="editor_idle" if may_be_running else (basis or "not_required"))
        return False

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
                    self._finish_watchdog_interrupt()
                    return
                if self.stop_event.is_set():
                    self.checkpoint(status="user_stopped")
                    self._safe_stop_pump("RECIPE STOP")
                    if self.stop_measurement_for_recipe_stop():
                        self.status_queue.put(("stopped", "Recipe stopped."))
                    return
                with self._checkpoint_lock:
                    if self._watchdog_interrupt.is_set() and not self.stop_event.is_set():
                        self._finish_watchdog_interrupt()
                        return
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
                if self._interrupted():
                    ok = False
                if not ok:
                    if self._watchdog_interrupt.is_set() and not self.stop_event.is_set():
                        self._finish_watchdog_interrupt()
                        return
                    if self.recovery_journal is not None and not self.stop_event.is_set():
                        self._hold_pump_for_recovery()
                    self.checkpoint(status="user_stopped" if self.stop_event.is_set() else "interrupted")
                    if self.stop_event.is_set() or self.recovery_journal is None:
                        self._safe_stop_pump("RECIPE STOP")
                    if self.stop_event.is_set():
                        if self.stop_measurement_for_recipe_stop():
                            self.status_queue.put(("stopped", "Recipe stopped."))
                        return
                    self.status_queue.put(("stopped", "Recipe stopped."))
                    return
                self.checkpoint("step_completed", remaining_seconds=0.0)
                if self._recovery_prepared and not self._journal_failed:
                    self.recovery_continued = True

        if self._editor_loss_at is not None and not self._wait_for_discrete_dropview_control():
            if self.stop_event.is_set():
                self.checkpoint(status="user_stopped")
                self._safe_stop_pump("RECIPE STOP")
                if self.stop_measurement_for_recipe_stop():
                    self.status_queue.put(("stopped", "Recipe stopped."))
            elif self._watchdog_interrupt.is_set():
                self._finish_watchdog_interrupt()
            return
        if self._watchdog_interrupt.is_set() and not self.stop_event.is_set():
            self._finish_watchdog_interrupt()
            return
        self._safe_stop_pump("RECIPE FINISH")
        self.checkpoint(status="completed")
        self.status_queue.put(("finished", f"Recipe '{recipe.name}' completed."))
