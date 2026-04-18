# bench_test/ui/tabs/script_params_panel.py
# -*- coding: utf-8 -*-
"""
ScriptParamsPanel
=================
.scr parametrelerini gösterir ve düzenler.
Repeat Count ve Wait Duration kullanıcı tarafından değiştirilebilir.
Method File ve CSV File otomatik olarak dışarıdan set edilir (salt-okunur).

Sinyaller:
    params_changed()  — herhangi bir parametre değiştiğinde yayar
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox,
    QGroupBox, QLabel
)
from PyQt6.QtCore import pyqtSignal, Qt

from bench_test.config import DEFAULT_REPEAT_COUNT, DEFAULT_WAIT_DURATION_SEC
from bench_test.ui.widgets import _btn


class ScriptParamsPanel(QWidget):
    params_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── Kullanıcı parametreleri ───────────────────────────
        user_grp = QGroupBox("Parameters")
        user_form = QFormLayout(user_grp)

        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 99999)
        self.repeat_spin.setValue(DEFAULT_REPEAT_COUNT)
        self.repeat_spin.valueChanged.connect(self.params_changed)
        user_form.addRow("Repeat Count:", self.repeat_spin)

        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(1.0, 3600.0)
        self.wait_spin.setDecimals(1)
        self.wait_spin.setValue(DEFAULT_WAIT_DURATION_SEC)
        self.wait_spin.setSuffix(" sn")
        self.wait_spin.valueChanged.connect(self.params_changed)
        user_form.addRow("Wait Duration:", self.wait_spin)

        layout.addWidget(user_grp)

        # ── Görüntüleyici ayarları (salt-okunur) ─────────────
        viewer_grp = QGroupBox("Viewer Settings")
        viewer_form = QFormLayout(viewer_grp)
        self.delay_lbl = QLabel("—")
        self.delay_lbl.setStyleSheet("color: #888; font-style: italic;")
        viewer_form.addRow("Response Delay:", self.delay_lbl)


        # ── Otomatik alanlar (salt-okunur) ────────────────────
        auto_grp = QGroupBox("Auto (read-only)")
        auto_form = QFormLayout(auto_grp)

        self.method_edit = QLineEdit()
        self.method_edit.setReadOnly(True)
        self.method_edit.setPlaceholderText("Method .tp yüklenince dolar")
        self.method_edit.setStyleSheet("color: #888; font-style: italic;")
        auto_form.addRow("Method File:", self.method_edit)

        self.csv_edit = QLineEdit()
        self.csv_edit.setReadOnly(True)
        self.csv_edit.setPlaceholderText("Part Number girilince dolar")
        self.csv_edit.setStyleSheet("color: #888; font-style: italic;")
        auto_form.addRow("CSV File:", self.csv_edit)

        layout.addWidget(auto_grp)
        layout.addWidget(viewer_grp)
        layout.addStretch()

    # ── Dışarıdan set ─────────────────────────────────────────────

    def set_method_path(self, path: str):
        """MethodEditorPanel'den tp_loaded sinyaliyle tetiklenir."""
        self.method_edit.setText(path)
        self.method_edit.setStyleSheet(
            "color: #4CAF50; font-style: normal;" if path
            else "color: #888; font-style: italic;"
        )

    def set_csv_path(self, path: str):
        """PackagePanel'den part_number/root değişince tetiklenir."""
        self.csv_edit.setText(path)
        self.csv_edit.setStyleSheet(
            "color: #4CAF50; font-style: normal;" if path
            else "color: #888; font-style: italic;"
        )

    # ── Dışa veri ─────────────────────────────────────────────────

    def get_scr_params(self) -> dict:
        """
        MeasurementSetupTab tarafından .scr üretmek için kullanılır.
        """
        return {
            "method_file":  self.method_edit.text().strip(),
            "output_csv":   self.csv_edit.text().strip(),
            "repeat_times": self.repeat_spin.value(),
            "wait_ms":      int(self.wait_spin.value() * 1000),
        }

    def is_ready(self) -> bool:
        """Method ve CSV yolu doluysa True."""
        return bool(self.method_edit.text()) and bool(self.csv_edit.text())

    def restore_from_scr(self, scr_path: str):
        """Geçmiş session .scr dosyasından repeat ve wait parametrelerini yükler."""
        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(scr_path)
            root = tree.getroot()
            for action in root.findall(".//action"):
                atype = action.get("type", "")
                if atype == "REPEAT":
                    times = action.findtext("times")
                    if times:
                        self.repeat_spin.setValue(int(times))
                elif atype == "WAIT":
                    time_ms = action.get("timeMS")
                    if time_ms:
                        self.wait_spin.setValue(float(time_ms) / 1000.0)
        except Exception:
            pass  # Parse hatası sessizce geçilir, mevcut değerler korunur

    def set_response_delay(self, minutes: int, seconds: int):
        """ViewerTab delay_changed sinyalinden güncellenir."""
        if minutes == 0 and seconds == 0:
            self.delay_lbl.setText("—")
            self.delay_lbl.setStyleSheet("color: #888; font-style: italic;")
        else:
            self.delay_lbl.setText(f"{minutes} min  {seconds} sec")
            self.delay_lbl.setStyleSheet("color: #4CAF50; font-style: normal;")

    def clear(self):
        """ScriptParamsPanel'i açılış haline getirir."""
        self.repeat_spin.setValue(DEFAULT_REPEAT_COUNT)
        self.wait_spin.setValue(DEFAULT_WAIT_DURATION_SEC)
        self.method_edit.clear()
        self.method_edit.setStyleSheet("color: #888; font-style: italic;")
        self.csv_edit.clear()
        self.csv_edit.setStyleSheet("color: #888; font-style: italic;")