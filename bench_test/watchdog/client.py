import math
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bench_test.recipe.recovery import expanded_step_indices
from bench_test.watchdog.state import (
    RunStopSignal, atomic_write, launch, owner_alive, process_identity, read_json, recovery_source,
    runtime_directory,
)
from bench_test.watchdog.windows_update import WindowsUpdateGuard


class WatchdogClient(QObject):
    status_changed = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(self, recipe_tab, dv_controller, parent=None, directory=None):
        super().__init__(parent)
        self.tab, self.dv = recipe_tab, dv_controller
        self.directory = directory or runtime_directory()
        self.owner = process_identity()
        self.runner = None
        self._armed = False
        self._run_id = ""
        self._record_path = ""
        self._pending = None
        self._ack = None
        self._command_state = ""
        self._finalizing = False
        self._last_spawn = -1000
        self._last_status = ""
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

    def arm(self, runner):
        try:
            self._arm(runner)
        except Exception as exc:
            # The recipe has already started; report monitoring failure without
            # throwing out of the Qt start callback or claiming it never started.
            self.status_changed.emit(f"Watchdog unavailable: {exc}")
            self.log_message.emit(f"Watchdog arming failed: {exc}")

    def _arm(self, runner):
        if runner.recovery_journal is None or runner._journal_failed:
            return
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
        self._record_path = runner.recovery_journal.path
        runner.recovery_journal.update(watchdog_run_id=run_id)
        self._armed = True
        self._publish()
        if changed_run or self._update_guard is None:
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

    def _publish(self, status=None):
        if not self._run_id:
            return
        runner = self.runner
        paused = runner is not None and not runner.pause_event.is_set()
        mode = status or ("paused" if paused else "armed")
        atomic_write(self.directory / "active.json", {
            "version": 1, "owner": self.owner, "run_id": self._run_id,
            "armed": self._armed, "status": mode, "ui_heartbeat": time.monotonic(),
            "worker_heartbeat": getattr(runner, "worker_heartbeat", time.monotonic()),
            "worker_alive": bool(runner and runner.is_alive()),
            "outcome": getattr(runner, "outcome", "prepared"),
            "phase": getattr(runner, "_recovery_phase", "prepared"),
            "record_path": self._record_path,
            "recovery_source": getattr(runner, "recovery_source_path", ""),
            "measurement_running": bool(runner and runner.measurement_running),
            "measurements_dir": getattr(runner, "session_measurements_dir", ""),
            "measurement_epoch": getattr(runner, "measurement_epoch", 0),
            "dropview_health": getattr(self.dv, "last_health", {}),
            "finalizing": self._finalizing, "ack_token": self._ack,
            "command_state": self._command_state,
        })

    def set_finalizing(self, active):
        self._finalizing = active
        if self._armed:
            self._publish()

    def _ensure_supervisor(self):
        now = time.monotonic()
        if now - self._last_spawn < 15:
            return
        state = read_json(self.directory / "supervisor_status.json")
        if state and owner_alive(state.get("owner", {})):
            return
        self._last_spawn = now
        launch("--watchdog")

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

    def accept_startup(self, token=None):
        """Called after login/UI creation. No auto-login or background desktop tricks."""
        if token:
            command = read_json(self.directory / "command.json")
            if not command or command.get("token") != token or owner_alive(command.get("target_owner", {})):
                raise RuntimeError("Invalid recovery command or previous application is still running.")
            previous = read_json(self.directory / "active.json")
            if previous and previous.get("run_id") == command["run_id"]:
                path, _ = recovery_source(previous, reclaim=True)
                command = {**command, "record_path": path}
        else:
            previous = read_json(self.directory / "active.json")
            if (not previous or not previous.get("armed") or previous.get("status") in ("closed", "user_stopped", "completed", "paused")
                    or owner_alive(previous.get("owner", {}))):
                return
            path, _ = recovery_source(previous, reclaim=True)
            command = {"token": uuid.uuid4().hex, "run_id": previous["run_id"], "record_path": path,
                       "target_owner": previous["owner"], "reason": "application_reopened"}
        self._run_id = command["run_id"]
        self._stop_signal = RunStopSignal(self._run_id)
        if self._stop_signal.is_set():
            raise RuntimeError("The interrupted run was explicitly cancelled.")
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
            if outcome in ("completed", "completed_after_interruption", "user_stopped"):
                self.disarm("user_stopped" if outcome == "user_stopped" else "completed")
                return
            command = read_json(self.directory / "command.json")
            if (command and command.get("run_id") == self._run_id and command.get("token") != self._ack
                    and command.get("target_owner") == self.owner):
                self._ack = command["token"]
                if self.runner and (self.runner.stop_event.is_set() or not self.runner.pause_event.is_set()):
                    self._command_state = "cancelled"
                else:
                    self._pending = command
                    self._command_state = "waiting_worker"
                    if self.runner:
                        self.runner.request_recovery(command["reason"])
            self._publish()
            self._continue_recovery()
            self._ensure_supervisor()
            if self._update_guard and not self._update_check_running and time.monotonic() - self._update_poll_at > 60:
                self._update_poll_at = time.monotonic()
                self._update_check_running = True
                guard = self._update_guard
                def verify_updates():
                    try:
                        self._update_status = guard.status(self._update_required_until)
                    except OSError as exc:
                        self.log_message.emit(f"Windows Update verification failed: {exc}")
                    finally:
                        self._update_check_running = False
                threading.Thread(target=verify_updates, daemon=True).start()
            supervisor = read_json(self.directory / "supervisor_status.json") or {}
            message = "Watchdog: " + supervisor.get("message", "starting") + " | Updates: " + self._update_status
            if message != self._last_status:
                self._last_status = message
                self.status_changed.emit(message)
        except Exception as exc:
            self.status_changed.emit(f"Watchdog unavailable: {exc}")
            self.log_message.emit(f"Watchdog client error: {exc}")
