# bench_test/ui/tabs/method_editor_panel.py
# -*- coding: utf-8 -*-
"""
MethodEditorPanel
=================
Loads a .tp (DropView method) file and shows its parameters.
Measurement and Information fields are editable.
Options, Pretreatment and Multichannel summary fields are read-only
except Ei, which is shown with a spin box.
"""

import os
import xml.etree.ElementTree as ET

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QDoubleSpinBox,
    QGroupBox,
    QMessageBox,
    QScrollArea,
    QGridLayout,
)
from PyQt6.QtCore import pyqtSignal, Qt

from bench_test.measurement.session import (
    MEASUREMENT_MODE_CONTINUOUS_PAD,
    MEASUREMENT_MODE_CV,
    MEASUREMENT_MODE_SCRIPT_PAD,
)
from bench_test.utils.paths import get_value, remember_value, open_file
from bench_test.ui.widgets import _btn


class MethodEditorPanel(QWidget):
    tp_loaded = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self._current_tech_id = ""
        self._measurement_mode = MEASUREMENT_MODE_SCRIPT_PAD
        self._mode_provider = None
        self._build_ui()
        self._show_empty_for_mode(self._measurement_mode)

    @staticmethod
    def _last_method_key_for_mode(mode: str) -> str:
        mode = mode or MEASUREMENT_MODE_SCRIPT_PAD
        return f"last_method_file_{mode}"

    @staticmethod
    def _expected_tech_id_for_mode(mode: str) -> str:
        if mode == MEASUREMENT_MODE_CV:
            return "CV"
        return "PAD"

    @staticmethod
    def _method_tech_id(path: str) -> str:
        tree = ET.parse(path)
        technic = tree.getroot().find("technic")
        return technic.get("id", "") if technic is not None else ""

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        self.file_lbl = QLabel("File not loaded")
        self.file_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self.file_lbl.setWordWrap(True)
        top_row.addWidget(self.file_lbl, stretch=1)
        top_row.addWidget(_btn("Load Method", self._load))
        layout.addLayout(top_row)

        self.technic_lbl = QLabel("Pulsed Amperometric Detection")
        self.technic_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.technic_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(self.technic_lbl)

        self.node_lbl = QLabel("Node 1")
        self.node_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.node_lbl.setStyleSheet("color: #666;")
        layout.addWidget(self.node_lbl)

        opt_grp = QGroupBox("Options")
        opt_form = QFormLayout(opt_grp)
        self.cellon_lbl = self._ro_label()
        self.standbypotential_lbl = self._ro_label()
        opt_form.addRow("Leave cell on:", self.cellon_lbl)
        opt_form.addRow("Standby potential:", self.standbypotential_lbl)
        layout.addWidget(opt_grp)

        pre_grp = QGroupBox("Pretreatment")
        pre_grid = QGridLayout(pre_grp)
        self._pre = {}

        pre_params = [
            ("Econd", "V"),
            ("tcond", "s"),
            ("Edep", "V"),
            ("tdep", "s"),
            ("tequil", "s"),
        ]

        row = 0
        col = 0
        pre_grid.setColumnMinimumWidth(2, 50)
        for pid, unit in pre_params:
            lbl = self._ro_label()
            self._pre[pid] = lbl
            pre_grid.addWidget(QLabel(f"{pid} [{unit}]"), row, col)
            pre_grid.addWidget(lbl, row, col + 1)
            col += 3
            if col >= 6:
                col = 0
                row += 1
        layout.addWidget(pre_grp)

        meas_grp = QGroupBox("Measurement")
        meas_grid = QGridLayout(meas_grp)
        self._meas = {}
        self._meas_rows = {}

        meas_params = [
            ("E1", "V"),
            ("t1", "s"),
            ("E2", "V"),
            ("t2", "s"),
            ("E3", "V"),
            ("t3", "s"),
            ("E4", "V"),
            ("t4", "s"),
            ("E5", "V"),
            ("t5", "s"),
            ("ti", "s"),
            ("t", "s"),
            ("Estep", "V"),
            ("Srate", "V/s"),
            ("nscans", ""),
        ]

        row = 0
        col = 0
        meas_grid.setColumnMinimumWidth(2, 50)
        for pid, unit in meas_params:
            label = QLabel(f"{pid} [{unit}]" if unit else pid)
            spin = self._dspin(
                decimals=2,
                rng=(-10.0, 10.0) if pid.startswith("E") else (0.0, 85000.0),
            )
            self._meas[pid] = spin
            self._meas_rows[pid] = (label, spin)
            meas_grid.addWidget(label, row, col)
            meas_grid.addWidget(spin, row, col + 1)
            col += 3
            if col >= 6:
                col = 0
                row += 1
        layout.addWidget(meas_grp)

        # 1) Multichannel Parameters (Information ustunde)
        multi_grp = QGroupBox("Multichannel Parameters")
        multi_form = QFormLayout(multi_grp)
        self.multi_tech_lbl = self._ro_label()
        self.multi_channel_lbl = self._ro_label()
        self.multi_current_range_lbl = self._ro_label()
        self.multi_ei_spin = self._dspin(decimals=3, rng=(-10.0, 10.0))
        self.multi_ei_label = QLabel("Ei [V]:")
        self._cv_channel = {}
        self._cv_channel_rows = {}
        multi_form.addRow("Technic:", self.multi_tech_lbl)
        multi_form.addRow("Measurement of:", self.multi_channel_lbl)
        multi_form.addRow("Current range:", self.multi_current_range_lbl)
        multi_form.addRow(self.multi_ei_label, self.multi_ei_spin)
        for pid in ("Ebegin", "Evtx1", "Evtx2"):
            spin = self._dspin(decimals=3, rng=(-10.0, 10.0))
            label = QLabel(f"{pid} [V]:")
            multi_form.addRow(label, spin)
            self._cv_channel[pid] = spin
            self._cv_channel_rows[pid] = (label, spin)
        layout.addWidget(multi_grp)

        # 2) Information (en altta)
        info_grp = QGroupBox("Information")
        info_form = QFormLayout(info_grp)
        self.sensor_edit = QLineEdit()
        self.sample_edit = QLineEdit()
        self.sensor_edit.setPlaceholderText("e.g. USTAT-001")
        self.sample_edit.setPlaceholderText("e.g. UNAM-XXX-YYY-ZZZ")
        info_form.addRow("Sensor:", self.sensor_edit)
        info_form.addRow("Sample:", self.sample_edit)
        layout.addWidget(info_grp)

        self._apply_technic_to_ui("PAD")
        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _ro_label(self) -> QLabel:
        lbl = QLabel("-")
        lbl.setStyleSheet("color: #555;")
        return lbl

    def _dspin(self, decimals=2, rng=(-10.0, 10.0)) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(*rng)
        spin.setMaximumWidth(100)
        return spin

    def _load(self):
        self._sync_mode_from_provider()
        path = open_file(
            self,
            "Select Method File",
            "method_dir",
            "DropView Method (*.tp *.tc);;All files (*.*)",
        )
        if path:
            self.load_from_path(path)

    def set_mode_provider(self, provider):
        self._mode_provider = provider
        self._sync_mode_from_provider()

    def _sync_mode_from_provider(self):
        if self._mode_provider is None:
            return
        try:
            mode = self._mode_provider()
        except Exception:
            return
        if mode and mode != self._measurement_mode:
            self.set_measurement_mode(mode)

    def load_from_path(self, path: str, persist: bool = True):
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            technic = root.find("technic")
            if technic is None:
                raise ValueError("<technic> not found in method file.")

            tech_id = technic.get("id", "")
            expected_tech = self._expected_tech_id_for_mode(self._measurement_mode)
            if tech_id != expected_tech:
                raise ValueError(
                    f"Selected method is {self._technic_display_name(tech_id)}, "
                    f"but current mode expects {self._technic_display_name(expected_tech)}."
                )
            self.technic_lbl.setText(self._technic_display_name(expected_tech))

            self.cellon_lbl.setText(technic.findtext("cellon", "-"))
            self.standbypotential_lbl.setText(
                technic.findtext("standbypotentialvalue", "0.0")
            )

            for pid, lbl in self._pre.items():
                val = technic.find(f"pretreatmentUserParameters/parameter[@id='{pid}']")
                lbl.setText(val.text if val is not None else "-")

            for pid, spin in self._meas.items():
                val = technic.find(f"commonUserParameters/parameter[@id='{pid}']")
                if val is not None:
                    try:
                        spin.setValue(float(val.text))
                    except (ValueError, TypeError):
                        spin.setValue(0.0)

            sensor = technic.findtext("sensor", "")
            sample = technic.findtext("sample", "")
            if sensor:
                self.sensor_edit.setText(sensor)
            if sample:
                self.sample_edit.setText(sample)

            self.multi_tech_lbl.setText(self._technic_display_name(expected_tech))
            self.multi_channel_lbl.setText(
                f"channel {technic.findtext('numchannels', '1')}"
            )
            channel = technic.find("channelUserParameters/channel[@id='0']")
            if channel is not None:
                ei = channel.find("parameter[@id='Ei']")
                if ei is not None:
                    try:
                        self.multi_ei_spin.setValue(float(ei.text))
                    except (ValueError, TypeError):
                        self.multi_ei_spin.setValue(0.0)
                else:
                    self.multi_ei_spin.setValue(0.0)

                for pid, spin in self._cv_channel.items():
                    param = channel.find(f"parameter[@id='{pid}']")
                    if param is not None:
                        try:
                            spin.setValue(float(param.text))
                        except (ValueError, TypeError):
                            spin.setValue(0.0)
                    else:
                        spin.setValue(0.0)

                current = channel.find("parameter[@id='Current']")
                if current is None or current.text is None:
                    self.multi_current_range_lbl.setText("-")
                else:
                    raw = current.text.strip()
                    try:
                        self.multi_current_range_lbl.setText(
                            "Auto" if float(raw) == 0.0 else raw
                        )
                    except (ValueError, TypeError):
                        self.multi_current_range_lbl.setText(raw)
            else:
                self.multi_ei_spin.setValue(0.0)
                for spin in self._cv_channel.values():
                    spin.setValue(0.0)
                self.multi_current_range_lbl.setText("-")

            self._current_path = path
            self._current_tech_id = expected_tech
            if persist:
                remember_value(
                    self._last_method_key_for_mode(self._measurement_mode),
                    path,
                )
                if expected_tech == "PAD":
                    remember_value("last_method_file", path)
            self.file_lbl.setText(os.path.basename(path))
            self.file_lbl.setStyleSheet("color: #4CAF50; font-size: 11px;")
            self.file_lbl.setToolTip(path)
            self._apply_technic_to_ui(expected_tech)
            self._apply_measurement_mode_to_ui()
            self.tp_loaded.emit(path)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load method:\n{e}")

    @staticmethod
    def _technic_display_name(tech_id: str) -> str:
        if tech_id == "PAD":
            return "Pulsed Amperometric Detection"
        if tech_id == "CV":
            return "Cyclic Voltammetry"
        return tech_id or "-"

    def _apply_technic_to_ui(self, tech_id: str):
        cv = tech_id == "CV"
        cv_meas = {"Estep", "Srate", "nscans"}
        for pid, widgets in self._meas_rows.items():
            visible = (pid in cv_meas) if cv else (pid not in cv_meas)
            for widget in widgets:
                widget.setVisible(visible)

        self.multi_ei_spin.setVisible(not cv)
        self.multi_ei_label.setVisible(not cv)
        for pid, widgets in self._cv_channel_rows.items():
            for widget in widgets:
                widget.setVisible(cv)

    def _restore_last_method(self, mode: str = None):
        mode = mode or self._measurement_mode
        mode_key = self._last_method_key_for_mode(mode)
        expected_tech = self._expected_tech_id_for_mode(mode)
        path = get_value(mode_key, "")
        used_legacy_key = False
        if not path and expected_tech == "PAD":
            path = get_value("last_method_file", "")
            used_legacy_key = bool(path)
        if not path:
            self._show_empty_for_mode(mode)
            return

        if os.path.isfile(path):
            try:
                tech_id = self._method_tech_id(path)
            except Exception:
                tech_id = ""
            if tech_id != expected_tech:
                remember_value(mode_key, "")
                if used_legacy_key and get_value("last_method_file", "") == path:
                    remember_value("last_method_file", "")
                self._show_empty_for_mode(mode)
                return
            self.load_from_path(path)
            return

        # Dosya artık erişilebilir değil; sessizce boş bırak ve stale kaydı temizle.
        remember_value(mode_key, "")
        if get_value("last_method_file", "") == path:
            remember_value("last_method_file", "")
        self._show_empty_for_mode(mode)

    def get_tp_data(self) -> dict:
        return {
            "sensor": self.sensor_edit.text().strip(),
            "sample": self.sample_edit.text().strip(),
            "measurement": {pid: spin.value() for pid, spin in self._meas.items()},
            "source_path": self._current_path,
        }

    def set_sensor_sample(self, sensor: str, sample: str):
        if sensor:
            self.sensor_edit.setText(sensor)
        if sample:
            self.sample_edit.setText(sample)

    def get_current_path(self) -> str:
        return self._current_path

    def get_method_paths_by_mode(self) -> dict:
        paths = {}
        current_mode = self._measurement_mode or MEASUREMENT_MODE_SCRIPT_PAD
        if self._current_path:
            paths[current_mode] = self._current_path
        for mode in (
            MEASUREMENT_MODE_SCRIPT_PAD,
            MEASUREMENT_MODE_CONTINUOUS_PAD,
            MEASUREMENT_MODE_CV,
        ):
            if mode not in paths:
                paths[mode] = get_value(self._last_method_key_for_mode(mode), "")
        if not paths.get(MEASUREMENT_MODE_CONTINUOUS_PAD):
            paths[MEASUREMENT_MODE_CONTINUOUS_PAD] = paths.get(MEASUREMENT_MODE_SCRIPT_PAD, "")
        if not paths.get(MEASUREMENT_MODE_SCRIPT_PAD):
            paths[MEASUREMENT_MODE_SCRIPT_PAD] = paths.get(MEASUREMENT_MODE_CONTINUOUS_PAD, "")
        return paths

    def set_measurement_mode(self, mode: str):
        mode = mode or MEASUREMENT_MODE_SCRIPT_PAD
        if mode == self._measurement_mode:
            if not self._current_path:
                self._restore_last_method(mode)
            self._apply_measurement_mode_to_ui()
            return
        self._measurement_mode = mode
        expected = self._expected_tech_id_for_mode(mode)
        if self._current_path and self._current_tech_id == expected:
            self._apply_measurement_mode_to_ui()
            return
        self._restore_last_method(mode)
        self._apply_measurement_mode_to_ui()

    def _show_empty_for_mode(self, mode: str):
        self.clear(technic_id=self._expected_tech_id_for_mode(mode))
        self.tp_loaded.emit("")

    def _apply_measurement_mode_to_ui(self):
        if not hasattr(self, "_meas"):
            return

        t_spin = self._meas.get("t")
        if t_spin is None:
            return

        continuous = self._measurement_mode == MEASUREMENT_MODE_CONTINUOUS_PAD
        if continuous:
            t_spin.blockSignals(True)
            t_spin.setValue(t_spin.maximum())
            t_spin.blockSignals(False)
            t_spin.setToolTip(
                "Continuous PAD uses DropView AutoSave; duration is controlled by the recipe."
            )
        else:
            t_spin.setToolTip("")
        t_spin.setEnabled(not continuous)

    def clear(self, technic_id: str = "PAD"):
        """MethodEditorPanel'i açılış haline getirir."""
        self._current_path = ""
        self._current_tech_id = ""
        self.file_lbl.setText("File not loaded")
        self.file_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self.file_lbl.setToolTip("")

        self.technic_lbl.setText(self._technic_display_name(technic_id))
        self.node_lbl.setText("Node 1")
        self.cellon_lbl.setText("-")
        self.standbypotential_lbl.setText("-")

        for lbl in self._pre.values():
            lbl.setText("-")

        for spin in self._meas.values():
            spin.setValue(0.0)

        self.sensor_edit.clear()
        self.sample_edit.clear()
        self.multi_tech_lbl.setText(self._technic_display_name(technic_id))
        self.multi_channel_lbl.setText("-")
        self.multi_current_range_lbl.setText("-")
        self.multi_ei_spin.setValue(0.0)
        for spin in self._cv_channel.values():
            spin.setValue(0.0)
        self._apply_technic_to_ui(technic_id)
        self._apply_measurement_mode_to_ui()
