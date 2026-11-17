"""One asynchronous DPAD fit shared by the two Viewer plots."""
from __future__ import annotations

import hashlib
import logging
import sys

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, sip

from bench_test.measurement.weibull_fit import FitOptions, fit_steps, prepare_series, a_port_states
from bench_test.utils.paths import get_value, remember_value


METHOD = "weibull_robust"


class _Signals(QtCore.QObject):
    finished = QtCore.pyqtSignal(int, object, object, str)


class _FitJob(QtCore.QRunnable):
    def __init__(self, generation, key, snapshot, cache):
        super().__init__()
        self.signals = _Signals()
        self.generation, self.key = generation, key
        self.snapshot, self.cache = snapshot, cache

    def run(self):
        try:
            x, y, ds, mode, offset, events, options = self.snapshot
            x, y = prepare_series(x, y, ds, mode, offset)
            result = fit_steps(x, y*1e9, events, options, self.cache)
            error = ""
        except ImportError as exc:
            logging.getLogger(__name__).exception("Weibull dependency import failed")
            help_text = ("EXE paketindeki SciPy bağımlılıkları eksik; paket yeniden oluşturulmalı."
                         if getattr(sys, "frozen", False) else
                         "SciPy bağımlılıklarını yükleyin: requirements-weibull.txt.")
            result, error = None, f"Weibull yükleme hatası: {exc}. {help_text}"
        except Exception as exc:
            result, error = None, f"Weibull hesap hatası: {exc}"
        try:
            self.signals.finished.emit(self.generation, self.key, result, error)
        except RuntimeError:
            # Qt may destroy the receiver/signal object during application exit
            # while a numerical worker finishes. Do not hide other errors.
            if not sip.isdeleted(self.signals):
                raise


class WeibullOverlayController(QtCore.QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.plots = (viewer.plot_widget_top.getPlotItem(), viewer.plot_widget_bottom.getPlotItem())
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.pool.setThreadPriority(QtCore.QThread.Priority.LowPriority)
        self._syncing = False
        self._ready = False
        self._supported = False
        self._context = None
        self._events = ()
        self._x, self._y = np.empty(0), np.empty(0)
        self._generation = 0
        self._requested_key = None
        self._result_key = None
        self._result = None
        self._cache = {}
        self._pending = None
        self._job = None
        self._last_error = ""
        self._shared_selected = False
        minimum = max(3, int(get_value("viewer_weibull_min_points", 10)))
        tolerance = max(.01, float(get_value("viewer_weibull_tolerance_na", 4.)))
        self._remembered = (minimum, tolerance)
        for plot in self.plots:
            plot.ctrl.weibullMinPointsSpin.setValue(minimum)
            plot.ctrl.weibullToleranceSpin.setValue(tolerance)
            plot._weibull_options_changed = self.options_changed

    def begin_rebuild(self):
        self._ready = False

    def reset(self):
        self._ready = False
        self._supported = False
        self._generation += 1
        self._requested_key = self._result_key = None
        self._result = None
        self._cache = {}
        self._pending = None
        self._remove_overlays()
        self._set_status("Weibull yalnız DPAD ölçümlerinde kullanılabilir.")
        for plot in self.plots:
            plot.ctrl.smoothingMethodCombo.model().item(2).setEnabled(False)

    def set_data(self, supported, context, events):
        self._supported = supported
        self._ready = True
        if context != self._context:
            self._context = context
            self._generation += 1
            self._requested_key = self._result_key = None
            self._result = None
            self._cache = {}
        self._events = tuple(events)
        source = self.viewer.bottom_curve
        x, y = source.getOriginalDataset() if source is not None else (None, None)
        self._x = np.asarray(x, dtype=float).copy() if x is not None else np.empty(0)
        self._y = np.asarray(y, dtype=float).copy() if y is not None else np.empty(0)
        for plot in self.plots:
            plot.ctrl.smoothingMethodCombo.model().item(2).setEnabled(supported)
        self.request()

    def _remove_overlays(self, all_averages=False):
        for plot in self.plots:
            curves = getattr(plot, "_movingAverageCurves", {})
            for source, overlay in list(curves.items()):
                if all_averages or overlay.opts.get("weibullOverlay"):
                    plot.removeItem(overlay)
                    curves.pop(source, None)

    def _set_status(self, message):
        for plot in self.plots:
            plot.ctrl.weibullStatusLabel.setText(message)

    def options_changed(self, origin):
        if self._syncing:
            return
        method = origin.ctrl.smoothingMethodCombo.currentData()
        was_shared = self._shared_selected
        self._syncing = True
        try:
            if method == METHOD or was_shared:
                for plot in self.plots:
                    if plot is origin:
                        continue
                    ctrl = plot.ctrl
                    blockers = [QtCore.QSignalBlocker(widget) for widget in
                        (ctrl.smoothingMethodCombo, ctrl.weibullMinPointsSpin, ctrl.weibullToleranceSpin)]
                    ctrl.smoothingMethodCombo.setCurrentIndex(ctrl.smoothingMethodCombo.findData(method))
                    ctrl.weibullMinPointsSpin.setValue(origin.ctrl.weibullMinPointsSpin.value())
                    ctrl.weibullToleranceSpin.setValue(origin.ctrl.weibullToleranceSpin.value())
                    del blockers
                    plot.updateMovingAverage()
                self._shared_selected = method == METHOD
                if method == METHOD:
                    # Once enabled, show the common curve on both plots. Users
                    # can subsequently hide either Average independently.
                    if not was_shared:
                        self._remove_overlays(all_averages=True)
                        for plot in self.plots:
                            plot.ctrl.averageGroup.setChecked(True)
                    settings = (origin.ctrl.weibullMinPointsSpin.value(), origin.ctrl.weibullToleranceSpin.value())
                    if settings != self._remembered:
                        remember_value("viewer_weibull_min_points", settings[0])
                        remember_value("viewer_weibull_tolerance_na", settings[1])
                        self._remembered = settings
        finally:
            self._syncing = False
        if method != METHOD:
            self._generation += 1
            self._pending = None
            self._requested_key = None
            self._remove_overlays()
            if was_shared:
                for plot in self.plots:
                    if plot is not origin:
                        plot.updateMovingAverage()
            return
        self.request()

    def request(self):
        if not self._ready:
            return
        selected = any(p.ctrl.smoothingMethodCombo.currentData() == METHOD and p.ctrl.averageGroup.isChecked() for p in self.plots)
        if not selected:
            self._remove_overlays()
            self._generation += 1
            self._pending = None
            self._requested_key = None
            return
        if not self._supported:
            self._invalidate("Weibull yalnız DPAD ölçümlerinde kullanılabilir.")
            return
        bottom = self.plots[1]
        ds, auto, mode, offset = bottom.downsampleMode()
        if auto:
            self._invalidate("Weibull için alt grafikte Auto yerine sabit downsampling seçin.")
            return
        if not self._events:
            self._invalidate("Gerçek valf durumlarını içeren uygun log bulunamadı.")
            return
        ctrl = bottom.ctrl
        options = FitOptions(delay_seconds=self.viewer._get_offset_sec(),
            min_points=ctrl.weibullMinPointsSpin.value(), tolerance_na=ctrl.weibullToleranceSpin.value())
        data_digest = hashlib.blake2b(self._x.tobytes()+self._y.tobytes(), digest_size=16).hexdigest()
        event_key = tuple(a_port_states(self._events, float(self._x[-1]))) if len(self._x) else ()
        key = (self._context, data_digest, ds, mode, offset, options, event_key)
        if key == self._result_key:
            self._draw()
            return
        if key == self._requested_key:
            return
        self._requested_key = key
        self._generation += 1
        snapshot = (self._x, self._y, ds, mode, offset, self._events, options)
        self._pending = (self._generation, key, snapshot, self._cache)
        self._remove_overlays()
        self._set_status("Weibull arka planda hesaplanıyor…")
        self._start_pending()

    def _invalidate(self, status):
        self._generation += 1
        self._pending = None
        self._requested_key = None
        self._remove_overlays()
        self._set_status(status)

    def _start_pending(self):
        if self._job is not None or self._pending is None:
            return
        self._job = _FitJob(*self._pending)
        self._pending = None
        self._job.signals.finished.connect(self._finished)
        self.pool.start(self._job)

    @QtCore.pyqtSlot(int, object, object, str)
    def _finished(self, generation, key, result, error):
        self._job = None
        if generation == self._generation:
            self._requested_key = None
            if error:
                self._set_status(error)
                if error != self._last_error:
                    self.viewer.log_signal.emit(error)
                    self._last_error = error
            else:
                self._result_key, self._result = key, result
                self._cache = result.cache
                self._last_error = ""
                if self._ready:
                    self._draw()
        self._start_pending()

    def _draw(self):
        if self._result is None:
            return
        self._set_status(self._result.status)
        for plot, source in zip(self.plots, (self.viewer.top_curve, self.viewer.bottom_curve)):
            overlays = plot._movingAverageCurves
            if source is None:
                continue
            if not plot.ctrl.averageGroup.isChecked() or plot.ctrl.smoothingMethodCombo.currentData() != METHOD:
                overlay = overlays.pop(source, None)
                if overlay is not None:
                    plot.removeItem(overlay)
                continue
            overlay = overlays.get(source)
            if overlay is None:
                overlay = pg.PlotDataItem()
                overlay.opts.update(movingAverageOverlay=True, weibullOverlay=True)
                overlay.setPen(pg.mkPen((255, 140, 0), width=3))
                plot.addItem(overlay, skipAverage=True)
                overlay.setDownsampling(ds=1, auto=False, method="subsample")
                overlay.setClipToView(False)
                for collection in (plot.curves, plot.dataItems):
                    if overlay in collection:
                        collection.remove(overlay)
                plot.itemMeta.pop(overlay, None)
                overlays[source] = overlay
            overlay.setData(self._result.x, self._result.y*1e-9, connect="finite")
