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

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox,
    QGroupBox, QLabel
)
from PyQt6.QtCore import pyqtSignal, Qt

from bench_test.config import DEFAULT_REPEAT_COUNT, DEFAULT_WAIT_DURATION_SEC
from bench_test.ui.widgets import _btn
from bench_test.utils.paths import get_value, remember_value


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
        params_grp = QGroupBox("Script Parameters")
        params_form = QFormLayout(params_grp)

        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 99999)
        self.repeat_spin.setValue(get_value("repeat_count", DEFAULT_REPEAT_COUNT))
        self.repeat_spin.valueChanged.connect(self.params_changed)
        self.repeat_spin.valueChanged.connect(
            lambda v: remember_value("repeat_count", v)
        )
        params_form.addRow("Repeat Count:", self.repeat_spin)

        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(1.0, 3600.0)
        self.wait_spin.setDecimals(1)
        self.wait_spin.setValue(
            get_value("wait_duration_sec", DEFAULT_WAIT_DURATION_SEC)
        )
        self.wait_spin.setSuffix(" sn")
        self.wait_spin.valueChanged.connect(self.params_changed)
        self.wait_spin.valueChanged.connect(
            lambda v: remember_value("wait_duration_sec", v)
        )
        params_form.addRow("Wait Duration:", self.wait_spin)




        # ── Otomatik alanlar (salt-okunur) ────────────────────
        self.method_edit = QLineEdit()
        self.method_edit.setReadOnly(True)
        self.method_edit.setPlaceholderText("Auto-filled when method is loaded")
        self.method_edit.setStyleSheet("color: #888; font-style: italic;")
        params_form.addRow("Method File:", self.method_edit)

        self.csv_edit = QLineEdit()
        self.csv_edit.setReadOnly(True)
        self.csv_edit.setPlaceholderText("Auto-filled when Part Number is entered")
        self.csv_edit.setStyleSheet("color: #888; font-style: italic;")
        params_form.addRow("CSV File:", self.csv_edit)

        layout.addWidget(params_grp)

        # ── Deney koşulları ───────────────────────────────────
        exp_grp = QGroupBox("Experiment Conditions")
        exp_form = QFormLayout(exp_grp)

        # ── Sıcaklık ayarları ─────────────
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 50.0)
        self.temp_spin.setDecimals(1)
        self.temp_spin.setSuffix(" °C")
        self.temp_spin.setValue(get_value("temperature_c", 25.0))
        self.temp_spin.valueChanged.connect(
            lambda v: remember_value("temperature_c", v)
        )
        exp_form.addRow("Temperature:", self.temp_spin)

        # ── Akış hızı  ayarları ─────────────
        self.flow_spin = QDoubleSpinBox()
        self.flow_spin.setRange(0.0, 100.0)
        self.flow_spin.setDecimals(1)
        self.flow_spin.setSuffix(" mL/min")
        self.flow_spin.setValue(get_value("flow_rate_ml_min", 0.0))
        self.flow_spin.valueChanged.connect(
            lambda v: remember_value("flow_rate_ml_min", v)
        )
        exp_form.addRow("Flow Rate:", self.flow_spin)

        # ── Görüntüleyici ayarları (salt-okunur) ─────────────
        self.delay_lbl = QLabel("—")
        self.delay_lbl.setStyleSheet("color: #888; font-style: italic;")
        exp_form.addRow("Response Delay:", self.delay_lbl)


        layout.addWidget(exp_grp)

        # ── Port - Glikoz Eşlemesi ────────────────────────────
        glucose_grp = QGroupBox("Port – Glucose Mapping")
        glucose_outer = QHBoxLayout(glucose_grp)
        glucose_outer.setSpacing(10)

        default_glucose = {
            "1": 0,
            "2": 40,
            "3": 65,
            "4": 125,
            "5": 215,
            "6": 300,
            "7": 400,
            "8": -1,
        }
        had_saved_glucose = get_value("port_glucose", None) is not None
        saved_glucose = get_value("port_glucose", default_glucose)
        self._glucose_spins: dict[int, QDoubleSpinBox] = {}
        for col_ports in [(1, 2, 3, 4), (5, 6, 7, 8)]:
            col_form = QFormLayout()
            for port in col_ports:
                saved_val = saved_glucose.get(str(port))
                spin = QDoubleSpinBox()
                spin.setRange(-1.0, 1000.0)
                spin.setDecimals(0)
                spin.setSpecialValueText("—")
                spin.setMinimum(-1.0)
                spin.setSuffix(" mg/dL")
                if saved_val is None or saved_val < 0:
                    spin.setValue(-1.0)
                else:
                    spin.setValue(float(saved_val))
                spin.valueChanged.connect(self._save_glucose_map)
                col_form.addRow(f"Port {port}:", spin)
                self._glucose_spins[port] = spin
            glucose_outer.addLayout(col_form)

        if not had_saved_glucose:
            self._save_glucose_map()

        layout.addWidget(glucose_grp)
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

    def restore_from_scr(self, scr_path: str, log_fn=None):
        """
        Geçmiş session .scr dosyasından repeat ve wait parametrelerini yükler.
        Değerleri last_paths'a yazmaz; session restore, kullanıcı tercihi değildir.
        """
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(scr_path)
            root = tree.getroot()
            for action in root.findall(".//action"):
                atype = action.get("type", "")
                if atype == "REPEAT":
                    times = action.findtext("times")
                    if times:
                        self.repeat_spin.blockSignals(True)
                        self.repeat_spin.setValue(int(times))
                        self.repeat_spin.blockSignals(False)
                elif atype == "WAIT":
                    time_ms = action.get("timeMS")
                    if time_ms:
                        self.wait_spin.blockSignals(True)
                        self.wait_spin.setValue(float(time_ms) / 1000.0)
                        self.wait_spin.blockSignals(False)
        except Exception as e:
            msg = f"[HATA] .scr parse edilemedi ({os.path.basename(scr_path)}): {e}"
            if log_fn:
                log_fn(msg)
            else:
                import logging

                logging.getLogger(__name__).warning(msg)

    def set_response_delay(self, minutes: int, seconds: int):
        """ViewerTab delay_changed sinyalinden güncellenir."""
        if minutes == 0 and seconds == 0:
            self.delay_lbl.setText("—")
            self.delay_lbl.setStyleSheet("color: #888; font-style: italic;")
        else:
            self.delay_lbl.setText(f"{minutes} min  {seconds} sec")
            self.delay_lbl.setStyleSheet("color: #4CAF50; font-style: normal;")

    def _save_glucose_map(self):
        """Port-glikoz eşlemesini last_paths.json'a yazar. -1.0 = boş (tire)."""
        mapping = {
            str(port): (spin.value() if spin.value() >= 0 else None)
            for port, spin in self._glucose_spins.items()
        }
        remember_value("port_glucose", mapping)

    def get_glucose_map(self) -> dict:
        """Viewer için {port_int: mg_dl_float} döner. Boş portlar dahil edilmez."""
        return {
            port: spin.value()
            for port, spin in self._glucose_spins.items()
            if spin.value() >= 0
        }

    def clear(self):
        """Session'a ait geçici alanları temizler.
        Kullanıcı tercihlerine (repeat, wait, temp, flow, glucose) dokunmaz.
        """
        self.method_edit.clear()
        self.method_edit.setStyleSheet("color: #888; font-style: italic;")
        self.csv_edit.clear()
        self.csv_edit.setStyleSheet("color: #888; font-style: italic;")
        self.delay_lbl.setText("—")
        self.delay_lbl.setStyleSheet("color: #888; font-style: italic;")
