"""Watchdog fault injection; no real process termination, UI or registry writes."""
import sys
import tempfile
import threading
import unittest
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.recipe.models import Recipe, RecipeStep
from bench_test.recipe.recovery import RecoveryJournal
from bench_test.watchdog.state import atomic_write, read_json, RunStopSignal, recovery_source
from bench_test.watchdog.supervisor import Supervisor, fault_reason
from bench_test.watchdog.progress import CsvProgressProbe
from bench_test.watchdog.windows_update import WindowsUpdateGuard, POLICY, EFFECTIVE, PAUSE_NAMES, REBOOT_KEYS


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)


class SupervisorTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.now, self.alive = 1000, True
        recipe = Recipe("PAD", [RecipeStep(0, 90), RecipeStep(0, 90)], 2)
        self.path = self.directory / "record.json"
        self.record = dict(schema_version=1, measurement_mode="script_pad", status="running",
                           phase="waiting", recipe_loop=1, sequence_index=1, step_index=1,
                           recipe=asdict(recipe), active_step=asdict(recipe.steps[0]),
                           measurement_running=True, active_measurement_kind="discrete_pad",
                           paused=False, stop_requested=False, remaining_seconds=30,
                           session_dir=str(self.directory))
        RecoveryJournal(self.path, self.record).update()
        self.state = dict(run_id=uuid.uuid4().hex, armed=True, status="armed",
                          owner={"pid": 123456, "created": 1}, record_path=str(self.path),
                          ui_heartbeat=self.now, worker_heartbeat=self.now, worker_alive=True,
                          phase="waiting", outcome="running", measurement_running=True,
                          dropview_health={"state": "unknown", "sampled_at": self.now})
        self.launch, self.terminate = Mock(), Mock(side_effect=self.kill)
        self.monitor = Supervisor(self.directory, lambda: self.now, lambda _: self.alive,
                                  self.launch, self.terminate)

    def kill(self, identity):
        self.alive = False

    def tick(self, **changes):
        self.state.update(changes)
        atomic_write(self.directory / "active.json", self.state)
        return self.monitor.tick()

    def message(self):
        return read_json(self.directory / "supervisor_status.json")["message"]

    def test_unknown_health_never_restarts(self):
        for time in (1000, 2000, 4000):
            self.now = time
            self.tick(ui_heartbeat=time, worker_heartbeat=time)
        self.assertFalse(self.launch.called)
        self.assertFalse(self.terminate.called)
        self.assertEqual(self.message(), "connection_not_verified")

    def test_dead_owner_launches_once_with_saved_token(self):
        self.alive = False
        self.tick()
        token = read_json(self.directory / "command.json")["token"]
        self.tick()
        self.tick()
        self.launch.assert_called_once_with("--watchdog-recover", token)
        self.terminate.assert_not_called()

    def test_hung_ui_requests_cooperation_before_termination(self):
        self.now += 61
        self.tick(worker_heartbeat=self.now)
        self.terminate.assert_not_called()
        self.now += 59
        self.tick()
        self.terminate.assert_not_called()
        self.now += 2
        self.tick()
        self.terminate.assert_called_once_with(self.state["owner"])
        self.launch.assert_called_once()

    def test_completed_source_prevents_pending_kill(self):
        self.now += 61
        self.tick()
        RecoveryJournal(self.path, self.record).update(status="completed")
        self.now += 61
        self.tick()
        self.terminate.assert_not_called()
        self.assertTrue(self.message().startswith("blocked:"))

    def test_dead_owner_after_durable_completion_exits_without_restart(self):
        self.alive = False
        RecoveryJournal(self.path, self.record).update(status="completed")
        self.assertFalse(self.tick())
        self.assertEqual(self.message(), "completed")
        self.launch.assert_not_called()

    def test_new_owner_invalidates_stale_command(self):
        self.now += 61
        self.tick()
        self.now += 61
        self.tick(owner={"pid": 999999, "created": 2})
        self.terminate.assert_not_called()
        self.assertNotIn("pending", self.monitor.ledger)

    def test_stop_signal_fences_stale_disk_state(self):
        self.now += 61
        self.tick()
        signal = RunStopSignal(self.state["run_id"])
        signal.set()
        self.now += 61
        self.tick()
        self.terminate.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(self.message(), "user_stopped")

    def test_connection_fault_requires_persistence(self):
        self.tick(dropview_health={"dv_open": False, "sampled_at": self.now})
        self.assertTrue(self.message().startswith("checking:"))
        self.now += 31
        self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now,
                  dropview_health={"dv_open": False, "sampled_at": self.now})
        self.assertIn("dropview_missing", self.message())

    def test_planned_exit_does_not_use_measurement_health(self):
        self.tick(phase="dropview_exit", dropview_health={"dv_open": False, "sampled_at": self.now})
        self.assertEqual(self.message(), "healthy")

    def test_retry_limit_blocks_more_launches(self):
        self.tick()
        self.monitor.ledger["attempts"] = 3
        self.alive = False
        self.tick()
        self.assertIn("retry limit", self.message())
        self.launch.assert_not_called()

    def test_attempts_reset_only_after_five_healthy_minutes(self):
        self.tick()
        self.monitor.ledger["attempts"] = 2
        for delta in (0, 299, 301):
            self.now = 1000 + delta
            self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now,
                      dropview_health={"state": "connected", "responding": True, "sampled_at": self.now})
            self.assertEqual(self.monitor.ledger["attempts"], 0 if delta == 301 else 2)

    def test_reboot_discards_monotonic_backoff_but_preserves_retry_count(self):
        atomic_write(self.directory / "supervisor_ledger.json", {
            "run_id": self.state["run_id"], "boot": -1, "attempts": 2, "last_attempt": 9999999})
        restarted = Supervisor(self.directory, lambda: 10, lambda _: False, self.launch, self.terminate)
        self.assertEqual(restarted.ledger["attempts"], 2)
        self.assertNotIn("last_attempt", restarted.ledger)

    def test_pause_and_long_finalization_are_not_faults(self):
        self.assertIsNone(fault_reason({**self.state, "status": "paused"}, 9999, True))
        self.assertIsNone(fault_reason({**self.state, "finalizing": True}, 1800, True))
        self.assertEqual(fault_reason({**self.state, "finalizing": True}, 2000, True), "finalization_timeout")

    def test_parent_reclaim_requires_lineage_and_never_mutates_in_flight(self):
        parent = self.directory / "parent.json"
        RecoveryJournal(parent, self.record).update(status="recovering", recovery_session=str(self.directory))
        RecoveryJournal(self.path, self.record).update(status="prepared")
        state = {**self.state, "recovery_source": str(parent)}
        path, record = recovery_source(state, allow_in_flight=True)
        self.assertEqual(path, str(parent))
        self.assertEqual(record["status"], "interrupted")
        self.assertEqual(RecoveryJournal.read(parent)["status"], "recovering")
        recovery_source(state, reclaim=True)
        self.assertEqual(RecoveryJournal.read(parent)["status"], "interrupted")
        RecoveryJournal(parent, self.record).update(status="recovering", recovery_session="different")
        with self.assertRaisesRegex(ValueError, "belong"):
            recovery_source(state, reclaim=True)


class ProgressTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        patcher = patch("bench_test.watchdog.progress.threading.Thread", ImmediateThread)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.probe = CsvProgressProbe()

    def observe(self, now, epoch=1):
        return self.probe.observe(str(self.directory), epoch, now)

    def test_stall_requires_a_learned_cadence(self):
        for index, now in enumerate((1000, 1060, 1120)):
            (self.directory / "data.csv").write_text("x" * (index + 1))
            self.assertEqual(self.observe(now), (None, None))
        self.assertEqual(self.observe(2400)[0], "discrete_data_stalled")

    def test_initial_absence_is_only_a_warning(self):
        self.observe(1000)
        self.assertEqual(self.observe(3000), (None, "discrete_data_not_verified"))

    def test_storage_error_is_not_a_recovery_command(self):
        result = self.probe.observe(str(self.directory / "absent"), 1, 1000)
        self.assertEqual(result, (None, "measurement_storage_unavailable"))

    def test_new_measurement_resets_cadence(self):
        self.probe.intervals = [60, 60]
        self.observe(1000, 2)
        self.assertEqual(self.probe.intervals, [])

    def test_blocked_probe_does_not_spawn_additional_threads(self):
        self.probe.lock.acquire()
        with patch("bench_test.watchdog.progress.threading.Thread") as thread:
            self.observe(1000)
            self.observe(2000)
            thread.assert_not_called()
        self.probe.lock.release()


class ClientTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        from PyQt6.QtWidgets import QApplication
        from bench_test.watchdog.client import WatchdogClient
        self.app = QApplication.instance() or QApplication([])
        self.tab = SimpleNamespace(package_tab=SimpleNamespace(sm=SimpleNamespace(state="idle")),
                                   recover_from_record=Mock())
        self.client = WatchdogClient(self.tab, SimpleNamespace(last_health={}), directory=self.directory)
        self.client._timer.stop()
        self.addCleanup(self.client.close)
        self.launch_patch = patch("bench_test.watchdog.client.launch")
        self.launch = self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)
        self.thread_patch = patch("bench_test.watchdog.client.threading.Thread", ImmediateThread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.guard_patch = patch("bench_test.watchdog.client.WindowsUpdateGuard")
        self.guard = self.guard_patch.start().return_value
        self.guard.protect.return_value = "not_verified"
        self.guard.status.return_value = "not_verified"
        self.guard.restore.return_value = "restored"
        self.addCleanup(self.guard_patch.stop)
        recipe = Recipe("PAD", [RecipeStep(0, 90)], 2)
        pause = threading.Event()
        pause.set()
        self.runner = SimpleNamespace(recovery_journal=RecoveryJournal(self.directory / "record.json", {}),
                                      _journal_failed=False, recovery_record=None, recipe=recipe,
                                      pause_event=pause, stop_event=threading.Event(), is_alive=Mock(return_value=True),
                                      outcome="running", worker_heartbeat=1000, measurement_running=True,
                                      _recovery_phase="waiting", request_recovery=Mock())
        self.client.arm(self.runner)

    def test_arm_publishes_owner_and_separate_worker(self):
        active = read_json(self.directory / "active.json")
        self.assertEqual(active["owner"], self.client.owner)
        self.assertTrue(active["armed"])
        self.assertEqual(active["worker_heartbeat"], 1000)
        self.launch.assert_called_once_with("--watchdog")

    def command(self, **changes):
        command = dict(token=uuid.uuid4().hex, run_id=self.client._run_id, target_owner=self.client.owner,
                       record_path=str(self.directory / "record.json"), reason="test fault")
        command.update(changes)
        atomic_write(self.directory / "command.json", command)
        return command

    def test_command_waits_for_worker_and_session_finalization(self):
        self.command()
        self.client._tick()
        self.runner.request_recovery.assert_called_once_with("test fault")
        self.tab.recover_from_record.assert_not_called()
        self.runner.is_alive.return_value = False
        self.tab.package_tab.sm.state = "aggregating"
        self.client._tick()
        self.tab.recover_from_record.assert_not_called()
        self.tab.package_tab.sm.state = "idle"
        self.client._tick()
        self.tab.recover_from_record.assert_called_once()

    def test_command_for_another_owner_is_ignored(self):
        self.command(target_owner={"pid": 987654, "created": 1})
        self.client._tick()
        self.runner.request_recovery.assert_not_called()

    def test_explicit_stop_fences_and_persists(self):
        signal = RunStopSignal(self.client._run_id)
        self.client.disarm()
        self.assertTrue(signal.is_set())
        self.assertFalse(self.client.is_armed)
        self.assertEqual(read_json(self.directory / "active.json")["status"], "user_stopped")
        self.guard.restore.assert_called_once()

    def test_recording_failure_fences_recovery_without_unpausing_live_experiment(self):
        self.runner._journal_failed = True
        self.client._tick()
        self.assertFalse(self.client.is_armed)
        self.assertTrue(self.client._stop_signal.is_set())
        self.guard.restore.assert_not_called()

    def test_update_verification_can_revoke_old_positive_status(self):
        self.client._update_status = "paused_verified_until:old"
        self.client._update_poll_at = -1000
        self.client._tick()
        self.assertEqual(self.client._update_status, "not_verified")
        self.guard.status.assert_called_with(self.client._update_required_until)

    def test_invalid_startup_token_never_calls_recovery(self):
        self.command()
        with self.assertRaises(RuntimeError):
            self.client.accept_startup("wrong")
        self.tab.recover_from_record.assert_not_called()

    def test_helper_launch_error_does_not_escape_recipe_start_callback(self):
        self.client._last_spawn = -1000
        self.launch.side_effect = OSError("simulated launch failure")
        messages = []
        self.client.log_message.connect(messages.append)
        self.client.arm(self.runner)
        self.assertTrue(any("arming failed" in message for message in messages))


class FakeRegistry:
    def __init__(self):
        self.values = {(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "EditionID"):
                       {"value": "Professional", "kind": 1}}
        self.keys, self.writes = set(), []
        self.fail_name = None

    def read(self, path, name):
        return self.values.get((path, name))

    def write(self, path, name, value):
        if name == self.fail_name:
            raise PermissionError("simulated access denied")
        self.values[path, name] = dict(value)
        self.writes.append((path, name))

    def delete(self, path, name):
        self.values.pop((path, name), None)

    def exists(self, path):
        return path in self.keys


class UpdateGuardTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.registry = FakeRegistry()
        self.now = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.guard = WindowsUpdateGuard(self.directory, "run", self.registry, lambda: self.now)

    def effective(self, date="2026-10-22"):
        for kind in ("Quality", "Feature"):
            self.registry.values[EFFECTIVE, f"Paused{kind}Status"] = {"value": 1, "kind": 4}
            self.registry.values[EFFECTIVE, f"Paused{kind}Date"] = {"value": date, "kind": 1}

    def test_request_is_not_claimed_verified_without_effective_state(self):
        self.assertIn("not_verified", self.guard.protect())
        self.assertEqual(len(self.registry.writes), 2)
        self.effective()
        self.assertTrue(self.guard.status().startswith("paused_verified_until:"))

    def test_restore_preserves_external_edits(self):
        self.guard.protect()
        external = {"value": "2026-10-23", "kind": 1}
        self.registry.values[POLICY, PAUSE_NAMES[0]] = external
        self.guard.restore()
        self.assertEqual(self.registry.read(POLICY, PAUSE_NAMES[0]), external)
        self.assertIsNone(self.registry.read(POLICY, PAUSE_NAMES[1]))
        self.assertTrue(read_json(self.directory / "update_policy.json")["closed"])

    def test_partial_failure_rolls_back_owned_policy(self):
        self.registry.fail_name = PAUSE_NAMES[1]
        self.assertTrue(self.guard.protect().startswith("needs_admin"))
        self.assertIsNone(self.registry.read(POLICY, PAUSE_NAMES[0]))

    def test_existing_policy_is_never_overwritten(self):
        self.registry.values[POLICY, PAUSE_NAMES[0]] = {"value": "2026-10-01", "kind": 1}
        self.assertTrue(self.guard.protect().startswith("external_policy"))
        self.assertFalse(self.registry.writes)

    def test_pending_reboot_is_not_claimed_protected(self):
        self.registry.keys.add(REBOOT_KEYS[0])
        self.assertTrue(self.guard.protect().startswith("pending_reboot"))
        self.assertFalse(self.registry.writes)

    def test_cancelled_guard_has_no_late_writes(self):
        self.guard.restore()
        self.assertEqual(self.guard.protect(), "cancelled")
        self.assertFalse(self.registry.writes)

    def test_duration_limit_and_remaining_coverage(self):
        self.assertTrue(self.guard.protect(36).startswith("unsupported_duration"))
        self.guard.protect()
        self.effective()
        self.assertTrue(self.guard.status(self.now + timedelta(days=36)).startswith("insufficient_pause"))

    def test_unknown_effective_date_is_not_assumed_35_days(self):
        self.guard.protect()
        self.effective("2026-10-21")
        self.assertTrue(self.guard.status().startswith("not_verified"))

    def test_previous_run_ownership_blocks_new_writes_and_green_status(self):
        self.guard.protect()
        self.effective()
        other = WindowsUpdateGuard(self.directory, "next", self.registry, lambda: self.now)
        self.assertTrue(other.protect().startswith("owned_by_previous_run"))
        self.assertTrue(other.status().startswith("owned_by_previous_run"))
        self.assertEqual(len(self.registry.writes), 2)


if __name__ == "__main__":
    unittest.main()
