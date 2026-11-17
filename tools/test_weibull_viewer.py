"""Headless Qt integration checks; never opens hardware or a measurement session."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from datetime import datetime
from pathlib import Path
import sys
import time
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from PyQt6 import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_test.measurement.log_parser import ValveEvent
from bench_test.ui.tabs.viewer_tab import ViewerTab
from bench_test.measurement.weibull_fit import FitResult, FitOptions
from bench_test.ui.weibull_overlay import _FitJob


class WeibullViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = patch('bench_test.ui.weibull_overlay.remember_value')
        self.settings.start()
        self.viewer = ViewerTab()
        self.controller = self.viewer._weibull
        self.top, self.bottom = self.controller.plots
        self.x = 1700000000.+np.arange(80)*60
        self.y = np.full(80, 85e-9)
        self.viewer.top_curve = self.top.plot(self.x, self.y*2)
        self.viewer.bottom_curve = self.bottom.plot(self.x, self.y)
        for curve in (self.viewer.top_curve, self.viewer.bottom_curve):
            curve.opts['movingAverageSource'] = True
        self.events = [ValveEvent(datetime.fromtimestamp(self.x[0]-630),1,'Load','test.log')]
        self.controller.set_data(True, ('test',), self.events)

    def tearDown(self):
        self.controller.reset()
        self.controller.pool.waitForDone(10000)
        self.app.processEvents()
        self.viewer._poll_timer.stop()
        self.viewer.close()
        self.viewer.deleteLater()
        self.app.processEvents()
        self.settings.stop()

    def wait_result(self):
        deadline = time.monotonic()+15
        while self.controller._job is not None or self.controller._pending is not None:
            self.app.processEvents()
            if time.monotonic()>deadline:
                self.fail('Background Weibull task timed out')
            time.sleep(.005)
        self.app.processEvents()

    def enable(self):
        combo = self.bottom.ctrl.smoothingMethodCombo
        combo.setCurrentIndex(combo.findData('weibull_robust'))
        self.wait_result()

    def test_same_lower_graph_fit_is_drawn_on_both_graphs(self):
        self.enable()
        a=self.top._movingAverageCurves[self.viewer.top_curve].getData()
        b=self.bottom._movingAverageCurves[self.viewer.bottom_curve].getData()
        np.testing.assert_array_equal(a[0],b[0])
        np.testing.assert_array_equal(a[1],b[1])
        self.assertAlmostEqual(np.nanmean(a[1]),85e-9,delta=1e-10)
        self.assertEqual(len(self.viewer.top_curve.getOriginalDataset()[0]),80)
        self.assertEqual(len(self.viewer.bottom_curve.getOriginalDataset()[0]),80)

    def test_zoom_and_clip_do_not_launch_optimization(self):
        self.enable()
        with patch('bench_test.ui.weibull_overlay.fit_steps', side_effect=AssertionError('zoom caused fit')):
            self.bottom.setClipToView(True)
            self.top.setXRange(self.x[30],self.x[40],padding=0)
            self.app.processEvents()
            self.wait_result()
        self.assertIsNone(self.controller._job)

    def test_settings_sync_and_tolerance_uses_na(self):
        self.enable()
        self.bottom.ctrl.weibullMinPointsSpin.setValue(12)
        self.bottom.ctrl.weibullToleranceSpin.setValue(3.)
        self.wait_result()
        self.assertEqual(self.top.ctrl.weibullMinPointsSpin.value(),12)
        self.assertEqual(self.top.ctrl.weibullToleranceSpin.value(),3.)
        self.assertEqual(self.controller._result_key[5].tolerance_na,3.)

    def test_switch_back_restores_both_existing_methods(self):
        self.enable()
        for method in ('savitzky_golay','moving_average'):
            combo=self.bottom.ctrl.smoothingMethodCombo
            combo.setCurrentIndex(combo.findData(method))
            self.app.processEvents()
            for plot,source in ((self.top,self.viewer.top_curve),(self.bottom,self.viewer.bottom_curve)):
                self.assertFalse(plot._movingAverageCurves[source].opts.get('weibullOverlay',False))

    def test_reset_discards_late_result_and_missing_log_is_explicit(self):
        self.enable()
        self.controller.set_data(True,('test',),[])
        self.assertIn('log',self.bottom.ctrl.weibullStatusLabel.text())
        self.assertFalse(self.bottom._movingAverageCurves)
        self.controller.reset()
        self.wait_result()
        self.assertFalse(self.top._movingAverageCurves)

    def test_background_result_after_reset_is_ignored_and_ui_stays_responsive(self):
        started, release = threading.Event(), threading.Event()
        def slow_fit(*args):
            started.set()
            release.wait(5)
            return FitResult(x=self.x.copy(),y=np.full(80,85.),status='old result')
        with patch('bench_test.ui.weibull_overlay.fit_steps',side_effect=slow_fit):
            combo=self.bottom.ctrl.smoothingMethodCombo
            combo.setCurrentIndex(combo.findData('weibull_robust'))
            self.assertTrue(started.wait(2))
            responsive=[]
            QTimer.singleShot(0,lambda:responsive.append(True))
            self.app.processEvents()
            self.assertTrue(responsive)
            self.controller.reset()
            release.set()
            self.wait_result()
        self.assertIsNone(self.controller._result)
        self.assertFalse(self.top._movingAverageCurves)

    def test_worker_can_finish_after_qt_signal_object_is_destroyed(self):
        snapshot=(self.x,self.y,1,'subsample',0,self.events,FitOptions())
        job=_FitJob(0,'closed-viewer',snapshot,{})
        sip.delete(job.signals)
        with patch('bench_test.ui.weibull_overlay.fit_steps',return_value=FitResult()):
            job.run()

    def test_frozen_import_failure_reports_missing_module_and_logs_traceback(self):
        snapshot=(self.x,self.y,1,'subsample',0,self.events,FitOptions())
        job=_FitJob(0,'missing-dependency',snapshot,{})
        received=[]
        job.signals.finished.connect(lambda *args: received.append(args))
        missing='scipy._external.array_api_compat.numpy.fft'
        with patch('bench_test.ui.weibull_overlay.fit_steps',
                   side_effect=ModuleNotFoundError(f"No module named '{missing}'")), \
             patch('bench_test.ui.weibull_overlay.sys.frozen',True,create=True), \
             self.assertLogs('bench_test.ui.weibull_overlay',level='ERROR') as logs:
            job.run()
        self.assertIsNone(received[0][2])
        self.assertIn(missing,received[0][3])
        self.assertIn('EXE paketindeki',received[0][3])
        self.assertIn('Traceback',logs.output[0])

    def test_export_contains_shared_curve_and_parameters(self):
        self.enable()
        self.top.setXRange(self.x[0],self.x[-1],padding=0)
        self.app.processEvents()
        payload=self.viewer._build_visible_xlsx_export()
        self.assertEqual(payload['series']['top_average'],payload['series']['bottom_average'])
        meta=dict(payload['metadata'])
        self.assertEqual(meta['average_method'],'weibull_robust')
        self.assertEqual(meta['weibull_loss'],'cauchy')
        self.assertEqual(meta['weibull_drift'],'linear_per_A_phase')
        self.assertEqual(meta['weibull_drift_units'],'nA/hour')
        self.assertEqual(meta['weibull_drift_constraint'],'slope<=0')
        self.assertEqual(meta['weibull_tolerance_nA'],self.bottom.ctrl.weibullToleranceSpin.value())

    def test_shared_drifting_curve_ends_at_latest_measurement(self):
        minutes=(self.x-self.x[0])/60
        drifting=(85.-8.*minutes/60)*1e-9
        self.viewer.bottom_curve.setData(self.x,drifting)
        self.controller.set_data(True,('test',),self.events)
        self.enable()
        fitted=next(iter(self.controller._result.cache.values()))['fit']
        self.assertAlmostEqual(fitted['slopes'][0],-8.,delta=.01)
        top=self.top._movingAverageCurves[self.viewer.top_curve].getData()
        bottom=self.bottom._movingAverageCurves[self.viewer.bottom_curve].getData()
        np.testing.assert_array_equal(top[1],bottom[1])
        self.assertLessEqual(np.nanmax(top[0]),self.x[-1])

    def test_repeated_acknowledgements_do_not_start_new_job(self):
        self.enable()
        repeated=self.events+[ValveEvent(datetime.fromtimestamp(self.x[10]),1,'Load','test.log')]
        with patch('bench_test.ui.weibull_overlay.fit_steps',side_effect=AssertionError('duplicate event caused fit')):
            self.controller.set_data(True,('test',),repeated)
            self.wait_result()

    def test_b_mode_changes_do_not_start_a_new_fit_or_reset_the_curve(self):
        self.enable()
        key=self.controller._result_key
        changed=self.events+[
            ValveEvent(datetime.fromtimestamp(self.x[10]),None,'Inject','test.log'),
            ValveEvent(datetime.fromtimestamp(self.x[20]),None,'Load','test.log')]
        with patch('bench_test.ui.weibull_overlay.fit_steps',side_effect=AssertionError('B-only change caused fit')):
            self.controller.set_data(True,('test',),changed)
            self.wait_result()
        self.assertEqual(self.controller._result_key,key)
        self.assertTrue(self.bottom._movingAverageCurves)

    def test_manual_lower_downsampling_is_used_for_fit(self):
        self.enable()
        from bench_test.measurement.weibull_fit import fit_steps
        with patch('bench_test.ui.weibull_overlay.fit_steps',wraps=fit_steps) as observed:
            self.bottom.setDownsampling(ds=3,auto=False,mode='mean')
            self.wait_result()
            xx,yy=observed.call_args.args[:2]
        np.testing.assert_array_equal(xx,self.x[1:79:3])
        np.testing.assert_allclose(yy,85.)


if __name__=='__main__':
    unittest.main()
