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
from bench_test.watchdog.progress import CsvProgressProbe


TERMINAL = {"closed", "user_stopped", "completed", "paused"}
STAGE_TIMEOUT = {"waiting": 45, "valve_a": 90, "valve_b": 90, "pump": 90,
                 "dropview_start": 600, "measurement_start": 600,
                 "measurement_stop": 600, "dropview_exit": 600, "reconciling": 900}


def fault_reason(state, now, alive):
    if state.get("status") in TERMINAL or not state.get("armed"):
        return None
    if not alive:
        return "application_exited"
    ui_age = now - state.get("ui_heartbeat", now)
    if state.get("finalizing"):
        return "finalization_timeout" if ui_age > 900 else None
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
        if now - health.get("sampled_at", 0) < 20:
            if health.get("dv_open") is False:
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
    process = psutil.Process(identity["pid"])
    if abs(process.create_time() - identity["created"]) >= 0.01:
        return
    process.terminate()
    process.wait(timeout=15)


class Supervisor:
    def __init__(self, directory, clock=time.monotonic, is_alive=owner_alive,
                 start_app=launch, terminate=terminate_owner):
        self.directory = directory
        self.clock, self.is_alive = clock, is_alive
        self.start_app, self.terminate = start_app, terminate
        self.ledger = read_json(directory / "supervisor_ledger.json") or {}
        # Monotonic timestamps belong to one boot, unlike the durable retry count.
        self.boot = psutil.boot_time()
        if self.ledger and self.ledger.get("boot") != self.boot:
            self.ledger.pop("last_attempt", None)
            if self.ledger.get("pending"):
                self.ledger["pending"]["issued_at"] = self.clock()
                self.ledger["pending"]["launched_at"] = None
            self.ledger["boot"] = self.boot
            self.save()
        self.suspect = None
        self.suspect_since = 0
        self.healthy_since = None
        self.stop_signal = None
        self.signal_run = None
        self.progress = CsvProgressProbe()

    def save(self):
        atomic_write(self.directory / "supervisor_ledger.json", self.ledger)

    def publish(self, message):
        atomic_write(self.directory / "supervisor_status.json", {
            "owner": process_identity(), "heartbeat": self.clock(), "message": message,
            "attempts": self.ledger.get("attempts", 0),
        })

    def tick(self):
        now = self.clock()
        state = read_json(self.directory / "active.json")
        if not state:
            self.publish("idle")
            return True
        alive = self.is_alive(state.get("owner", {}))
        if state.get("status") in TERMINAL or not state.get("armed"):
            self.publish(state.get("status", "idle"))
            return alive and state.get("status") != "closed"
        if not alive:
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
        if self.ledger.get("run_id") != root:
            self.ledger = {"run_id": root, "attempts": 0, "boot": self.boot}
            self.save()
        pending = self.ledger.get("pending")
        if pending:
            acked = state.get("ack_token") == pending["token"]
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
                grace = 900 if state.get("finalizing") else (
                    180 if acked and state.get("command_state") == "starting_recovery" else 60)
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
                self.terminate(state["owner"])
                if self.is_alive(state["owner"]):
                    self.publish("blocked: application could not be stopped")
                    return True
                alive = False
            if pending and not alive:
                if pending.get("launched_at") is None:
                    path, _ = recovery_source(state, reclaim=True)
                    pending["record_path"] = path
                    atomic_write(self.directory / "command.json", {key: value for key, value in pending.items() if key != "launched_at"})
                    # Record intent before launching so a supervisor restart cannot
                    # launch the same recovery command twice.
                    pending["launched_at"] = now
                    self.save()
                    if not self.stop_signal.is_set():
                        self.start_app("--watchdog-recover", pending["token"])
                elif now - pending["launched_at"] > 180:
                    self.ledger.pop("pending", None)
                    self.save()
                self.publish("restarting_application")
                return True
        reason = fault_reason(state, now, alive)
        data_reason, data_notice = self.progress.observe(
            state.get("measurements_dir"), state.get("measurement_epoch"), now,
            active=alive and state.get("measurement_running"))
        reason = reason or (data_reason if state.get("phase") == "waiting" else None)
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
            self.publish(data_notice or message)
            return True
        self.healthy_since = None
        if reason != self.suspect:
            self.suspect, self.suspect_since = reason, now
        if reason.startswith(("dropview_", "dropsens_")) and now - self.suspect_since < 30:
            self.publish("checking:" + reason)
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
        atomic_write(self.directory / "command.json", command)
        self.ledger.update(attempts=self.ledger.get("attempts", 0) + 1,
                           last_attempt=now, pending={**command, "launched_at": None})
        self.save()
        self.publish("recovery_requested:" + reason)
        return True


def run_supervisor():
    directory = runtime_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bench_test.watchdog")
    handler = RotatingFileHandler(directory / "watchdog.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with ProcessLock(directory / "supervisor.lock"):
            supervisor = Supervisor(directory)
            last_message = None
            while True:
                try:
                    keep_running = supervisor.tick()
                    status = read_json(directory / "supervisor_status.json") or {}
                    if status.get("message") != last_message:
                        last_message = status.get("message")
                        logger.info(last_message)
                    active = read_json(directory / "active.json") or {}
                    if (active.get("status") in ("closed", "user_stopped", "completed")
                            or status.get("message") in ("user_stopped", "completed")):
                        from bench_test.watchdog.windows_update import WindowsUpdateGuard
                        WindowsUpdateGuard(directory, active.get("run_id", "")).restore()
                    if not keep_running:
                        return
                except Exception:
                    logger.exception("Watchdog observation failed")
                time.sleep(2)
    except RuntimeError:
        return  # Another supervisor owns this installation.
    finally:
        handler.close()
        logger.removeHandler(handler)
