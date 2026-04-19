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

_C_MEASURE_LINE = (33, 150, 243)

POLL_INTERVAL_MS = 10_000   # 10 saniye


class ViewerTab(QWidget):
    log_signal     = pyqtSignal(str)
    session_loaded = pyqtSignal(str)
    delay_changed  = pyqtSignal(int, int)   # (minutes, seconds)

    def __init__(self, package_tab=None):
        super().__init__()
        self.package_tab   = package_tab
        self._session      : Optional[MeasurementSession] = None
        self._parse_result : Optional[ParseResult]        = None
        self._marker_items : list = []   # grafikteki marker öğeleri

        self._measure_dots : list = []
        self._measure_range_hooks: dict = {}
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
        self._load_session(session)

    def _browse_session(self):
        """Geçmiş session dizinini kullanıcı seçer."""
        path = open_dir(self, "Session Dizini Seç", "browse_session")
        if not path:
            return

        # session.json var mı?
        json_path = os.path.join(path, "session.json")
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

    # ── Yenileme ──────────────────────────────────────────────────

    def _refresh(self, reset_view: bool = False):
        """Veriyi yeniden okur ve grafiği günceller."""
        if not self._session:
            return

        # Session durumunu diskten yenile (başka process güncelliyor olabilir)
        try:
            json_path = os.path.join(self._session.session_dir, "session.json")
            if os.path.isfile(json_path):
                refreshed = MeasurementSession.load(self._session.session_dir)
                self._session = refreshed
        except Exception:
            pass

        self._update_meta()
        self._load_log()
        self._plot_data(reset_view=reset_view)

    def _update_meta(self):
        """Session meta bilgilerini annotation paneline yazar."""
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
        log_path = self._find_log()
        if not log_path:
            self._parse_result = None
            self.notes_edit.setPlainText("Log dosyası bulunamadı.")
            self.sys_edit.setPlainText("")
            return

        parser = LogParser()
        self._parse_result = parser.parse(log_path)

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

    # ── Grafik çizimi ─────────────────────────────────────────────

    def _plot_data(self, reset_view: bool = False):
        if not self.plot_widget_top:
            return

        preserved_top_range = None
        if not reset_view:
            preserved_top_range = self.plot_widget_top.getViewBox().viewRange()

        self.plot_widget_top.clear()
        if self.plot_widget_bottom:
            self.plot_widget_bottom.clear()
        self._marker_items.clear()
        self._measure_dots.clear()

        timestamps, currents = self._read_measurement_data()

        if not timestamps:
            self._show_no_data_msg()
            return

        # Ana seri — Current (uA) -> sadece point, çizgi yok
        self.top_curve = self.plot_widget_top.plot(
            timestamps,
            currents,
            pen=None,   # çizgi çizme
            symbol="o",
            symbolSize=5,
            symbolBrush=pg.mkBrush(_C_DATA_LINE),
            symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
            name="Current (uA)"
        )


        # ── Bottom grafik: downsample edilmiş veri ─────────────────
        if self.plot_widget_bottom:
            self.bottom_curve = self.plot_widget_bottom.plot(
                timestamps,
                currents,
                pen=None,  # çizgi çizme
                symbol="o",
                symbolSize=5,
                symbolBrush=pg.mkBrush(_C_DATA_LINE),
                symbolPen=pg.mkPen(color=_C_DATA_LINE, width=1),
                name="Mean Current (uA)"
            )

            # 🔥 Downsample (GUI'deki: 5x Subsample)
            self.bottom_curve.setDownsampling(
                ds=5,
                auto=False,
                method='mean'
            )

        # Marker çizgileri
        if self._parse_result:
            self._draw_markers(timestamps)

        # Measure dot'larını doğru konuma yerleştir.
        # Yeni session yüklenince auto-range uygulanır; normal refresh'te ise
        # kullanıcının mevcut zoom/pan görünümü korunur.
        top_view_box = self.plot_widget_top.getViewBox()
        if reset_view or preserved_top_range is None:
            top_view_box.enableAutoRange()
            top_view_box.autoRange()
        else:
            top_view_box.disableAutoRange()
            x_range, y_range = preserved_top_range
            top_view_box.setRange(xRange=x_range, yRange=y_range, padding=0)
        self._update_measure_dots_for_plot(self.plot_widget_top)
        if self.plot_widget_bottom:
            # Alt grafik Y eksenini ust grafikten linked olarak aliyor;
            # burada yeniden autoRange yaparsak ortak Y araligi alttaki
            # downsample edilmis gorunume gore yeniden hesaplanabiliyor.
            self.plot_widget_bottom.getViewBox().disableAutoRange(axis="y")
            self._update_measure_dots_for_plot(self.plot_widget_bottom)

    def _read_measurement_data(self):
        """
        xlsx varsa xlsx'ten, yoksa CSV'lerden okur.
        (timestamp_unix_list, current_list) döner.
        """
        # xlsx
        xlsx_path = self._session.get_file_path("xlsx")
        if not xlsx_path:
            # Oluşturulmuş xlsx'i measurements/ altında ara
            candidate = os.path.join(
                self._session.measurements_dir,
                f"{self._session.part_number}.xlsx")
            if os.path.isfile(candidate):
                xlsx_path = candidate

        if xlsx_path and os.path.isfile(xlsx_path) and _OPENPYXL_OK:
            return self._read_xlsx(xlsx_path)

        # xlsx yoksa CSV'lerden oku (canlı mod)
        return self._read_csvs()

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

    def _read_csvs(self):
        """CSV dosyalarından veri okur (canlı mod fallback)."""
        import glob
        from datetime import timedelta

        mdir = self._session.measurements_dir
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

    def _draw_markers(self, data_timestamps: list):
        """Step ve sistem marker çizgilerini grafiğe ekler."""
        if not data_timestamps:
            return

        t_min = min(data_timestamps)-120
        t_max = max(data_timestamps)

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
        """Step marker için kısa etiket: 'S3 P1 Load'"""
        parts = []
        if ev.port_a:
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

    def _show_no_data_msg(self):
        """Veri yoksa grafik alanına mesaj yazar."""
        if not self.plot_widget_top:
            return
        text = pg.TextItem(
            "Veri bulunamadı.\nÖnce Aggregator çalıştırın veya CSV bekleyin.",
            color=(150, 150, 150), anchor=(0.5, 0.5))
        self.plot_widget_top.addItem(text)
        text.setPos(0, 0)

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
