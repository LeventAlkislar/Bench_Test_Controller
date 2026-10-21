"""Package reconstruction and device checks for Discrete PAD recovery."""
import copy
import json
import os
import shutil
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import datetime

from PyQt6.QtCore import QLockFile

from bench_test.measurement.session import MeasurementSession
from bench_test.recipe.recovery import (
    RecoveryJournal, plan_recovery, recipe_from_snapshot,
)
from bench_test.utils.debug_log import _debug_log_path


class RecipeExecutionLock:
    """Qt releases stale PID locks after a process dies; no timed lock stealing."""

    def __init__(self, path=None):
        path = path or os.path.join(os.path.dirname(_debug_log_path()), ".recipe_execution.lock")
        self._lock = QLockFile(path)
        self._lock.setStaleLockTime(0)

    def __enter__(self):
        if not self._lock.tryLock(0):
            raise RuntimeError("Another recipe or recovery is already controlling the devices.")
        return self

    def __exit__(self, *_):
        self._lock.unlock()


def create_recovery_package(record: dict, source_path: str):
    """Copy saved inputs without changing/deleting any old package or measurement."""
    plan = plan_recovery(record)
    source_dir = os.path.realpath(record["session_dir"])
    expected_path = os.path.join(source_dir, "logs", "recipe_recovery.json")
    if os.path.normcase(os.path.realpath(source_path)) != os.path.normcase(expected_path):
        raise ValueError("The recovery record does not belong to this session.")
    source = MeasurementSession.load(source_dir)
    if source.measurement_mode != "script_pad":
        raise ValueError("Source session is not Discrete PAD.")
    recipe = recipe_from_snapshot(record["recipe"])
    # Missing inputs must fail before creating a new package.
    method = source.get_file_path("method_discrete_pad") or source.get_file_path("tp")
    script = source.get_file_path("script")
    if not method or not script:
        raise ValueError("The source package has no saved PAD method or script.")
    tree = ET.parse(script)
    actions = tree.getroot().find("actions")
    if actions is None:
        raise ValueError("The saved script has no actions.")
    loads = actions.findall("action[@type='LOADMETHOD']/file")
    exports = actions.findall("action[@type='EXPORTCURVES']/file")
    if len(loads) != 1 or not exports:
        raise ValueError("Recovery requires one LOADMETHOD and at least one EXPORTCURVES action.")
    old_method = os.path.normcase(os.path.realpath(loads[0].text or ""))
    if old_method != os.path.normcase(os.path.realpath(method)):
        raise ValueError("The script refers to a different method; recovery cannot infer its settings.")

    created = datetime.now()
    directory = os.path.join(os.path.dirname(source_dir),
                             created.strftime("%Y%m%d_%H%M%S") + "_recovery_" + uuid.uuid4().hex[:8])
    os.makedirs(directory, exist_ok=False)
    session = MeasurementSession(directory, source.part_number, created,
                                 experiment_params=copy.deepcopy(source.experiment_params),
                                 measurement_mode="script_pad")
    for sub in (session.params_dir, session.measurements_dir, session.logs_dir):
        os.makedirs(sub, exist_ok=False)
    method_dest = os.path.join(session.params_dir, os.path.basename(method))
    shutil.copy2(method, method_dest)
    session.register_file("tp", method_dest)
    session.register_file("method_discrete_pad", method_dest)
    loads[0].text = method_dest
    for element in exports:
        element.text = os.path.join(session.measurements_dir, source.part_number + ".csv")
    script_dest = os.path.join(session.params_dir, os.path.basename(script))
    tree.write(script_dest, encoding="UTF-8", xml_declaration=True)
    session.register_file("script", script_dest)
    recipe_dest = os.path.join(session.params_dir, "recipe.json")
    with open(recipe_dest, "w", encoding="utf-8") as stream:
        json.dump(asdict(recipe), stream, ensure_ascii=False, indent=2, allow_nan=False)
    session.register_file("recipe", recipe_dest)
    origin = os.path.join(session.logs_dir, "recovery_origin.json")
    with open(origin, "w", encoding="utf-8") as stream:
        json.dump({"source_session": source_dir, "source_record": os.path.abspath(source_path),
                   "source_revision": record["revision"], "source_loop": record["recipe_loop"],
                   "source_sequence": record["sequence_index"], "source_phase": record["phase"],
                   "target": asdict(plan)}, stream, ensure_ascii=False, indent=2)
    session.register_file("recovery_origin", origin)
    journal = RecoveryJournal.create(session, recipe, record.get("connections", {}))
    journal.update(recovered_from=source_dir, source_record=os.path.abspath(source_path),
                   requested_settings=record.get("requested_settings", {}))
    session.register_file("recipe_recovery", journal.path)
    session.save()
    return session, recipe, journal, plan


def verify_recovery_valves(record, controller_a, controller_b):
    """Read actual positions. An unfinished move may still need its target applied."""
    observed = {}
    settings = record.get("requested_settings", {})
    for name, controller, query, maximum, pending_phase in (
        ("valve_a", controller_a, "get_current_port", 8, "valve_a"),
        ("valve_b", controller_b, "get_current_state", 2, "valve_b"),
    ):
        config = record.get("connections", {}).get(name, {})
        if controller is None:
            raise RuntimeError(f"{name}: controller is unavailable.")
        if not controller.is_connected():
            if not config.get("port"):
                raise RuntimeError(f"{name}: saved serial connection is unavailable.")
            controller.address = int(config["address"])
            if not controller.connect(config["port"], int(config["baudrate"])):
                raise RuntimeError(f"{name}: connection failed.")
        else:
            port = controller.serial_port
            if (config.get("port") != port.port or config.get("baudrate") != port.baudrate
                    or config.get("address") != controller.address):
                raise RuntimeError(f"{name}: current connection differs from the saved device.")
        response = getattr(controller, query)()
        position = response.get("param1")
        if (not response.get("success") or response.get("status") != 0
                or type(position) is not int or not 1 <= position <= maximum):
            raise RuntimeError(f"{name}: position could not be verified: {response}")
        expected = settings.get(name)
        if expected is not None and position != expected and record["phase"] != pending_phase:
            raise RuntimeError(f"{name}: expected {expected}, actual {position}; recovery blocked.")
        observed[name] = position
    return observed


def reconnect_recovery_pump(record, recipe, pump):
    """Preserve output; never reopen an Arduino port or send startup STOP/SPEED."""
    needed = (record.get("requested_settings", {}).get("pump_rpm") is not None
              or any(step.pump_rpm is not None for step in recipe.steps))
    if not needed:
        return
    if pump is None:
        raise RuntimeError("Recovery requires the saved pump controller.")
    config = record.get("connections", {}).get("pump", {})
    pump_type = config.get("pump_type")
    if pump.is_connected():
        if pump.active_type != pump_type or pump.port != config.get("pump_com"):
            raise RuntimeError("The connected pump differs from the saved pump.")
        return
    if pump_type != "flexicon":
        raise RuntimeError("Arduino serial reopen can reset the pump; automatic reconnect is disabled.")
    if not config.get("pump_com"):
        raise RuntimeError("Saved pump port is missing.")
    pump.select_type("flexicon")
    pump.configure_flexicon(config.get("flexicon_pump_id") or 1,
                            config.get("flexicon_baudrate") or 9600)
    if not pump.connect(config["pump_com"]):
        raise RuntimeError("Pump serial connection could not be restored.")
