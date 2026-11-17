"""Aggregation lifecycle tests with real Qt delivery and fake Excel work."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication
from bench_test.measurement.state_machine import SessionStateMachine, RUNNING, IDLE


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.sm = SessionStateMachine()
        self.sm._state = RUNNING
        self.session = SimpleNamespace(measurement_mode="script_pad", measurements_dir="unused",
                                       part_number="PAD", register_file=Mock(), save=Mock(), set_status=Mock())
        self.sm._session = self.session
        self.activity = []
        self.sm.aggregate_activity.connect(self.activity.append)
        self.addCleanup(self.sm._timer.stop)
        logger = patch("bench_test.measurement.state_machine.debug_log")
        logger.start()
        self.addCleanup(logger.stop)

    def wait_finished(self):
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: loop.quit() if not self.sm._aggregate_running else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(2000)
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertFalse(self.sm._aggregate_running, "Aggregate completion was not delivered to Qt.")

    def test_excel_work_keeps_qt_responsive_and_session_updates_on_ui_thread(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        worker_threads, completion_threads = [], []
        main_thread = threading.get_ident()

        def build(**kwargs):
            worker_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test worker not released")
            return "PAD.xlsx"

        self.session.save.side_effect = lambda: completion_threads.append(threading.get_ident())
        with patch("bench_test.measurement.state_machine.Aggregator") as aggregate:
            aggregate.return_value.run_silent.side_effect = build
            self.sm._run_aggregate(periodic=True)
            self.assertTrue(entered.wait(1))
            loop = QEventLoop()
            timer_fired = []
            QTimer.singleShot(0, lambda: (timer_fired.append(True), loop.quit()))
            loop.exec()
            self.assertTrue(timer_fired)
            self.assertTrue(self.sm._aggregate_running)
            self.assertEqual(self.activity, [True])
            release.set()
            self.wait_finished()
        self.assertNotEqual(worker_threads, [main_thread])
        self.assertEqual(completion_threads, [main_thread])
        self.assertEqual(self.activity, [True, False])
        self.session.register_file.assert_called_once_with("xlsx", "PAD.xlsx")

    def test_worker_and_session_save_errors_both_clear_activity(self):
        for failure in ("worker", "save"):
            with self.subTest(failure=failure), patch("bench_test.measurement.state_machine.Aggregator") as aggregate:
                aggregate.return_value.run_silent.return_value = "PAD.xlsx"
                aggregate.return_value.run_silent.side_effect = OSError("Excel failed") if failure == "worker" else None
                self.session.save.side_effect = OSError("session failed") if failure == "save" else None
                self.activity.clear()
                self.sm._run_aggregate()
                self.wait_finished()
                self.assertEqual(self.activity, [True, False])

    def test_thread_start_failure_clears_activity(self):
        with patch("bench_test.measurement.state_machine.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread unavailable")
            self.sm._run_aggregate()
        self.assertFalse(self.sm._aggregate_running)
        self.assertEqual(self.activity, [True, False])

    def test_continuous_pad_skips_excel_and_finishes_without_a_worker(self):
        self.session.measurement_mode = "continuous_pad"
        with patch("bench_test.measurement.state_machine.Aggregator") as aggregate:
            self.sm.finish()
        aggregate.assert_not_called()
        self.assertEqual(self.sm.state, IDLE)
        self.assertFalse(self.sm.aggregation_running)

    def test_manual_aggregate_cannot_overlap_background_work(self):
        from bench_test.ui.tabs.package_panel import PackagePanel
        panel = SimpleNamespace(_session=self.session, sm=SimpleNamespace(state="ready", aggregation_running=True))
        with patch("bench_test.ui.tabs.package_panel.QMessageBox.warning") as warning, \
                patch("bench_test.ui.tabs.package_panel.Aggregator") as aggregate:
            PackagePanel._run_aggregator(panel)
        warning.assert_called_once()
        aggregate.assert_not_called()

    def test_stop_during_periodic_job_queues_final_pass_before_idle(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def build(**kwargs):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test worker not released")
            return "PAD.xlsx"

        with patch("bench_test.measurement.state_machine.Aggregator") as aggregate:
            aggregate.return_value.run_silent.side_effect = build
            self.sm._run_aggregate(periodic=True)
            self.assertTrue(entered.wait(1))
            self.sm.stop()
            self.assertEqual(self.sm.state, "aggregating")
            release.set()
            self.wait_finished()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sm.state, IDLE)
        self.assertEqual(self.activity, [True, False, True, False])

    def test_reset_ignores_result_from_previous_session(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def build(**kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test worker not released")
            return "PAD.xlsx"

        with patch("bench_test.measurement.state_machine.Aggregator") as aggregate:
            aggregate.return_value.run_silent.side_effect = build
            self.sm._run_aggregate(periodic=True)
            self.assertTrue(entered.wait(1))
            self.sm.reset()
            release.set()
            self.wait_finished()
        self.session.register_file.assert_not_called()
        self.assertEqual(self.activity[-1], False)


if __name__ == "__main__":
    unittest.main()
