# bench_test/ui/tabs/viewer_tab.py
# -*- coding: utf-8 -*-
"""
ViewerTab
=========
Ölçüm verilerini (xlsx/CSV) grafik olarak gösterir.

Özellikler:
- xlsx veya CSV'den gelen Current (uA) verisini A'ya çevirerek Time grafiği (pyqtgraph)
- Step geçişleri → turuncu dikey çizgi + etiket
- Sistem olayları (started/stopped/paused/resumed) → mavi/kırmızı çizgi
- Manuel notlar → sol annotation panelinde liste
- Zoom/Pan: fare tekerleği ve sürükleme (pyqtgraph built-in)
- Aktif session: PackagePanel.get_session() ile otomatik yüklenir
- Geçmiş session: Browse butonu ile session dizini seçilir
- Canlı mod: session IN_PROGRESS iken 30sn'de bir yeniler
"""

import os
import shutil
import math
from bisect import bisect_left
from datetime import datetime, timedelta
from typing import Optional, List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QGroupBox, QLabel, QPushButton, QFileDialog,
    QTextEdit, QScrollArea, QFrame, QSizePolicy,
    QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF
from PyQt6.QtGui import QFont, QColor

try:
    import pyqtgraph as pg
    from pyqtgraph import DateAxisItem
    from bench_test.pyqtgraph_downsample_patch import install_offset_downsample
    from bench_test.ui.visible_xlsx_exporter import register_visible_xlsx_exporter

    install_offset_downsample()
    register_visible_xlsx_exporter()
    _PG_OK = True
except ImportError:
    _PG_OK = False

if _PG_OK:
    class VerticalDateAxisItem(DateAxisItem):
        """Date axis with compact two-line timestamp labels rotated 90 degrees."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            tick_font = QFont()
            tick_font.setPointSize(9)
            self.setHeight(70)
            self.setStyle(
                tickFont=tick_font,
                tickTextOffset=8,
                textFillLimits=[(0, 10)],
            )

        def tickStrings(self, values, scale, spacing):
            labels = []
            for value in values:
                try:
                    labels.append(
                        datetime.fromtimestamp(value).strftime("%y-%m-%d\n%H:%M:%S")
                    )
                except (OverflowError, ValueError, OSError):
                    labels.append("")
            return labels

        def drawPicture(self, painter, axisSpec, tickSpecs, textSpecs):
            painter.setRenderHint(painter.RenderHint.Antialiasing, False)
            painter.setRenderHint(painter.RenderHint.TextAntialiasing, True)

            pen, p1, p2 = axisSpec
            painter.setPen(pen)
            painter.drawLine(p1, p2)

            for tick_pen, tick_p1, tick_p2 in tickSpecs:
                painter.setPen(tick_pen)
                painter.drawLine(tick_p1, tick_p2)

            if self.style["tickFont"] is not None:
                painter.setFont(self.style["tickFont"])
            painter.setPen(self.textPen())
            painter.setClipRect(self.boundingRect().toAlignedRect())

            for rect, _flags, text in textSpecs:
                if self.orientation != "bottom":
                    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
                    continue

                painter.save()
                painter.translate(rect.center().x(), rect.top() + rect.width() / 2.0)
                painter.rotate(-90)
                rotated_rect = QRectF(
                    -rect.width() / 2.0,
                    -rect.height() / 2.0,
                    rect.width(),
                    rect.height(),
                )
                painter.drawText(
                    rotated_rect,
                    int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip),
                    text,
                )
                painter.restore()

from bench_test.measurement.session import (
    MEASUREMENT_MODE_CONTINUOUS_PAD,
    MEASUREMENT_MODE_CV,
    MeasurementSession,
    SessionStatus,
)
from bench_test.measurement.legacy_session import LegacySession
from bench_test.measurement.log_parser import LogParser, ParseResult, StepEvent, SystemEvent
from bench_test.measurement.data_io import (
    MeasurementDataError,
    find_session_xlsx_path,
    read_csv_measurement_series,
    read_session_measurement_series,
    read_session_pad_segment_series,
    read_xlsx_measurement_series,
)
from bench_test.measurement.dropsens_io import read_cv_measurement, read_pad_measurement
from bench_test.ui.widgets import _btn
from bench_test.utils.paths import open_dir, get_value

# ── Renkler ───────────────────────────────────────────────────────
_C_STEP_LINE    = (255,   0,   0)   # Kırmızı  — STEP (Sx Px) marker
_C_RESET_LINE   = (237, 125, 49)    # Turuncu  — RESET marker
_C_STARTED      = (76,  175,  80)   # Yeşil    — recipe started
_C_STOPPED      = (244,  67,  54)   # Kırmızı  — recipe stopped
_C_PAUSED       = (255, 193,   7)   # Sarı     — paused/resumed
_C_DATA_LINE    = (0,    0, 128)    # Lacivert — Current serisi
_C_BG           = (255, 255, 255)   # Beyaz arka plan
_C_GRID         = (210, 210, 210)   # Açık gri ızgara

# Sistem event renklerine göre renk eşleme
_SYSTEM_COLORS = {
    "started" : _C_STARTED,
    "completed": _C_STARTED,
    "stopped" : _C_STOPPED,
    "error"   : _C_STOPPED,
    "paused"  : _C_PAUSED,
    "resumed" : _C_PAUSED,
}

_PORT_COLORS = {
    1: (255, 255, 255, 100),   # sabit
    2: (180, 220, 230, 100),   # açık cyan
    3: (180, 200, 215, 100),   # orta cyan
    4: (155, 135, 200, 100),   # yumuşak mor (cyan → mor geçiş)
    5: (210, 155, 135, 100),   # pastel kırmızımsı turuncu (mor → turuncu geçiş)
    6: (225, 175, 145, 100),   # açık turuncu
    7: (235, 200, 170, 100),   # çok açık turuncu
    8: (255, 255, 255, 100),   # sabit
}

#Açık turuncu:	RGB: (240, 180, 120)
#Orta turuncu:	RGB: (225, 150, 90)
#Koyu turuncu:	RGB: (200, 120, 60)
#Orta mor:	RGB: (150, 110, 190)
#Orta cyan:	RGB: (120, 190, 210)
#Açık cyan:	RGB: (170, 220, 235)



_PORT_COLOR_DEFAULT = (160, 160, 160, 0)

_C_MEASURE_LINE = (33, 150, 243)
_HOVER_DISTANCE_PX = 5
_CURRENT_UA_TO_A = 1e-6

POLL_INTERVAL_MS = 10_000   # 10 saniye


class ViewerTab(QWidget):
    log_signal     = pyqtSignal(str)
    session_loaded = pyqtSignal(str)
    delay_changed  = pyqtSignal(int, int)   # (minutes, seconds)

    def __init__(self, package_tab=None):
        super().__init__()
        self.package_tab   = package_tab
        self._session      : Optional[MeasurementSession] = None
        self._history_sessions: List[MeasurementSession] = []
        self._dropsens_pad = None
        self._parse_result : Optional[ParseResult]        = None
        self._marker_items : list = []   # grafikteki marker öğeleri

        self._measure_dots : list = []
        self._measure_range_hooks: dict = {}
        self._saved_top_view_range: Optional[dict] = None
        self._manual_top_view_active = False
        self._applying_top_view_state = False
        self._rebuilding_top_view = False
        self._top_view_tracking_connected = False
        self._hover_hooks: dict = {}
        self._hover_points: dict = {}
        self._hover_labels: dict = {}
        self._hover_state: dict = {}
        self._event_marker_points: list = []
        self._vline_hover_points: list = []
        self._restoring_response_delay = False
        self._plot_axis_mode = "time"
        self._polling_live = False
        self._viewer_mode = "pad"
        self._viewer_mode_user_selected = False
        self.view_mode_widget = None
        self.view_pad_btn = None
        self.view_cv_btn = None
        self.top_group = None
        self.bottom_group = None
        self.plot_widget_top = None
        self.plot_widget_bottom = None
        self.top_curve = None
        self.bottom_curve = None

        self._build_ui()
        self._setup_poll_timer()

    # ── UI inşası ─────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(4)

        # ── Araç çubuğu ───────────────────────────────────────────
        toolbar = QHBoxLayout()

        self.session_lbl = QLabel("Oturum yüklenmedi")
        self.session_lbl.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self.session_lbl, stretch=1)

        toolbar.addWidget(_btn("Load Active Session", self._load_active_session, "#FF9800"))
        toolbar.addWidget(_btn("Refresh",             self._refresh_or_load, "#4CAF50"))
        toolbar.addWidget(_btn("Load Session",     self._browse_session,       "#2196F3"))
        toolbar.addWidget(_btn("Load DropSens PAD", self._browse_dropsens_pad, "#607D8B"))

        self.delete_btn = _btn("Delete Session", self._delete_session,  "#F44336")
        self.delete_btn.setEnabled(False)
        self.delete_btn.setToolTip("Seçili session dizinini kalıcı olarak siler.")
        toolbar.addWidget(self.delete_btn)

        self.live_lbl = QLabel("")
        self.live_lbl.setStyleSheet("color: #4CAF50; font-size: 10px;")
        toolbar.addWidget(self.live_lbl)

        # ── Tepki Gecikmesi ───────────────────────────────────────
        from PyQt6.QtWidgets import QSpinBox as _QSpinBox
        from PyQt6.QtWidgets import QFrame
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        toolbar.addWidget(sep)
        toolbar.addWidget(QLabel("Response Delay:"))

        self._delay_min_spin = _QSpinBox()
        self._delay_min_spin.setRange(0, 59)
        self._delay_min_spin.setSuffix(" min")
        self._delay_min_spin.setFixedWidth(72)
        self._delay_min_spin.setValue(get_value("response_delay_min", 0))

        self._delay_sec_spin = _QSpinBox()
        self._delay_sec_spin.setRange(0, 59)
        self._delay_sec_spin.setSuffix(" sec")
        self._delay_sec_spin.setFixedWidth(72)
        self._delay_sec_spin.setValue(get_value("response_delay_sec", 0))

        self._delay_min_spin.valueChanged.connect(self._on_delay_changed)
        self._delay_sec_spin.valueChanged.connect(self._on_delay_changed)

        toolbar.addWidget(self._delay_min_spin)
        toolbar.addWidget(self._delay_sec_spin)

        root.addLayout(toolbar)

        # ── Ana splitter: grafik + annotation ─────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Sol: annotation paneli
        ann_widget = self._build_annotation_panel()
        ann_widget.setMinimumWidth(220)
        ann_widget.setMaximumWidth(320)
        splitter.addWidget(ann_widget)

        # Sağ: grafik
        chart_widget = self._build_chart_area()
        splitter.addWidget(chart_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

    def _build_annotation_panel(self) -> QWidget:
        """Sol panel: session meta + manuel notlar."""
        panel = QGroupBox("Annotation")
        layout = QVBoxLayout(panel)
        layout.setSpacing(4)

        # Session meta
        meta_grp = QGroupBox("Session Info")
        meta_lay = QVBoxLayout(meta_grp)
        self.meta_lbl = QLabel("—")
        self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setStyleSheet("font-size: 11px; color: #ccc;")
        meta_lay.addWidget(self.meta_lbl)
        layout.addWidget(meta_grp)

        # Manuel notlar
        notes_grp = QGroupBox("User Notes")
        notes_lay = QVBoxLayout(notes_grp)
        self.notes_edit = QTextEdit()
        self.notes_edit.setReadOnly(True)
        self.notes_edit.setFont(QFont("Consolas", 9))
        self.notes_edit.setMinimumHeight(200)
        notes_lay.addWidget(self.notes_edit)
        layout.addWidget(notes_grp, stretch=1)

        # Sistem event özeti
        sys_grp = QGroupBox("System Events")
        sys_lay = QVBoxLayout(sys_grp)
        self.sys_edit = QTextEdit()
        self.sys_edit.setReadOnly(True)
        self.sys_edit.setFont(QFont("Consolas", 9))
        self.sys_edit.setMaximumHeight(120)
        sys_lay.addWidget(self.sys_edit)
        layout.addWidget(sys_grp)

        return panel

    def _build_chart_area(self) -> QWidget:
        """Sağ panel: pyqtgraph veya fallback mesaj."""
        container = QWidget()
        layout    = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Top GroupBox (panel) ─────────────────────────
        top_group = QGroupBox("Measurement Timeline")
        top_group_layout = QVBoxLayout(top_group)
        top_group_layout.setContentsMargins(10, 10, 10, 10)

        if not _PG_OK:
            lbl = QLabel(
                "pyqtgraph bulunamadı.\n"
                "Kurmak için: pip install pyqtgraph")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #F44336; font-size: 13px;")
            layout.addWidget(lbl)
            self.plot_widget_top = None
            self.plot_widget_bottom = None
            return container

        pg.setConfigOption("background", pg.mkColor(*_C_BG))
        pg.setConfigOption("foreground", "k")   # siyah yazı

        # Zaman ekseni için dikey etiketli tarih ekseni
        self.view_mode_widget = QWidget()
        view_mode_layout = QHBoxLayout(self.view_mode_widget)
        view_mode_layout.setContentsMargins(0, 0, 0, 4)
        view_mode_layout.setSpacing(6)
        view_mode_layout.addWidget(QLabel("View:"))

        self.view_pad_btn = QPushButton("PAD")
        self.view_pad_btn.setCheckable(True)
        self.view_pad_btn.clicked.connect(lambda: self._on_view_mode_selected("pad"))
        view_mode_layout.addWidget(self.view_pad_btn)

        self.view_cv_btn = QPushButton("CV")
        self.view_cv_btn.setCheckable(True)
        self.view_cv_btn.clicked.connect(lambda: self._on_view_mode_selected("cv"))
        view_mode_layout.addWidget(self.view_cv_btn)
        view_mode_layout.addStretch(1)
        self.view_mode_widget.setVisible(False)
        layout.addWidget(self.view_mode_widget)

        date_axis = VerticalDateAxisItem(orientation="bottom")
        self.plot_widget_top = pg.PlotWidget(axisItems={"bottom": date_axis})
        self.plot_widget_top.setLabel("left",   "Current", units="A")
        self.plot_widget_top.setLabel("bottom", "Time")
        self.plot_widget_top.showGrid(x=True, y=True, alpha=0.8)
        self.plot_widget_top.getViewBox().setBorder(pg.mkPen((0, 0, 0), width=1))

        # Grafik yüksekliğini daha kısa tut
        self.plot_widget_top.setSizePolicy(QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.plot_widget_top.setMinimumHeight(280)
        self.plot_widget_top.setMaximumHeight(520)

        self.plot_widget_top.getViewBox().setMouseMode(
            pg.ViewBox.RectMode)   # sürükle = zoom rect; sağ tık = pan
        self._ensure_top_view_tracking()
        self._ensure_hover_tracking(self.plot_widget_top)

        # Legend
        self.legend = self.plot_widget_top.addLegend(offset=(10, 10))

        top_group_layout.addWidget(self.plot_widget_top)
        self.top_group = top_group
        layout.addWidget(top_group)

        # ── Bottom GroupBox (panel) ──────────────────────
        bottom_group = QGroupBox("Mean Measurement Timeline")
        bottom_group_layout = QVBoxLayout(bottom_group)
        bottom_group_layout.setContentsMargins(10, 10, 10, 10)

        bottom_date_axis = VerticalDateAxisItem(orientation="bottom")
        self.plot_widget_bottom = pg.PlotWidget(
            axisItems={"bottom": bottom_date_axis}
        )
        self.plot_widget_bottom.setLabel("left", "Current", units="A")
        self.plot_widget_bottom.setLabel("bottom", "Time")
        self.plot_widget_bottom.showGrid(x=True, y=True, alpha=0.8)
        self.plot_widget_bottom.getViewBox().setBorder(pg.mkPen((0, 0, 0), width=1))

        self.plot_widget_top.setSizePolicy(QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.plot_widget_top.setMinimumHeight(280)
        self.plot_widget_top.setMaximumHeight(520)

        self.plot_widget_bottom.getViewBox().setMouseMode(
            pg.ViewBox.RectMode
        )
        self._ensure_hover_tracking(self.plot_widget_bottom)

        # Üst grafik ile aynı eksen davranışı
        self.plot_widget_bottom.setXLink(self.plot_widget_top)
        self.plot_widget_bottom.setYLink(self.plot_widget_top)
        self._apply_empty_time_axis()
        self._install_visible_xlsx_export_provider()

        bottom_group_layout.addWidget(self.plot_widget_bottom)
        self.bottom_group = bottom_group
        layout.addWidget(bottom_group)

        layout.addStretch(1)
        return container

    # ── Tepki gecikmesi ───────────────────────────────────────────

    def _get_offset_sec(self) -> float:
        """Kullanıcının girdiği toplam gecikmeyi saniye cinsinden döner."""
        return self._delay_min_spin.value() * 60.0 + self._delay_sec_spin.value()

    def _on_delay_changed(self):
        """Spinbox değişince sinyal yay, grafiği yenile."""
        if self._restoring_response_delay:
            return
        self.delay_changed.emit(self._delay_min_spin.value(), self._delay_sec_spin.value())
        if self._session or self._history_sessions or self._dropsens_pad:
            self._refresh()

    def get_response_delay_params(self) -> dict:
        """Session experiment_params icin response delay alanlarini dondurur."""
        return {
            "response_delay_min": self._delay_min_spin.value(),
            "response_delay_sec": self._delay_sec_spin.value(),
        }

    def restore_response_delay(self, params: dict):
        """Session experiment_params alanindan response delay degerlerini yukler."""
        if not params:
            return
        self._restoring_response_delay = True
        try:
            if "response_delay_min" in params:
                self._delay_min_spin.setValue(int(params["response_delay_min"]))
            if "response_delay_sec" in params:
                self._delay_sec_spin.setValue(int(params["response_delay_sec"]))
        finally:
            self._restoring_response_delay = False

    def restore_default_response_delay(self):
        """experiment_params olmayan eski session icin son delay tercihini yukler."""
        self.restore_response_delay({
            "response_delay_min": get_value("response_delay_min", 0),
            "response_delay_sec": get_value("response_delay_sec", 0),
        })

    # ── Zamanlayıcı ───────────────────────────────────────────────
    def _setup_poll_timer(self):
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll)

    def _is_pollable_session(self, session: Optional[MeasurementSession] = None) -> bool:
        session = session or self._session
        if session is None:
            return False
        if session.status != SessionStatus.IN_PROGRESS:
            return False
        return getattr(session, "measurement_mode", "") != MEASUREMENT_MODE_CONTINUOUS_PAD

    def _sync_polling_for_session(self, live: bool = False):
        continuous_pad = (
            self._session is not None
            and getattr(self._session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD
        )
        if self._is_pollable_session():
            self._polling_live = live
            label = "Live mode" if live else "Archive live monitor"
            self.live_lbl.setText(f"{label} ({POLL_INTERVAL_MS // 1000}sn)")
            self.delete_btn.setEnabled(False)
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            return

        self._poll_timer.stop()
        self._polling_live = False
        self.live_lbl.setText("Continuous PAD (.mtp)" if continuous_pad else "")
        if self._session is not None:
            self.delete_btn.setEnabled(self._session.status != SessionStatus.IN_PROGRESS)

    def _on_poll(self):
        """10sn'de bir: canlı session gösteriliyorsa mevcut veriyi yeniden yükle."""
        if not self._session:
            self._poll_timer.stop()
            return
        if not self._is_pollable_session():
            self._sync_polling_for_session(live=self._polling_live)
            return
        self._refresh(reset_view=False)
        self._sync_polling_for_session(live=self._polling_live)

    # ── Session yükleme ───────────────────────────────────────────

    def _load_active_session(self):
        """PackagePanel'den aktif session'ı al."""
        session = self._get_active_session()
        if not session:
            QMessageBox.information(self, "Viewer",
                "Aktif oturum yok.\nÖnce bir recipe başlatın.")
            return

        self._reset_auto_follow()

        mw = self._get_main_window()
        if mw:
            mw.switch_display(session, "active")
            return
        self._load_session(session, live=True)

    def _get_main_window(self):
        """MainWindow referansini parent zincirinden bul."""
        w = self.parent()
        while w:
            if hasattr(w, "switch_display"):
                return w
            w = w.parent() if hasattr(w, "parent") else None
        return None

    def _get_active_session(self):
        """MainWindow context veya package panel uzerinden aktif session'i bul."""
        mw = self._get_main_window()
        if mw and hasattr(mw, "_ctx") and mw._ctx.active_session is not None:
            return mw._ctx.active_session
        if self.package_tab:
            return self.package_tab.get_session()
        return None

    def _reset_auto_follow(self):
        """Manual zoom lock'i kapat, tekrar auto-follow moduna don."""
        self._manual_top_view_active = False
        self._saved_top_view_range = None

    def _refresh_or_load(self):
        """Refresh: yüklü oturumu yeniler; yoksa aktif oturumu yükler."""
        self._reset_auto_follow()
        if self._session or self._history_sessions or self._dropsens_pad:
            self._refresh(reset_view=True)
        else:
            self._load_active_session()

    def _find_history_sessions(self, part_dir: str) -> List[MeasurementSession]:
        """Part klasoru altindaki session klasorlerini yukler."""
        sessions: List[MeasurementSession] = []
        try:
            entries = sorted(os.scandir(part_dir), key=lambda e: e.name)
        except OSError:
            return sessions

        for entry in entries:
            if not entry.is_dir():
                continue
            json_path = os.path.join(entry.path, "session.json")
            if not os.path.isfile(json_path):
                continue
            try:
                sessions.append(MeasurementSession.load(entry.path))
            except Exception as exc:
                self.log_signal.emit(
                    f"Viewer history session atlandi: {entry.path} | {exc}"
                )

        sessions.sort(key=lambda item: item.created_at)
        return sessions


    def _browse_session(self):
        """Geçmiş session dizinini kullanıcı seçer."""
        path = open_dir(self, "Session Dizini Seç", "browse_session")
        if not path:
            return

        # ── 1. Yol: session.json var mı? ─────────────────────────────
        json_path = os.path.join(path, "session.json")
        if os.path.isfile(json_path):
            try:
                session = MeasurementSession.load(path)
            except Exception as e:
                QMessageBox.critical(self, "Viewer",
                                     f"Session yüklenemedi:\n{e}")
                return
            mw = self._get_main_window()
            if mw:
                mw.switch_display(session, "archived")
            else:
                self._load_session(session)
            self.session_loaded.emit(str(session.session_dir))
            return

        # ── 2. Yol: Alt klasörlerde session.json var mı? (history) ────
        history_sessions = self._find_history_sessions(path)
        if history_sessions:
            mw = self._get_main_window()
            if mw:
                mw.switch_display(None, "history", history_sessions=history_sessions)
                return
            self.render_history(history_sessions)
            return

        # ── 3. Yol: Legacy mod — xlsx veya csv var mı? ────────────────
        legacy = LegacySession.discover(path)
        if legacy:
            mw = self._get_main_window()
            if mw:
                mw.switch_display(legacy, "legacy")
            else:
                self._load_session(legacy)
            return

        QMessageBox.warning(self, "Viewer",
                            "Seçilen dizinde tanınan veri bulunamadı.\n"
                            "session.json, standart xlsx veya CSV dosyası aranır.")

    def _browse_dropsens_pad(self):
        """Standalone DropSens PAD dosyası seçer ve grafikte gösterir."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "DropSens PAD Dosyası Seç",
            "",
            "DropSens PAD (*.mtp);;All Files (*)",
        )
        if not path:
            return

        try:
            measurement = read_pad_measurement(path)
        except MeasurementDataError as e:
            QMessageBox.warning(self, "DropSens PAD", str(e))
            return

        self._reset_auto_follow()
        self._session = None
        self._history_sessions = []
        self._dropsens_pad = measurement
        self._viewer_mode_user_selected = False
        self._parse_result = None
        self._poll_timer.stop()
        self.live_lbl.setText("")
        self.delete_btn.setEnabled(False)
        self._refresh(reset_view=True)
        self._set_plot_titles(measurement.part_number)
        mw = self._get_main_window()
        if mw and hasattr(mw, "set_part_banner"):
            mw.set_part_banner(measurement.part_number)


    def _load_session(self, session: MeasurementSession, live: bool = False):
        """Session nesnesini set eder ve grafiği yeniler."""
        self._session = session
        self._history_sessions = []
        self._dropsens_pad = None
        self._viewer_mode_user_selected = False
        self.restore_response_delay(getattr(session, "experiment_params", {}))

        # Canlı mod: sadece IN_PROGRESS iken
        continuous_pad = (
            getattr(session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD
        )

        if session.status == SessionStatus.IN_PROGRESS and not continuous_pad:
            self.live_lbl.setText("⟳ Canlı mod (10sn)")
            self.delete_btn.setEnabled(False)   # Çalışan session silinemez
        else:
            self._poll_timer.stop()
            self.live_lbl.setText("Continuous PAD (.mtp)" if continuous_pad else "")
            self.delete_btn.setEnabled(session.status != SessionStatus.IN_PROGRESS)

        self._refresh(reset_view=True)
        self._sync_polling_for_session(live=live)

    def render_history(self, sessions: List[MeasurementSession]) -> None:
        """Bir part altindaki tum session verilerini yukler."""
        self._session = None
        self._history_sessions = list(sessions)
        self._dropsens_pad = None
        self._viewer_mode_user_selected = False
        self._poll_timer.stop()
        self.live_lbl.setText("")
        self.delete_btn.setEnabled(False)
        if sessions:
            part_text = f"{sessions[0].part_number} [session: {len(sessions)}]"
        else:
            part_text = ""
        self._refresh(reset_view=True)
        self._set_plot_titles(part_text)

    # ── Yenileme ──────────────────────────────────────────────────

    def render_session(self, session: MeasurementSession, live: bool = False) -> None:
        """
        MainWindow.switch_display() tarafindan cagrilir.
        live=True ise poll_timer baslatilir.
        """
        self._load_session(session, live=live)
        self._set_plot_titles(session.part_number)
        if live and self._is_pollable_session() and not self._poll_timer.isActive():
            self.live_lbl.setText(f"⟳ Canlı mod ({POLL_INTERVAL_MS // 1000}sn)")

    def _set_plot_titles(self, part_text: str = ""):
        """Part bilgisini grafik başlıklarına yazar."""
        title_opts = {"color": "k", "size": "10pt", "bold": True}
        cv_view = self._viewer_mode == "cv"
        if self.plot_widget_top:
            suffix = "CV Potential-Current" if cv_view else "Raw"
            title = f"{part_text} - {suffix}" if part_text else ""
            self.plot_widget_top.setTitle(title, **title_opts)
        if self.plot_widget_bottom:
            suffix = "" if cv_view else "Mean"
            title = f"{part_text} - {suffix}" if part_text and suffix else ""
            self.plot_widget_bottom.setTitle(title, **title_opts)

    def _on_view_mode_selected(self, mode: str):
        if mode not in ("pad", "cv"):
            return
        if mode == "cv" and not self._has_cv_measurements():
            self._sync_view_mode_controls()
            return
        if mode == "pad" and not self._has_pad_measurements():
            self._sync_view_mode_controls()
            return
        self._viewer_mode = mode
        self._viewer_mode_user_selected = True
        self._sync_view_mode_controls()
        self._reset_auto_follow()
        self._refresh(reset_view=True)
        self._set_plot_titles(self._current_part_title())

    def _current_part_title(self) -> str:
        if self._session is not None:
            return self._session.part_number
        if self._history_sessions:
            return f"{self._history_sessions[0].part_number} [session: {len(self._history_sessions)}]"
        if self._dropsens_pad:
            return self._dropsens_pad.part_number
        return ""

    def _sync_view_mode_options(self):
        has_pad = self._has_pad_measurements()
        has_cv = self._has_cv_measurements()

        if has_pad and has_cv:
            if not self._viewer_mode_user_selected or self._viewer_mode not in ("pad", "cv"):
                self._viewer_mode = "pad"
        elif has_cv:
            self._viewer_mode = "cv"
            self._viewer_mode_user_selected = False
        else:
            self._viewer_mode = "pad"
            if not has_pad:
                self._viewer_mode_user_selected = False

        self._sync_view_mode_controls(has_pad=has_pad, has_cv=has_cv)

    def _sync_view_mode_controls(self, has_pad: bool | None = None, has_cv: bool | None = None):
        if not self.view_mode_widget or not self.view_pad_btn or not self.view_cv_btn:
            return
        if has_pad is None:
            has_pad = self._has_pad_measurements()
        if has_cv is None:
            has_cv = self._has_cv_measurements()
        mixed = has_pad and has_cv
        self.view_mode_widget.setVisible(mixed)
        self.view_pad_btn.setEnabled(has_pad)
        self.view_cv_btn.setEnabled(has_cv)
        self.view_pad_btn.setChecked(self._viewer_mode == "pad")
        self.view_cv_btn.setChecked(self._viewer_mode == "cv")

    def _refresh(self, reset_view: bool = False):
        """Veriyi yeniden okur ve grafiği günceller."""
        if not self._session and not self._history_sessions and not self._dropsens_pad:
            return

        # Session durumunu diskten yenile (başka process güncelliyor olabilir)
        try:
            if self._session is not None:
                json_path = os.path.join(self._session.session_dir, "session.json")
            else:
                json_path = ""
            if json_path and os.path.isfile(json_path):
                refreshed = MeasurementSession.load(self._session.session_dir)
                self._session = refreshed
        except Exception:
            pass

        if self._history_sessions:
            refreshed_sessions = []
            for session in self._history_sessions:
                try:
                    json_path = os.path.join(session.session_dir, "session.json")
                    if os.path.isfile(json_path):
                        refreshed_sessions.append(
                            MeasurementSession.load(session.session_dir)
                        )
                except Exception:
                    continue
            if refreshed_sessions:
                self._history_sessions = refreshed_sessions

        self._sync_view_mode_options()
        self._update_meta()
        self._load_log()
        self._plot_data(reset_view=reset_view)

    def _update_meta(self):
        """Session meta bilgilerini annotation paneline yazar."""
        if self._dropsens_pad:
            m = self._dropsens_pad
            self.meta_lbl.setText(
                f"<b>Part:</b> {m.part_number}<br>"
                f"<b>Mode:</b> DropSens PAD<br>"
                f"<b>File:</b> <small>{m.path}</small>"
            )
            self.session_lbl.setText(f"{m.part_number}  [DropSens PAD]")
            return

        if self._history_sessions:
            first = self._history_sessions[0]
            created_start = first.created_at.strftime("%Y-%m-%d %H:%M:%S")
            created_end = self._history_sessions[-1].created_at.strftime("%Y-%m-%d %H:%M:%S")
            self.meta_lbl.setText(
                f"<b>Part:</b> {first.part_number}<br>"
                f"<b>Mode:</b> History<br>"
                f"<b>Sessions:</b> {len(self._history_sessions)}<br>"
                f"<b>Range:</b> {created_start} - {created_end}<br>"
                f"<b>Dir:</b> <small>{os.path.dirname(first.session_dir)}</small>"
            )
            self.session_lbl.setText(
                f"{first.part_number}  [session: {len(self._history_sessions)}]"
            )
            return

        s = self._session
        # Legacy session özel meta
        if isinstance(s, LegacySession):
            self.meta_lbl.setText(
                f"<b>Part:</b> {s.part_number}<br>"
                f"<b>Mode:</b> <span style='color:#FF9800'>Legacy</span><br>"
                f"<b>Dir:</b> <small>{s.session_dir}</small>"
            )
            self.session_lbl.setText(f"{s.part_number}  [legacy]")
            return
        created = s.created_at.strftime("%Y-%m-%d %H:%M:%S")
        status_color = {
            "completed"  : "#4CAF50",
            "in_progress": "#2196F3",
            "aborted"    : "#F44336",
            "error"      : "#F44336",
            "pending"    : "#888888",
        }.get(s.status.value, "#888888")
        measurement_mode = getattr(s, "measurement_mode", "")
        mode_label = {
            MEASUREMENT_MODE_CONTINUOUS_PAD: "Continuous PAD",
            MEASUREMENT_MODE_CV: "CV",
        }.get(measurement_mode, "Script PAD")

        self.meta_lbl.setText(
            f"<b>Part:</b> {s.part_number}<br>"
            f"<b>Mode:</b> {mode_label}<br>"
            f"<b>Created:</b> {created}<br>"
            f"<b>Status:</b> <span style='color:{status_color}'>"
            f"{s.status.value}</span><br>"
            f"<b>Dir:</b> <small>{s.session_dir}</small>"
        )

        name = f"{s.part_number}  [{s.status.value}]"
        self.session_lbl.setText(name)

    def _load_log(self):
        """Log dosyasını parse eder, annotation panellerini doldurur."""
        if self._dropsens_pad:
            log_path = getattr(self._dropsens_pad, "recipe_log_path", "") or ""
            if log_path and os.path.isfile(log_path):
                self._parse_result = LogParser().parse(log_path)
            else:
                self._parse_result = None
            self.notes_edit.setPlainText("")
            self.sys_edit.setPlainText("")
            return

        log_paths = self._find_logs()
        if not log_paths:
            self._parse_result = None
            self.notes_edit.setPlainText("Log dosyası bulunamadı.")
            self.sys_edit.setPlainText("")
            return

        parser = LogParser()
        parse_results = [parser.parse(path) for path in log_paths]
        self._parse_result = self._merge_parse_results(parse_results)

        # Manuel notlar
        if self._parse_result.manual_notes:
            lines = []
            for n in self._parse_result.manual_notes:
                ts = n.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"[{ts}] {n.text}")
            self.notes_edit.setPlainText("\n".join(lines))
        else:
            self.notes_edit.setPlainText("Manuel not yok.")

        # Sistem eventleri
        if self._parse_result.system_events:
            lines = []
            for e in self._parse_result.system_events:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"[{ts}] {e.detail[:50]}")
            self.sys_edit.setPlainText("\n".join(lines))
        else:
            self.sys_edit.setPlainText("")

    def _find_log(self) -> Optional[str]:
        """Session'daki log dosyasını bulur."""
        if not self._session:
            return None
        # Önce session.json'da kayıtlı yola bak
        log_path = self._session.get_file_path("log")
        if log_path and os.path.isfile(log_path):
            return log_path
        # Yoksa logs/ dizininde session.log ara
        candidate = os.path.join(self._session.logs_dir, "session.log")
        return candidate if os.path.isfile(candidate) else None

    def _find_logs(self) -> List[str]:
        """Tek session veya history modu icin mevcut log dosyalarini bulur."""
        if self._history_sessions:
            log_paths = []
            for session in self._history_sessions:
                candidate = os.path.join(session.logs_dir, "session.log")
                if os.path.isfile(candidate):
                    log_paths.append(candidate)
            return log_paths

        log_path = self._find_log()
        return [log_path] if log_path else []

    def _merge_parse_results(self, results: List[ParseResult]) -> ParseResult:
        """Birden fazla parse sonucunu tek zaman ekseninde birlestirir."""
        merged = ParseResult()
        for result in results:
            merged.step_events.extend(result.step_events)
            merged.system_events.extend(result.system_events)
            merged.manual_notes.extend(result.manual_notes)

        merged.step_events.sort(key=lambda item: item.timestamp)
        merged.system_events.sort(key=lambda item: item.timestamp)
        merged.manual_notes.sort(key=lambda item: item.timestamp)
        return merged

    # ── Grafik çizimi ─────────────────────────────────────────────

    def _configure_plot_axes(self, mode: str):
        """Switch plot axes between timestamp timeline and CV numeric axes."""
        if not _PG_OK or not self.plot_widget_top:
            return
        if self._plot_axis_mode == mode:
            return

        if mode == "cv":
            self.plot_widget_top.getPlotItem().setAxisItems({
                "bottom": pg.AxisItem(orientation="bottom"),
            })
            self.plot_widget_top.setLabel("left", "Current", units="A")
            self.plot_widget_top.setLabel("bottom", "Potential", units="V")

            if self.plot_widget_bottom:
                self.plot_widget_bottom.getPlotItem().setAxisItems({
                    "bottom": VerticalDateAxisItem(orientation="bottom"),
                })
                self.plot_widget_bottom.setXLink(None)
                self.plot_widget_bottom.setYLink(None)
                self.plot_widget_bottom.setLabel("left", "Current", units="A")
                self.plot_widget_bottom.setLabel("bottom", "Time")
        else:
            self.plot_widget_top.getPlotItem().setAxisItems({
                "bottom": VerticalDateAxisItem(orientation="bottom"),
            })
            self.plot_widget_top.setLabel("left", "Current", units="A")
            self.plot_widget_top.setLabel("bottom", "Time")

            if self.plot_widget_bottom:
                self.plot_widget_bottom.getPlotItem().setAxisItems({
                    "bottom": VerticalDateAxisItem(orientation="bottom"),
                })
                self.plot_widget_bottom.setXLink(self.plot_widget_top)
                self.plot_widget_bottom.setYLink(self.plot_widget_top)
                self.plot_widget_bottom.setLabel("left", "Current", units="A")
                self.plot_widget_bottom.setLabel("bottom", "Time")

        self._plot_axis_mode = mode

    def _apply_graph_layout_for_mode(self, mode: str):
        if not self.plot_widget_top:
            return
        if mode == "cv":
            if self.bottom_group:
                self.bottom_group.setVisible(False)
            self.plot_widget_top.setMaximumHeight(16777215)
        else:
            if self.bottom_group:
                self.bottom_group.setVisible(True)
            self.plot_widget_top.setMaximumHeight(520)

    def _plot_data(self, reset_view: bool = False):
        if not self.plot_widget_top:
            return

        self.top_curve = None
        self.bottom_curve = None

        if reset_view:
            self._manual_top_view_active = False
            self._saved_top_view_range = None

        if self._viewer_mode == "cv" and self._has_cv_measurements():
            self._plot_cv_data(reset_view=reset_view)
            return

        self._apply_graph_layout_for_mode("pad")
        self._configure_plot_axes("time")
        self._rebuilding_top_view = True

        self.plot_widget_top.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._marker_items.clear()
        self._measure_dots.clear()
        self._clear_hover_data()

        series_data = self._read_measurement_series()
        if not series_data:
            self._show_no_data_msg()
            self._rebuilding_top_view = False
            return

        timestamps = []
        currents = []
        bottom_timestamps = []
        bottom_currents = []

        for series_item in series_data:
            _label, series_timestamps, series_currents, mean_kind = self._normalize_pad_series_item(series_item)
            timestamps.extend(series_timestamps)
            currents.extend(series_currents)
            mean_group_size = 21 if mean_kind == "continuous" else 5
            centered_mean = mean_kind == "continuous"
            mean_ts, mean_cur = self._build_mean_series(
                series_timestamps,
                series_currents,
                group_size=mean_group_size,
                centered=centered_mean,
            )
            bottom_timestamps.extend(mean_ts)
            bottom_currents.extend(mean_cur)

        if timestamps:
            sorted_pairs = sorted(zip(timestamps, currents), key=lambda item: item[0])
            timestamps = [item[0] for item in sorted_pairs]
            currents = [item[1] for item in sorted_pairs]
        if bottom_timestamps:
            bottom_pairs = sorted(
                zip(bottom_timestamps, bottom_currents),
                key=lambda item: item[0],
            )
            bottom_timestamps = [item[0] for item in bottom_pairs]
            bottom_currents = [item[1] for item in bottom_pairs]

        self._set_hover_data(self.plot_widget_top, timestamps, currents)
        if self.plot_widget_bottom:
            self._set_hover_data(
                self.plot_widget_bottom,
                bottom_timestamps,
                bottom_currents,
            )

        # Ana seri — kaynak Current uA gelir; grafikte A olarak çizilir.
        for series_item in series_data:
            _label, series_timestamps, series_currents, _mean_kind = self._normalize_pad_series_item(series_item)
            series_curve = self.plot_widget_top.plot(
                series_timestamps,
                series_currents,
                pen=None,
                symbol="o",
                symbolSize=5,
                symbolBrush=pg.mkBrush(_C_DATA_LINE),
                symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
                name=None,
            )
            series_curve.opts["movingAverageSkip"] = True

        self.top_curve = self.plot_widget_top.plot(
            timestamps,
            currents,
            pen=None,   # çizgi çizme
            symbol="o",
            symbolSize=5,
            symbolBrush=pg.mkBrush(_C_DATA_LINE),
            symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
            name=None
        )
        self.top_curve.opts["movingAverageSource"] = True


        # ── Bottom grafik: downsample edilmiş veri ─────────────────
        if self.plot_widget_bottom:
            for series_item in series_data:
                _label, series_timestamps, series_currents, mean_kind = self._normalize_pad_series_item(series_item)
                mean_group_size = 21 if mean_kind == "continuous" else 5
                centered_mean = mean_kind == "continuous"
                mean_ts, mean_cur = self._build_mean_series(
                    series_timestamps,
                    series_currents,
                    group_size=mean_group_size,
                    centered=centered_mean,
                )
                series_curve = self.plot_widget_bottom.plot(
                    mean_ts,
                    mean_cur,
                    pen=None,
                    symbol="o",
                    symbolSize=5,
                    symbolBrush=pg.mkBrush(_C_DATA_LINE),
                    symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
                    name=None,
                )
                series_curve.opts["movingAverageSkip"] = True
            self.bottom_curve = self.plot_widget_bottom.plot(
                bottom_timestamps,
                bottom_currents,
                pen=None,  # çizgi çizme
                symbol="o",
                symbolSize=5,
                symbolBrush=pg.mkBrush(_C_DATA_LINE),
                symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
                name=None
            )
            self.bottom_curve.opts["movingAverageSource"] = True

        self.plot_widget_top.getPlotItem().updateMovingAverage()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.getPlotItem().updateMovingAverage()

        # Marker çizgileri
        if self._parse_result:
            self._draw_markers(timestamps)

        # Measure dot'larını doğru konuma yerleştir.
        # Yeni session yüklenince auto-range uygulanır; normal refresh'te ise
        # kullanıcının mevcut zoom/pan görünümü korunur.
        if reset_view or not self._manual_top_view_active or self._saved_top_view_range is None:
            self._auto_fit_top_view()
        else:
            self._apply_saved_top_view_range()
        self._update_measure_dots_for_plot(self.plot_widget_top)
        if self.plot_widget_bottom:
            # Alt grafik Y eksenini ust grafikten linked olarak aliyor;
            # burada yeniden autoRange yaparsak ortak Y araligi alttaki
            # downsample edilmis gorunume gore yeniden hesaplanabiliyor.
            self.plot_widget_bottom.getViewBox().disableAutoRange(axis="y")
            self._update_measure_dots_for_plot(self.plot_widget_bottom)
        self._rebuilding_top_view = False

    def _plot_cv_data(self, reset_view: bool = False):
        """Draw CV .mtc curves as a single potential-current view."""
        self.top_curve = None
        self.bottom_curve = None

        self._apply_graph_layout_for_mode("cv")
        self._configure_plot_axes("cv")
        self._rebuilding_top_view = True

        self.plot_widget_top.clear()
        if getattr(self, "legend", None):
            self.legend.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._marker_items.clear()
        self._measure_dots.clear()
        self._clear_hover_data()

        series_data = self._read_cv_measurement_series()
        if not series_data:
            self._show_no_data_msg()
            self._rebuilding_top_view = False
            return

        colors = [
            (0, 0, 128),
            (33, 150, 243),
            (76, 175, 80),
            (244, 67, 54),
            (156, 39, 176),
            (255, 152, 0),
        ]
        hover_x = []
        hover_y = []

        file_color_map = {}
        legend_file_labels = set()
        next_color_index = 0
        for label, times_s, potentials_v, currents_a in series_data:
            file_label = label.split(" - ", 1)[0]
            if file_label not in file_color_map:
                file_color_map[file_label] = colors[next_color_index % len(colors)]
                next_color_index += 1
            color = file_color_map[file_label]
            legend_label = None
            if file_label not in legend_file_labels:
                legend_label = file_label
                legend_file_labels.add(file_label)
            pen = pg.mkPen(color=color, width=1)
            self.plot_widget_top.plot(
                potentials_v,
                currents_a,
                pen=pen,
                name=legend_label,
            )
            hover_x.extend(potentials_v)
            hover_y.extend(currents_a)

        self._set_hover_data(
            self.plot_widget_top,
            hover_x,
            hover_y,
            x_kind="potential",
        )

        if reset_view or not self._manual_top_view_active or self._saved_top_view_range is None:
            self._auto_fit_top_view()
        else:
            self._apply_saved_top_view_range()

        self._rebuilding_top_view = False

    def _plot_cv_bottom_pad_data(self):
        if not self.plot_widget_bottom:
            return

        series_data = self._read_pad_plot_series(self._session)
        if not series_data:
            self._show_bottom_no_data_msg("PAD verisi bulunamadı.")
            return

        bottom_timestamps = []
        bottom_currents = []
        for series_item in series_data:
            _label, series_timestamps, series_currents, mean_kind = self._normalize_pad_series_item(series_item)
            mean_group_size = 21 if mean_kind == "continuous" else 5
            centered_mean = mean_kind == "continuous"
            mean_ts, mean_cur = self._build_mean_series(
                series_timestamps,
                series_currents,
                group_size=mean_group_size,
                centered=centered_mean,
            )
            bottom_timestamps.extend(mean_ts)
            bottom_currents.extend(mean_cur)
            self.plot_widget_bottom.plot(
                mean_ts,
                mean_cur,
                pen=None,
                symbol="o",
                symbolSize=5,
                symbolBrush=pg.mkBrush(_C_DATA_LINE),
                symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
                name=None,
            )

        if bottom_timestamps:
            pairs = sorted(
                zip(bottom_timestamps, bottom_currents),
                key=lambda item: item[0],
            )
            bottom_timestamps = [item[0] for item in pairs]
            bottom_currents = [item[1] for item in pairs]

        self._set_hover_data(
            self.plot_widget_bottom,
            bottom_timestamps,
            bottom_currents,
        )

    def _has_cv_measurements(self) -> bool:
        return bool(self._find_cv_measurement_files())

    def _has_pad_measurements(self) -> bool:
        if self._dropsens_pad is not None:
            return True
        if self._history_sessions:
            return True
        return bool(self._read_pad_plot_series(self._session))

    def _find_cv_measurement_files(self) -> List[str]:
        if self._dropsens_pad or self._history_sessions or self._session is None:
            return []
        measurements_dir = getattr(self._session, "measurements_dir", "")
        if not measurements_dir or not os.path.isdir(measurements_dir):
            return []
        try:
            files = [
                os.path.join(measurements_dir, name)
                for name in os.listdir(measurements_dir)
                if name.lower().endswith(".mtc")
            ]
        except OSError:
            return []
        files.sort(key=lambda path: os.path.getctime(path))
        return files

    def _read_cv_measurement_series(self):
        series = []
        for path in self._find_cv_measurement_files():
            try:
                measurement = read_cv_measurement(path)
            except MeasurementDataError as exc:
                self.log_signal.emit(f"Viewer CV verisi okuma hatasi: {exc}")
                continue

            file_label = os.path.splitext(os.path.basename(path))[0]
            for idx, curve in enumerate(measurement.curves, start=1):
                times_s = curve.points.get("time", [])
                potentials_v = curve.points.get("potential", [])
                currents_ua = curve.points.get("i1", [])
                total = min(len(potentials_v), len(currents_ua))
                if total <= 0:
                    continue
                if times_s:
                    times_s = times_s[:total]
                else:
                    times_s = list(range(total))
                curve_label = curve.title or curve.name or f"Curve {idx}"
                label = f"{file_label} - {curve_label}" if curve_label else file_label
                series.append((
                    label,
                    times_s,
                    potentials_v[:total],
                    [value * _CURRENT_UA_TO_A for value in currents_ua[:total]],
                ))
        return series

    def _normalize_pad_series_item(self, item):
        if len(item) >= 4:
            return item
        label, timestamps, currents = item
        mean_kind = "continuous" if self._dropsens_pad or self._is_continuous_pad_view() else "discrete"
        return label, timestamps, currents, mean_kind

    def _read_pad_plot_series(self, session: MeasurementSession):
        """Read PAD-like session data separately from CV files."""
        if session is None:
            return []

        series = []
        xlsx_path = find_session_xlsx_path(session)
        if xlsx_path:
            try:
                timestamps, currents = read_xlsx_measurement_series(
                    xlsx_path,
                    current_scale=_CURRENT_UA_TO_A,
                )
                if timestamps:
                    series.append(("Discrete PAD", timestamps, currents, "discrete"))
            except MeasurementDataError as exc:
                self.log_signal.emit(f"Viewer xlsx verisi okuma hatasi: {exc}")
        else:
            try:
                timestamps, currents = read_csv_measurement_series(
                    session.measurements_dir,
                    current_scale=_CURRENT_UA_TO_A,
                )
                if timestamps:
                    series.append(("Discrete PAD", timestamps, currents, "discrete"))
            except MeasurementDataError:
                pass

        try:
            timestamps, currents = read_session_pad_segment_series(
                session.measurements_dir,
                current_scale=_CURRENT_UA_TO_A,
            )
            if timestamps:
                series.append(("Continuous PAD", timestamps, currents, "continuous"))
        except MeasurementDataError:
            pass

        return series

    def _read_measurement_series(self):
        """Tek session veya history modu icin olcum serilerini okur."""
        if self._dropsens_pad:
            timestamps, currents = self._read_dropsens_pad_data()
            if not timestamps:
                return []
            return [("Current", timestamps, currents, "continuous")]

        if self._history_sessions:
            all_series = []
            for session in self._history_sessions:
                timestamps, currents = self._read_session_measurement_data(session)
                if not timestamps:
                    self.log_signal.emit(
                        f"Viewer history xlsx atlandi: {session.session_dir}"
                    )
                    continue
                label = session.created_at.strftime("%Y-%m-%d %H:%M:%S")
                mean_kind = (
                    "continuous"
                    if getattr(session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD
                    else "discrete"
                )
                all_series.append((label, timestamps, currents, mean_kind))
            return all_series

        return self._read_pad_plot_series(self._session)

    def _is_continuous_pad_view(self) -> bool:
        if self._session is not None:
            return getattr(self._session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD
        if self._history_sessions:
            return any(
                getattr(session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD
                for session in self._history_sessions
            )
        return False

    def _read_measurement_data(self):
        """
        xlsx varsa xlsx'ten, yoksa CSV'lerden okur.
        (timestamp_unix_list, current_list) döner.
        """
        return self._read_session_measurement_data(self._session)

    def _read_session_measurement_data(self, session: MeasurementSession):
        if session is None:
            return [], []
        try:
            return read_session_measurement_series(
                session,
                current_scale=_CURRENT_UA_TO_A,
            )
        except MeasurementDataError as e:
            self.log_signal.emit(f"Viewer olcum verisi okuma hatasi: {e}")
            return [], []

    def _read_dropsens_pad_data(self):
        """Loaded DropSens PAD file data as unix timestamps and current in A."""
        measurement = self._dropsens_pad
        if measurement is None or not measurement.curves:
            return [], []

        curve = measurement.curves[0]
        times = curve.points.get("time", [])
        currents_ua = curve.points.get("i1", [])
        total = min(len(times), len(currents_ua))
        if total <= 0:
            return [], []

        base_time = measurement.base_time
        timestamps = [
            (base_time + timedelta(seconds=times[idx])).timestamp()
            for idx in range(total)
        ]
        currents = [currents_ua[idx] * _CURRENT_UA_TO_A for idx in range(total)]
        return timestamps, currents

    def _build_mean_series(
        self,
        timestamps,
        currents,
        group_size: int = 5,
        centered: bool = False,
    ):
        """Alt grafik için blok veya merkezli kayan pencere ortalaması üretir."""
        if not timestamps or not currents or group_size <= 1:
            return list(timestamps), list(currents)

        mean_timestamps = []
        mean_currents = []
        total = min(len(timestamps), len(currents))
        window = max(1, int(group_size))

        if not centered:
            for start in range(0, total, window):
                end = min(total, start + window)
                chunk_times = timestamps[start:end]
                chunk_currents = currents[start:end]
                if not chunk_times or not chunk_currents:
                    continue
                mean_timestamps.append(sum(chunk_times) / len(chunk_times))
                mean_currents.append(sum(chunk_currents) / len(chunk_currents))
            return mean_timestamps, mean_currents

        half_window = window // 2
        for idx in range(total):
            start = max(0, idx - half_window)
            end = min(total, idx + half_window + 1)
            chunk_times = timestamps[start:end]
            chunk_currents = currents[start:end]
            if not chunk_times or not chunk_currents:
                continue
            mean_timestamps.append(timestamps[idx])
            mean_currents.append(sum(chunk_currents) / len(chunk_currents))

        return mean_timestamps, mean_currents

    def _install_visible_xlsx_export_provider(self):
        """Expose Viewer visible data to the custom pyqtgraph XLSX exporter."""
        for plot_widget in (self.plot_widget_top, self.plot_widget_bottom):
            if not plot_widget:
                continue
            plot_item = plot_widget.getPlotItem()
            plot_item._visible_xlsx_export_provider = self._build_visible_xlsx_export

    def _build_visible_xlsx_export(self) -> dict:
        if self._viewer_mode == "cv":
            raise ValueError("Visible XLSX export sadece PAD zaman gorunumu icin kullanilabilir.")
        if not self.plot_widget_top or not self.plot_widget_bottom:
            raise ValueError("Export edilecek grafik bulunamadi.")

        x_range = self.plot_widget_top.getViewBox().viewRange()[0]
        x_min, x_max = sorted(x_range)

        top_plot = self.plot_widget_top.getPlotItem()
        bottom_plot = self.plot_widget_bottom.getPlotItem()
        top_source = getattr(self, "top_curve", None)
        bottom_source = getattr(self, "bottom_curve", None)
        top_average = self._visible_average_curve(top_plot, top_source)
        bottom_average = self._visible_average_curve(bottom_plot, bottom_source)

        series = {
            "top_pad": self._visible_curve_series(top_source, x_min, x_max),
            "bottom_mean": self._visible_curve_series(bottom_source, x_min, x_max),
            "top_average": self._visible_curve_series(top_average, x_min, x_max),
            "bottom_average": self._visible_curve_series(bottom_average, x_min, x_max),
        }
        return {
            "series": series,
            "metadata": self._visible_export_metadata(
                x_min,
                x_max,
                top_average is not None or bottom_average is not None,
            ),
        }

    def _visible_average_curve(self, plot_item, source_curve):
        if plot_item is None or source_curve is None:
            return None
        overlay_curves = getattr(plot_item, "_movingAverageCurves", {})
        return overlay_curves.get(source_curve)

    def _visible_curve_series(self, curve, x_min: float, x_max: float) -> dict:
        if curve is None or not hasattr(curve, "getData"):
            return {"x": [], "y": []}

        data_x, data_y = curve.getData()
        if data_x is None or data_y is None:
            return {"x": [], "y": []}

        result_x = []
        result_y = []
        total = min(len(data_x), len(data_y))
        for idx in range(total):
            try:
                x_val = float(data_x[idx])
                y_val = float(data_y[idx])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(x_val) and math.isfinite(y_val)):
                continue
            if x_min <= x_val <= x_max:
                result_x.append(x_val)
                result_y.append(y_val / _CURRENT_UA_TO_A)
        return {"x": result_x, "y": result_y}

    def _visible_export_metadata(
        self,
        x_min: float,
        x_max: float,
        average_exported: bool,
    ) -> list:
        top_plot = self.plot_widget_top.getPlotItem() if self.plot_widget_top else None
        bottom_plot = self.plot_widget_bottom.getPlotItem() if self.plot_widget_bottom else None
        metadata = [
            ("exported_at", datetime.now()),
            ("visible_start", datetime.fromtimestamp(x_min)),
            ("visible_end", datetime.fromtimestamp(x_max)),
            ("viewer_mode", self._viewer_mode),
            ("average_exported", average_exported),
        ]
        metadata.extend(self._plot_downsample_metadata(top_plot, "top"))
        metadata.extend(self._plot_downsample_metadata(bottom_plot, "bottom"))
        metadata.extend(self._plot_average_metadata(top_plot))
        return metadata

    def _plot_downsample_metadata(self, plot_item, prefix: str) -> list:
        if plot_item is None or not hasattr(plot_item, "downsampleMode"):
            return [
                (f"{prefix}_downsample_enabled", False),
            ]
        try:
            ds, auto, method, offset = plot_item.downsampleMode()
        except Exception:
            return [
                (f"{prefix}_downsample_enabled", False),
            ]
        return [
            (f"{prefix}_downsample_enabled", ds > 1 or bool(auto)),
            (f"{prefix}_downsample_ds", ds),
            (f"{prefix}_downsample_auto", bool(auto)),
            (f"{prefix}_downsample_method", method),
            (f"{prefix}_downsample_offset", offset),
        ]

    def _plot_average_metadata(self, plot_item) -> list:
        ctrl = getattr(plot_item, "ctrl", None) if plot_item is not None else None
        if ctrl is None or not hasattr(ctrl, "averageGroup"):
            return [
                ("average_enabled", False),
            ]

        method = ""
        if hasattr(ctrl, "smoothingMethodCombo"):
            method = ctrl.smoothingMethodCombo.currentData()
        return [
            ("average_enabled", ctrl.averageGroup.isChecked()),
            ("average_method", method or "moving_average"),
            (
                "average_radius",
                ctrl.movingAverageRadiusSpin.value()
                if hasattr(ctrl, "movingAverageRadiusSpin")
                else "",
            ),
            (
                "savgol_polyorder",
                ctrl.savgolPolySpin.value()
                if hasattr(ctrl, "savgolPolySpin")
                else "",
            ),
            (
                "savgol_deriv",
                ctrl.savgolDerivCombo.currentData()
                if hasattr(ctrl, "savgolDerivCombo")
                else "",
            ),
        ]

    def _draw_glucose_regions(self, data_timestamps: list):
        """Port A step geçişleri arasındaki bölgeleri porta göre renklendirir."""
        if not self._parse_result or not data_timestamps:
            return

        t_min = min(data_timestamps)
        t_max = max(data_timestamps)
        offset = self._get_offset_sec()

        # Sadece port_a içeren step event'leri zamana göre sırala
        port_events = sorted(
            [ev for ev in self._parse_result.step_events
             if ev.port_a is not None
             and getattr(ev, "marker_kind", "step") != "measure"],
            key=lambda ev: ev.timestamp,
        )

        if not port_events:
            return

        for i, ev in enumerate(port_events):
            t_start = ev.timestamp.timestamp() + offset
            t_end = (port_events[i + 1].timestamp.timestamp() + offset
                     if i + 1 < len(port_events)
                     else t_max)

            if t_end <= t_start or t_end < t_min or t_start > t_max:
                continue
            t_start = max(t_start, t_min)
            t_end = min(t_end, t_max)

            rgba = _PORT_COLORS.get(ev.port_a, _PORT_COLOR_DEFAULT)
            brush = pg.mkBrush(*rgba)

            for pw in (self.plot_widget_top, self.plot_widget_bottom):
                if not pw:
                    continue
                region = pg.LinearRegionItem(
                    values=(t_start, t_end),
                    orientation="vertical",
                    brush=brush,
                    movable=False,
                    pen=pg.mkPen(None),
                )
                region.setZValue(-10)
                pw.addItem(region)
                self._marker_items.append(region)

    def _draw_markers(self, data_timestamps: list):
        """Step ve sistem marker çizgilerini grafiğe ekler."""
        if not data_timestamps:
            return

        t_min = min(data_timestamps)
        t_max = max(data_timestamps)
        marker_margin = max(5.0, min(60.0, (t_max - t_min) * 0.02))
        marker_min = t_min - marker_margin
        marker_max = t_max + marker_margin
        self._draw_glucose_regions(data_timestamps)

        # Step marker'ları
        offset = self._get_offset_sec()
        for ev in self._parse_result.step_events:
            t_raw = ev.timestamp.timestamp()

            if getattr(ev, "marker_kind", "step") == "measure":
                if not (marker_min <= t_raw <= marker_max):
                    continue
                is_start = getattr(ev, "marker_label", "") == "Start Measure"
                self._add_measure_marker(
                    t_raw,  # offset yok
                    is_start=is_start,
                    label="START" if is_start else "STOP",
                )
                continue

            t = t_raw + offset
            if not (marker_min <= t <= marker_max):
                continue

            label = self._step_label(ev)

            # port_a değiştiyse kırmızı kalın; sadece valve_b değiştiyse turuncu ince.
            if ev.port_a is not None:
                color = _C_STEP_LINE    # kırmızı
                width = 1
            else:
                color = _C_RESET_LINE   # turuncu
                width = 1

            self._add_vline(t, color, label, width=width)

        # Sistem event marker'ları
        for ev in self._parse_result.system_events:
            t = ev.timestamp.timestamp()
            if not (marker_min <= t <= marker_max):
                continue
            color = _SYSTEM_COLORS.get(ev.event_type, _C_PAUSED)
            self._add_measure_marker(
                t,
                is_start=(ev.event_type == "started"),
                marker_color=color,
                label=self._system_event_label(ev.event_type),
            )

    def _add_vline(self, x: float, color: tuple, label: str,
                   dashed: bool = False, width: int = 1):

        """Dikey InfiniteLine + TextItem ekler."""
        if not self.plot_widget_top:
            return

        style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        pen = pg.mkPen(color=color, width=width, style=style)

        # Üst grafik çizgisi
        line_top = pg.InfiniteLine(
            pos=x, angle=90, pen=pen, movable=False, label=label,
            labelOpts={
                "position": 0.97,
                "color": color,
#                "fill": _C_BG,
#                "border": pg.mkPen(color=_C_BG, width=1),
                "fill": (255, 255, 255, 0),
                "border": pg.mkPen(None),
                "movable": False,
                "anchors": [(0, 0), (0, 0)],
            })

        font = QFont()
        font.setPointSize(8)  # daha büyük
        font.setBold(True)  # bold
        line_top.label.setFont(font)

        self.plot_widget_top.addItem(line_top, ignoreBounds=False)
        self._marker_items.append(line_top)
        self._vline_hover_points.append({
            "plot": self.plot_widget_top,
            "x": x,
            "label": label,
        })

        # Alt grafik çizgisi
        if self.plot_widget_bottom:
            line_bottom = pg.InfiniteLine(
                pos=x, angle=90, pen=pen, movable=False, label=label,
                labelOpts={
                    "position": 0.97,
                    "color": color,
                    "fill": (255, 255, 255, 0),
                    "border": pg.mkPen(None),
                    "movable": False,
                    "anchors": [(0, 0), (0, 0)],
                })

            font = QFont()
            font.setPointSize(8)  # daha büyük
            font.setBold(True)  # bold
            line_bottom.label.setFont(font)

            self.plot_widget_bottom.addItem(line_bottom, ignoreBounds=False)
            self._marker_items.append(line_bottom)
            self._vline_hover_points.append({
                "plot": self.plot_widget_bottom,
                "x": x,
                "label": label,
            })

    def _add_measure_marker(self, x: float, is_start: bool,
                            marker_color: tuple = None, label: str = ""):
        """Measure event'lerinde sadece nokta cizer."""
        if marker_color is None:
            marker_color = _C_STARTED if is_start else _C_STOPPED

        def _draw_on(plot_widget):
            if not plot_widget:
                return
            marker_entry = {
                "plot": plot_widget,
                "dot": None,
                "x": x,
                "y": 0.0,
                "label": label,
            }
            dot = pg.ScatterPlotItem(
                [x], [0.0],
                size=8,
                pen=pg.mkPen(marker_color, width=1),
                brush=pg.mkBrush(marker_color),
            )
            plot_widget.addItem(dot, ignoreBounds=True)
            self._marker_items.append(dot)
            marker_entry["dot"] = dot
            self._measure_dots.append(marker_entry)
            self._event_marker_points.append(marker_entry)
            self._ensure_measure_dot_tracking(plot_widget)
            self._update_measure_dots_for_plot(plot_widget)

        _draw_on(self.plot_widget_top)
        _draw_on(self.plot_widget_bottom)

    def _system_event_label(self, event_type: str) -> str:
        """Hover icin sistem event etiketini doner."""
        labels = {
            "started": "START",
            "completed": "DONE",
            "stopped": "STOP",
            "paused": "PAUSE",
            "resumed": "RESUME",
            "error": "ERROR",
        }
        return labels.get(event_type, event_type.upper())

    def _ensure_measure_dot_tracking(self, plot_widget):
        """Zoom veya pan sonrasi measure noktalarini ust banda tasir."""
        if not plot_widget or plot_widget in self._measure_range_hooks:
            return

        vb = plot_widget.getViewBox()

        def _on_range_changed(*_):
            self._update_measure_dots_for_plot(plot_widget)

        vb.sigRangeChanged.connect(_on_range_changed)
        self._measure_range_hooks[plot_widget] = _on_range_changed

    def _ensure_top_view_tracking(self):
        """Üst grafik aralığını kaydeder; kullanıcı zoom/pan yaptığında manual moda geçer."""
        if not self.plot_widget_top or self._top_view_tracking_connected:
            return

        vb = self.plot_widget_top.getViewBox()

        def _on_top_range_changed(*_):
            if self._applying_top_view_state or self._rebuilding_top_view:
                return
            if not self._top_plot_has_data():
                return
            self._save_current_top_view_range(manual=True)

        vb.sigRangeChanged.connect(_on_top_range_changed)
        self._top_view_tracking_connected = True

    def _top_plot_has_data(self) -> bool:
        """Ust grafikte manuel zoom'u anlamlandiracak gercek veri var mi?"""
        if not self.plot_widget_top:
            return False
        point_data = self._hover_points.get(self.plot_widget_top)
        if not point_data:
            return False
        return bool(point_data.get("x"))

    def _save_current_top_view_range(self, manual: bool = False):
        """Üst grafiğin mevcut view aralığını state olarak saklar."""
        if not self.plot_widget_top:
            return

        x_range, y_range = self.plot_widget_top.getViewBox().viewRange()
        self._saved_top_view_range = {
            "x": [x_range[0], x_range[1]],
            "y": [y_range[0], y_range[1]],
        }
        if manual:
            self._manual_top_view_active = True

    def _apply_saved_top_view_range(self):
        """Kayıtlı üst grafik aralığını yeniden uygular."""
        if not self.plot_widget_top or not self._saved_top_view_range:
            return

        top_view_box = self.plot_widget_top.getViewBox()
        self._applying_top_view_state = True
        try:
            top_view_box.disableAutoRange()
            top_view_box.setRange(
                xRange=self._saved_top_view_range["x"],
                yRange=self._saved_top_view_range["y"],
                padding=0,
            )
        finally:
            self._applying_top_view_state = False

    def _auto_fit_top_view(self):
        """Üst grafiğe kontrollü bir kez auto-fit uygular ve sonucu saklar."""
        if not self.plot_widget_top:
            return

        top_view_box = self.plot_widget_top.getViewBox()
        self._applying_top_view_state = True
        try:
            top_view_box.enableAutoRange()
            top_view_box.autoRange()
        finally:
            self._applying_top_view_state = False
        self._save_current_top_view_range(manual=False)

    def _update_measure_dots_for_plot(self, plot_widget):
        """Measure noktalarini mevcut gorunur Y araligina gore konumlar."""
        if not plot_widget:
            return

        y_min, y_max = plot_widget.getViewBox().viewRange()[1]
        y_pos = y_min + (y_max - y_min) * 0.90
        for item in self._measure_dots:
            if item["plot"] is plot_widget:
                item["dot"].setData([item["x"]], [y_pos])
                item["y"] = y_pos

    def _step_label(self, ev: StepEvent) -> str:
        """Step marker için kısa etiket: '50.0 mg/dL' veya 'P3'"""
        if ev.port_a:
            glucose_map = self._current_glucose_map()
            mg = glucose_map.get(str(ev.port_a))
            if mg is not None:
                try:
                    return f"{float(mg):.0f} mg/dL"
                except (TypeError, ValueError):
                    pass
            return f"P{ev.port_a}"
        return ""

    def _current_glucose_map(self) -> dict:
        """Tekil session varsa once session snapshot'ini, yoksa son tercihi kullanir."""
        params = getattr(self._session, "experiment_params", {}) if self._session else {}
        glucose_map = params.get("port_glucose") if isinstance(params, dict) else None
        if isinstance(glucose_map, dict) and glucose_map:
            return glucose_map
        return get_value("port_glucose", {})

    def _delete_session(self):
        """Yüklü session dizinini kullanıcı onayı alarak siler."""
        if not self._session:
            return

        session_dir = self._session.session_dir
        part_number = self._session.part_number

        reply = QMessageBox.question(
            self,
            "Session Sil",
            f"Bu işlem geri alınamaz!\n\n"
            f"Silinecek: {part_number}\n"
            f"{session_dir}\n\n"
            f"Tüm dosyalar (CSV, xlsx, log) kalıcı olarak silinecek.\n"
            f"Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            shutil.rmtree(session_dir)
            self.log_signal.emit(f"Session silindi: {session_dir}")
        except Exception as e:
            import traceback
            err = f"{type(e).__name__}: {e}"
            self.log_signal.emit(err)
            self.log_signal.emit(traceback.format_exc())
            QMessageBox.critical(self, "Hata", f"Dizin silinemedi:\n{err}")
            return

        # UI temizle
        self._session = None
        self._history_sessions = []
        self._parse_result = None
        self._viewer_mode = "pad"
        self._viewer_mode_user_selected = False
        self._sync_view_mode_controls(has_pad=False, has_cv=False)
        self._apply_graph_layout_for_mode("pad")
        self.session_lbl.setText("Oturum silindi")
        self.meta_lbl.setText("—")
        self.notes_edit.clear()
        self.sys_edit.clear()
        self.delete_btn.setEnabled(False)
        self._set_plot_titles("")
        if self.plot_widget_top:
            self.plot_widget_top.clear()
            if getattr(self, "legend", None):
                self.legend.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._apply_empty_time_axis()
        self._clear_hover_data()
        mw = self._get_main_window()
        if mw and hasattr(mw, "set_part_banner"):
            mw.set_part_banner("")

    def _show_no_data_msg(self):
        """Veri yoksa grafik alanına mesaj yazar."""
        if not self.plot_widget_top:
            return
        x_start, x_end, x_mid = self._empty_time_axis_range()
        text = pg.TextItem(
            "Veri bulunamadı.\nÖnce Aggregator çalıştırın veya CSV bekleyin.",
            color=(150, 150, 150), anchor=(0.5, 0.5))
        self.plot_widget_top.addItem(text)
        text.setPos(x_mid, 0)
        self._apply_empty_time_axis(x_start, x_end)

    def _show_bottom_no_data_msg(self, message: str):
        """Alt grafikte veri yoksa sade bir mesaj gösterir."""
        if not self.plot_widget_bottom:
            return
        x_start, x_end, x_mid = self._empty_time_axis_range()
        text = pg.TextItem(
            message,
            color=(150, 150, 150),
            anchor=(0.5, 0.5),
        )
        self.plot_widget_bottom.addItem(text)
        text.setPos(x_mid, 0)
        self.plot_widget_bottom.getViewBox().disableAutoRange(axis="x")
        self.plot_widget_bottom.setXRange(x_start, x_end, padding=0)

    def _empty_time_axis_range(self):
        """Veri yokken epoch yerine bugunun tarih araligini kullanir."""
        today_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_end = today_start.replace(hour=23, minute=59, second=59)
        x_start = today_start.timestamp()
        x_end = today_end.timestamp()
        return x_start, x_end, (x_start + x_end) / 2.0

    def _apply_empty_time_axis(self, x_start: float = None, x_end: float = None):
        """Bos grafiklerde X ekseninin bugunun tarihlerini gostermesini saglar."""
        if x_start is None or x_end is None:
            x_start, x_end, _ = self._empty_time_axis_range()
        if self.plot_widget_top:
            self.plot_widget_top.getViewBox().disableAutoRange(axis="x")
            self.plot_widget_top.setXRange(x_start, x_end, padding=0)
        if self.plot_widget_bottom:
            self.plot_widget_bottom.getViewBox().disableAutoRange(axis="x")
            self.plot_widget_bottom.setXRange(x_start, x_end, padding=0)

    def _ensure_hover_tracking(self, plot_widget):
        """Grafikte fare hareketini izleyip yakın noktalar için tooltip gösterir."""
        if not plot_widget or plot_widget in self._hover_hooks:
            return

        def _on_mouse_moved(pos):
            self._handle_plot_hover(plot_widget, pos)

        proxy = pg.SignalProxy(
            plot_widget.scene().sigMouseMoved,
            rateLimit=60,
            slot=_on_mouse_moved,
        )
        self._hover_hooks[plot_widget] = proxy

    def _set_hover_data(self, plot_widget, timestamps, currents, x_kind: str = "time"):
        """Hover için kullanılacak veri serisini saklar."""
        if not plot_widget:
            return
        x_values = list(timestamps)
        self._hover_points[plot_widget] = {
            "x": x_values,
            "y": list(currents),
            "x_kind": x_kind,
            "sorted": all(
                x_values[idx] <= x_values[idx + 1]
                for idx in range(max(0, len(x_values) - 1))
            ),
        }
        self._hover_state[plot_widget] = {
            "index": None,
            "text": None,
        }
        self._ensure_hover_label(plot_widget)

    def _clear_hover_data(self):
        """Eski session verisine ait hover bilgisini temizler."""
        self._hover_points.clear()
        self._hover_state.clear()
        self._event_marker_points.clear()
        self._vline_hover_points.clear()
        for label in self._hover_labels.values():
            if label:
                label.hide()
        self._hover_labels.clear()

    def _ensure_hover_label(self, plot_widget):
        """Grafik viewport'u üzerinde opak hover etiketini oluşturur."""
        if not plot_widget or plot_widget in self._hover_labels:
            return

        label = QLabel(plot_widget.viewport())
        label.setStyleSheet(
            "background-color: rgb(255, 255, 225);"
            "color: rgb(0, 0, 0);"
            "border: 1px solid rgb(120, 120, 120);"
            "padding: 4px 6px;"
        )
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setWordWrap(False)
        label.adjustSize()
        label.hide()
        label.raise_()
        self._hover_labels[plot_widget] = label

    def _handle_plot_hover(self, plot_widget, evt):
        """Fare konumuna en yakın veri noktasını bulup sabit etiketi günceller."""
        if not plot_widget:
            return

        pos = evt[0] if isinstance(evt, tuple) else evt
        view_box = plot_widget.getViewBox()
        plot_rect = view_box.sceneBoundingRect()

        if not plot_rect.contains(pos):
            self._hide_hover_label(plot_widget)
            return

        marker_data = self._find_nearest_event_marker(plot_widget, pos)
        if marker_data is not None:
            marker_id = f"marker:{id(marker_data['dot'])}"
            label_text = self._format_marker_hover_text(marker_data)
            self._show_hover_label(plot_widget, marker_id, label_text, pos)
            return

        vline_data = self._find_nearest_vline(plot_widget, pos)
        if vline_data is not None:
            label_text = self._format_vline_hover_text(vline_data)
            vline_id = f"vline:{id(vline_data)}"
            self._show_hover_label(plot_widget, vline_id, label_text, pos)
            return

        point_data = self._hover_points.get(plot_widget)
        if not point_data or not point_data["x"]:
            self._hide_hover_label(plot_widget)
            return

        nearest_index = self._find_nearest_point_index(plot_widget, pos, point_data)
        if nearest_index is None:
            self._hide_hover_label(plot_widget)
            return

        x_value = point_data["x"][nearest_index]
        y_value = point_data["y"][nearest_index]
        label_text = self._format_data_point_hover_text(
            x_value,
            y_value,
            point_data.get("x_kind", "time"),
        )
        self._show_hover_label(plot_widget, nearest_index, label_text, pos)

    def _show_hover_label(self, plot_widget, point_key,
                          label_text: str, scene_pos):
        """Ayni noktadaysa etiketi yeniden çizmeden görünür tutar."""
        label = self._hover_labels.get(plot_widget)
        state = self._hover_state.get(plot_widget)
        if not label or state is None:
            return

        state_changed = state["index"] != point_key or state["text"] != label_text
        if state_changed:
            label.setText(label_text.replace("\n", "<br>"))
            label.adjustSize()
            state["index"] = point_key
            state["text"] = label_text

        label_x, label_y = self._calc_hover_label_pos(plot_widget, scene_pos, label)
        label.move(label_x, label_y)
        if not label.isVisible():
            label.show()

    def _hide_hover_label(self, plot_widget):
        """Hover etiketi görünmeyecekse gizler."""
        label = self._hover_labels.get(plot_widget)
        state = self._hover_state.get(plot_widget)
        if label:
            label.hide()
        if state is not None:
            state["index"] = None
            state["text"] = None

    def _calc_hover_label_pos(self, plot_widget, scene_pos, label):
        """Hover etiketini viewport sınırları içinde konumlar."""
        viewport = plot_widget.viewport()
        cursor_pos = plot_widget.mapFromScene(scene_pos)

        offset_x = 14
        offset_y = -14
        x_pos = cursor_pos.x() + offset_x
        y_pos = cursor_pos.y() + offset_y - label.height()

        max_x = max(0, viewport.width() - label.width())
        max_y = max(0, viewport.height() - label.height())
        x_pos = max(0, min(x_pos, max_x))
        y_pos = max(0, min(y_pos, max_y))
        return x_pos, y_pos

    def _find_nearest_point_index(self, plot_widget, scene_pos, point_data):
        """Piksel eşiği içindeki en yakın veri noktasının indeksini döner."""
        x_values = point_data["x"]
        y_values = point_data["y"]
        if not x_values:
            return None

        mouse_point = plot_widget.getViewBox().mapSceneToView(scene_pos)
        if point_data.get("sorted", True):
            insert_at = bisect_left(x_values, mouse_point.x())
            candidate_start = max(0, insert_at - 3)
            candidate_end = min(len(x_values), insert_at + 3)
        else:
            candidate_start = 0
            candidate_end = len(x_values)

        best_index = None
        best_distance = None

        for idx in range(candidate_start, candidate_end):
            scene_point = plot_widget.getViewBox().mapViewToScene(
                pg.Point(x_values[idx], y_values[idx])
            )
            distance = (scene_point.x() - scene_pos.x()) ** 2 + (
                scene_point.y() - scene_pos.y()
            ) ** 2
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = idx

        if best_distance is None:
            return None
        if best_distance > _HOVER_DISTANCE_PX ** 2:
            return None
        return best_index

    def _find_nearest_event_marker(self, plot_widget, scene_pos):
        """Hover mesafesindeki en yakin event marker'ini doner."""
        best_item = None
        best_distance = None
        view_box = plot_widget.getViewBox()

        for item in self._event_marker_points:
            if item["plot"] is not plot_widget or not item.get("label"):
                continue
            scene_point = view_box.mapViewToScene(
                pg.Point(item["x"], item.get("y", 0.0))
            )
            distance = (scene_point.x() - scene_pos.x()) ** 2 + (
                scene_point.y() - scene_pos.y()
            ) ** 2
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_item = item

        if best_distance is None or best_distance > _HOVER_DISTANCE_PX ** 2:
            return None
        return best_item

    def _find_nearest_vline(self, plot_widget, scene_pos):
        """Fareye yatayda yakin olan dikey cizgiyi doner."""
        best_item = None
        best_distance = None
        view_box = plot_widget.getViewBox()
        mouse_point = view_box.mapSceneToView(scene_pos)

        for item in self._vline_hover_points:
            if item["plot"] is not plot_widget:
                continue
            scene_point = view_box.mapViewToScene(
                pg.Point(item["x"], mouse_point.y())
            )
            distance = abs(scene_point.x() - scene_pos.x())
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_item = item

        if best_distance is None or best_distance > _HOVER_DISTANCE_PX:
            return None
        return best_item

    def _format_data_point_hover_text(
        self,
        timestamp: float,
        current_a: float,
        x_kind: str = "time",
    ) -> str:
        """Measurement point hover bilgisini ortak iki satirli forma cevirir."""
        if x_kind == "potential":
            return (
                f"Potential: {timestamp:.4f} V\n"
                f"Current: {current_a / _CURRENT_UA_TO_A:.3f} uA"
            )
        if x_kind == "seconds":
            return (
                f"Time: {timestamp:.3f} s\n"
                f"Current: {current_a / _CURRENT_UA_TO_A:.3f} uA"
            )
        return self._format_hover_text({
            "timestamp": timestamp,
            "value": f"{current_a / _CURRENT_UA_TO_A:.3f} uA",
        })

    def _format_marker_hover_text(self, marker_data) -> str:
        """Event marker hover bilgisini ortak iki satirli forma cevirir."""
        return self._format_hover_text({
            "timestamp": marker_data["x"],
            "value": (marker_data.get("label") or "-").strip() or "-",
        })

    def _format_vline_hover_text(self, vline_data) -> str:
        """Dikey cizgi hover bilgisini ortak iki satirli forma cevirir."""
        return self._format_hover_text({
            "timestamp": vline_data["x"],
            "value": (vline_data.get("label") or "-").strip() or "-",
        })

    def _format_hover_text(self, payload: dict) -> str:
        """Hover payload'ini standart Time/Value metnine donusturur."""
        ts_text = datetime.fromtimestamp(payload["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
#        return f"Time: {ts_text}\nValue: {payload['value']}"
        return f"Value: {payload['value']}\nTime: {ts_text}"

    def _on_session_state_changed(self, state: str):
        """MainWindow state_changed bağlantısı için."""
        state_norm = (state or "").strip().lower()
        mw = self._get_main_window()
        ctx = getattr(mw, "_ctx", None) if mw is not None else None

        if state_norm == "running":
            if ctx is not None and ctx.display_mode != "active":
                return
            if self._session:
                self.live_lbl.setText(f"⟳ Canlı mod ({POLL_INTERVAL_MS // 1000}sn)")
                self._refresh(reset_view=False)
            return
        if state_norm == "idle":
            if ctx is not None and ctx.display_mode != "active":
                return
            self._poll_timer.stop()
            self.live_lbl.setText("")
            self._refresh()

    def clear(self):
        """ViewerTab'ı açılış haline getirir."""
        self._session = None
        self._history_sessions = []
        self._dropsens_pad = None
        self._parse_result = None
        self._marker_items.clear()
        self._measure_dots.clear()
        self._event_marker_points.clear()
        self._vline_hover_points.clear()
        self._measure_range_hooks.clear()
        self._saved_top_view_range = None
        self._manual_top_view_active = False
        self._viewer_mode = "pad"
        self._viewer_mode_user_selected = False
        self._sync_view_mode_controls(has_pad=False, has_cv=False)
        self._apply_graph_layout_for_mode("pad")

        self._poll_timer.stop()
        self.live_lbl.setText("")
        self.session_lbl.setText("Oturum yüklenmedi")
        self.session_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self.meta_lbl.setText("—")
        self.notes_edit.clear()
        self.sys_edit.clear()
        self.delete_btn.setEnabled(False)

        self._set_plot_titles("")

        if self.plot_widget_top:
            self.plot_widget_top.clear()
            if getattr(self, "legend", None):
                self.legend.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._apply_empty_time_axis()
