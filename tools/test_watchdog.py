"""Watchdog fault injection; no real process termination, UI or registry writes."""
import sys
import os
import queue
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
from bench_test.recipe.runner import RecipeRunner
from bench_test.watchdog import state as watchdog_state
from bench_test.watchdog.state import atomic_write, read_json, RunStopSignal, recovery_source
from bench_test.watchdog.supervisor import Supervisor, fault_reason
from bench_test.watchdog.progress import CsvProgressProbe, read_csv_expectation
from bench_test.dropview.script_generator import generate_dropview_script
from bench_test.watchdog.windows_update import WindowsUpdateGuard, POLICY, EFFECTIVE, PAUSE_NAMES, REBOOT_KEYS


class PackagedWatchdogTests(unittest.TestCase):
    def test_packaged_watchdog_receives_exact_versioned_application_path(self):
        btc = str(Path.cwd() / "release files" / "BenchTestController_v18.0.2.exe")
        watchdog = str(Path(btc).with_name(watchdog_state.WATCHDOG_EXECUTABLE))
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", btc), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(watchdog_state.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as probe, \
                patch.object(watchdog_state.subprocess, "Popen") as popen:
            watchdog_state.launch_watchdog()
        self.assertEqual(popen.call_args.args[0], [
            watchdog, "--app-executable", btc, "--app-version", watchdog_state.APP_VERSION,
        ])
        self.assertEqual(probe.call_args.args[0], [watchdog, "--check-version", watchdog_state.APP_VERSION])
        self.assertEqual(probe.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_standalone_watchdog_uses_btc_state_and_relaunches_that_btc(self):
        btc = str(Path.cwd() / "BenchTestController_v18.0.2.exe")
        watchdog = str(Path(btc).with_name(watchdog_state.WATCHDOG_EXECUTABLE))
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", btc):
            btc_directory = watchdog_state.runtime_directory()
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", watchdog), \
                patch.object(watchdog_state.subprocess, "Popen") as popen:
            self.assertEqual(watchdog_state.runtime_directory(app_executable=btc), btc_directory)
            watchdog_state.launch("--watchdog-recover", "token", executable=btc)
        self.assertEqual(popen.call_args.args[0], [btc, "--watchdog-recover", "token"])
        self.assertEqual(popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_missing_matching_watchdog_does_not_fall_back_to_btc(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(Path, "is_file", return_value=False), \
                patch.object(watchdog_state.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "Matching watchdog not found"):
                watchdog_state.launch_watchdog()
        popen.assert_not_called()

    def test_renamed_incompatible_watchdog_is_not_started(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(watchdog_state.subprocess, "run", return_value=SimpleNamespace(returncode=2)), \
                patch.object(watchdog_state.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "version check failed"):
                watchdog_state.launch_watchdog()
        popen.assert_not_called()

    def test_version_probe_timeout_does_not_start_supervision(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(watchdog_state.subprocess, "run", side_effect=watchdog_state.subprocess.TimeoutExpired("WD", 15)), \
                patch.object(watchdog_state.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "Could not verify watchdog"):
                watchdog_state.launch_watchdog()
        popen.assert_not_called()

    def test_source_launch_keeps_embedded_watchdog(self):
        with patch.object(sys, "frozen", False, create=True), \
                patch.object(watchdog_state, "launch") as launch:
            watchdog_state.launch_watchdog()
        launch.assert_called_once_with("--watchdog")

    def test_watchdog_entry_checks_embedded_version_before_supervision(self):
        import watchdog_main

        for arguments, accepted, starts in (
                (["--check-version", watchdog_state.APP_VERSION], True, False),
                (["--check-version", "0.0.0"], False, False),
                (["--app-executable", str(Path.cwd() / "BTC.exe"), "--app-version", "0.0.0"], False, False),
                (["--app-executable", str(Path.cwd() / "BTC.exe"), "--app-version", watchdog_state.APP_VERSION], True, True)):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["WD", *arguments]), \
                    patch.object(watchdog_main, "run_supervisor") as run, patch("sys.stderr"):
                if accepted:
                    watchdog_main.main()
                else:
                    with self.assertRaises(SystemExit) as error:
                        watchdog_main.main()
                    self.assertEqual(error.exception.code, 2)
                self.assertEqual(run.called, starts)
                if starts:
                    run.assert_called_once_with(app_executable=str(Path.cwd() / "BTC.exe"))


class ImmediateThread:
    def __init__(self, target, args=(), **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        dialogs = patch("bench_test.dropview.automator.dismiss_connection_error_dialogs")
        dialogs.start()
        self.addCleanup(dialogs.stop)
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

    def startup_client(self):
        from PyQt6.QtWidgets import QApplication
        from bench_test.watchdog.client import WatchdogClient
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            self.app = QApplication.instance() or QApplication(["watchdog-relaunch"])
        tab = SimpleNamespace(package_tab=SimpleNamespace(sm=SimpleNamespace(state="idle")),
                              recover_from_record=Mock())
        client = WatchdogClient(tab, SimpleNamespace(last_health={}), directory=self.directory)
        client._timer.stop()
        self.addCleanup(client.close)
        return client, tab

    def test_unknown_health_never_restarts(self):
        for time in (1000, 2000, 4000):
            self.now = time
            self.tick(ui_heartbeat=time, worker_heartbeat=time)
        self.assertFalse(self.launch.called)
        self.assertFalse(self.terminate.called)
        self.assertEqual(self.message(), "connection_not_verified")

    def test_recording_failure_keeps_csv_observation_but_blocks_restart_until_restored(self):
        with patch.object(self.monitor.progress, "observe", return_value=(None, None)) as observe:
            self.assertTrue(self.tick(recovery_available=False))
            self.assertTrue(self.tick(worker_alive=False))
        self.assertEqual(observe.call_count, 2)
        status = read_json(self.directory / "supervisor_status.json")
        self.assertEqual(status["confirmed_fault"], "worker_exited")
        self.assertIn("recovery_blocked", status["message"])
        self.assertFalse((self.directory / "command.json").exists())
        self.launch.assert_not_called()
        self.terminate.assert_not_called()
        with self.assertRaisesRegex(ValueError, "recording is not current"):
            recovery_source(self.state)
        self.tick(recovery_available=True)
        self.assertTrue((self.directory / "command.json").exists())

    def test_recording_failure_cancels_pending_termination_even_after_grace(self):
        self.tick(worker_alive=False)
        self.assertIn("pending", self.monitor.ledger)
        self.now += 100
        self.tick(recovery_available=False)
        self.assertNotIn("pending", self.monitor.ledger)
        self.terminate.assert_not_called()
        self.launch.assert_not_called()

    def test_status_heartbeat_is_written_every_ten_seconds_but_faults_publish_immediately(self):
        with patch("bench_test.watchdog.supervisor.atomic_write", wraps=atomic_write) as write:
            for now in (1000, 1005, 1010, 1015):
                self.now = now
                self.tick(ui_heartbeat=now, worker_heartbeat=now)
            status_writes = [item for item in write.call_args_list
                             if item.args[0].name == "supervisor_status.json"]
            self.assertEqual([item.args[1]["heartbeat"] for item in status_writes], [1000, 1010])
            self.now = 1016
            self.monitor.confirmed_fault = "worker_exited"
            self.monitor.publish("blocked:worker_exited")
        self.assertEqual(self.message(), "blocked:worker_exited")
        self.assertEqual(read_json(self.directory / "supervisor_status.json")["heartbeat"], 1016)

    def test_supervisor_loop_reuses_tick_snapshots_without_reading_files_again(self):
        from bench_test.watchdog import supervisor as module
        monitor = Mock(last_status={"message": "healthy"}, last_state={})
        monitor.tick.return_value = False
        with patch.object(module, "runtime_directory", return_value=self.directory), \
                patch.object(module, "Supervisor", return_value=monitor), \
                patch.object(module, "read_json") as read:
            module.run_supervisor()
        monitor.tick.assert_called_once()
        read.assert_not_called()

    def test_five_second_heartbeat_cadence_does_not_trigger_false_timeouts(self):
        for now in range(1000, 1121, 5):
            state = {**self.state, "ui_heartbeat": now, "worker_heartbeat": now}
            for offset in (0, 4.9):
                self.assertIsNone(fault_reason(state, now + offset, True))
        self.assertEqual(fault_reason(state, now + 46, True), "worker_timeout:waiting")
        self.assertEqual(fault_reason(state, now + 61, True), "ui_not_responding")

    def test_unreadable_active_state_keeps_observing_without_acting_on_cached_heartbeat(self):
        from bench_test.watchdog import supervisor as module
        self.tick()
        self.now += 120
        real_read = module.read_json
        for error in (PermissionError("locked"), ValueError("bad JSON")):
            with self.subTest(error=error), \
                    patch.object(module, "read_json", side_effect=lambda path: (_ for _ in ()).throw(error)
                                 if path.name == "active.json" else real_read(path)), \
                    patch.object(self.monitor.progress, "observe", return_value=("discrete_data_stalled", None)) as observe:
                self.assertTrue(self.monitor.tick())
                observe.assert_called_once()
                self.assertFalse(self.monitor.active_read_ok)
                self.assertIn("active_read", self.monitor.last_status["check_errors"])
                self.assertEqual(self.monitor.last_active_read, 1000)
                self.assertEqual(self.message(), "observation_incomplete")
                self.terminate.assert_not_called()
                self.launch.assert_not_called()
                self.assertNotIn("pending", self.monitor.ledger)
        self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now)
        self.assertNotIn("active_read", self.monitor.check_errors)

    def test_csv_exception_does_not_hide_independent_worker_fault(self):
        with patch.object(self.monitor.progress, "observe", side_effect=RuntimeError("CSV parser failed")):
            self.tick(worker_alive=False)
        self.assertEqual(self.monitor.last_status["confirmed_fault"], "worker_exited")
        self.assertIn("csv_check", self.monitor.last_status["check_errors"])
        self.assertTrue((self.directory / "command.json").exists())

    def test_unreadable_active_state_never_executes_pending_termination(self):
        self.tick(worker_alive=False)
        self.now += 100
        (self.directory / "active.json").write_text("{invalid JSON", encoding="utf-8")
        self.monitor.tick()
        self.assertFalse(self.monitor.active_read_ok)
        self.terminate.assert_not_called()
        self.launch.assert_not_called()

    def test_corrupt_ledger_does_not_prevent_observation_or_reset_retry_limit(self):
        (self.directory / "supervisor_ledger.json").write_text("{invalid JSON", encoding="utf-8")
        self.monitor = Supervisor(self.directory, lambda: self.now, lambda _: self.alive,
                                  self.launch, self.terminate)
        with patch.object(self.monitor.progress, "observe", return_value=(None, None)) as observe:
            self.tick(worker_alive=False)
        observe.assert_called_once()
        self.assertIn("ledger_read", self.monitor.check_errors)
        self.assertFalse((self.directory / "command.json").exists())
        atomic_write(self.directory / "supervisor_ledger.json",
                     {"run_id": self.state["run_id"], "attempts": 3, "boot": self.monitor.boot})
        self.tick()
        self.assertEqual(self.message(), "blocked: recovery retry limit reached")

    def test_process_check_exception_does_not_skip_csv_or_restart(self):
        self.monitor.is_alive = Mock(side_effect=OSError("process inaccessible"))
        with patch.object(self.monitor.progress, "observe", return_value=(None, None)) as observe:
            self.tick()
        observe.assert_called_once()
        self.assertIn("process_check", self.monitor.last_status["check_errors"])
        self.launch.assert_not_called()
        self.terminate.assert_not_called()

    def test_status_write_failure_does_not_skip_fault_handling_and_recovers(self):
        from bench_test.watchdog import supervisor as module
        write = module.atomic_write
        with patch.object(module, "atomic_write", side_effect=lambda path, value:
                          (_ for _ in ()).throw(PermissionError("status locked"))
                          if path.name == "supervisor_status.json" else write(path, value)):
            self.tick(worker_alive=False)
        self.assertIn("status_write", self.monitor.last_status["check_errors"])
        self.assertEqual(self.monitor.last_status["confirmed_fault"], "worker_exited")
        self.assertTrue((self.directory / "command.json").exists())
        self.tick()
        self.assertNotIn("status_write", self.monitor.check_errors)
        self.assertNotIn("status_write", read_json(self.directory / "supervisor_status.json")["check_errors"])

    def test_ledger_write_failure_keeps_observing_but_never_dispatches_unrecorded_recovery(self):
        from bench_test.watchdog import supervisor as module
        write = module.atomic_write
        with patch.object(module, "atomic_write", side_effect=lambda path, value:
                          (_ for _ in ()).throw(OSError("disk full"))
                          if path.name == "supervisor_ledger.json" else write(path, value)), \
                patch.object(self.monitor.progress, "observe", return_value=(None, None)) as observe:
            for now in (1000, 1100, 1200):
                self.now = now
                self.tick(worker_alive=False, ui_heartbeat=now)
            self.assertEqual(observe.call_count, 3)
        self.assertIn("recovery_control", self.monitor.last_status["check_errors"])
        self.assertFalse((self.directory / "command.json").exists())
        self.terminate.assert_not_called()
        self.launch.assert_not_called()
        self.tick()
        self.assertTrue((self.directory / "command.json").exists())

    def test_log_failure_does_not_skip_supervisor_loop_completion(self):
        from bench_test.watchdog import supervisor as module
        monitor = Mock(last_status={"message": "healthy"}, last_state={}, check_errors={}, active_read_ok=True)
        monitor.tick.return_value = False
        logger = Mock()
        logger.info.side_effect = OSError("log disk full")
        with patch.object(module, "runtime_directory", return_value=self.directory), \
                patch.object(module, "Supervisor", return_value=monitor), \
                patch.object(module.logging, "getLogger", return_value=logger):
            module.run_supervisor()
        monitor.tick.assert_called_once()
        self.assertIn("logging", monitor.check_errors)

    def test_supervisor_restores_update_policy_for_both_modes(self):
        from bench_test.watchdog import supervisor as module
        for simulation in (False, True):
            with self.subTest(simulation=simulation):
                run_id = uuid.uuid4().hex
                monitor = Mock(last_status={"message": "completed"}, active_read_ok=True,
                               last_state={"status": "completed", "run_id": run_id,
                                           "simulation_recovery": simulation})
                monitor.tick.return_value = False
                with patch.object(module, "runtime_directory", return_value=self.directory), \
                        patch.object(module, "Supervisor", return_value=monitor), \
                        patch("bench_test.watchdog.windows_update.WindowsUpdateGuard") as guard:
                    module.run_supervisor()
                guard.assert_called_once_with(self.directory, run_id)
                guard.return_value.restore.assert_called_once_with()

    def test_dead_owner_launches_once_with_saved_token(self):
        self.alive = False
        self.tick()
        token = read_json(self.directory / "command.json")["token"]
        self.tick()
        self.tick()
        self.launch.assert_called_once_with("--watchdog-recover", token)
        self.terminate.assert_not_called()

    def test_watchdog_relaunch_token_hands_saved_run_to_new_application(self):
        self.alive = False
        self.tick()
        self.tick()
        token = self.launch.call_args.args[1]
        client, tab = self.startup_client()
        with patch("bench_test.watchdog.client.owner_alive", return_value=False):
            client.accept_startup(token)
            with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                client.accept_startup(token)
        tab.recover_from_record.assert_called_once_with(str(self.path))
        active = read_json(self.directory / "active.json")
        self.assertEqual(active["owner"], client.owner)
        self.assertEqual(active["run_id"], self.state["run_id"])
        self.assertEqual(active["ack_token"], token)
        self.assertEqual(active["command_state"], "running")
        self.launch.assert_called_once()

    def test_stale_relaunch_token_cannot_recover_another_or_cancelled_run(self):
        self.alive = False
        self.tick()
        token = read_json(self.directory / "command.json")["token"]
        client, tab = self.startup_client()
        for changes in (
                {"run_id": uuid.uuid4().hex},
                {"owner": {"pid": 123456, "created": 2}},
                {"status": "user_stopped", "armed": False},
                {"status": "paused"},
                {"armed": False}):
            with self.subTest(changes=changes):
                atomic_write(self.directory / "active.json", {**self.state, **changes})
                with patch("bench_test.watchdog.client.owner_alive", return_value=False), \
                        patch("bench_test.watchdog.client.recovery_source") as source:
                    with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                        client.accept_startup(token)
                    source.assert_not_called()
        tab.recover_from_record.assert_not_called()
        self.assertFalse(client.is_armed)

    def test_startup_checks_stop_signal_before_reclaiming_the_journal(self):
        self.alive = False
        self.tick()
        token = read_json(self.directory / "command.json")["token"]
        self.monitor.stop_signal.set()
        client, tab = self.startup_client()
        with patch("bench_test.watchdog.client.owner_alive", return_value=False), \
                patch("bench_test.watchdog.client.recovery_source") as source:
            with self.assertRaisesRegex(RuntimeError, "explicitly cancelled"):
                client.accept_startup(token)
            source.assert_not_called()
        tab.recover_from_record.assert_not_called()

    def test_manual_reopen_recovers_only_after_previous_owner_exits(self):
        atomic_write(self.directory / "active.json", self.state)
        client, tab = self.startup_client()
        with patch("bench_test.watchdog.client.owner_alive", return_value=True):
            client.accept_startup()
        tab.recover_from_record.assert_not_called()
        with patch("bench_test.watchdog.client.owner_alive", return_value=False):
            client.accept_startup()
        tab.recover_from_record.assert_called_once_with(str(self.path))

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

    def test_hung_ui_failed_termination_waits_for_backoff_without_launching(self):
        self.terminate.side_effect = PermissionError("denied")
        self.now += 61
        self.tick()
        self.now += 61
        self.tick()
        self.assertTrue(self.message().startswith("blocked: application could not be stopped"))
        self.now += 2
        self.tick()
        self.assertEqual(self.message(), "retry_backoff")
        self.terminate.assert_called_once()
        self.launch.assert_not_called()

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
        self.now += 14
        self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now,
                  dropview_health={"dv_open": False, "sampled_at": self.now})
        self.assertIsNone(read_json(self.directory / "command.json"))
        self.now += 1
        self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now)
        self.assertIsNone(read_json(self.directory / "command.json"))  # Cached sample is not confirmation.
        self.tick(dropview_health={"dv_open": False, "sampled_at": self.now})
        self.assertEqual(read_json(self.directory / "command.json")["reason"], "dropview_missing")

    def test_disconnection_warning_counts_distinct_controller_reads(self):
        for seconds, sampled, expected_warning in (
                (0, 0, None), (2, 0, None), (3, 3, None), (5, 3, None), (6, 6, "dropsens_disconnected")):
            self.now = 1000 + seconds
            self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now,
                      dropview_health={"dv_open": True, "state": "disconnected", "sampled_at": 1000 + sampled})
            status = read_json(self.directory / "supervisor_status.json")
            self.assertEqual(status["warning"], expected_warning)
            self.assertIsNone(status["confirmed_fault"])
        self.assertIsNone(read_json(self.directory / "command.json"))
        self.now = 1015
        self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now,
                  dropview_health={"dv_open": True, "state": "disconnected", "sampled_at": self.now})
        status = read_json(self.directory / "supervisor_status.json")
        self.assertEqual(status["confirmed_fault"], "dropsens_disconnected")
        self.assertEqual(status["last_connection_read"], self.now)

    def test_unreadable_connection_warning_preserves_last_verified_read_without_recovery(self):
        self.tick(dropview_health={"dv_open": True, "state": "connected", "sampled_at": self.now})
        for time in (1010, 1029, 1030):
            self.now = time
            self.tick(ui_heartbeat=time, worker_heartbeat=time,
                      dropview_health={"dv_open": True, "state": "unknown", "sampled_at": time})
            status = read_json(self.directory / "supervisor_status.json")
            self.assertEqual(status["last_connection_read"], 1000)
            self.assertEqual(status["warning"], "connection_read_unverified" if time == 1030 else None)
        self.assertIsNone(read_json(self.directory / "command.json"))

    def test_unresponsive_ui_warns_at_30_seconds_without_interruption(self):
        self.now += 30
        self.tick(worker_heartbeat=self.now)
        status = read_json(self.directory / "supervisor_status.json")
        self.assertEqual(status["warning"], "ui_response_delayed")
        self.assertIsNone(status["confirmed_fault"])
        self.assertIsNone(read_json(self.directory / "command.json"))

    def test_verified_dropview_process_exit_skips_window_disappearance_debounce(self):
        import psutil
        identity = {"pid": 234567, "created": 2}
        with patch("bench_test.watchdog.supervisor.psutil.Process", side_effect=psutil.NoSuchProcess(234567)):
            # The supervisor also queries its own identity when publishing.
            with patch("bench_test.watchdog.supervisor.process_identity", return_value=self.state["owner"]):
                self.tick(dropview_health={"dv_open": False, "sampled_at": self.now,
                                           "process_identity": identity})
        self.assertEqual(read_json(self.directory / "command.json")["reason"], "dropview_process_exited")
        self.terminate.assert_not_called()

    def test_missing_first_csv_uses_existing_recovery_only_during_measurement_wait(self):
        self.monitor.progress.observe = Mock(return_value=("discrete_first_export_missing", None))
        self.tick(phase="measurement_stop")
        self.assertIsNone(read_json(self.directory / "command.json"))
        self.tick(phase="waiting")
        self.assertEqual(read_json(self.directory / "command.json")["reason"], "discrete_first_export_missing")

    def test_simulation_requests_standard_recovery_for_confirmed_connection_faults(self):
        RecoveryJournal(self.path, {**self.record, "simulation_recovery": True}).update()
        for state, reason in (
                ({"dv_open": False}, "dropview_missing"),
                ({"dv_open": True, "responding": False}, "dropview_not_responding"),
                ({"dv_open": True, "state": "disconnected"}, "dropsens_disconnected")):
            with self.subTest(reason=reason):
                self.tick(run_id=uuid.uuid4().hex, simulation_recovery=True,
                          ui_heartbeat=self.now, worker_heartbeat=self.now,
                          dropview_health={**state, "sampled_at": self.now})
                self.assertEqual(self.message(), "checking:" + reason)
                self.now += 15
                self.tick(ui_heartbeat=self.now, worker_heartbeat=self.now,
                          dropview_health={**state, "sampled_at": self.now})
                command = read_json(self.directory / "command.json")
                self.assertEqual(command["reason"], reason)
                self.assertEqual(command["record_path"], str(self.path))
                self.assertNotIn("observe_only", command)
                self.assertEqual(self.monitor.ledger["attempts"], 1)
        self.terminate.assert_not_called()
        self.launch.assert_not_called()

    def test_simulation_relaunches_after_owner_exit_using_standard_recovery(self):
        RecoveryJournal(self.path, {**self.record, "simulation_recovery": True}).update()
        self.alive = False
        self.tick(simulation_recovery=True)
        command = read_json(self.directory / "command.json")
        self.assertEqual(command["reason"], "application_exited")
        self.tick()
        self.launch.assert_called_once_with("--watchdog-recover", command["token"])
        self.terminate.assert_not_called()

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

    def test_finalization_does_not_mask_normal_faults(self):
        self.assertIsNone(fault_reason({**self.state, "status": "paused"}, 9999, True))
        for changes, now, alive, expected in (
            ({}, 1061, True, "ui_not_responding"),
            ({"ui_heartbeat": 1061}, 1061, True, "worker_timeout:waiting"),
            ({"worker_alive": False}, 1000, True, "worker_exited"),
            ({"dropview_health": {"state": "disconnected", "sampled_at": 1000}}, 1000, True, "dropsens_disconnected"),
            ({}, 1000, False, "application_exited"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(fault_reason({**self.state, "finalizing": True, **changes}, now, alive), expected)

    def test_long_aggregation_warns_without_restarting_a_healthy_measurement(self):
        self.now = 2000
        self.tick(finalizing=True, finalizing_started=1000, ui_heartbeat=2000, worker_heartbeat=2000,
                  dropview_health={"state": "connected", "responding": True, "sampled_at": 2000})
        status = self.monitor.last_status
        self.assertEqual(status["aggregation_elapsed"], 1000)
        self.assertEqual(status["aggregation_warning"], "aggregation_taking_long")
        self.assertIsNone(status["confirmed_fault"])
        self.assertNotIn("pending", self.monitor.ledger)
        self.launch.assert_not_called()
        self.tick(finalizing=False)
        self.assertIsNone(self.monitor.last_status["aggregation_elapsed"])
        self.assertIsNone(self.monitor.last_status["aggregation_warning"])

    def test_csv_stall_is_still_detected_during_aggregation(self):
        with patch.object(self.monitor.progress, "observe", return_value=("discrete_data_stalled", None)) as observe:
            self.tick(finalizing=True, finalizing_started=self.now)
        observe.assert_called_once()
        self.assertEqual(self.monitor.last_status["confirmed_fault"], "discrete_data_stalled")
        self.assertIn("pending", self.monitor.ledger)

    def test_aggregation_does_not_extend_pending_recovery_grace(self):
        self.tick(worker_alive=False, finalizing=True, finalizing_started=self.now)
        self.now += 61
        self.tick(ui_heartbeat=self.now)
        self.terminate.assert_called_once()
        self.launch.assert_called_once()

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

    def test_prepared_child_does_not_fall_back_to_an_unrelated_running_record(self):
        parent = self.directory / "parent.json"
        RecoveryJournal(parent, self.record).update(status="running")
        RecoveryJournal(self.path, self.record).update(status="prepared",
                                                      source_record=str(self.directory / "other.json"))
        before = parent.read_bytes()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            recovery_source({**self.state, "recovery_source": str(parent)}, reclaim=True)
        self.assertEqual(parent.read_bytes(), before)

    def test_reclaim_does_not_rewrite_a_parent_with_user_stop_recorded(self):
        parent = self.directory / "parent.json"
        RecoveryJournal(parent, self.record).update(status="recovering", stop_requested=True,
                                                   recovery_session=str(self.directory))
        RecoveryJournal(self.path, self.record).update(status="prepared", source_record=str(parent))
        before = parent.read_bytes()
        with self.assertRaisesRegex(ValueError, "user stop"):
            recovery_source({**self.state, "recovery_source": str(parent)}, reclaim=True)
        self.assertEqual(parent.read_bytes(), before)


class ProgressTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        patcher = patch("bench_test.watchdog.progress.threading.Thread", ImmediateThread)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.probe = CsvProgressProbe()

    def observe(self, now, epoch=1):
        return self.probe.observe(str(self.directory), epoch, now)

    def write_export(self, name="PAD-001.csv", curve=1, rows='"0";"0.1"\n"2.5";"0.2"\n'):
        path = self.directory / name
        path.write_text(f'"i400sN1_1#{curve:03d}";"";""\n""\n"Time (s)";"Current (uA)";""\n{rows}',
                        encoding="utf-8")
        return path

    def test_stall_requires_a_learned_cadence(self):
        for index, now in enumerate((1000, 1060, 1120)):
            self.write_export(name=f"data-{index}.csv", curve=index + 1)
            self.assertEqual(self.observe(now), (None, None))
        self.assertEqual(self.observe(2400)[0], "discrete_data_stalled")

    def test_initial_absence_is_only_a_warning(self):
        self.observe(1000)
        self.assertEqual(self.observe(3000), (None, "discrete_data_not_verified"))

    def test_storage_error_is_not_a_recovery_command(self):
        result = self.probe.observe(str(self.directory / "absent"), 1, 1000)
        self.assertEqual(result, (None, "measurement_storage_unavailable"))

    def test_storage_failure_counter_requires_new_scans(self):
        missing = str(self.directory / "absent")
        for now, failures in ((1000, 1), (1001, 1), (1060, 2)):
            self.probe.observe(missing, 1, now)
            self.assertEqual(self.probe.storage_failures, failures)

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

    def test_unexpected_csv_parser_failure_is_unverified_storage_not_missing_export(self):
        self.write_export()
        with patch("bench_test.watchdog.progress.inspect_csv_progress", side_effect=ValueError("invalid parser input")):
            self.assertEqual(self.expected(1000), (None, "measurement_storage_unavailable"))
        self.assertIsNone(self.probe.last_successful_scan)
        self.assertFalse(self.probe.lock.locked())
        self.assertEqual(self.expected(1060), (None, None))

    def test_failed_scan_thread_start_releases_lock(self):
        with patch("bench_test.watchdog.progress.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread unavailable")
            with self.assertRaises(RuntimeError):
                self.expected(1000)
        self.assertFalse(self.probe.lock.locked())

    def expected(self, now, exports=5, started_ns=None):
        return self.probe.observe(
            str(self.directory), 1000, now, started_ns=started_ns,
            expectation={"first_seconds": 10, "interval_seconds": 120,
                         "exports": exports, "csv_stem": "pad"})

    def test_first_export_requires_second_successful_scan_after_warning(self):
        self.assertEqual(self.expected(1000), (None, None))
        self.assertEqual(self.expected(1060), (None, None))
        self.assertEqual(self.expected(1120), (None, "discrete_first_export_late"))
        self.assertEqual(self.expected(1122), (None, "discrete_first_export_late"))
        self.assertEqual(self.expected(1180), ("discrete_first_export_missing", None))

    def test_late_first_export_clears_warning(self):
        self.expected(1000)
        self.assertEqual(self.expected(1120)[1], "discrete_first_export_late")
        (self.directory / "PAD-001.csv").write_text("new data")
        self.assertEqual(self.expected(1180), (None, None))

    def test_subsequent_missed_exports_use_configured_interval(self):
        self.write_export()
        self.assertEqual(self.expected(1000), (None, None))
        self.assertEqual(self.expected(1180), (None, "discrete_export_late"))
        self.assertEqual(self.expected(1300), ("discrete_data_stalled", None))

    def test_one_scan_after_multiple_deadlines_does_not_trigger_recovery_immediately(self):
        self.write_export()
        self.expected(1000)
        self.assertEqual(self.expected(1400), (None, "discrete_export_late"))
        self.assertEqual(self.expected(1402), (None, "discrete_export_late"))
        self.assertEqual(self.expected(1460), ("discrete_data_stalled", None))

    def test_old_unrelated_or_empty_files_do_not_satisfy_first_export(self):
        start = 1_760_000_000_000_000_000
        old = self.directory / "PAD-001.csv"
        old.write_text("old measurement")
        os.utime(old, ns=(start - 1_000_000_000,) * 2)
        (self.directory / "other.csv").write_text("unrelated")
        (self.directory / "PAD-002.csv").touch()
        self.expected(1000, started_ns=start)
        self.assertEqual(self.expected(1120, started_ns=start)[1], "discrete_first_export_late")

    def test_all_numbered_exports_complete_without_stall_fault(self):
        self.write_export()
        self.expected(1000, exports=1)
        self.assertEqual(self.expected(4000, exports=1), (None, None))

    def test_storage_error_interrupts_first_export_confirmation(self):
        self.expected(1000)
        self.assertEqual(self.expected(1120)[1], "discrete_first_export_late")
        with patch("bench_test.watchdog.progress.os.scandir", side_effect=OSError("offline")):
            self.assertEqual(self.expected(1180), (None, "measurement_storage_unavailable"))
        self.assertEqual(self.expected(1240), (None, "discrete_first_export_late"))
        self.assertEqual(self.expected(1300)[0], "discrete_first_export_missing")

    def test_stale_scan_cannot_confirm_an_export_timeout(self):
        self.expected(1000)
        self.expected(1120)
        self.probe.lock.acquire()
        try:
            self.assertEqual(self.expected(1500), (None, "measurement_probe_not_responding"))
        finally:
            self.probe.lock.release()

    def test_distinct_curves_can_repeat_times_and_current_values(self):
        self.write_export()
        self.assertEqual(self.expected(1000), (None, None))
        self.write_export("PAD-002.csv", curve=2)
        self.assertEqual(self.expected(1240), (None, None))
        self.assertEqual(self.probe.content_last_progress, 1240)

    def test_metadata_changes_with_same_curve_and_times_warn_without_recovery(self):
        self.write_export()
        self.expected(1000)
        self.write_export("PAD-002.csv", curve=1)
        self.assertEqual(self.expected(1120), (None, None))
        self.write_export("PAD-003.csv", curve=1)
        self.assertEqual(self.expected(1240), (None, "measurement_csv_records_not_advancing"))
        self.write_export("PAD-004.csv", curve=1, rows='"0";"0.1"\n"5";"0.2"\n')
        self.assertEqual(self.expected(1300), (None, None))

    def test_invalid_measurement_content_requires_two_distinct_scans(self):
        self.write_export(rows='"0";"nan"\n')
        self.assertEqual(self.expected(1000), (None, None))
        self.assertEqual(self.expected(1002), (None, None))
        self.write_export("PAD-002.csv", curve=2, rows='"0";"0.1"\n"0";"0.2"\n')
        self.assertEqual(self.expected(1060), (None, "measurement_csv_invalid"))
        self.write_export("PAD-003.csv", curve=3)
        self.assertEqual(self.expected(1120), (None, None))

    def test_unknown_header_is_unverified_not_invalid_or_a_recovery_fault(self):
        (self.directory / "PAD-001.csv").write_text("unsupported CSV layout")
        self.assertEqual(self.expected(1000), (None, None))
        self.assertEqual(self.expected(1060), (None, "measurement_csv_content_unverified"))

    def test_timing_uses_packaged_method_duration_and_wait_only_after_first_export(self):
        method = self.directory / "pad.tp"
        method.write_text(
            '<root><technic id="PAD"><commonUserParameters><parameter id="t">10</parameter>'
            '</commonUserParameters><pretreatmentUserParameters><parameter id="tcond">2</parameter>'
            '</pretreatmentUserParameters></technic></root>', encoding="utf-8")
        script = self.directory / "pad.scr"
        generate_dropview_script(str(method), str(self.directory / "PAD.csv"), 8, 47500, str(script))
        self.assertEqual(read_csv_expectation(script, self.directory),
                         {"first_seconds": 12, "interval_seconds": 59.5, "exports": 8, "csv_stem": "pad"})
        self.assertIsNone(read_csv_expectation(script, self.directory / "not_the_export_directory"))
        method.write_text('<root><technic id="PAD" /></root>', encoding="utf-8")
        self.assertIsNone(read_csv_expectation(script, self.directory))


class SimulationFlowTests(TemporaryTest):
    def test_discrete_pad_faults_use_standard_recovery_without_pump_or_valve_commands(self):
        from PyQt6.QtWidgets import QApplication
        from bench_test.watchdog.client import WatchdogClient

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication(["watchdog-simulation"])
        cases = (
            ("covered", "unknown", {"state": "unknown"}, None),
            ("stopped", "stopped", {}, "stopped unexpectedly"),
            ("missing_editor", "unknown", {"editor_missing": True}, "Editor could not be recovered"),
            ("disconnected", "running", {"state": "disconnected"}, "dropsens_disconnected"),
            ("dropview_hang", "running", {"responding": False}, "dropview_not_responding"),
            ("dropview_exit", "running", {"dv_open": False}, "dropview_process_exited"),
            ("missing_csv", "running", {}, "discrete_first_export_missing"),
        )
        for scenario, measurement, health_changes, expected in cases:
            with self.subTest(scenario=scenario):
                directory = self.directory / scenario
                directory.mkdir()
                csv_directory = directory / "measurements"
                csv_directory.mkdir()
                method = directory / "pad.tp"
                method.write_text(
                    '<root><technic id="PAD"><commonUserParameters><parameter id="t">10</parameter>'
                    '</commonUserParameters></technic></root>', encoding="utf-8")
                script = str(directory / "pad.scr")
                generate_dropview_script(str(method), str(csv_directory / "PAD.csv"), 6, 47500, script)

                clock = [1000.0]
                pump, dv = Mock(), Mock()
                dv.do_start_measure.return_value = True
                dv.discrete_measurement_owner = {"pid": 42, "created": 100}
                runner = RecipeRunner(
                    None, None, Recipe("Simulated PAD", [
                        RecipeStep(0, 4, dropview_action="start_discrete_pad"),
                        RecipeStep(0, 0, dropview_action="stop_discrete_pad"),
                    ], 1), queue.Queue(), threading.Event(), dv, pump_ctrl=pump,
                    simulation_mode=True, pump_simulation_mode=True, session_scr_path=script,
                    simulation_recovery=True,
                    recovery_journal=RecoveryJournal(str(directory / "record.json"), {
                        "schema_version": 1, "measurement_mode": "script_pad", "simulation_recovery": True}),
                    session_measurements_dir=str(csv_directory) if scenario == "missing_csv" else "")
                runner.is_alive = Mock(return_value=True)
                tab = SimpleNamespace(package_tab=SimpleNamespace(sm=SimpleNamespace(state="running")),
                                      recover_from_record=Mock())
                health = SimpleNamespace(last_health={})
                runtime = directory / "runtime"
                launch, terminate = Mock(), Mock()
                supervisor = Supervisor(runtime, clock=lambda: clock[0], is_alive=lambda _: True,
                                        start_app=launch, terminate=terminate)
                client = WatchdogClient(tab, health, directory=runtime)
                client._timer.stop()
                messages = []
                client.log_message.connect(messages.append)

                def tick(_sleep):
                    clock[0] += 3
                    runner.worker_heartbeat = clock[0]
                    health.last_health = {
                        "dv_open": True, "responding": True, "state": "connected",
                        "process_identity": dv.discrete_measurement_owner,
                        "sampled_at": clock[0], **health_changes,
                    }
                    client._publish()
                    supervisor.tick()
                    client._tick()

                observation = {"state": measurement, "editor_missing": scenario == "missing_editor"}
                def observe_editor(**_):
                    if scenario == "covered" and runner._recovery_phase == "measurement_stop":
                        return {"state": "running"}
                    return observation

                with patch.object(client, "_ensure_supervisor"), \
                        patch.object(runner, "_check_dropview_connected", return_value=True), \
                        patch("bench_test.recipe.runner.debug_log"), \
                        patch("bench_test.dropview.automator.get_discrete_measurement_state", side_effect=observe_editor), \
                        patch("bench_test.watchdog.supervisor.dropview_process_exited", return_value=scenario == "dropview_exit"), \
                        patch("bench_test.watchdog.client.WindowsUpdateGuard") as update_guard, \
                        patch("bench_test.watchdog.progress.threading.Thread", ImmediateThread), \
                        patch("bench_test.recipe.runner.time.monotonic", side_effect=lambda: clock[0]), \
                        patch("bench_test.recipe.runner.time.time", side_effect=lambda: clock[0]), \
                        patch("bench_test.recipe.runner.time.sleep", side_effect=tick):
                    try:
                        update_guard.return_value.protect.return_value = "not_verified"
                        update_guard.return_value.status.return_value = "not_verified"
                        update_guard.return_value.restore.return_value = "restored"
                        client.arm(runner)
                        self.assertTrue(read_json(client.directory / "active.json")["recovery_available"])
                        runner.run()
                        runner.is_alive.return_value = False
                        if expected:
                            self.assertEqual(runner.outcome, "operator_required" if scenario == "missing_editor" else "interrupted",
                                             list(runner.status_queue.queue))
                            self.assertEqual(runner.current_sequence_index, 1)
                            self.assertIn(expected, runner._watchdog_reason)
                            dv.do_stop_measure.assert_not_called()
                        else:
                            self.assertEqual(runner.outcome, "completed", list(runner.status_queue.queue))
                            self.assertEqual(runner.current_sequence_index, 2)
                            self.assertTrue(any("connection read unverified" in line for line in messages))
                        pump.assert_not_called()
                        self.assertEqual(pump.mock_calls, [])
                        dv.do_start_measure.assert_called_once()
                        tab.recover_from_record.assert_not_called()
                        tab.package_tab.sm.state = "idle"
                        client._publish()
                        supervisor.tick()
                        client._last_io_tick = -float("inf")
                        client._tick()
                        if expected and scenario != "missing_editor":
                            tab.recover_from_record.assert_called_once_with(runner.recovery_journal.path)
                        else:
                            tab.recover_from_record.assert_not_called()
                        launch.assert_not_called()
                        terminate.assert_not_called()
                        update_guard.assert_called_once()
                        update_guard.return_value.protect.assert_called_once()
                    finally:
                        client.close()
        app.processEvents()


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
        self.launch_patch = patch("bench_test.watchdog.client.launch_watchdog")
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
                                      _recovery_phase="waiting", request_recovery=Mock(), retry_failed_checkpoint=Mock())
        self.client.arm(self.runner)

    def test_arm_publishes_owner_and_separate_worker(self):
        active = read_json(self.directory / "active.json")
        self.assertEqual(active["owner"], self.client.owner)
        self.assertTrue(active["armed"])
        self.assertEqual(active["worker_heartbeat"], 1000)
        self.launch.assert_called_once_with()

    def test_routine_io_is_limited_to_five_seconds_and_status_is_read_once(self):
        from bench_test.watchdog import client as module
        clock = [1000.0]
        with patch.object(module.time, "monotonic", side_effect=lambda: clock[0]):
            self.client._publish()
            self.client._last_io_tick = -float("inf")
            with patch.object(module, "atomic_write", wraps=atomic_write) as write, \
                    patch.object(module, "read_json", wraps=read_json) as read:
                for now in (1000, 1001, 1002, 1003, 1004, 1005):
                    clock[0] = now
                    self.client._tick()
                self.assertEqual(write.call_count, 1)
                self.assertEqual(write.call_args.args[1]["ui_heartbeat"], 1005)
                self.assertEqual([item.args[0].name for item in read.call_args_list],
                                 ["command.json", "supervisor_status.json"] * 2)

    def test_pause_finalization_and_stop_publish_without_waiting_for_interval(self):
        from bench_test.watchdog import client as module
        clock = [1000.0]
        with patch.object(module.time, "monotonic", side_effect=lambda: clock[0]):
            self.client._publish()
            self.client._tick()
            clock[0] = 1001
            self.runner.pause_event.clear()
            self.client._tick()
            self.assertEqual(read_json(self.directory / "active.json")["status"], "paused")
            self.client.set_finalizing(True)
            self.assertTrue(read_json(self.directory / "active.json")["finalizing"])
            self.client.disarm()
            active = read_json(self.directory / "active.json")
            self.assertFalse(active["armed"])
            self.assertEqual(active["status"], "user_stopped")

    def test_aggregation_start_time_survives_duplicate_start_and_clears_on_finish(self):
        with patch("bench_test.watchdog.client.time.monotonic", return_value=1000):
            self.client.set_finalizing(True)
        with patch("bench_test.watchdog.client.time.monotonic", return_value=1010):
            self.client.set_finalizing(True)
        self.assertEqual(read_json(self.directory / "active.json")["finalizing_started"], 1000)
        self.client.set_finalizing(False)
        self.assertIsNone(read_json(self.directory / "active.json")["finalizing_started"])

    def test_health_state_change_is_published_before_next_periodic_heartbeat(self):
        from bench_test.watchdog import client as module
        with patch.object(module.time, "monotonic", return_value=1000):
            self.client._publish()
            self.client._last_io_tick = 1000
            self.client.dv.last_health = {"state": "disconnected", "responding": True}
            self.client._tick()
        self.assertEqual(read_json(self.directory / "active.json")["dropview_health"]["state"],
                         "disconnected")

    def test_publishes_only_observations_for_the_current_measurement_owner(self):
        owner = {"pid": 42, "created": 100}
        self.runner._measurement_owner = owner
        self.runner.measurement_epoch = 1000
        health = {"state": "disconnected", "dv_open": True, "sampled_at": 1001, "process_identity": owner}
        for observation, published in (
            ({**health, "process_identity": {**owner, "created": 200}}, {}),
            ({**health, "sampled_at": 999}, {}),
            (health, health),
        ):
            with self.subTest(observation=observation):
                self.client.dv.last_health = observation
                self.client._publish()
                self.assertEqual(read_json(self.directory / "active.json")["dropview_health"], published)

    def test_new_reading_timestamps_do_not_repeat_the_same_warning(self):
        log, status = [], []
        self.client.log_message.connect(log.append)
        self.client.status_changed.connect(status.append)
        snapshot = {"run_id": self.client._run_id, "message": "checking:dropsens_disconnected",
                    "warning": "dropsens_disconnected", "last_connection_read": 1000}
        self.client._display_watchdog_status(snapshot)
        self.client._display_watchdog_status({**snapshot, "last_connection_read": 1003})
        self.assertEqual(len(log), 1)
        self.assertEqual(len(status), 2)

    def test_recovery_success_logged_only_after_reaching_resumed_step(self):
        log = []
        self.client.log_message.connect(log.append)
        self.runner.recovery_record = {"record": True}
        self.client._report_recovery_result("running")
        self.assertEqual(log, [])
        self.runner.recovery_continued = True
        self.client._report_recovery_result("running")
        self.client._report_recovery_result("running")
        self.assertEqual(log, ["Recovery verified: recipe resumed in the new package."])

    def test_simulation_requires_a_recovery_record_like_real_mode(self):
        self.client.disarm()
        self.guard.reset_mock()
        self.runner.simulation_mode = True
        self.runner.measurement_mode = "script_pad"
        self.runner.recovery_journal = None
        self.client.arm(self.runner)
        self.assertFalse(self.client.is_armed)
        self.runner.request_recovery.assert_not_called()
        self.tab.recover_from_record.assert_not_called()
        self.assertEqual(self.guard.mock_calls, [])

    def test_simulation_command_waits_for_worker_before_recovering(self):
        self.client.disarm()
        self.runner.simulation_mode = self.runner.simulation_recovery = True
        self.client.arm(self.runner)
        self.command(reason="dropsens_disconnected")
        self.client._tick()
        self.runner.request_recovery.assert_called_once_with("dropsens_disconnected")
        self.tab.recover_from_record.assert_not_called()
        self.runner.is_alive.return_value = False
        self.client._tick()
        self.tab.recover_from_record.assert_called_once_with(str(self.directory / "record.json"))

    def test_simulation_uses_shared_watchdog_and_same_update_policy(self):
        self.client.disarm()
        self.guard.reset_mock()
        self.launch.reset_mock()
        self.runner.simulation_mode = self.runner.simulation_recovery = True
        self.runner.measurement_mode = "script_pad"

        self.client.arm(self.runner)

        active = read_json(self.directory / "active.json")
        self.assertTrue(active["armed"])
        self.assertTrue(active["simulation_recovery"])
        self.assertNotIn("observe_only", active)
        self.assertEqual(active["record_path"], self.runner.recovery_journal.path)
        self.assertEqual(active["status"], "armed")
        self.assertFalse((self.directory / "simulation").exists())
        self.launch.assert_not_called()  # Reuse the existing supervisor in the shared directory.
        self.guard.protect.assert_called_once_with(15)
        self.assertEqual(self.client._update_status, "not_verified")
        self.client.disarm()
        self.guard.restore.assert_called_once()

    def test_simulation_restart_reads_shared_record_with_or_without_token(self):
        for with_token in (False, True):
            with self.subTest(with_token=with_token):
                self.client.disarm()
                self.tab.recover_from_record.reset_mock()
                old_owner = {"pid": -1, "created": 0}
                run_id, token = uuid.uuid4().hex, uuid.uuid4().hex
                atomic_write(self.directory / "active.json", {
                    "armed": True, "simulation_recovery": True, "owner": old_owner,
                    "run_id": run_id, "status": "armed",
                })
                atomic_write(self.directory / "command.json", {
                    "token": token, "run_id": run_id, "target_owner": old_owner,
                })
                self.runner.is_alive.return_value = False
                with patch("bench_test.watchdog.client.recovery_source",
                           return_value=("test-record.json", {"simulation_recovery": True})):
                    self.client.accept_startup(token if with_token else None)

                self.tab.recover_from_record.assert_called_once_with("test-record.json")
                active = read_json(self.directory / "active.json")
                self.assertTrue(active["simulation_recovery"])
                self.assertEqual(active["run_id"], run_id)
                if with_token:
                    self.assertEqual(active["ack_token"], token)
                self.assertFalse((self.directory / "simulation").exists())

    def test_focus_policy_is_limited_to_active_unpaused_measurement_wait(self):
        self.runner._interrupted = Mock(return_value=False)
        self.assertTrue(self.client.should_focus_connection())
        self.client.set_finalizing(True)
        self.assertTrue(self.client.should_focus_connection())
        self.runner.pause_event.clear()
        self.assertFalse(self.client.should_focus_connection())
        self.runner.pause_event.set()
        self.runner._recovery_phase = "measurement_stop"
        self.assertFalse(self.client.should_focus_connection())

    def command(self, **changes):
        command = dict(token=uuid.uuid4().hex, run_id=self.client._run_id, target_owner=self.client.owner,
                       record_path=str(self.directory / "record.json"), reason="test fault")
        command.update(changes)
        atomic_write(self.client.directory / "command.json", command)
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

    def test_recording_failure_keeps_monitoring_and_blocks_commands_without_cancelling_run(self):
        self.command()
        self.runner._journal_failed = True
        self.client._tick()
        self.assertTrue(self.client.is_armed)
        self.assertFalse(self.client._stop_signal.is_set())
        self.assertFalse(read_json(self.directory / "active.json")["recovery_available"])
        self.runner.request_recovery.assert_not_called()
        self.tab.recover_from_record.assert_not_called()
        self.guard.restore.assert_not_called()

    def test_recording_restoration_does_not_undo_user_stop(self):
        self.runner._journal_failed = True
        self.client._tick()
        self.client.disarm()
        self.runner._journal_failed = False
        self.client._tick()
        self.assertFalse(self.client.is_armed)
        self.assertTrue(self.client._stop_signal.is_set())
        self.tab.recover_from_record.assert_not_called()

    def test_recording_restoration_republishes_eligibility_and_block_reason_is_visible(self):
        messages = []
        self.client.status_changed.connect(messages.append)
        self.runner._journal_failed = True
        self.client._tick()
        self.assertTrue(any("Monitoring active; recovery blocked" in message for message in messages))
        self.runner._journal_failed = False
        self.client._tick()
        self.assertTrue(read_json(self.directory / "active.json")["recovery_available"])
        self.assertFalse(self.client._stop_signal.is_set())

    def test_initial_recording_failure_still_arms_monitoring(self):
        with patch.object(self.runner.recovery_journal, "update", side_effect=OSError("disk full")):
            self.client.arm(self.runner)
        self.assertTrue(self.client.is_armed)
        self.assertFalse(read_json(self.directory / "active.json")["recovery_available"])

    def test_command_read_error_does_not_skip_status_read_or_update_verification(self):
        from bench_test.watchdog import client as module
        read = module.read_json
        with patch.object(module, "read_json", side_effect=lambda path:
                          (_ for _ in ()).throw(ValueError("invalid command JSON"))
                          if path.name == "command.json" else read(path)) as attempt:
            self.client._tick()
        self.assertIn("supervisor_status.json", [call.args[0].name for call in attempt.call_args_list])
        self.assertIn("command.json", self.client._io_errors)
        self.guard.status.assert_called_once()
        self.runner.request_recovery.assert_not_called()

    def test_active_publish_error_does_not_skip_status_and_blocks_recovery_command(self):
        from bench_test.watchdog import client as module
        self.command()
        messages = []
        self.client.status_changed.connect(messages.append)
        self.client._last_publish_at = -float("inf")
        with patch.object(module, "atomic_write", side_effect=PermissionError("active locked")):
            self.client._tick()
        self.assertIn("active_write", self.client._io_errors)
        self.runner.request_recovery.assert_not_called()
        self.guard.status.assert_called_once()
        self.assertTrue(any("Incomplete monitoring" in message for message in messages))

    def test_supervisor_read_error_displays_cached_status_as_incomplete_without_respawning(self):
        from bench_test.watchdog import client as module
        self.client._last_supervisor = {"run_id": self.client._run_id, "message": "healthy"}
        self.client._last_spawn = -1000
        self.launch.reset_mock()
        read = module.read_json
        with patch.object(module, "read_json", side_effect=lambda path:
                          (_ for _ in ()).throw(PermissionError("status locked"))
                          if path.name == "supervisor_status.json" else read(path)):
            self.client._tick()
        self.assertIn("supervisor_status.json", self.client._last_status)
        self.assertIn("Incomplete monitoring", self.client._last_status)
        self.launch.assert_not_called()

    def test_transient_checkpoint_conflict_keeps_watchdog_armed(self):
        replace = os.replace
        with patch("bench_test.utils.file_io.os.replace") as attempt, \
                patch("bench_test.utils.file_io.time.sleep"), \
                patch("bench_test.utils.file_io.debug_log"), \
                patch("bench_test.recipe.recovery.debug_log"):
            def blocked_once(source, destination):
                if attempt.call_count == 1:
                    raise PermissionError("locked")
                return replace(source, destination)
            attempt.side_effect = blocked_once
            self.runner.recovery_journal.update(phase="waiting", remaining_seconds=99)
            self.client._tick()
        self.assertTrue(self.client.is_armed)
        self.assertFalse(self.client._stop_signal.is_set())
        self.assertEqual(read_json(self.runner.recovery_journal.path)["remaining_seconds"], 99)
        self.assertTrue(read_json(self.directory / "active.json")["armed"])

    def test_transient_watchdog_state_conflict_does_not_lose_heartbeat(self):
        replace = os.replace
        with patch("bench_test.utils.file_io.os.replace") as attempt, \
                patch("bench_test.utils.file_io.time.sleep"), \
                patch("bench_test.utils.file_io.debug_log"):
            def blocked_once(source, destination):
                if attempt.call_count == 1:
                    raise PermissionError("locked")
                return replace(source, destination)
            attempt.side_effect = blocked_once
            self.client._publish()
        self.assertTrue(read_json(self.directory / "active.json")["armed"])
        self.assertTrue(self.client.is_armed)

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
        self.assertTrue(any("startup failed" in message for message in messages))


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
