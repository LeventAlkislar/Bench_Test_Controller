import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil
from PyQt6.QtCore import QLockFile

SIMULATION_RECOVERY_ARG = "--simulation-recovery"
WATCHDOG_EXECUTABLE = "BenchTestWatchdog.exe"


def runtime_directory(simulation_recovery=False, *, app_executable=None):
    installation = app_executable
    if installation is None:
        installation = sys.executable if getattr(sys, "frozen", False) else str(Path(__file__).resolve().parents[2])
    key = hashlib.sha256(os.path.normcase(installation).encode("utf-8")).hexdigest()[:16]
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(base) / "BenchTestController" / "watchdog" / key
    return directory / "simulation" if simulation_recovery else directory


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".watchdog_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"Invalid watchdog state: {path}")
    return data


def process_identity(pid=None):
    process = psutil.Process(pid or os.getpid())
    return {"pid": process.pid, "created": process.create_time()}


def owner_alive(identity):
    try:
        process = psutil.Process(identity["pid"])
        return (process.is_running() and abs(process.create_time() - identity["created"]) < 0.01
                and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.AccessDenied:
        return True
    except (psutil.Error, KeyError, TypeError, ValueError):
        return False


def process_exited(identity):
    """Confirm exit of the recorded process without treating access errors as exit."""
    if not identity:
        return False
    try:
        process = psutil.Process(identity["pid"])
        return abs(process.create_time() - identity["created"]) >= 0.01 or not process.is_running()
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, KeyError, TypeError, ValueError):
        return False


def app_command(*arguments):
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-B", str(Path(__file__).resolve().parents[2] / "main.py"), *arguments]


def launch(*arguments, executable=None):
    options = {"close_fds": True, "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}
    if executable is not None:
        # Each packaged program must own its extraction directory and process lifetime.
        options["env"] = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        return subprocess.Popen([executable, *arguments], **options)
    return subprocess.Popen(app_command(*arguments), **options)


def launch_watchdog(*arguments):
    if getattr(sys, "frozen", False):
        watchdog = Path(sys.executable).with_name(WATCHDOG_EXECUTABLE)
        if watchdog.is_file():
            return launch("--app-executable", sys.executable, *arguments, executable=str(watchdog))
    return launch("--watchdog", *arguments)


class ProcessLock:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(path))
        self.lock.setStaleLockTime(0)

    def __enter__(self):
        if not self.lock.tryLock(0):
            raise RuntimeError("Another instance already owns this watchdog/application.")
        return self

    def __exit__(self, *_):
        self.lock.unlock()


class RunStopSignal:
    """Immediate cancellation even while journal/disk I/O is blocked."""
    def __init__(self, run_id):
        import win32event
        self.handle = win32event.CreateEvent(None, True, False, "Local\\BenchTestController_Stop_" + run_id)

    def set(self):
        import win32event
        win32event.SetEvent(self.handle)

    def is_set(self):
        import win32event
        return win32event.WaitForSingleObject(self.handle, 0) == 0


def recovery_source(state, reclaim=False, allow_in_flight=False):
    """Prefer the child cursor; reclaim a parent only after its worker is gone."""
    from bench_test.recipe.recovery import RecoveryJournal, plan_recovery
    path = state["record_path"]
    record = RecoveryJournal.read(path)
    simulation_recovery = state.get("simulation_recovery", False)
    if record.get("simulation_recovery", False) != simulation_recovery:
        raise ValueError("Watchdog and recovery record modes differ.")
    if record.get("stop_requested") or record.get("closed_by_user") or record.get("paused"):
        raise ValueError("User stop/close/pause forbids automatic recovery.")
    if record.get("status") in ("recovering", "recovery_blocked", "prepared") and state.get("recovery_source"):
        parent_path = state["recovery_source"]
        parent = RecoveryJournal.read(parent_path)
        if parent.get("simulation_recovery", False) != simulation_recovery:
            raise ValueError("Parent and child recovery record modes differ.")
        child_dir = os.path.normcase(os.path.realpath(record["session_dir"]))
        linked_child = parent.get("recovery_session")
        same_child = bool(linked_child and os.path.normcase(os.path.realpath(linked_child)) == child_dir)
        saved_parent = record.get("source_record")
        if saved_parent:
            if os.path.normcase(os.path.realpath(saved_parent)) != os.path.normcase(os.path.realpath(parent_path)):
                raise ValueError("Recovery attempt does not belong to this parent record.")
        elif not same_child:
            raise ValueError("Recovery attempt does not belong to this child session.")
        if parent.get("status") == "recovering" and (reclaim or allow_in_flight):
            if not same_child:
                raise ValueError("Recovery attempt does not belong to this child session.")
            candidate = {**parent, "status": "interrupted"}
            if reclaim:
                plan_recovery(candidate)
                RecoveryJournal(parent_path, parent).update(status="interrupted", recovery_error="Worker process exited during recovery.")
                parent = RecoveryJournal.read(parent_path)
            else:
                parent = candidate
        path, record = parent_path, parent
    plan_recovery(record)
    return str(path), record
