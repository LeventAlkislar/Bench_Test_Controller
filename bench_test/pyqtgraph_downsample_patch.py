"""Project-local PyQtGraph extensions for graph display processing.

This module intentionally patches PyQtGraph at runtime instead of editing the
installed package under .venv. Removing the import restores stock behavior.
"""

from __future__ import annotations

import bisect
import math

import numpy as np


_INSTALLED = False


def install_offset_downsample() -> None:
    """Add offset-aware downsample and moving average controls to PyQtGraph."""
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        from pyqtgraph import functions as fn
        from pyqtgraph.Qt import QtCore, QtWidgets
        from pyqtgraph.graphicsItems.PlotDataItem import PlotDataItem, PlotDataset
        from pyqtgraph.graphicsItems.PlotItem.PlotItem import PlotItem
    except Exception:
        return

    original_plotitem_init = PlotItem.__init__
    original_plotitem_avg_toggled = PlotItem.avgToggled
    original_plotitem_add_avg_curve = PlotItem.addAvgCurve
    original_plotitem_set_downsampling = PlotItem.setDownsampling
    original_plotitem_clear = PlotItem.clear
    original_plotitem_clear_plots = PlotItem.clearPlots
    original_plotdata_set_downsampling = PlotDataItem.setDownsampling
    original_plotdata_update_items = PlotDataItem.updateItems
    original_get_display_dataset = PlotDataItem._getDisplayDataset

    def patch_plotitem_controls(plot_item: PlotItem) -> None:
        ctrl = plot_item.ctrl
        if hasattr(ctrl, "downsampleOffsetSpin"):
            return

        ctrl.movingAverageRadiusLabel = QtWidgets.QLabel(ctrl.averageGroup)
        ctrl.movingAverageRadiusLabel.setObjectName("movingAverageRadiusLabel")
        ctrl.movingAverageRadiusLabel.setText("Window:")
        ctrl.movingAverageRadiusLabel.setToolTip(
            "Number of previous and next visible data points used for Average."
        )
        ctrl.gridLayout_5.addWidget(ctrl.movingAverageRadiusLabel, 1, 0, 1, 1)

        ctrl.movingAverageRadiusSpin = QtWidgets.QSpinBox(ctrl.averageGroup)
        ctrl.movingAverageRadiusSpin.setObjectName("movingAverageRadiusSpin")
        ctrl.movingAverageRadiusSpin.setMinimum(1)
        ctrl.movingAverageRadiusSpin.setMaximum(100000)
        ctrl.movingAverageRadiusSpin.setValue(5)
        ctrl.movingAverageRadiusSpin.setToolTip(
            "Window N: average each displayed point with up to N points before "
            "and N points after it."
        )
        ctrl.gridLayout_5.addWidget(ctrl.movingAverageRadiusSpin, 1, 1, 1, 1)

        ctrl.downsampleOffsetLabel = QtWidgets.QLabel(ctrl.decimateGroup)
        ctrl.downsampleOffsetLabel.setObjectName("downsampleOffsetLabel")
        ctrl.downsampleOffsetLabel.setText("Start:")
        ctrl.downsampleOffsetLabel.setToolTip(
            "Used only when Subsample is selected."
        )
        ctrl.gridLayout_4.addWidget(ctrl.downsampleOffsetLabel, 4, 1, 1, 1)

        ctrl.downsampleOffsetSpin = QtWidgets.QSpinBox(ctrl.decimateGroup)
        ctrl.downsampleOffsetSpin.setObjectName("downsampleOffsetSpin")
        ctrl.downsampleOffsetSpin.setMinimum(0)
        ctrl.downsampleOffsetSpin.setMaximum(100000000)
        ctrl.downsampleOffsetSpin.setToolTip(
            "Zero-based data index to use as the first displayed Subsample point."
        )
        ctrl.gridLayout_4.addWidget(ctrl.downsampleOffsetSpin, 4, 2, 1, 1)

        ctrl.movingAverageRadiusSpin.valueChanged.connect(
            plot_item.updateMovingAverage
        )
        ctrl.downsampleOffsetSpin.valueChanged.connect(plot_item.updateDownsampling)

    def is_average_overlay(curve) -> bool:
        return bool(getattr(curve, "opts", {}).get("movingAverageOverlay", False))

    def invalidate_display_dataset(curve) -> None:
        curve._datasetDisplay = None
        curve._adsLastValue = 1
        curve.updateItems(styleUpdate=False)

    def refresh_average_curve(plot_item: PlotItem, source_curve) -> None:
        if is_average_overlay(source_curve):
            return
        if not isinstance(source_curve, PlotDataItem):
            return
        if not hasattr(source_curve, "_getDisplayDataset"):
            return

        enabled = plot_item.ctrl.averageGroup.isChecked()
        overlay_curves = getattr(plot_item, "_movingAverageCurves", {})
        explicit_sources = [
            curve
            for curve in plot_item.curves
            if isinstance(curve, PlotDataItem)
            and curve.opts.get("movingAverageSource", False)
        ]
        if explicit_sources and not source_curve.opts.get("movingAverageSource", False):
            overlay = overlay_curves.pop(source_curve, None)
            if overlay is not None:
                plot_item.removeItem(overlay)
            source_curve.opts["movingAverageEnabled"] = False
            source_curve.opts.pop("movingAveragePlotItem", None)
            return
        if source_curve.opts.get("movingAverageSkip", False):
            overlay = overlay_curves.pop(source_curve, None)
            if overlay is not None:
                plot_item.removeItem(overlay)
            source_curve.opts["movingAverageEnabled"] = False
            source_curve.opts.pop("movingAveragePlotItem", None)
            return
        if not enabled:
            overlay = overlay_curves.pop(source_curve, None)
            if overlay is not None:
                plot_item.removeItem(overlay)
            return

        source_curve.opts["movingAverageEnabled"] = True
        source_curve.opts["movingAveragePlotItem"] = plot_item
        source_curve.opts["movingAverageRadius"] = (
            plot_item.ctrl.movingAverageRadiusSpin.value()
        )

        dataset = source_curve._getDisplayDataset()
        if dataset is None or len(dataset.x) == 0:
            return

        overlay = overlay_curves.get(source_curve)
        if overlay is None:
            overlay = PlotDataItem()
            overlay.opts["movingAverageOverlay"] = True
            overlay.setPen(fn.mkPen((255, 140, 0), width=3))
            overlay.setShadowPen(None)
            overlay.setZValue(source_curve.zValue() + 1)
            overlay.setDownsampling(ds=1, auto=False, method="subsample")
            plot_item.addItem(overlay, skipAverage=True)
            overlay.setDownsampling(ds=1, auto=False, method="subsample")
            overlay.setClipToView(False)
            if overlay in plot_item.curves:
                plot_item.curves.remove(overlay)
            if overlay in plot_item.dataItems:
                plot_item.dataItems.remove(overlay)
            plot_item.itemMeta.pop(overlay, None)
            overlay_curves[source_curve] = overlay
            plot_item._movingAverageCurves = overlay_curves

        radius = source_curve.opts.get("movingAverageRadius", 5)
        connect = dataset.connect if dataset.connect is not None else "all"
        overlay.setData(
            dataset.x,
            moving_average(dataset.y, radius),
            connect=connect,
            stepMode=None,
        )
        overlay.setDownsampling(ds=1, auto=False, method="subsample")
        overlay._datasetDisplay = None

    def remove_average_curves(plot_item: PlotItem) -> None:
        for overlay in list(getattr(plot_item, "_movingAverageCurves", {}).values()):
            plot_item.removeItem(overlay)
        plot_item._movingAverageCurves = {}

    def patched_plotitem_init(self, *args, **kwargs):
        original_plotitem_init(self, *args, **kwargs)
        self._movingAverageCurves = {}
        patch_plotitem_controls(self)
        self.updateMovingAverage()

    def patched_avg_toggled(self, enabled: bool):
        if hasattr(self.ctrl, "movingAverageRadiusSpin"):
            if enabled:
                self.updateMovingAverage()
            else:
                for curve in self.curves:
                    if not is_average_overlay(curve):
                        curve.opts["movingAverageEnabled"] = False
                        curve.opts.pop("movingAveragePlotItem", None)
                remove_average_curves(self)
            self.replot()
            return
        original_plotitem_avg_toggled(self, enabled)

    def patched_clear(self):
        remove_average_curves(self)
        original_plotitem_clear(self)
        self._movingAverageCurves = {}

    def patched_clear_plots(self):
        remove_average_curves(self)
        original_plotitem_clear_plots(self)
        self._movingAverageCurves = {}

    def patched_add_avg_curve(self, curve):
        if hasattr(self.ctrl, "movingAverageRadiusSpin"):
            refresh_average_curve(self, curve)
            return
        original_plotitem_add_avg_curve(self, curve)

    @QtCore.Slot()
    def patched_update_moving_average(self):
        enabled = self.ctrl.averageGroup.isChecked()
        radius = 5
        if hasattr(self.ctrl, "movingAverageRadiusSpin"):
            radius = self.ctrl.movingAverageRadiusSpin.value()
        for curve in self.curves:
            if hasattr(curve, "setMovingAverage") and not is_average_overlay(curve):
                curve.setMovingAverage(enabled=enabled, radius=radius)
                refresh_average_curve(self, curve)

    def patched_plotitem_set_downsampling(
        self,
        ds=None,
        auto=None,
        mode=None,
        offset=None,
    ):
        original_plotitem_set_downsampling(self, ds=ds, auto=auto, mode=mode)
        if offset is not None and hasattr(self.ctrl, "downsampleOffsetSpin"):
            self.ctrl.downsampleOffsetSpin.setValue(max(0, int(offset)))

    @QtCore.Slot()
    def patched_update_downsampling(self):
        ds, auto, method, offset = self.downsampleMode()
        clip = self.ctrl.clipToViewCheck.isChecked()
        for curve in self.curves:
            if is_average_overlay(curve):
                continue
            if hasattr(curve, "setDownsampling"):
                curve.setDownsampling(ds, auto, method, offset=offset)
            if hasattr(curve, "setClipToView"):
                curve.setClipToView(clip)
        self.updateMovingAverage()

    def patched_downsample_mode(self):
        if self.ctrl.downsampleCheck.isChecked():
            ds = self.ctrl.downsampleSpin.value()
        else:
            ds = 1

        auto = (
            self.ctrl.downsampleCheck.isChecked()
            and self.ctrl.autoDownsampleCheck.isChecked()
        )

        if self.ctrl.subsampleRadio.isChecked():
            method = "subsample"
        elif self.ctrl.meanRadio.isChecked():
            method = "mean"
        elif self.ctrl.peakRadio.isChecked():
            method = "peak"
        else:
            raise ValueError(
                "One of the method radios must be selected for: 'subsample', "
                "'mean', 'peak'."
            )

        offset = 0
        if hasattr(self.ctrl, "downsampleOffsetSpin"):
            offset = self.ctrl.downsampleOffsetSpin.value()
        return ds, auto, method, offset

    def patched_plotdata_set_downsampling(
        self,
        ds=None,
        auto=None,
        method="peak",
        offset=None,
    ):
        if offset is not None:
            offset = max(0, int(offset))
            if self.opts.get("downsampleOffset", 0) != offset:
                self.opts["downsampleOffset"] = offset
                invalidate_display_dataset(self)

        original_plotdata_set_downsampling(self, ds=ds, auto=auto, method=method)

    def patched_plotdata_set_moving_average(
        self,
        enabled=None,
        radius=None,
    ):
        changed = False
        if enabled is not None:
            enabled = bool(enabled)
            if self.opts.get("movingAverageEnabled", False) != enabled:
                self.opts["movingAverageEnabled"] = enabled
                changed = True
        if radius is not None:
            radius = max(1, int(radius))
            if self.opts.get("movingAverageRadius", 5) != radius:
                self.opts["movingAverageRadius"] = radius
                changed = True
        if changed:
            invalidate_display_dataset(self)

    def patched_plotdata_update_items(self, styleUpdate=True):
        original_plotdata_update_items(self, styleUpdate=styleUpdate)
        if is_average_overlay(self):
            return
        if self.opts.get("movingAverageEnabled", False):
            plot_item = self.opts.get("movingAveragePlotItem")
            if plot_item is not None:
                refresh_average_curve(plot_item, self)

    def moving_average(values, radius: int):
        if radius <= 0 or len(values) == 0:
            return values

        dtype = np.result_type(values.dtype, np.float64)
        finite = np.isfinite(values)
        clean_values = np.where(finite, values, 0).astype(dtype, copy=False)
        counts = finite.astype(np.int64)
        sums = np.concatenate(([0], np.cumsum(clean_values, dtype=dtype)))
        count_sums = np.concatenate(([0], np.cumsum(counts)))

        indexes = np.arange(len(values))
        left = np.maximum(indexes - radius, 0)
        right = np.minimum(indexes + radius + 1, len(values))
        window_counts = count_sums[right] - count_sums[left]
        window_sums = sums[right] - sums[left]

        result = np.empty(len(values), dtype=dtype)
        result.fill(np.nan)
        np.divide(
            window_sums,
            window_counts,
            out=result,
            where=window_counts > 0,
        )
        return result

    def patched_get_display_dataset(self):
        offset_enabled = (
            self.opts.get("downsampleMethod") == "subsample"
            and self.opts.get("downsampleOffset", 0) > 0
        )
        if not offset_enabled:
            return original_get_display_dataset(self)

        if self._dataset is None:
            return None
        if (
            self._datasetDisplay is not None
            and not (
                self.property("xViewRangeWasChanged")
                and self.opts["clipToView"]
            )
            and not (
                self.property("xViewRangeWasChanged")
                and self.opts["autoDownsample"]
            )
            and not (
                self.property("yViewRangeWasChanged")
                and self.opts["dynamicRangeLimit"] is not None
            )
        ):
            return self._datasetDisplay

        if self._datasetMapped is None:
            x = self._dataset.x
            y = self._dataset.y
            if y.dtype == bool:
                y = y.astype(np.uint8)
            if x.dtype == bool:
                x = x.astype(np.uint8)
            if self.opts["subtractMeanMode"]:
                y = y - np.mean(y)
            if self.opts["fftMode"]:
                x, y = self._fourierTransform(x, y)
                if self.opts["logMode"][0]:
                    x = x[1:]
                    y = y[1:]
            if self.opts["derivativeMode"]:
                y = np.diff(self._dataset.y) / np.diff(self._dataset.x)
                x = x[:-1]
            if self.opts["phasemapMode"]:
                x = self._dataset.y[:-1]
                y = np.diff(self._dataset.y) / np.diff(self._dataset.x)

            dataset = PlotDataset(
                x,
                y,
                self._dataset.xAllFinite,
                self._dataset.yAllFinite,
            )
            if True in self.opts["logMode"]:
                dataset.applyLogMapping(self.opts["logMode"])
            self._datasetMapped = dataset

        x = self._datasetMapped.x
        y = self._datasetMapped.y
        x_all_finite = self._datasetMapped.xAllFinite
        y_all_finite = self._datasetMapped.yAllFinite

        view = self.getViewBox()
        if view is None:
            view_range = None
        else:
            view_range = view.viewRect()
        if view_range is None:
            view_range = self.viewRect()

        ds = self.opts["downsample"]
        if not isinstance(ds, int):
            ds = 1

        if self.opts["autoDownsample"]:
            if x_all_finite:
                finite_x = x
            else:
                finite_x = x[np.isfinite(x)]
            if view_range is not None and len(finite_x) > 1:
                dx = float(finite_x[-1] - finite_x[0]) / (len(finite_x) - 1)
                if dx != 0.0:
                    width = self.getViewBox().width()
                    if width != 0.0:
                        ds_float = max(
                            1.0,
                            abs(
                                view_range.width()
                                / dx
                                / (width * self.opts["autoDownsampleFactor"])
                            ),
                        )
                        if math.isfinite(ds_float):
                            ds = int(ds_float)
            if math.isclose(ds, self._adsLastValue, rel_tol=0.01):
                ds = self._adsLastValue
            self._adsLastValue = ds

        global_start = 0
        connect = self.opts["connect"] if isinstance(self.opts["connect"], np.ndarray) else None
        if self.opts["clipToView"]:
            if view is None or view.autoRangeEnabled()[0]:
                pass
            elif view_range is not None and len(x) > 1:
                x0 = bisect.bisect_left(x, view_range.left()) - ds
                x0 = fn.clip_scalar(x0, 0, len(x))
                x1 = bisect.bisect_left(x, view_range.right()) + ds
                x1 = fn.clip_scalar(x1, x0, len(x))
                global_start = x0
                x = x[x0:x1]
                y = y[x0:x1]
                if connect is not None:
                    connect = connect[x0:x1]

        if ds > 1:
            method = self.opts["downsampleMethod"]
            if method == "subsample":
                offset = self.opts.get("downsampleOffset", 0)
                start = (offset - global_start) % ds
                x = x[start::ds]
                y = y[start::ds]
                if connect is not None:
                    connect = connect[start::ds]
            elif method == "mean":
                n = len(x) // ds
                stx = ds // 2
                x = x[stx:stx + n * ds:ds]
                y = y[:n * ds].reshape(n, ds).mean(axis=1)
                if connect is not None:
                    connect = connect[:n * ds].reshape(n, ds).all(axis=1)
            elif method == "peak":
                n = len(x) // ds
                x1 = np.empty((n, 2))
                stx = ds // 2
                x1[:] = x[stx:stx + n * ds:ds, np.newaxis]
                x = x1.reshape(n * 2)
                y1 = np.empty((n, 2))
                y2 = y[:n * ds].reshape((n, ds))
                y1[:, 0] = y2.max(axis=1)
                y1[:, 1] = y2.min(axis=1)
                y = y1.reshape(n * 2)
                if connect is not None:
                    c = np.ones((n * 2), dtype=bool)
                    c[1::2] = connect[:n * ds].reshape(n, ds).all(axis=1)
                    connect = c

        if self.opts["dynamicRangeLimit"] is not None and view_range is not None:
            data_range = self._datasetMapped.dataRect()
            if data_range is not None:
                view_height = view_range.height()
                limit = self.opts["dynamicRangeLimit"]
                hyst = self.opts["dynamicRangeHyst"]
                if (
                    view_height > 0
                    and not data_range.bottom() < view_range.top()
                    and not data_range.top() > view_range.bottom()
                    and data_range.height() > 2 * hyst * limit * view_height
                ):
                    cache_is_good = False
                    if self._datasetDisplay is not None:
                        top_exc = -(self._drlLastClip[0] - view_range.bottom()) / view_height
                        bot_exc = (self._drlLastClip[1] - view_range.top()) / view_height
                        if (
                            limit / hyst <= top_exc <= limit * hyst
                            and limit / hyst <= bot_exc <= limit * hyst
                        ):
                            x = self._datasetDisplay.x
                            y = self._datasetDisplay.y
                            cache_is_good = True
                    if not cache_is_good:
                        min_val = view_range.bottom() - limit * view_height
                        max_val = view_range.top() + limit * view_height
                        y = fn.clip_array(y, min_val, max_val)
                        self._drlLastClip = (min_val, max_val)

        dataset = PlotDataset(x, y, x_all_finite, y_all_finite, connect)
        self._datasetDisplay = dataset
        self.setProperty("xViewRangeWasChanged", False)
        self.setProperty("yViewRangeWasChanged", False)
        return dataset

    PlotItem.__init__ = patched_plotitem_init
    PlotItem.avgToggled = patched_avg_toggled
    PlotItem.addAvgCurve = patched_add_avg_curve
    PlotItem.setDownsampling = patched_plotitem_set_downsampling
    PlotItem.clear = patched_clear
    PlotItem.clearPlots = patched_clear_plots
    PlotItem.updateDownsampling = patched_update_downsampling
    PlotItem.updateMovingAverage = patched_update_moving_average
    PlotItem.downsampleMode = patched_downsample_mode
    PlotDataItem.setDownsampling = patched_plotdata_set_downsampling
    PlotDataItem.setMovingAverage = patched_plotdata_set_moving_average
    PlotDataItem.updateItems = patched_plotdata_update_items
    PlotDataItem._getDisplayDataset = patched_get_display_dataset

    _INSTALLED = True
