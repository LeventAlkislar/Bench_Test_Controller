# bench_test/ui/tabs/viewer_tab.py
# -*- coding: utf-8 -*-
"""
ViewerTab
=========
Ölçüm verilerini (xlsx/CSV) grafik olarak gösterir.

Özellikler:
- xlsx veya CSV'den Current (uA) vs Time grafiği (pyqtgraph)
- Step geçişleri → turuncu dikey çizgi + etiket
- Sistem olayları (started/stopped/paused/resumed) → mavi/kırmızı çizgi
- Manuel notlar → sol annotation panelinde liste
- Zoom/Pan: fare tekerleği ve sürükleme (pyqtgraph built-in)
- Aktif session: PackageTab.get_session() ile otomatik yüklenir
- Geçmiş session: Browse butonu ile session dizini seçilir
- Canlı mod: session IN_PROGRESS iken 30sn'de bir yeniler
"""

import os
import shutil
from bisect import bisect_left
from datetime import datetime
from typing import Optional, List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QGroupBox, QLabel, QPushButton, QFileDialog,
    QTextEdit, QScrollArea, QFrame, QSizePolicy,
    QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor

try:
    import pyqtgraph as pg
    from pyqtgraph import DateAxisItem
    _PG_OK = True
except ImportError:
    _PG_OK = False

try:
    import openpyxl
    _OPENPYXL_OK = True
except ImportError:
    _OPENPYXL_OK = False

from bench_test.measurement.session import MeasurementSession, SessionStatus
from bench_test.measurement.aggregator import Aggregator, AggregatorError
from bench_test.measurement.log_parser import LogParser, ParseResult, StepEvent, SystemEvent
from bench_test.ui.widgets import _btn
from bench_test.utils.paths import open_dir, get_value, remember_value

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
    "stopped" : _C_STOPPED,
    "error"   : _C_STOPPED,
    "paused"  : _C_PAUSED,
    "resumed" : _C_PAUSED,
}

_PORT_COLORS = {
    1: (255, 100, 100, 40),
    2: (100, 200, 100, 40),
    3: (100, 150, 255, 40),
    4: (255, 200, 80, 40),
    5: (200, 100, 255, 40),
    6: (80, 220, 220, 40),
    7: (255, 140, 60, 40),
    8: (180, 180, 180, 40),
}
_PORT_COLOR_DEFAULT = (160, 160, 160, 30)

_C_MEASURE_LINE = (33, 150, 243)
_HOVER_DISTANCE_PX = 14

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
        self._parse_result : Optional[ParseResult]        = None
        self._marker_items : list = []   # grafikteki marker öğeleri

        self._measure_dots : list = []
        self._measure_range_hooks: dict = {}
        self._saved_top_view_range: Optional[dict] = None
        self._manual_top_view_active = False
        self._applying_top_view_state = False
        self._top_view_tracking_connected = False
        self._hover_hooks: dict = {}
        self._hover_points: dict = {}
        self._hover_labels: dict = {}
        self._hover_state: dict = {}
        self.plot_widget_top = None
        self.plot_widget_bottom = None

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
        toolbar.addWidget(_btn("Refresh",             self._refresh, "#4CAF50"))
        toolbar.addWidget(_btn("Load Session",     self._browse_session,       "#2196F3"))

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

        # Zaman ekseni için DateAxisItem
        date_axis = DateAxisItem(orientation="bottom")
        self.plot_widget_top = pg.PlotWidget(axisItems={"bottom": date_axis})
        self.plot_widget_top.setLabel("left",   "Current", units="uA")
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
        layout.addWidget(top_group)

        # ── Bottom GroupBox (panel) ──────────────────────
        bottom_group = QGroupBox("Mean Measurement Timeline")
        bottom_group_layout = QVBoxLayout(bottom_group)
        bottom_group_layout.setContentsMargins(10, 10, 10, 10)

        bottom_date_axis = DateAxisItem(orientation="bottom")
        self.plot_widget_bottom = pg.PlotWidget(
            axisItems={"bottom": bottom_date_axis}
        )
        self.plot_widget_bottom.setLabel("left", "Current", units="uA")
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

        bottom_group_layout.addWidget(self.plot_widget_bottom)
        layout.addWidget(bottom_group)

        layout.addStretch(1)
        return container

    # ── Tepki gecikmesi ───────────────────────────────────────────

    def _get_offset_sec(self) -> float:
        """Kullanıcının girdiği toplam gecikmeyi saniye cinsinden döner."""
        return self._delay_min_spin.value() * 60.0 + self._delay_sec_spin.value()

    def _on_delay_changed(self):
        """Spinbox değişince kaydet, sinyal yay, grafiği yenile."""
        remember_value("response_delay_min", self._delay_min_spin.value())
        remember_value("response_delay_sec", self._delay_sec_spin.value())
        self.delay_changed.emit(self._delay_min_spin.value(), self._delay_sec_spin.value())
        if self._session:
            self._refresh()

    # ── Zamanlayıcı ───────────────────────────────────────────────
    def _setup_poll_timer(self):
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll)

    def _on_poll(self):
        """10sn'de bir: session IN_PROGRESS ise aggregate çalıştır, sonra grafiği güncelle."""
        if not self._session:
            return
        if self._session.status != SessionStatus.IN_PROGRESS:
            return

        # CSV varsa silent aggregate — yoksa (henüz ölçüm başlamadı) sessizce atla
        try:
            agg = Aggregator(self._session)
            agg.run_silent(offset_hours=0.0)
        except AggregatorError:
            pass  # CSV yok veya okunamadı — grafik güncellemesini atla, bir sonraki döngüde tekrar dene
            return
        except Exception:
            return

        # Aggregate başarılı — grafiği güncelle
        self._refresh(reset_view=False)

    # ── Session yükleme ───────────────────────────────────────────

    def _load_active_session(self):
        """PackageTab'dan aktif session'ı al."""
        if not self.package_tab:
            QMessageBox.warning(self, "Viewer", "PackageTab bağlı değil.")
            return
        session = self.package_tab.get_session()
        if not session:
            QMessageBox.information(self, "Viewer",
                "Aktif oturum yok.\nÖnce bir recipe başlatın.")
            return
        mw = self._get_main_window()
        if mw:
            mw.switch_display(session, "active")
            return
        self._load_session(session)

    def _get_main_window(self):
        """MainWindow referansini parent zincirinden bul."""
        w = self.parent()
        while w:
            if hasattr(w, "switch_display"):
                return w
            w = w.parent() if hasattr(w, "parent") else None
        return None

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

        # session.json var mı?
        json_path = os.path.join(path, "session.json")
        if not os.path.isfile(json_path):
            history_sessions = self._find_history_sessions(path)
            if not history_sessions:
                QMessageBox.warning(self, "Viewer",
                    "SeÃ§ilen dizin geÃ§erli bir session veya part klasÃ¶rÃ¼ deÄŸil.\n"
                    "LÃ¼tfen session.json iÃ§eren bir session dizini veya altÄ±nda "
                    "session klasÃ¶rleri bulunan bir part dizini seÃ§in.")
                return

            mw = self._get_main_window()
            if mw:
                mw.switch_display(None, "history", history_sessions=history_sessions)
                return
            self.render_history(history_sessions)
            return
        if not os.path.isfile(json_path):
            QMessageBox.warning(self, "Viewer",
                "Seçilen dizinde session.json bulunamadı.\n"
                "Lütfen geçerli bir session dizini seçin.")
            return

        try:
            session = MeasurementSession.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Viewer",
                f"Session yüklenemedi:\n{e}")
            return

        self._load_session(session)
        self.session_loaded.emit(str(session.session_dir))

    def _load_session(self, session: MeasurementSession):
        """Session nesnesini set eder ve grafiği yeniler."""
        self._session = session
        self._history_sessions = []

        # Canlı mod: sadece IN_PROGRESS iken
        if session.status == SessionStatus.IN_PROGRESS:
            self._poll_timer.start()
            self.live_lbl.setText("⟳ Canlı mod (30sn)")
            self.delete_btn.setEnabled(False)   # Çalışan session silinemez
        else:
            self._poll_timer.stop()
            self.live_lbl.setText("")
            self.delete_btn.setEnabled(True)

        self._refresh(reset_view=True)

    def render_history(self, sessions: List[MeasurementSession]) -> None:
        """Bir part altindaki tum session verilerini yukler."""
        self._session = None
        self._history_sessions = list(sessions)
        self._poll_timer.stop()
        self.live_lbl.setText("")
        self.delete_btn.setEnabled(False)
        self._refresh(reset_view=True)

    # ── Yenileme ──────────────────────────────────────────────────

    def render_session(self, session: MeasurementSession, live: bool = False) -> None:
        """
        MainWindow.switch_display() tarafindan cagrilir.
        live=True ise poll_timer baslatilir.
        """
        self._load_session(session)
        if live and not self._poll_timer.isActive():
            self._poll_timer.start()
            self.live_lbl.setText(f"⟳ Canlı mod ({POLL_INTERVAL_MS // 1000}sn)")

    def _refresh(self, reset_view: bool = False):
        """Veriyi yeniden okur ve grafiği günceller."""
        if not self._session and not self._history_sessions:
            return

        # Session durumunu diskten yenile (başka process güncelliyor olabilir)
        try:
            json_path = os.path.join(self._session.session_dir, "session.json")
            if os.path.isfile(json_path):
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

        self._update_meta()
        self._load_log()
        self._plot_data(reset_view=reset_view)

    def _update_meta(self):
        """Session meta bilgilerini annotation paneline yazar."""
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
                f"{first.part_number}  [history: {len(self._history_sessions)}]"
            )
            return

        s = self._session
        created = s.created_at.strftime("%Y-%m-%d %H:%M:%S")
        status_color = {
            "completed"  : "#4CAF50",
            "in_progress": "#2196F3",
            "aborted"    : "#F44336",
            "error"      : "#F44336",
            "pending"    : "#888888",
        }.get(s.status.value, "#888888")

        self.meta_lbl.setText(
            f"<b>Part:</b> {s.part_number}<br>"
            f"<b>Created:</b> {created}<br>"
            f"<b>Status:</b> <span style='color:{status_color}'>"
            f"{s.status.value}</span><br>"
            f"<b>Dir:</b> <small>{s.session_dir}</small>"
        )

        name = f"{s.part_number}  [{s.status.value}]"
        self.session_lbl.setText(name)

    def _load_log(self):
        """Log dosyasını parse eder, annotation panellerini doldurur."""
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

    def _plot_data(self, reset_view: bool = False):
        if not self.plot_widget_top:
            return

        if reset_view:
            self._manual_top_view_active = False
            self._saved_top_view_range = None
        elif self._saved_top_view_range is None:
            self._save_current_top_view_range()

        self.plot_widget_top.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._marker_items.clear()
        self._measure_dots.clear()
        self._clear_hover_data()

        series_data = self._read_measurement_series()
        if not series_data:
            self._show_no_data_msg()
            return

        timestamps = []
        currents = []
        bottom_timestamps = []
        bottom_currents = []

        for _, series_timestamps, series_currents in series_data:
            timestamps.extend(series_timestamps)
            currents.extend(series_currents)
            mean_ts, mean_cur = self._build_mean_series(
                series_timestamps,
                series_currents,
                group_size=5,
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

        # Ana seri — Current (uA) -> sadece point, çizgi yok
        for label, series_timestamps, series_currents in series_data:
            self.plot_widget_top.plot(
                series_timestamps,
                series_currents,
                pen=None,
                symbol="o",
                symbolSize=5,
                symbolBrush=pg.mkBrush(_C_DATA_LINE),
                symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
                name=None,
            )

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


        # ── Bottom grafik: downsample edilmiş veri ─────────────────
        if self.plot_widget_bottom:
            for label, series_timestamps, series_currents in series_data:
                mean_ts, mean_cur = self._build_mean_series(
                    series_timestamps,
                    series_currents,
                    group_size=5,
                )
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

        # Marker çizgileri
        if self._parse_result:
            self._draw_markers(timestamps)

        # Measure dot'larını doğru konuma yerleştir.
        # Yeni session yüklenince auto-range uygulanır; normal refresh'te ise
        # kullanıcının mevcut zoom/pan görünümü korunur.
        if reset_view or self._saved_top_view_range is None:
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

    def _read_measurement_series(self):
        """Tek session veya history modu icin olcum serilerini okur."""
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
                all_series.append((label, timestamps, currents))
            return all_series

        timestamps, currents = self._read_session_measurement_data(self._session)
        if not timestamps:
            return []
        return [("Current (uA)", timestamps, currents)]

    def _read_measurement_data(self):
        """
        xlsx varsa xlsx'ten, yoksa CSV'lerden okur.
        (timestamp_unix_list, current_list) döner.
        """
        return self._read_session_measurement_data(self._session)

    def _read_session_measurement_data(self, session: MeasurementSession):
        # xlsx
        xlsx_path = session.get_file_path("xlsx")
        if not xlsx_path:
            # Oluşturulmuş xlsx'i measurements/ altında ara
            candidate = os.path.join(
                session.measurements_dir,
                f"{session.part_number}.xlsx")
            if os.path.isfile(candidate):
                xlsx_path = candidate

        if xlsx_path and os.path.isfile(xlsx_path) and _OPENPYXL_OK:
            return self._read_xlsx(xlsx_path)

        # xlsx yoksa CSV'lerden oku (canlı mod)
        return self._read_csvs(session)

    def _build_mean_series(self, timestamps, currents, group_size: int = 5):
        """Alt grafik için grup ortalamalı seri üretir."""
        if not timestamps or not currents or group_size <= 1:
            return list(timestamps), list(currents)

        mean_timestamps = []
        mean_currents = []
        total = min(len(timestamps), len(currents))

        for start in range(0, total, group_size):
            chunk_times = timestamps[start:start + group_size]
            chunk_currents = currents[start:start + group_size]
            if not chunk_times or not chunk_currents:
                continue
            mean_timestamps.append(sum(chunk_times) / len(chunk_times))
            mean_currents.append(sum(chunk_currents) / len(chunk_currents))

        return mean_timestamps, mean_currents

    def _read_xlsx(self, path: str):
        """xlsx'ten Time/Current kolonlarını okur."""
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            timestamps, currents = [], []
            for row in ws.iter_rows(min_row=2, values_only=True):
                ts_val, cur_val = row[0], row[1]
                if ts_val is None or cur_val is None:
                    continue
                # openpyxl datetime → unix timestamp
                if isinstance(ts_val, datetime):
                    timestamps.append(ts_val.timestamp())
                    currents.append(float(cur_val))
            wb.close()
            return timestamps, currents
        except Exception as e:
            self.log_signal.emit(f"Viewer xlsx okuma hatası: {e}")
            return [], []

    def _read_csvs(self, session: MeasurementSession):
        """CSV dosyalarından veri okur (canlı mod fallback)."""
        import glob
        from datetime import timedelta

        mdir = session.measurements_dir
        files = sorted(glob.glob(os.path.join(mdir, "*.csv")),
                       key=lambda p: os.path.getctime(p))
        if not files:
            return [], []

        timestamps, currents = [], []
        for path in files:
            try:
                ctime = datetime.fromtimestamp(os.path.getctime(path))
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                count = 0
                for line in lines[3:]:   # CSV_DATA_START = 3
                    if count >= 5:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(";")
                    if len(parts) < 2:
                        continue
                    try:
                        t_s = float(parts[0].strip().strip('"'))
                        cur = float(parts[1].strip().strip('"'))
                    except ValueError:
                        continue
                    ts = ctime + timedelta(seconds=t_s)
                    timestamps.append(ts.timestamp())
                    currents.append(cur)
                    count += 1
            except Exception:
                continue

        return timestamps, currents

    def _draw_glucose_regions(self, data_timestamps: list):
        """Port A step geÃ§iÅŸleri arasÄ±ndaki bÃ¶lgeleri porta gÃ¶re renklendirir."""
        if not self._parse_result or not data_timestamps:
            return

        t_max = max(data_timestamps)
        offset = self._get_offset_sec()

        # Sadece port_a iÃ§eren step event'leri zamana gÃ¶re sÄ±rala
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

            if t_end <= t_start:
                continue

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

        t_min = min(data_timestamps)-120
        t_max = max(data_timestamps)
        self._draw_glucose_regions(data_timestamps)

        # Step marker'ları
        offset = self._get_offset_sec()
        for ev in self._parse_result.step_events:
            t_raw = ev.timestamp.timestamp()
            if not (t_min <= t_raw <= t_max):
                continue

            if getattr(ev, "marker_kind", "step") == "measure":
                self._add_measure_marker(
                    t_raw,  # offset yok
                    is_start=(getattr(ev, "marker_label", "") == "Start Measure"),
                )
                continue

            t = t_raw + offset

            label = self._step_label(ev)

            # STEP: Sx Px içerenler
            if label.startswith("S") and " P" in label:
                color = _C_STEP_LINE  # kırmızı
                width = 1  # ince

            # RESET: diğer step eventler
            else:
                color = _C_RESET_LINE  # turuncu
                width = 3  # kalın

            # Port/Load-Inject önceliği label içeriğinden değil event alanlarından gelsin.
            if ev.port_a is not None:
                color = _C_STEP_LINE
                width = 3
            elif ev.valve_b is not None:
                color = _C_RESET_LINE
                width = 1
            else:
                color = _C_RESET_LINE
                width = 3

            self._add_vline(t, color, label, width=width)

        # Sistem event marker'ları
        for ev in self._parse_result.system_events:
            t = ev.timestamp.timestamp()
            if not (t_min <= t <= t_max):
                continue
            color = _SYSTEM_COLORS.get(ev.event_type, _C_PAUSED)
            self._add_vline(t, color, ev.event_type.upper(), dashed=True)

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
                "fill": _C_BG,
                "border": pg.mkPen(color=_C_BG, width=1),
                "movable": False,
            })
        self.plot_widget_top.addItem(line_top)
        self._marker_items.append(line_top)

        # Alt grafik çizgisi
        if self.plot_widget_bottom:
            line_bottom = pg.InfiniteLine(
                pos=x, angle=90, pen=pen, movable=False, label=label,
                labelOpts={
                    "position": 0.97,
                    "color": color,
                    "fill": _C_BG,
                    "border": pg.mkPen(color=_C_BG, width=1),
                    "movable": False,
                })
            self.plot_widget_bottom.addItem(line_bottom)
            self._marker_items.append(line_bottom)

    def _add_measure_marker(self, x: float, is_start: bool):
        """Measure event'lerinde sadece nokta cizer."""
        marker_color = _C_STARTED if is_start else _C_STOPPED

        def _draw_on(plot_widget):
            if not plot_widget:
                return
            dot = pg.ScatterPlotItem(
                [x], [0.0],
                size=10,
                pen=pg.mkPen(marker_color, width=1),
                brush=pg.mkBrush(marker_color),
            )
            plot_widget.addItem(dot)
            self._marker_items.append(dot)
            self._measure_dots.append({"plot": plot_widget, "dot": dot, "x": x})
            self._ensure_measure_dot_tracking(plot_widget)
            self._update_measure_dots_for_plot(plot_widget)

        _draw_on(self.plot_widget_top)
        _draw_on(self.plot_widget_bottom)

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
            if self._applying_top_view_state:
                return
            self._save_current_top_view_range(manual=True)

        vb.sigRangeChanged.connect(_on_top_range_changed)
        self._top_view_tracking_connected = True

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

    def _step_label(self, ev: StepEvent) -> str:
        """Step marker için kısa etiket: 'S3 50.0 mg/dL Load'"""
        parts = []
        if ev.port_a:
            glucose_map = get_value("port_glucose", {})
            mg = glucose_map.get(str(ev.port_a))
            if mg is not None:
                parts.append(f"{mg} mg/dL")
            else:
                parts.append(f"P{ev.port_a}")
        if ev.valve_b:
            parts.append(ev.valve_b)
        if ev.loop_info:
            parts.append(ev.loop_info)
        return " ".join(parts) if parts else "STEP"

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
        self.session_lbl.setText("Oturum silindi")
        self.meta_lbl.setText("—")
        self.notes_edit.clear()
        self.sys_edit.clear()
        self.delete_btn.setEnabled(False)
        if self.plot_widget_top:
            self.plot_widget_top.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._clear_hover_data()

    def _show_no_data_msg(self):
        """Veri yoksa grafik alanına mesaj yazar."""
        if not self.plot_widget_top:
            return
        text = pg.TextItem(
            "Veri bulunamadı.\nÖnce Aggregator çalıştırın veya CSV bekleyin.",
            color=(150, 150, 150), anchor=(0.5, 0.5))
        self.plot_widget_top.addItem(text)
        text.setPos(0, 0)

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

    def _set_hover_data(self, plot_widget, timestamps, currents):
        """Hover için kullanılacak veri serisini saklar."""
        if not plot_widget:
            return
        self._hover_points[plot_widget] = {
            "x": list(timestamps),
            "y": list(currents),
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
        ts_text = datetime.fromtimestamp(x_value).strftime("%Y-%m-%d %H:%M:%S")
        label_text = f"Time: {ts_text}\nCurrent: {y_value:.3f} uA"
        self._show_hover_label(plot_widget, nearest_index, label_text, pos)

    def _show_hover_label(self, plot_widget, point_index: int,
                          label_text: str, scene_pos):
        """Ayni noktadaysa etiketi yeniden çizmeden görünür tutar."""
        label = self._hover_labels.get(plot_widget)
        state = self._hover_state.get(plot_widget)
        if not label or state is None:
            return

        state_changed = state["index"] != point_index or state["text"] != label_text
        if state_changed:
            label.setText(label_text.replace("\n", "<br>"))
            label.adjustSize()
            state["index"] = point_index
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
        insert_at = bisect_left(x_values, mouse_point.x())

        best_index = None
        best_distance = None
        candidate_start = max(0, insert_at - 3)
        candidate_end = min(len(x_values), insert_at + 3)

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

    def _on_session_state_changed(self, state: str):
        """MainWindow state_changed bağlantısı için."""
        state_norm = (state or "").strip().lower()
        if state_norm == "running":
            if self._session:
                self._poll_timer.start()
                self.live_lbl.setText(f"⟳ Canlı mod ({POLL_INTERVAL_MS // 1000}sn)")
            return
        if state_norm == "idle":
            self._poll_timer.stop()
            self.live_lbl.setText("")
            self._refresh()

    def clear(self):
        """ViewerTab'ı açılış haline getirir."""
        self._session = None
        self._parse_result = None
        self._marker_items.clear()
        self._measure_dots.clear()
        self._measure_range_hooks.clear()
        self._saved_top_view_range = None
        self._manual_top_view_active = False

        self._poll_timer.stop()
        self.live_lbl.setText("")
        self.session_lbl.setText("Oturum yüklenmedi")
        self.session_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self.meta_lbl.setText("—")
        self.notes_edit.clear()
        self.sys_edit.clear()
        self.delete_btn.setEnabled(False)

        if self.plot_widget_top:
            self.plot_widget_top.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
