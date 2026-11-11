"""Durable Discrete PAD execution records and side-effect-free recovery planning.

This module does not restart software or issue hardware commands. A recovery
plan must be reconciled with the real devices before a future executor uses it.
"""
import copy
import json
import math
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from bench_test.recipe.models import Recipe, RecipeStep, StepLoop


class RecoveryVerificationBlocked(RuntimeError):
    """Automatic continuation requires evidence that software cannot currently obtain."""


SCHEMA_VERSION = 1
EDITOR_CONTROL_UNVERIFIED = "editor_control_unverified"
RECORD_FILENAME = "recipe_recovery.json"


def is_discrete_pad_recipe(recipe: Recipe) -> bool:
    return all(step.dropview_action in (
        "none", "start_dropview", "start_measure", "start_discrete_pad",
        "stop_measure", "stop_discrete_pad", "exit_dropview",
    ) for step in recipe.steps)


def validate_recovery_step(step: RecipeStep):
    if (type(step.port) is not int or not 0 <= step.port <= 8
            or type(step.valve_b_state) is not int or not 0 <= step.valve_b_state <= 2
            or type(step.enabled) is not bool
            or type(step.duration_minutes) not in (int, float)
            or not math.isfinite(step.duration_minutes) or step.duration_minutes < 0
            or (step.pump_rpm is not None and (type(step.pump_rpm) not in (int, float)
                or not math.isfinite(step.pump_rpm) or step.pump_rpm < 0))):
        raise ValueError("Invalid device settings or duration in the recovery step.")


def expanded_step_indices(recipe: Recipe) -> list[int]:
    """Use the runner's existing 0-based expansion, including disabled steps."""
    indices = []
    index = 0
    while index < len(recipe.steps):
        for loop in recipe.step_loops:
            if loop.start_step - 1 == index:
                for _ in range(loop.loop_count):
                    indices.extend(range(loop.start_step - 1, loop.end_step))
                index = loop.end_step
                break
        else:
            indices.append(index)
            index += 1
    return indices


def recipe_from_snapshot(data: dict) -> Recipe:
    recipe = Recipe(
        name=data["name"],
        steps=[RecipeStep(**step) for step in data["steps"]],
        loop_count=data["loop_count"],
        step_loops=[StepLoop(**loop) for loop in data.get("step_loops", [])],
    )
    if type(recipe.loop_count) is not int or recipe.loop_count < 1 or not recipe.steps:
        raise ValueError("Invalid recipe loop count or empty recipe in recovery record.")
    for step in recipe.steps:
        validate_recovery_step(step)
    previous_end = 0
    for loop in sorted(recipe.step_loops, key=lambda item: item.start_step):
        if (any(type(value) is not int for value in
                (loop.start_step, loop.end_step, loop.loop_count))
                or not previous_end < loop.start_step <= loop.end_step <= len(recipe.steps)
                or loop.loop_count < 1):
            raise ValueError("Invalid or overlapping step loop in recovery record.")
        previous_end = loop.end_step
    return recipe


class RecoveryJournal:
    """One writer object per run; replace the last complete JSON atomically."""

    def __init__(self, path: str, initial: dict):
        self.path = os.path.abspath(path)
        self._lock = threading.Lock()
        self._data = copy.deepcopy(initial)

    @classmethod
    def create(cls, session, recipe: Recipe, connections: dict, *, simulation_recovery=False):
        journal = cls(os.path.join(session.logs_dir, RECORD_FILENAME), {
            "schema_version": SCHEMA_VERSION,
            "session_dir": session.session_dir,
            "measurement_mode": session.measurement_mode,
            "session_snapshot": session.to_dict(),
            "connections": connections,
            "simulation_recovery": simulation_recovery,
            "recipe": asdict(recipe),
            "status": "prepared",
            "phase": "prepared",
            "recipe_loop": 0,
            "sequence_index": 0,
            "step_index": 0,
            "measurement_running": False,
            "active_measurement_kind": "",
            "paused": False,
            "stop_requested": False,
            "requested_settings": {"valve_a": None, "valve_b": None, "pump_rpm": None},
        })
        journal.update()
        return journal

    @staticmethod
    def read(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported recovery record schema.")
        if type(data.get("simulation_recovery", False)) is not bool:
            raise ValueError("Invalid simulation recovery mode.")
        if "recovery_resume_basis" in data and data["recovery_resume_basis"] not in (
                "editor_idle", "process_exited", "link_restored", "not_required", "legacy_reconciled"):
            raise ValueError("Invalid recovery resume basis.")
        return data

    def update(self, **changes):
        with self._lock:
            candidate = copy.deepcopy(self._data)
            candidate.update(copy.deepcopy(changes))
            candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
            candidate["revision"] = self._data.get("revision", 0) + 1
            directory = os.path.dirname(self.path)
            os.makedirs(directory, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".recipe_recovery_", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(candidate, stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self._data = candidate


@dataclass(frozen=True)
class RecoveryPlan:
    recipe_loop: int
    sequence_index: int
    step_index: int
    skipped_measurement_wait: bool = False
    finished: bool = False


def plan_recovery(record: dict) -> RecoveryPlan:
    """Select a cursor only; never assume old measurement/device state is valid."""
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported recovery record schema.")
    if record.get("measurement_mode") != "script_pad":
        raise ValueError("Recovery is currently limited to Discrete PAD.")
    if record.get("observed_measurement_state") == EDITOR_CONTROL_UNVERIFIED:
        raise ValueError("Verify the previous Discrete PAD measurement before starting a new one.")
    if record.get("status") not in ("running", "interrupted"):
        raise ValueError("This run is not eligible for recovery.")
    if any(type(record.get(key)) is not bool for key in ("measurement_running", "paused", "stop_requested")):
        raise ValueError("Invalid measurement or user-intent flags in recovery record.")
    if record.get("active_measurement_kind") not in ("", "discrete_pad"):
        raise ValueError("The recorded measurement is not Discrete PAD.")
    if record.get("stop_requested") or record.get("paused") or record.get("closed_by_user"):
        raise ValueError("A user stop or pause must not trigger automatic recovery.")
    recipe = recipe_from_snapshot(record["recipe"])
    if not is_discrete_pad_recipe(recipe):
        raise ValueError("Recovery is not enabled for recipes containing unsupported measurement actions.")
    expanded = expanded_step_indices(recipe)
    loop = record["recipe_loop"]
    sequence = record["sequence_index"]
    if (type(loop) is not int or type(sequence) is not int
            or not 1 <= loop <= recipe.loop_count or not 1 <= sequence <= len(expanded)
            or record.get("step_index") != expanded[sequence - 1] + 1):
        raise ValueError("Recovery cursor does not match the saved recipe.")
    phase = record.get("phase")
    allowed_phases = {
        "step_pending", "valve_a", "valve_b", "pump", "dropview_start",
        "measurement_start", "waiting", "measurement_stop", "dropview_exit",
        "step_completed", "step_skipped",
    }
    if phase not in allowed_phases:
        raise ValueError("Recovery needs a known, recorded step phase.")
    # active_step is the copy being executed. Live edits apply at next arrival.
    active_step = RecipeStep(**record["active_step"])
    validate_recovery_step(active_step)
    if not is_discrete_pad_recipe(Recipe("active", [active_step])):
        raise ValueError("The interrupted step is not a Discrete PAD step.")
    if phase == "waiting":
        remaining = record.get("remaining_seconds")
        if (type(remaining) not in (int, float) or not math.isfinite(remaining)
                or not 0 <= remaining <= active_step.duration_minutes * 60):
            raise ValueError("Invalid remaining duration for the interrupted wait step.")
    if phase in ("step_completed", "step_skipped"):
        sequence += 1
        if sequence > len(expanded):
            loop += 1
            sequence = 1
    if loop > recipe.loop_count:
        return RecoveryPlan(loop, 0, 0, finished=True)
    return RecoveryPlan(loop, sequence, expanded[sequence - 1] + 1)
