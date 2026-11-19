"""Separate process. Never sends pump/valve/DropView commands itself."""
import logging
import os
import time
import uuid
from logging.handlers import RotatingFileHandler

import psutil

from bench_test.watchdog.state import (
    ProcessLock, RunStopSignal, atomic_write, launch, owner_alive, process_identity, read_json,
    recovery_source, runtime_directory,
)
from bench_test.watchdog.state import process_exited as dropview_process_exited
from bench_test.watchdog.progress import CsvProgressProbe


TERMINAL = {"closed", "user_stopped", "completed", "paused"}
DEVICE_FAULT_CONFIRM_SECONDS = 15
SUPERVISOR_POLL_INTERVAL = 5.0
STATUS_PUBLISH_INTERVAL = 10.0
AGGREGATION_WARNING_SECONDS = 900.0
STAGE_TIMEOUT = {"waiting": 45, "valve_a": 90, "valve_b": 90, "pump": 90,
                 "dropview_start": 600, "measurement_start": 600,
                 "measurement_stop": 600, "dropview_exit": 600, "reconciling": 900}


def fault_reason(state, now, alive):
    if state.get("status") in TERMINAL or not state.get("armed"):
        return None
    if not alive:
        return "application_exited"
    ui_age = now - state.get("ui_heartbeat", now)
    if state.get("status") == "starting":
        return "startup_timeout" if ui_age > 180 else None
    if ui_age > 60:
        return "ui_not_responding"
    if not state.get("worker_alive"):
        return "worker_exited" if state.get("outcome") not in ("prepared", "completed", "completed_after_interruption", "user_stopped") else None
    phase = state.get("phase", "prepared")
    limit = STAGE_TIMEOUT.get(phase, 90)
    if now - state.get("worker_heartbeat", now) > limit:
        return f"worker_timeout:{phase}"
    if phase == "waiting" and state.get("measurement_running"):
        health = state.get("dropview_health") or {}
        if 0 <= now - health.get("sampled_at", 0) < 20:
            if health.get("dv_open") is False:
                if dropview_process_exited(health.get("process_identity")):
                    return "dropview_process_exited"
                return "dropview_missing"
            if health.get("responding") is False:
                return "dropview_not_responding"
            if health.get("state") == "disconnected":
                return "dropsens_disconnected"
        # Covered/minimized windows yield unknown, not proof of a device crash.
    return None


def terminate_owner(identity):
    if identity.get("pid") == os.getpid() or not owner_alive(identity):
        return
    try:
        process = psutil.Process(identity["pid"])
        if abs(process.create_time() - identity["created"]) >= 0.01:
            return
        process.terminate()
        process.wait(timeout=15)
    except psutil.NoSuchProcess:
        pass


class Supervisor:
    def __init__(self, directory, clock=time.monotonic, is_alive=owner_alive,
                 start_app=launch, terminate=terminate_owner):
        self.directory = directory
        self.clock, self.is_alive = clock, is_alive
        self.start_app, self.terminate = start_app, terminate
        self.check_errors = {}
        self._ledger_dirty = False
        try:
            self.ledger = read_json(directory / "supervisor_ledger.json") or {}
        except (OSError, ValueError) as exc:
            self.check_errors["ledger_read"] = str(exc)
            self.ledger = {}
        # Monotonic timestamps belong to one boot, unlike the durable retry count.
        self.boot = psutil.boot_time()
        if self.ledger and self.ledger.get("boot") != self.boot:
            self.ledger.pop("last_attempt", None)
            if self.ledger.get("pending"):
                self.ledger["pending"]["issued_at"] = self.clock()
                self.ledger["pending"]["launched_at"] = None
            self.ledger["boot"] = self.boot
            try:
                self.save()
            except (OSError, ValueError) as exc:
                self.check_errors["recovery_control"] = str(exc)
        self.suspect = None
        self.suspect_since = 0
        self.suspect_samples = 0
        self.suspect_sample_at = None
        self.last_connection_read = None
        self.connection_unverified_since = None
        self.observation_warning = None
        self.confirmed_fault = None
        self.current_run = None
        self.healthy_since = None
        self.stop_signal = None
        self.signal_run = None
        self.progress = CsvProgressProbe()
        self.last_state = None
        self.last_active_read = None
        self.active_read_ok = False
        self.aggregation_started = None
        self.last_status = {}
        self._last_publish_at = -float("inf")
        self._last_publish_signature = None

    def save(self):
        self._ledger_dirty = True
        atomic_write(self.directory / "supervisor_ledger.json", self.ledger)
        self._ledger_dirty = False

    def publish(self, message, warning=None):
        now = self.clock()
        retry_status = self.check_errors.pop("status_write", None) is not None
        status = {
            "owner": process_identity(), "heartbeat": now, "message": message,
            "run_id": self.current_run,
            "warning": warning or self.observation_warning,
            "confirmed_fault": self.confirmed_fault,
            "last_connection_read": self.last_connection_read,
            "last_csv_scan": self.progress.last_successful_scan,
            "last_csv_progress": self.progress.last_change,
            "last_valid_csv": self.progress.content_last_progress,
            "attempts": self.ledger.get("attempts", 0),
            "check_errors": dict(self.check_errors),
            "last_active_read": self.last_active_read,
            "active_read_ok": self.active_read_ok,
            "aggregation_elapsed": (max(0.0, now - self.aggregation_started)
                                    if self.aggregation_started is not None else None),
            "aggregation_warning": ("aggregation_taking_long" if self.aggregation_started is not None
                                    and now - self.aggregation_started >= AGGREGATION_WARNING_SECONDS else None),
        }
        self.last_status = status
        signature = tuple(status[key] for key in (
            "owner", "message", "run_id", "warning", "confirmed_fault", "attempts"))
        signature += (tuple(sorted(self.check_errors.items())), self.active_read_ok, status["aggregation_warning"])
        if not retry_status and signature == self._last_publish_signature and now - self._last_publish_at < STATUS_PUBLISH_INTERVAL:
            return
        try:
            atomic_write(self.directory / "supervisor_status.json", status)
        except (OSError, ValueError, TypeError) as exc:
            self.check_errors["status_write"] = str(exc)
            status["check_errors"] = dict(self.check_errors)
            return
        self._last_publish_at, self._last_publish_signature = now, signature

    def _observe_health(self, state, now):
        if (state.get("worker_alive") and state.get("status") != "starting"
                and 30 <= now - state.get("ui_heartbeat", now) <= 60):
            self.observation_warning = "ui_response_delayed"
        if state.get("phase") != "waiting" or not state.get("measurement_running"):
            self.connection_unverified_since = None
            return
        health = state.get("dropview_health") or {}
        sampled_at = health.get("sampled_at", 0)
        if (0 <= now - sampled_at < 20 and health.get("dv_open") is not False
                and health.get("state") in ("connected", "disconnected")):
            self.last_connection_read = sampled_at
            self.connection_unverified_since = None
        else:
            if self.connection_unverified_since is None:
                self.connection_unverified_since = self.last_connection_read if (
                    self.last_connection_read is not None and now - self.last_connection_read < 30) else now
            if now - self.connection_unverified_since >= 30 and self.observation_warning is None:
                self.observation_warning = "connection_read_unverified"

    def tick(self):
        now = self.clock()
        try:
            state = read_json(self.directory / "active.json")
            if state is None and self.last_state is not None:
                raise ValueError("Active state file is missing.")
            self.active_read_ok = True
            self.last_active_read = now
            self.last_state = state
            self.check_errors.pop("active_read", None)
        except (OSError, ValueError, TypeError) as exc:
            self.active_read_ok = False
            self.check_errors["active_read"] = str(exc)
            state = self.last_state
        self.observation_warning = self.confirmed_fault = None
        if state and state.get("run_id") != self.current_run:
            self.aggregation_started = None
        if state and state.get("finalizing"):
            started = state.get("finalizing_started")
            if isinstance(started, (int, float)) and 0 <= started <= now:
                self.aggregation_started = started
            elif self.aggregation_started is None:
                self.aggregation_started = now
            if now - self.aggregation_started >= AGGREGATION_WARNING_SECONDS:
                self.observation_warning = "aggregation_taking_long"
        else:
            self.aggregation_started = None
        if not state:
            self.publish("active_state_unavailable" if not self.active_read_ok else "idle")
            return True
        root = state.get("run_id")
        if root != self.current_run:
            if self.current_run is not None:
                self.progress.reset()
            self.current_run = root
            self.last_connection_read = self.connection_unverified_since = None
        alive = None
        reason = data_reason = data_notice = None
        try:
            alive = self.is_alive(state.get("owner", {}))
            self.check_errors.pop("process_check", None)
        except (OSError, ValueError, KeyError, TypeError, psutil.Error) as exc:
            self.check_errors["process_check"] = str(exc)
        try:
            if alive is not None:
                reason = fault_reason(state, now, alive)
                if alive:
                    self._observe_health(state, now)
            self.check_errors.pop("health_check", None)
        except Exception as exc:
            self.check_errors["health_check"] = str(exc)
        try:
            data_reason, data_notice = self.progress.observe(
                state.get("measurements_dir"), state.get("measurement_epoch"), now,
                active=bool(alive and state.get("armed") and state.get("measurement_running")
                            and state.get("status") not in TERMINAL),
                expectation=state.get("csv_expectation"), started_ns=state.get("measurement_started_ns"))
            self.check_errors.pop("csv_check", None)
        except Exception as exc:
            self.check_errors["csv_check"] = str(exc)
            self.progress.reset()
        reason = reason or (data_reason if state.get("phase") == "waiting" else None)
        if not self.active_read_ok or alive is None:
            # Cached context permits observation, never process termination or
            # replaying recipe commands (a Stop may have occurred meanwhile).
            self.publish("observation_incomplete", warning="active_state_unavailable" if not self.active_read_ok else "process_check_unavailable")
            return True
        try:
            if "ledger_read" in self.check_errors:
                self.ledger = read_json(self.directory / "supervisor_ledger.json") or {}
                if self.ledger and self.ledger.get("boot") != self.boot:
                    self.ledger.pop("last_attempt", None)
                    if self.ledger.get("pending"):
                        self.ledger["pending"].update(issued_at=now, launched_at=None)
                    self.ledger["boot"] = self.boot
                    self.save()
                self.check_errors.pop("ledger_read")
            self.check_errors.pop("recovery_control", None)
            result = self._advance(state, now, alive, reason, data_notice)
            return result
        except Exception as exc:
            self.check_errors["recovery_control"] = str(exc)
            self.publish("recovery_control_unavailable")
            return True

    def _advance(self, state, now, alive, reason, data_notice):
        if state.get("status") in TERMINAL or not state.get("armed"):
            self.connection_unverified_since = None
            self.publish(state.get("status", "idle"))
            return alive and state.get("status") != "closed"
        if not alive and state.get("recovery_available") is not False:
            # Completion may reach the journal before the UI publishes it.
            record = read_json(state.get("record_path", "")) or {}
            if record.get("status") in ("completed", "completed_after_interruption", "user_stopped"):
                self.publish("user_stopped" if record["status"] == "user_stopped" else "completed")
                return False
        root = state["run_id"]
        if self.signal_run != root:
            self.stop_signal, self.signal_run = RunStopSignal(root), root
        if self.stop_signal.is_set():
            self.publish("user_stopped")
            return alive
        if self._ledger_dirty:
            self.save()
        if self.ledger.get("run_id") != root:
            self.ledger = {"run_id": root, "attempts": 0, "boot": self.boot}
            self.suspect = None
            self.healthy_since = None
            self.save()
        recovery_blocked = state.get("recovery_available") is False
        if recovery_blocked and self.ledger.pop("pending", None):
            self.save()
        pending = self.ledger.get("pending")
        if pending:
            acked = state.get("ack_token") == pending["token"]
            if not acked and state.get("owner") == pending["target_owner"] and pending.get("launched_at") is None:
                atomic_write(self.directory / "command.json", {key: value for key, value in pending.items() if key != "launched_at"})
            if not acked and state.get("owner") != pending["target_owner"]:
                self.ledger.pop("pending", None)
                self.save()
                self.publish("application_owner_changed")
                return True
            if acked and state.get("command_state") in ("running", "failed", "cancelled"):
                self.ledger.pop("pending", None)
                self.save()
                pending = None
            elif alive:
                self.confirmed_fault = pending["reason"]
                grace = 180 if acked and state.get("command_state") == "starting_recovery" else 60
                if now - pending["issued_at"] < grace:
                    self.publish("recovery_requested")
                    return True
                try:
                    recovery_source(state, allow_in_flight=True)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.ledger.pop("pending", None)
                    self.save()
                    self.publish("blocked: " + str(exc))
                    return True
                # Verify the PID creation time again immediately before termination.
                if self.stop_signal.is_set():
                    return True
                try:
                    self.terminate(state["owner"])
                    if self.is_alive(state["owner"]):
                        raise RuntimeError("process still running")
                except (psutil.Error, OSError, RuntimeError) as exc:
                    self.ledger.pop("pending", None)
                    self.ledger["last_attempt"] = now
                    self.save()
                    self.publish("blocked: application could not be stopped: " + str(exc))
                    return True
                alive = False
            if pending and not alive:
                self.confirmed_fault = pending["reason"]
                if pending.get("launched_at") is None:
                    path, _ = recovery_source(state, reclaim=True)
                    pending["record_path"] = path
                    atomic_write(self.directory / "command.json", {key: value for key, value in pending.items() if key != "launched_at"})
                    # Record intent before launching so a supervisor restart cannot
                    # launch the same recovery command twice.
                    pending["launched_at"] = now
                    try:
                        self.save()
                    except (OSError, ValueError, TypeError):
                        pending["launched_at"] = None
                        raise
                    if not self.stop_signal.is_set():
                        self.start_app("--watchdog-recover", pending["token"])
                elif now - pending["launched_at"] > 180:
                    self.ledger.pop("pending", None)
                    self.save()
                self.publish("restarting_application")
                return True
        if not reason:
            self.suspect = None
            health = state.get("dropview_health") or {}
            stable = (state.get("worker_alive") and state.get("outcome") == "running"
                      and state.get("phase") == "waiting" and health.get("state") == "connected"
                      and health.get("responding") is True)
            if not stable:
                self.healthy_since = None
            elif self.healthy_since is None:
                self.healthy_since = now
            if self.healthy_since is not None and now - self.healthy_since >= 300 and self.ledger.get("attempts"):
                self.ledger["attempts"] = 0
                self.save()
            unverified = (not health or health.get("state") == "unknown"
                          or now - health.get("sampled_at", 0) >= 20)
            message = "connection_not_verified" if state.get("measurement_running") and unverified else "healthy"
            warning = data_notice
            if data_notice == "measurement_storage_unavailable" and self.progress.storage_failures < 2:
                warning = None
            self.publish("recovery_blocked: recording unavailable" if recovery_blocked else
                         "observation_incomplete" if self.check_errors else data_notice or message,
                         warning=warning)
            return True
        self.healthy_since = None
        if reason != self.suspect:
            self.suspect, self.suspect_since = reason, now
            self.suspect_samples = 0
            self.suspect_sample_at = None
        if reason != "dropview_process_exited" and reason.startswith(("dropview_", "dropsens_")):
            sampled_at = (state.get("dropview_health") or {}).get("sampled_at")
            if sampled_at is not None and (self.suspect_sample_at is None or sampled_at > self.suspect_sample_at):
                self.suspect_samples += 1
                self.suspect_sample_at = sampled_at
            # The deadline alone cannot confirm a fault from a cached reading.
            if sampled_at is None or sampled_at - self.suspect_since < DEVICE_FAULT_CONFIRM_SECONDS:
                limit = 2 if reason == "dropview_missing" else 3
                self.publish("checking:" + reason, warning=reason if self.suspect_samples >= limit else None)
                return True
        self.confirmed_fault = reason
        if recovery_blocked:
            self.publish("recovery_blocked: recording unavailable", warning=reason)
            return True
        if self.ledger.get("attempts", 0) >= 3:
            self.publish("blocked: recovery retry limit reached")
            return True
        if now - self.ledger.get("last_attempt", -1000) < 30 * max(1, self.ledger.get("attempts", 0)):
            self.publish("retry_backoff")
            return True
        try:
            path, _ = recovery_source(state, reclaim=not alive, allow_in_flight=alive)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.publish("blocked: " + str(exc))
            return True
        token = uuid.uuid4().hex
        command = {"token": token, "run_id": root, "record_path": path,
                   "target_owner": state["owner"], "reason": reason, "issued_at": now}
        self.ledger.update(attempts=self.ledger.get("attempts", 0) + 1,
                           last_attempt=now, pending={**command, "launched_at": None})
        self.save()
        atomic_write(self.directory / "command.json", command)
        self.publish("recovery_requested:" + reason)
        return True


class WatchdogLogHandler(RotatingFileHandler):
    def handleError(self, record):
        # Surface handler failures to the isolated logging stage below.
        raise


def log_safely(logger, method, *args):
    try:
        getattr(logger, method)(*args)
        return None
    except Exception as exc:
        return str(exc)


def run_supervisor(*, app_executable=None):
    directory = runtime_directory(app_executable=app_executable)
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bench_test.watchdog")
    handler = WatchdogLogHandler(directory / "watchdog.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with ProcessLock(directory / "supervisor.lock"):
            supervisor = Supervisor(
                directory, start_app=lambda *args: launch(*args, executable=app_executable))
            last_message = None
            last_warning = None
            while True:
                try:
                    keep_running = supervisor.tick()
                    status = supervisor.last_status
                    if status.get("message") != last_message:
                        error = log_safely(logger, "info", status.get("message"))
                        if error:
                            supervisor.check_errors["logging"] = error
                        else:
                            supervisor.check_errors.pop("logging", None)
                            last_message = status.get("message")
                    warning = (status.get("run_id"), status.get("warning"))
                    if warning != last_warning and warning[1]:
                        error = log_safely(logger, "warning", "Prewarning: %s", warning[1])
                        if error:
                            supervisor.check_errors["logging"] = error
                        else:
                            supervisor.check_errors.pop("logging", None)
                            last_warning = warning
                    else:
                        last_warning = warning
                    active = supervisor.last_state or {}
                    if supervisor.active_read_ok and (
                            active.get("status") in ("closed", "user_stopped", "completed")
                            or status.get("message") in ("user_stopped", "completed")):
                        from bench_test.watchdog.windows_update import WindowsUpdateGuard
                        WindowsUpdateGuard(directory, active.get("run_id", "")).restore()
                    if not keep_running:
                        return
                except Exception:
                    log_safely(logger, "exception", "Watchdog observation failed")
                time.sleep(SUPERVISOR_POLL_INTERVAL)
    except RuntimeError:
        return  # Another supervisor owns this installation.
    finally:
        handler.close()
        logger.removeHandler(handler)
