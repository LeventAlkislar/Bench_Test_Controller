import math
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bench_test.measurement.session import MEASUREMENT_MODE_SCRIPT_PAD
from bench_test.recipe.recovery import expanded_step_indices, is_discrete_pad_recipe
from bench_test.watchdog.state import (
    SIMULATION_RECOVERY_ARG, RunStopSignal, atomic_write, launch_watchdog, owner_alive,
    process_identity, read_json, recovery_source, runtime_directory,
)
from bench_test.watchdog.windows_update import WindowsUpdateGuard


class WatchdogClient(QObject):
    status_changed = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(self, recipe_tab, dv_controller, parent=None, directory=None):
        super().__init__(parent)
        self.tab, self.dv = recipe_tab, dv_controller
        self._default_directory = directory or runtime_directory()
        self.directory = self._default_directory
        self.owner = process_identity()
        self.runner = None
        self._armed = False
        self._observe_only = False
        self._simulation_recovery = False
        self._run_id = ""
        self._record_path = ""
        self._pending = None
        self._ack = None
        self._command_state = ""
        self._finalizing = False
        self._last_spawn = -1000
        self._supervisor_starting = False
        self._last_status = ""
        self._last_warning = None
        self._reported_recovery_runner = None
        self._wall_origin = time.time() - time.monotonic()
        self._update_status = "not_checked"
        self._update_guard = None
        self._update_required_until = None
        self._stop_signal = None
        self._update_poll_at = 0
        self._update_check_running = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def is_armed(self):
        return self._armed

    @property
    def recovery_pending(self):
        return self._pending is not None

    def should_focus_connection(self):
        runner = self.runner
        return bool(self._armed and runner and runner.is_alive() and not self._finalizing
                    and runner.pause_event.is_set() and not runner._interrupted()
                    and runner._recovery_phase == "waiting" and runner.measurement_running)

    def arm(self, runner):
        try:
            self._arm(runner)
        except Exception as exc:
            # The recipe has already started; report monitoring failure without
            # throwing out of the Qt start callback or claiming it never started.
            self.status_changed.emit(f"Watchdog unavailable: {exc}")
            self.log_message.emit(f"Watchdog arming failed: {exc}")

    def _arm(self, runner):
        simulation_recovery = getattr(runner, "simulation_recovery", False) is True
        observe_only = (not simulation_recovery and getattr(runner, "simulation_mode", False) is True
                        and runner.measurement_mode == MEASUREMENT_MODE_SCRIPT_PAD
                        and is_discrete_pad_recipe(runner.recipe))
        if not observe_only and (runner.recovery_journal is None or runner._journal_failed):
            return
        self._select_recovery_mode(simulation_recovery)
        self._observe_only = observe_only
        self.runner = runner
        if self._pending:
            run_id = self._pending["run_id"]
        elif runner.recovery_record:
            run_id = runner.recovery_record.get("watchdog_run_id") or uuid.uuid4().hex
        else:
            run_id = uuid.uuid4().hex
        changed_run = run_id != self._run_id
        self._run_id = run_id
        self._stop_signal = RunStopSignal(run_id)
        if self._stop_signal.is_set():
            raise RuntimeError("This run was cancelled; automatic recovery is disabled.")
        if observe_only or simulation_recovery:
            self._update_guard = None
            self._update_status = "not_applied_in_simulation"
        self._record_path = ""
        if not observe_only:
            self._record_path = runner.recovery_journal.path
            runner.recovery_journal.update(watchdog_run_id=run_id)
        self._armed = True
        self._publish()
        if not observe_only and not simulation_recovery and (changed_run or self._update_guard is None):
            self._update_guard = WindowsUpdateGuard(self.directory, run_id)
            guard = self._update_guard
            recipe = runner.recipe
            minutes = sum(recipe.steps[index].duration_minutes for index in expanded_step_indices(recipe)
                          if recipe.steps[index].enabled) * recipe.loop_count
            days = max(15, math.ceil(minutes / 1440) + 1)
            self._update_required_until = datetime.now(timezone.utc) + timedelta(days=days)
            self._update_status = "checking"
            def protect():
                self._update_status = guard.protect(days)
                self.log_message.emit("Windows Update: " + self._update_status)
            threading.Thread(target=protect, daemon=True).start()
        self._ensure_supervisor()

    def _select_recovery_mode(self, simulation_recovery):
        if simulation_recovery != self._simulation_recovery:
            if self._armed:
                raise RuntimeError("Cannot change watchdog mode during an armed run.")
            self._simulation_recovery = simulation_recovery
            self.directory = self._default_directory / "simulation" if simulation_recovery else self._default_directory
            self._last_spawn = -1000

    def _publish(self, status=None):
        if not self._run_id:
            return
        runner = self.runner
        paused = runner is not None and not runner.pause_event.is_set()
        mode = status or ("paused" if paused else "armed")
        measurement_running = bool(runner and runner.measurement_running)
        measurement_epoch = getattr(runner, "measurement_epoch", 0)
        health = getattr(self.dv, "last_health", {})
        measurement_owner = getattr(runner, "_measurement_owner", None)
        if measurement_running and isinstance(measurement_owner, dict) and (
                health.get("process_identity") != measurement_owner or health.get("sampled_at", 0) < measurement_epoch):
            health = {}
        atomic_write(self.directory / "active.json", {
            "version": 1, "owner": self.owner, "run_id": self._run_id,
            "armed": self._armed, "status": mode, "ui_heartbeat": time.monotonic(),
            "observe_only": self._observe_only,
            "simulation_recovery": self._simulation_recovery,
            "worker_heartbeat": getattr(runner, "worker_heartbeat", time.monotonic()),
            "worker_alive": bool(runner and runner.is_alive()),
            "outcome": getattr(runner, "outcome", "prepared"),
            "phase": getattr(runner, "_recovery_phase", "prepared"),
            "record_path": self._record_path,
            "recovery_source": getattr(runner, "recovery_source_path", ""),
            "measurement_running": measurement_running,
            "measurements_dir": getattr(runner, "session_measurements_dir", ""),
            "measurement_epoch": measurement_epoch,
            "measurement_started_ns": getattr(runner, "measurement_started_ns", None),
            "csv_expectation": getattr(runner, "csv_expectation", None),
            "dropview_health": health,
            "finalizing": self._finalizing, "ack_token": self._ack,
            "command_state": self._command_state,
        })

    def set_finalizing(self, active):
        self._finalizing = active
        if self._armed:
            self._publish()

    def _ensure_supervisor(self):
        now = time.monotonic()
        if self._supervisor_starting or now - self._last_spawn < 15:
            return
        state = read_json(self.directory / "supervisor_status.json")
        if state and owner_alive(state.get("owner", {})):
            return
        self._last_spawn = now
        flags = (SIMULATION_RECOVERY_ARG,) if self._simulation_recovery else ()
        self._supervisor_starting = True
        def start():
            try:
                launch_watchdog(*flags)
            except Exception as exc:
                self.status_changed.emit(f"Watchdog unavailable: {exc}")
                self.log_message.emit(f"Watchdog startup failed: {exc}")
            finally:
                self._supervisor_starting = False
        # A packaged watchdog's version probe may include onefile extraction.
        threading.Thread(target=start, daemon=True).start()

    def disarm(self, reason="user_stopped"):
        if self._stop_signal:
            self._stop_signal.set()
        self._armed = False
        self._pending = None
        self._command_state = "cancelled"
        try:
            self._publish(reason)
        except OSError as exc:
            self.log_message.emit(f"Could not persist watchdog stop intent: {exc}")
        guard = self._update_guard
        if guard:
            guard.cancelled.set()
            def restore():
                self._update_status = guard.restore()
                self.log_message.emit("Windows Update: " + self._update_status)
            threading.Thread(target=restore, daemon=True).start()

    def close(self):
        self.disarm("closed")
        self._timer.stop()

    def accept_startup(self, token=None, *, simulation_recovery=False):
        """Called after login/UI creation. No auto-login or background desktop tricks."""
        self._select_recovery_mode(simulation_recovery)
        previous = read_json(self.directory / "active.json")
        if previous and previous.get("simulation_recovery", False) != simulation_recovery:
            raise RuntimeError("Startup and interrupted watchdog modes differ.")
        if token:
            command = read_json(self.directory / "command.json")
            if (not command or command.get("observe_only") or command.get("token") != token
                    or not previous or previous.get("observe_only") or not previous.get("armed")
                    or previous.get("status") not in ("armed", "starting")
                    or not previous.get("run_id") or previous["run_id"] != command.get("run_id")
                    or not previous.get("owner") or previous["owner"] != command.get("target_owner")):
                raise RuntimeError("Recovery command no longer matches the active interrupted run.")
            if owner_alive(previous["owner"]):
                raise RuntimeError("The previous application is still running.")
        else:
            if (not previous or previous.get("observe_only") or not previous.get("armed")
                    or previous.get("status") in ("closed", "user_stopped", "completed", "paused")
                    or owner_alive(previous.get("owner", {}))):
                return
            command = {"token": uuid.uuid4().hex, "run_id": previous["run_id"],
                       "target_owner": previous["owner"], "reason": "application_reopened"}
        signal = RunStopSignal(command["run_id"])
        if signal.is_set():
            raise RuntimeError("The interrupted run was explicitly cancelled.")
        path, _ = recovery_source(previous, reclaim=True)
        command = {**command, "record_path": path}
        self._run_id = command["run_id"]
        self._stop_signal = signal
        self._record_path = command["record_path"]
        self._armed = True
        self._pending = command
        self._ack = command["token"]
        self._command_state = "starting_recovery"
        self._publish("starting")
        self._continue_recovery()

    def _continue_recovery(self):
        if not self._pending:
            return
        if self.runner and self.runner.is_alive():
            return
        if self.tab.package_tab.sm.state in ("running", "aggregating"):
            return
        command = self._pending
        self._command_state = "starting_recovery"
        self._publish("starting")
        try:
            # Reload the record after the old runner has quiesced; never reuse a
            # snapshot captured before the cooperative interrupt took effect.
            self.tab.recover_from_record(command["record_path"])
            self._command_state = "running"
        except Exception as exc:
            self._command_state = "failed"
            self.log_message.emit(f"Automatic recovery failed: {exc}")
        self._pending = None
        self._publish()

    def _tick(self):
        if not self._armed:
            return
        try:
            if self.runner and self.runner._journal_failed:
                if self._stop_signal:
                    self._stop_signal.set()
                self._armed = False
                self._publish("recording_failed")
                self.status_changed.emit("Watchdog recovery disabled: execution recording failed")
                return
            outcome = getattr(self.runner, "outcome", "prepared")
            self._report_recovery_result(outcome)
            if outcome == "operator_required" and not self.runner.is_alive():
                self.disarm("operator_required")
                self.status_changed.emit("Watchdog: automatic recovery blocked; operator verification required")
                return
            if outcome in ("completed", "completed_after_interruption", "user_stopped"):
                self.disarm("user_stopped" if outcome == "user_stopped" else "completed")
                return
            if (self._observe_only and self._command_state == "observed"
                    and self.runner and not self.runner.is_alive()):
                self.disarm("simulation_interrupted")
                return
            command = read_json(self.directory / "command.json")
            if (command and command.get("run_id") == self._run_id and command.get("token") != self._ack
                    and command.get("target_owner") == self.owner):
                self._ack = command["token"]
                if self.runner and (self.runner.stop_event.is_set() or not self.runner.pause_event.is_set()):
                    self._command_state = "cancelled"
                elif self._observe_only:
                    message = f"Simulation watchdog: {command['reason']}; automatic recovery not executed."
                    self.log_message.emit(message)
                    self._command_state = "observed"
                    if self.runner and self.runner.is_alive():
                        self.runner.request_recovery(message)
                else:
                    self._pending = command
                    self._command_state = "waiting_worker"
                    if self.runner:
                        self.runner.request_recovery(command["reason"])
            self._publish()
            if not self._observe_only:
                self._continue_recovery()
            self._ensure_supervisor()
            if self._update_guard and not self._update_check_running and time.monotonic() - self._update_poll_at > 60:
                self._update_poll_at = time.monotonic()
                self._update_check_running = True
                guard = self._update_guard
                def verify_updates():
                    try:
                        status = guard.status(self._update_required_until)
                        if self._update_guard is guard and status != self._update_status:
                            self._update_status = status
                            self.log_message.emit("Windows Update: " + status)
                    except OSError as exc:
                        self.log_message.emit(f"Windows Update verification failed: {exc}")
                    finally:
                        self._update_check_running = False
                threading.Thread(target=verify_updates, daemon=True).start()
            supervisor = read_json(self.directory / "supervisor_status.json") or {}
            self._display_watchdog_status(supervisor)
        except Exception as exc:
            self.status_changed.emit(f"Watchdog unavailable: {exc}")
            self.log_message.emit(f"Watchdog client error: {exc}")

    def _report_recovery_result(self, outcome):
        runner = self.runner
        if not runner or not runner.recovery_record or runner is self._reported_recovery_runner:
            return
        if outcome == "completed_after_interruption":
            self.log_message.emit("Recovery finished: no recipe steps remain.")
        elif getattr(runner, "recovery_continued", False) is True:
            self.log_message.emit("Recovery verified: recipe resumed in the new package.")
        else:
            return
        self._reported_recovery_runner = runner

    def _display_watchdog_status(self, supervisor):
        if supervisor.get("run_id") != self._run_id:
            supervisor = {}
        warning = (self._run_id, supervisor.get("warning"))
        if warning != self._last_warning and warning[1]:
            self.log_message.emit("Watchdog warning: " + warning[1].replace("_", " "))
        self._last_warning = warning

        def display_time(key):
            value = supervisor.get(key)
            return (time.strftime("%H:%M:%S", time.localtime(self._wall_origin + value))
                    if isinstance(value, (int, float)) else "—")

        message = "Watchdog: " + supervisor.get("message", "starting")
        if supervisor.get("confirmed_fault"):
            message += " | Fault: " + supervisor["confirmed_fault"].replace("_", " ")
        if supervisor:
            message += (f" | Link read: {display_time('last_connection_read')}"
                        f" | CSV scan: {display_time('last_csv_scan')}"
                        f" | Valid data: {display_time('last_valid_csv')}")
        message += " | Updates: " + self._update_status
        if message != self._last_status:
            self._last_status = message
            self.status_changed.emit(message)
