# bench_test/ui/tabs/manual_tab.py
import threading
import time

import serial.tools.list_ports

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QGroupBox, QMessageBox, QScrollArea, QFrame, QTextEdit
)
from PyQt6.QtCore import pyqtSignal, Qt, QMetaObject, Q_ARG, QTimer
from PyQt6.QtGui import QFont

from bench_test.valve.multiport import ValveController, SV01Protocol
from bench_test.valve.injector import InjectorValveController
from bench_test.pump import ArduinoPumpController
from bench_test.dropview.controller import DropViewController
from bench_test.utils.paths import get_value, remember_value
from bench_test.ui.widgets import _btn, _lbl, _status_lbl


class ManualControlTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController, ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController,
                 pump_ctrl: ArduinoPumpController = None):
        super().__init__()
        self.ctrl_a = ctrl_a
        self.ctrl_b = ctrl_b
        self.dv_ctrl = dv_ctrl
        self.pump_ctrl = pump_ctrl or ArduinoPumpController()
        self._build()
        self._restore_saved()
        self._refresh_ports()
        self._pump_log_timer = None

        dv_ctrl.status_changed.connect(self._on_dv_status)
        dv_ctrl.status_changed.connect(self._update_summary)
        dv_ctrl.action_done.connect(self._on_dv_action_done)
        dv_ctrl.log_message.connect(self.log_signal)

    # ──────────────────────────────────────────────────────────
    #  Build
    # ──────────────────────────────────────────────────────────

    def _build(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)
        scroll.setWidget(container)

        layout.addWidget(self._build_valve_a())
        layout.addWidget(self._build_valve_b())
        layout.addWidget(self._build_pump())
        layout.addWidget(self._build_dropsens())
        layout.addWidget(self._build_general())
        layout.addStretch()

    def _separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #444;")
        return line

    # ── Valve A ───────────────────────────────────────────────

    def _build_valve_a(self):
        ga = QGroupBox("Valve A: SV-01 Multiport Valve (8-Port Selector)")
        fla = QVBoxLayout(ga)
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        # ── Sol: Bağlantı ayarları ────────────────────────────
        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("COM Port:"))
        self.port_a = QComboBox(); self.port_a.setMinimumWidth(100)
        row_a.addWidget(self.port_a)
        row_a.addWidget(QLabel("Baud:"))
        self.baud_a = QComboBox()
        self.baud_a.addItems(["9600", "19200", "38400", "57600", "115200"])
        saved_baud_a = get_value("valve_a_baud", "9600")
        if saved_baud_a in ["9600", "19200", "38400", "57600", "115200"]:
            self.baud_a.setCurrentText(saved_baud_a)
        row_a.addWidget(self.baud_a)
        row_a.addWidget(QLabel("Addr (hex):"))
        self.addr_a = QLineEdit(get_value("valve_a_addr", "00"))
        self.addr_a.setMaximumWidth(40)
        row_a.addWidget(self.addr_a)
        row_a.addStretch()
        left_col.addLayout(row_a)

        btn_conn_a = QHBoxLayout()
        self.conn_a_btn = _btn("Connect A",    self._connect_a,    "#4CAF50")
        self.disc_a_btn = _btn("Disconnect A", self._disconnect_a, "#F44336")
        self.test_a_btn = _btn("Test A",       self._test_a,       "#2196F3")
        self.disc_a_btn.setEnabled(False)
        self.test_a_btn.setEnabled(False)
        self.status_a = _status_lbl()
        btn_conn_a.addWidget(self.conn_a_btn)
        btn_conn_a.addWidget(self.disc_a_btn)
        btn_conn_a.addWidget(self.test_a_btn)
        btn_conn_a.addWidget(self.status_a)
        btn_conn_a.addStretch()
        left_col.addLayout(btn_conn_a)

        half_line = QFrame()
        half_line.setFrameShape(QFrame.Shape.HLine)
        half_line.setStyleSheet("color: #444;")
        half_line.setMaximumWidth(520)
        left_col.addWidget(half_line)

        # ── Sol: Hız kontrolü ─────────────────────────────────
        sr = QHBoxLayout()
        sr.addWidget(QLabel("Speed (5-350 rpm):"))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(5, 350)
        self.speed_spin.setValue(350)
        self.speed_spin.setFixedWidth(80)
        sr.addWidget(self.speed_spin)
        sr.addStretch()
        left_col.addLayout(sr)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Presets:"))
        for v, label in [(50, "Slow"), (150, "Medium"), (250, "Fast"), (350, "Max")]:
            pr.addWidget(_btn(f"{label} ({v})", lambda checked, s=v: self.speed_spin.setValue(s)))
        pr.addStretch()
        left_col.addLayout(pr)

        sb = QHBoxLayout()
        self.set_spd_btn  = _btn("Set Temporary",  self._set_speed_dynamic,   "#FF9800")
        self.set_perm_btn = _btn("Set Permanent",  self._set_speed_permanent, "#795548")
        self.qry_spd_btn  = _btn("Query Speed",    self._query_speed,         "#607D8B")
        self.speed_lbl = QLabel("Valve A Speed: Unknown")
        self.speed_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for b in [self.set_spd_btn, self.set_perm_btn, self.qry_spd_btn]:
            b.setEnabled(False)
        sb.addWidget(self.set_spd_btn)
        sb.addWidget(self.set_perm_btn)
        sb.addWidget(self.qry_spd_btn)
        sb.addWidget(self.speed_lbl)
        sb.addStretch()
        left_col.addLayout(sb)
        left_col.addWidget(_lbl("Note: 'Temporary' resets on power-off. 'Permanent' requires a device restart.", color="#888"))
        left_col.addStretch()

        # ── Sağ: Port butonları ───────────────────────────────
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.port_btns = []
        for i in range(1, 9):
            b = QPushButton(f"Port {i}")
            b.setMinimumSize(68, 34)
            b.clicked.connect(lambda checked, p=i: self._switch_port(p))
            grid.addWidget(b, (i - 1) // 4, (i - 1) % 4)
            self.port_btns.append(b)
        right_col.addWidget(QLabel("Port Selection:"))
        right_col.addLayout(grid)

        cur_port_row = QHBoxLayout()
        cur_port_row.addWidget(QLabel("Current Port:"))
        self.cur_port = QLabel("Unknown")
        self.cur_port.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.cur_port.setStyleSheet("color:#2196F3;")
        cur_port_row.addWidget(self.cur_port)
        cur_port_row.addStretch()
        right_col.addLayout(cur_port_row)

        ctrl_a_row = QHBoxLayout()
        ctrl_a_row.addWidget(_btn("Reset A", self._reset_a, "#FF9800"))
        ctrl_a_row.addWidget(_btn("Stop A",  self._stop_a,  "#F44336"))
        ctrl_a_row.addWidget(_btn("Query A", self._query_a, "#607D8B"))
        ctrl_a_row.addStretch()
        right_col.addLayout(ctrl_a_row)

        self.status_a_lbl = QLabel("Ready")
        right_col.addWidget(self.status_a_lbl)
        right_col.addStretch()

        top_row.addLayout(left_col, 3)
        top_row.addLayout(right_col, 2)
        fla.addLayout(top_row)

        return ga

    # ── Valve B ───────────────────────────────────────────────

    def _build_valve_b(self):
        gb = QGroupBox("Valve B: SY-07B Injector Valve (6-Port, 2-State)")
        flb = QVBoxLayout(gb)
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        right_col = QVBoxLayout()
        right_col.setSpacing(6)

        # ── Sol: Bağlantı ayarları ────────────────────────────
        row_b = QHBoxLayout()
        row_b.addWidget(QLabel("COM Port:"))
        self.port_b = QComboBox(); self.port_b.setMinimumWidth(100)
        row_b.addWidget(self.port_b)
        row_b.addWidget(QLabel("Baud:"))
        self.baud_b = QComboBox()
        self.baud_b.addItems(["9600", "19200", "38400", "57600", "115200"])
        saved_baud_b = get_value("valve_b_baud", "9600")
        if saved_baud_b in ["9600", "19200", "38400", "57600", "115200"]:
            self.baud_b.setCurrentText(saved_baud_b)
        row_b.addWidget(self.baud_b)
        row_b.addWidget(QLabel("Addr (hex):"))
        self.addr_b = QLineEdit(get_value("valve_b_addr", "00"))
        self.addr_b.setMaximumWidth(40)
        row_b.addWidget(self.addr_b)
        row_b.addStretch()
        left_col.addLayout(row_b)

        btn_conn_b = QHBoxLayout()
        self.conn_b_btn = _btn("Connect B",    self._connect_b,    "#4CAF50")
        self.disc_b_btn = _btn("Disconnect B", self._disconnect_b, "#F44336")
        self.test_b_btn = _btn("Test B",       self._test_b,       "#2196F3")
        self.disc_b_btn.setEnabled(False)
        self.test_b_btn.setEnabled(False)
        self.status_b = _status_lbl()
        btn_conn_b.addWidget(self.conn_b_btn)
        btn_conn_b.addWidget(self.disc_b_btn)
        btn_conn_b.addWidget(self.test_b_btn)
        btn_conn_b.addWidget(self.status_b)
        btn_conn_b.addStretch()
        left_col.addLayout(btn_conn_b)
        left_col.addStretch()

        # ── Sağ: Durum kontrolü ───────────────────────────────
        right_col.addWidget(_lbl("States: 1=Load (1-6, 2-3, 4-5) | 2=Inject (1-2, 3-4, 5-6)", color="#888"))

        sb_row = QHBoxLayout()
        self.load_btn   = QPushButton("Load\n(1-6, 2-3, 4-5)")
        self.inject_btn = QPushButton("Inject\n(1-2, 3-4, 5-6)")
        self.load_btn.setMinimumSize(140, 60)
        self.inject_btn.setMinimumSize(140, 60)
        self.load_btn.clicked.connect(self._set_load)
        self.inject_btn.clicked.connect(self._set_inject)
        sb_row.addWidget(self.load_btn)
        sb_row.addWidget(self.inject_btn)
        sb_row.addStretch()
        right_col.addLayout(sb_row)

        sr = QHBoxLayout()
        sr.addWidget(QLabel("Current State:"))
        self.cur_state = QLabel("Unknown")
        self.cur_state.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.cur_state.setStyleSheet("color:#9C27B0;")
        sr.addWidget(self.cur_state)
        sr.addStretch()
        right_col.addLayout(sr)

        ctrl_b_row = QHBoxLayout()
        ctrl_b_row.addWidget(_btn("Reset B", self._reset_b, "#FF9800"))
        ctrl_b_row.addWidget(_btn("Stop B",  self._stop_b,  "#F44336"))
        ctrl_b_row.addWidget(_btn("Query B", self._query_b, "#607D8B"))
        ctrl_b_row.addStretch()
        right_col.addLayout(ctrl_b_row)

        self.status_b_lbl = QLabel("Ready")
        right_col.addWidget(self.status_b_lbl)
        right_col.addStretch()

        top_row.addLayout(left_col, 3)
        top_row.addLayout(right_col, 2)
        flb.addLayout(top_row)

        return gb

    # ── Peristaltic Pump ─────────────────────────────────────

    def _build_pump(self):
        gp = QGroupBox("Peristaltic Pump - Arduino Nano + MCP4725")
        flp = QVBoxLayout(gp)

        row_conn = QHBoxLayout()
        row_conn.addWidget(QLabel("COM Port:"))
        self.port_pump = QComboBox()
        self.port_pump.setMinimumWidth(100)
        row_conn.addWidget(self.port_pump)
        row_conn.addWidget(QLabel("Baud: 115200"))
        self.conn_pump_btn = _btn("Connect Pump", self._connect_pump, "#4CAF50")
        self.disc_pump_btn = _btn("Disconnect Pump", self._disconnect_pump, "#F44336")
        self.test_pump_btn = _btn("Status", self._query_pump, "#607D8B")
        self.status_pump = _status_lbl()
        self.disc_pump_btn.setEnabled(False)
        self.test_pump_btn.setEnabled(False)
        row_conn.addWidget(self.conn_pump_btn)
        row_conn.addWidget(self.disc_pump_btn)
        row_conn.addWidget(self.test_pump_btn)
        row_conn.addWidget(self.status_pump)
        row_conn.addStretch()
        flp.addLayout(row_conn)

        row_speed = QHBoxLayout()
        row_speed.addWidget(QLabel("Speed mV (0-5000):"))
        self.pump_mv_spin = QSpinBox()
        self.pump_mv_spin.setRange(0, 5000)
        self.pump_mv_spin.setSingleStep(100)
        self.pump_mv_spin.setValue(500)
        self.pump_mv_spin.setFixedWidth(90)
        row_speed.addWidget(self.pump_mv_spin)
        self.set_pump_mv_btn = _btn("Set mV", self._set_pump_mv, "#FF9800")
        row_speed.addWidget(self.set_pump_mv_btn)

        row_speed.addWidget(QLabel("Max RPM:"))
        self.pump_max_rpm_spin = QDoubleSpinBox()
        self.pump_max_rpm_spin.setRange(0.1, 5000.0)
        self.pump_max_rpm_spin.setDecimals(1)
        self.pump_max_rpm_spin.setValue(300.0)
        self.pump_max_rpm_spin.setFixedWidth(90)
        row_speed.addWidget(self.pump_max_rpm_spin)
        self.set_pump_max_btn = _btn("Set Max", self._set_pump_max_rpm, "#795548")
        row_speed.addWidget(self.set_pump_max_btn)

        row_speed.addWidget(QLabel("RPM:"))
        self.pump_rpm_spin = QDoubleSpinBox()
        self.pump_rpm_spin.setRange(0.0, 5000.0)
        self.pump_rpm_spin.setDecimals(1)
        self.pump_rpm_spin.setValue(30.0)
        self.pump_rpm_spin.setFixedWidth(90)
        row_speed.addWidget(self.pump_rpm_spin)
        self.set_pump_rpm_btn = _btn("Set RPM", self._set_pump_rpm, "#FF9800")
        row_speed.addWidget(self.set_pump_rpm_btn)
        row_speed.addStretch()
        flp.addLayout(row_speed)

        row_run = QHBoxLayout()
        row_run.addWidget(QLabel("Pulse (ms):"))
        self.pump_pulse_spin = QSpinBox()
        self.pump_pulse_spin.setRange(1, 600000)
        self.pump_pulse_spin.setSingleStep(100)
        self.pump_pulse_spin.setValue(1000)
        self.pump_pulse_spin.setFixedWidth(100)
        row_run.addWidget(self.pump_pulse_spin)
        self.pump_pulse_fwd_btn = _btn("Pulse FWD", lambda: self._pulse_pump("FWD"), "#2196F3")
        self.pump_pulse_rev_btn = _btn("Pulse REV", lambda: self._pulse_pump("REV"), "#9C27B0")
        self.pump_run_fwd_btn = _btn("Run FWD", lambda: self._run_pump("FWD"), "#4CAF50")
        self.pump_run_rev_btn = _btn("Run REV", lambda: self._run_pump("REV"), "#4CAF50")
        self.stop_pump_btn = _btn("Stop Pump", self._stop_pump, "#B71C1C")
        for b in [
            self.pump_pulse_fwd_btn, self.pump_pulse_rev_btn,
            self.pump_run_fwd_btn, self.pump_run_rev_btn, self.stop_pump_btn,
        ]:
            row_run.addWidget(b)
        row_run.addStretch()
        flp.addLayout(row_run)

        self.pump_state_lbl = QLabel("Pump: Not connected")
        self.pump_state_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        flp.addWidget(self.pump_state_lbl)
        flp.addWidget(_lbl(
            "Pump speed is controlled by MCP4725 VOUT -> Pump Pin 9. Stop/Direction use Nano D8/D7.",
            color="#888"))
        self._set_pump_controls(False)
        return gp

    # ── DropSens ──────────────────────────────────────────────

    def _build_dropsens(self):
        gd = QGroupBox("DropSens - DropView 8400M")
        fld = QVBoxLayout(gd)

        row_dv = QHBoxLayout()
        row_dv.addWidget(QLabel("COM Port:"))
        self.port_dv = QComboBox(); self.port_dv.setMinimumWidth(100)
        row_dv.addWidget(self.port_dv)
        row_dv.addStretch()
        fld.addLayout(row_dv)

        row_prog = QHBoxLayout()
        self.dv_launch_btn     = _btn("Start DropView", self._dv_launch,     "#3F51B5", 130)
        self.dv_connect_btn    = _btn("Connect",        self._dv_connect,    "#4CAF50", 130)
        self.dv_disconnect_btn = _btn("Disconnect",     self._dv_disconnect, "#F44336", 130)
        self.dv_close_btn      = _btn("Exit DropView",  self._dv_close,      "#9C27B0", 130)
        self.dv_status         = _status_lbl("● Disconnected")

        row_prog.addWidget(self.dv_launch_btn)
        row_prog.addWidget(self.dv_connect_btn)
        row_prog.addWidget(self.dv_disconnect_btn)
        row_prog.addWidget(self.dv_close_btn)
        row_prog.addWidget(self.dv_status)
        row_prog.addStretch()
        fld.addLayout(row_prog)

        fld.addWidget(_lbl(
            "Start DropView: launches DropView             |  Exit DropView: closes DropView (Alt+F4)\n"
            "Connect: establishes DropSens connection (Ctrl+C)  |  Disconnect: disconnects DropSens (Ctrl+D)",
            color="#888"))

        return gd

    # ── Genel ─────────────────────────────────────────────────

    def _build_general(self):
        gg = QGroupBox("Genel")
        ggl = QVBoxLayout(gg)

        btn_row = QHBoxLayout()
        btn_row.addWidget(_btn("Refresh COM Ports",   self._refresh_ports))
        btn_row.addWidget(_btn("Emergency Stop ALL",  self._stop_all,   "#B71C1C", 160))
        btn_row.addWidget(_btn("Query All Status",    self._query_all,  "#455A64"))
        btn_row.addStretch()
        ggl.addLayout(btn_row)

        ggl.addWidget(QLabel("Connection Summary:"))
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(80)
        ggl.addWidget(self.summary_text)

        return gg

    # ──────────────────────────────────────────────────────────
    #  Restore saved settings
    # ──────────────────────────────────────────────────────────

    def _restore_saved(self):
        saved_a  = get_value("valve_a_port", "")
        saved_b  = get_value("valve_b_port", "")
        saved_dv = get_value("dropsens_com", "")
        saved_pump = get_value("pump_com", "")
        if saved_a:  self.port_a.setCurrentText(saved_a)
        if saved_b:  self.port_b.setCurrentText(saved_b)
        if saved_pump:
            self.port_pump.setCurrentText(saved_pump)
        if saved_dv and self.port_dv.findText(saved_dv) >= 0:
            self.port_dv.setCurrentText(saved_dv)

    # ──────────────────────────────────────────────────────────
    #  Port yenileme
    # ──────────────────────────────────────────────────────────

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        for combo in [self.port_a, self.port_b, self.port_pump]:
            current = combo.currentText()
            combo.clear()
            combo.addItems(ports)
            if current in ports:
                combo.setCurrentText(current)
        saved_dv   = get_value("dropsens_com", "")
        current_dv = self.port_dv.currentText()
        self.port_dv.clear()
        self.port_dv.addItems(ports)
        preferred = saved_dv or current_dv
        if preferred and self.port_dv.findText(preferred) >= 0:
            self.port_dv.setCurrentText(preferred)

    # ──────────────────────────────────────────────────────────
    #  Valve A – bağlantı
    # ──────────────────────────────────────────────────────────

    def _connect_a(self):
        try:
            self.ctrl_a.address = int(self.addr_a.text(), 16)
            self.ctrl_a.connect(self.port_a.currentText(), int(self.baud_a.currentText()))
            self.status_a.setText("● Connected")
            self.status_a.setStyleSheet("color:#4CAF50;")
            self.conn_a_btn.setEnabled(False)
            self.disc_a_btn.setEnabled(True)
            self.test_a_btn.setEnabled(True)
            self.set_spd_btn.setEnabled(True)
            self.set_perm_btn.setEnabled(True)
            self.qry_spd_btn.setEnabled(True)
            remember_value("valve_a_port", self.port_a.currentText())
            remember_value("valve_a_baud", self.baud_a.currentText())
            remember_value("valve_a_addr", self.addr_a.text())
            self.log_signal.emit(f"Valve A connected to {self.port_a.currentText()}")
            self._update_summary()
            self._query_speed()
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Valve A: {e}")
            self.log_signal.emit(f"Valve A connection failed: {e}")

    def _disconnect_a(self):
        self.ctrl_a.disconnect()
        self.status_a.setText("● Disconnected")
        self.status_a.setStyleSheet("color:#F44336;")
        self.conn_a_btn.setEnabled(True)
        self.disc_a_btn.setEnabled(False)
        self.test_a_btn.setEnabled(False)
        self.set_spd_btn.setEnabled(False)
        self.set_perm_btn.setEnabled(False)
        self.qry_spd_btn.setEnabled(False)
        self.speed_lbl.setText("Valve A Speed: Unknown")
        self.log_signal.emit("Valve A disconnected")
        self._update_summary()

    def _test_a(self):
        r = self.ctrl_a.test_connection()
        if r.get("success"):
            msg = f"Connection OK. Status: {r.get('status_message', '')}"
            QMessageBox.information(self, "Valve A Test", msg)
            self.log_signal.emit(f"Valve A test: {msg}")
        else:
            QMessageBox.critical(self, "Valve A Test", r.get("error", "Unknown"))

    # ── Valve A – hız ─────────────────────────────────────────

    def _set_speed_dynamic(self):
        r = self.ctrl_a.set_speed_dynamic(self.speed_spin.value())
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            self.speed_lbl.setText(f"Valve A Speed: {self.speed_spin.value()} rpm (temp)")
            self.log_signal.emit(f"Valve A speed set to {self.speed_spin.value()} rpm (temp)")
        else:
            QMessageBox.critical(self, "Error", r.get("error", r.get("status_message", "Failed")))

    def _set_speed_permanent(self):
        spd = self.speed_spin.value()
        if QMessageBox.question(self, "Confirm",
                                f"Set permanent speed to {spd} rpm?\n(Device restart required)") \
                == QMessageBox.StandardButton.Yes:
            r = self.ctrl_a.set_max_speed(spd)
            if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
                self.speed_lbl.setText(f"Valve A Speed: {spd} rpm (pending restart)")
                self.log_signal.emit(f"Valve A permanent speed set to {spd} rpm")
            else:
                QMessageBox.critical(self, "Error", r.get("error", "Failed"))

    def _query_speed(self):
        r = self.ctrl_a.get_max_speed()
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            spd = r.get("speed_rpm", 0)
            self.speed_lbl.setText(f"Valve A Speed: {spd} rpm")
            self.speed_spin.setValue(spd)
            self.log_signal.emit(f"Valve A max speed: {spd} rpm")

    # ── Valve A – kontrol ─────────────────────────────────────

    def _switch_port(self, port):
        if not self.ctrl_a.is_connected():
            QMessageBox.warning(self, "Warning", "Valve A not connected")
            return
        self.status_a_lbl.setText(f"Switching to Port {port}...")
        self.log_signal.emit(f"Valve A: Switching to port {port}...")

        def _run():
            try:
                r = self.ctrl_a.switch_port(port)
                if r.get("success"):
                    time.sleep(0.3)
                    self.ctrl_a.wait_for_completion(20.0)
                    QMetaObject.invokeMethod(self.cur_port, "setText",
                                             Qt.ConnectionType.QueuedConnection,
                                             Q_ARG(str, f"Port {port}"))
                    QMetaObject.invokeMethod(self.status_a_lbl, "setText",
                                             Qt.ConnectionType.QueuedConnection,
                                             Q_ARG(str, f"At Port {port}"))
                    self.log_signal.emit(f"Valve A: At port {port}")
                else:
                    QMetaObject.invokeMethod(self.status_a_lbl, "setText",
                                             Qt.ConnectionType.QueuedConnection,
                                             Q_ARG(str, f"Failed: {r.get('error', '')}"))
                    self.log_signal.emit(f"Valve A switch failed: {r.get('error', '')}")
            except Exception as e:
                self.log_signal.emit(f"ERROR _switch_port: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _reset_a(self):
        if not self.ctrl_a.is_connected():
            return
        self.log_signal.emit("Valve A: Resetting...")

        def _run():
            r = self.ctrl_a.reset()
            if r.get("success"):
                time.sleep(0.3)
                self.ctrl_a.wait_for_completion(20.0)
                QMetaObject.invokeMethod(self.cur_port, "setText",
                                         Qt.ConnectionType.QueuedConnection, Q_ARG(str, "Home"))
                QMetaObject.invokeMethod(self.status_a_lbl, "setText",
                                         Qt.ConnectionType.QueuedConnection, Q_ARG(str, "At Home"))
                self.log_signal.emit("Valve A: Reset complete")

        threading.Thread(target=_run, daemon=True).start()

    def _stop_a(self):
        if self.ctrl_a.is_connected():
            self.ctrl_a.stop()
            self.status_a_lbl.setText("STOPPED")
            self.log_signal.emit("Valve A: STOP")

    def _query_a(self):
        if not self.ctrl_a.is_connected():
            return
        r = self.ctrl_a.get_current_port()
        if r.get("success"):
            p = r.get("param1", 0)
            self.cur_port.setText("Home" if p == 0xFF else f"Port {p}")
            self.log_signal.emit(f"Valve A: port={p}")

    # ──────────────────────────────────────────────────────────
    #  Valve B – bağlantı
    # ──────────────────────────────────────────────────────────

    def _connect_b(self):
        try:
            self.ctrl_b.address = int(self.addr_b.text(), 16)
            self.ctrl_b.connect(self.port_b.currentText(), int(self.baud_b.currentText()))
            self.status_b.setText("● Connected")
            self.status_b.setStyleSheet("color:#4CAF50;")
            self.conn_b_btn.setEnabled(False)
            self.disc_b_btn.setEnabled(True)
            self.test_b_btn.setEnabled(True)
            remember_value("valve_b_port", self.port_b.currentText())
            remember_value("valve_b_baud", self.baud_b.currentText())
            remember_value("valve_b_addr", self.addr_b.text())
            self.log_signal.emit(f"Valve B connected to {self.port_b.currentText()}")
            self._update_summary()
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Valve B: {e}")
            self.log_signal.emit(f"Valve B connection failed: {e}")

    def _disconnect_b(self):
        self.ctrl_b.disconnect()
        self.status_b.setText("● Disconnected")
        self.status_b.setStyleSheet("color:#F44336;")
        self.conn_b_btn.setEnabled(True)
        self.disc_b_btn.setEnabled(False)
        self.test_b_btn.setEnabled(False)
        self.log_signal.emit("Valve B disconnected")
        self._update_summary()

    def _test_b(self):
        r = self.ctrl_b.test_connection()
        msg = f"Connection OK. Status: {r.get('status_message', '')}" \
            if r.get("success") else r.get("error", "")
        (QMessageBox.information if r.get("success") else QMessageBox.critical)(
            self, "Valve B Test", msg)

    # ── Valve B – kontrol ─────────────────────────────────────

    def _set_load(self):
        if not self.ctrl_b.is_connected():
            QMessageBox.warning(self, "Warning", "Valve B not connected")
            return
        self.log_signal.emit("Valve B: Setting Load...")

        def _run():
            r = self.ctrl_b.set_load_position()
            if r.get("success"):
                time.sleep(0.3)
                self.ctrl_b.wait_for_completion(20.0)
                QMetaObject.invokeMethod(self.cur_state, "setText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "Load (1-6, 2-3, 4-5)"))
                QMetaObject.invokeMethod(self.status_b_lbl, "setText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "Load Position"))
                self.log_signal.emit("Valve B: Load position")

        threading.Thread(target=_run, daemon=True).start()

    def _set_inject(self):
        if not self.ctrl_b.is_connected():
            QMessageBox.warning(self, "Warning", "Valve B not connected")
            return
        self.log_signal.emit("Valve B: Setting Inject...")

        def _run():
            r = self.ctrl_b.set_inject_position()
            if r.get("success"):
                time.sleep(0.3)
                self.ctrl_b.wait_for_completion(20.0)
                QMetaObject.invokeMethod(self.cur_state, "setText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "Inject (1-2, 3-4, 5-6)"))
                QMetaObject.invokeMethod(self.status_b_lbl, "setText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "Inject Position"))
                self.log_signal.emit("Valve B: Inject position")

        threading.Thread(target=_run, daemon=True).start()

    def _reset_b(self):
        if not self.ctrl_b.is_connected():
            return

        def _run():
            r = self.ctrl_b.reset()
            if r.get("success"):
                time.sleep(0.3)
                self.ctrl_b.wait_for_completion(20.0)
                QMetaObject.invokeMethod(self.cur_state, "setText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "Inject (1-2, 3-4, 5-6)"))
                QMetaObject.invokeMethod(self.status_b_lbl, "setText",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, "Inject Position"))
                self.log_signal.emit("Valve B: Reset to Inject")

        threading.Thread(target=_run, daemon=True).start()

    def _stop_b(self):
        if self.ctrl_b.is_connected():
            self.ctrl_b.stop()
            self.status_b_lbl.setText("STOPPED")
            self.log_signal.emit("Valve B: STOP")

    def _query_b(self):
        if not self.ctrl_b.is_connected():
            return
        r = self.ctrl_b.get_current_state()
        if r.get("success"):
            self.cur_state.setText(r.get("state_name", "Unknown"))
            self.log_signal.emit(f"Valve B: {r.get('state_name', '')}")

    # ──────────────────────────────────────────────────────────
    #  Peristaltic Pump
    # ──────────────────────────────────────────────────────────

    def _set_pump_controls(self, enabled: bool):
        for b in [
            self.test_pump_btn, self.set_pump_mv_btn, self.set_pump_max_btn,
            self.set_pump_rpm_btn, self.pump_pulse_fwd_btn, self.pump_pulse_rev_btn,
            self.pump_run_fwd_btn, self.pump_run_rev_btn, self.stop_pump_btn,
        ]:
            b.setEnabled(enabled)

    def _connect_pump(self):
        port = self.port_pump.currentText()
        if not port:
            QMessageBox.warning(self, "Warning", "Select a pump COM port")
            return
        self.log_signal.emit(f"Pump: Connecting to {port} @ 115200...")
        ok = self.pump_ctrl.connect(port)
        if not ok:
            QMessageBox.critical(self, "Pump Connection Error", "Pump controller identity check failed.")
            self.log_signal.emit("Pump connection failed")
            return
        self.status_pump.setText("● Connected")
        self.status_pump.setStyleSheet("color:#4CAF50;")
        self.conn_pump_btn.setEnabled(False)
        self.disc_pump_btn.setEnabled(True)
        self.port_pump.setEnabled(False)
        self._set_pump_controls(True)
        remember_value("pump_com", port)
        self.log_signal.emit(f"Pump connected to {port}")
        self._update_summary()
        self._query_pump()

    def _disconnect_pump(self):
        self.pump_ctrl.disconnect()
        self.status_pump.setText("● Disconnected")
        self.status_pump.setStyleSheet("color:#F44336;")
        self.conn_pump_btn.setEnabled(True)
        self.disc_pump_btn.setEnabled(False)
        self.port_pump.setEnabled(True)
        self._set_pump_controls(False)
        self.pump_state_lbl.setText("Pump: Not connected")
        self.log_signal.emit("Pump disconnected")
        self._update_summary()

    def _pump_command(self, fn, label: str):
        if not self.pump_ctrl.is_connected():
            QMessageBox.warning(self, "Warning", "Pump not connected")
            return []
        try:
            lines = fn()
        except Exception as e:
            QMessageBox.critical(self, "Pump Error", str(e))
            self.log_signal.emit(f"Pump {label} failed: {e}")
            return []
        for line in lines:
            self.log_signal.emit(f"Pump [{label}]: {line}")
        self._log_pump_pending()
        return lines

    def _log_pump_pending(self):
        for line in self.pump_ctrl.read_available():
            self.log_signal.emit(f"Pump: {line}")
            if line == "OK STOP TIMEOUT":
                self.pump_state_lbl.setText("Pump: STOPPED")

    def _query_pump(self):
        lines = self._pump_command(self.pump_ctrl.status, "STATUS")
        if lines:
            self.pump_state_lbl.setText(lines[-1])

    def _set_pump_mv(self):
        mv = self.pump_mv_spin.value()
        self._pump_command(lambda: self.pump_ctrl.set_speed_mv(mv), "SPEEDV")

    def _set_pump_max_rpm(self):
        rpm = self.pump_max_rpm_spin.value()
        self._pump_command(lambda: self.pump_ctrl.set_max_rpm(rpm), "MAXRPM")

    def _set_pump_rpm(self):
        rpm = self.pump_rpm_spin.value()
        self._pump_command(lambda: self.pump_ctrl.set_speed_rpm(rpm), "SPEED")

    def _run_pump(self, direction: str):
        self._pump_command(lambda: self.pump_ctrl.run(direction), f"RUN {direction}")
        self.pump_state_lbl.setText(f"Pump: RUNNING {direction}")

    def _pulse_pump(self, direction: str):
        duration = self.pump_pulse_spin.value()
        self._pump_command(lambda: self.pump_ctrl.pulse(direction, duration), f"PULSE {direction}")
        self.pump_state_lbl.setText(f"Pump: PULSE {direction} {duration} ms")
        QTimer.singleShot(duration + 300, self._log_pump_pending)

    def _stop_pump(self):
        self._pump_command(self.pump_ctrl.stop, "STOP")
        self.pump_state_lbl.setText("Pump: STOPPED")

    # ──────────────────────────────────────────────────────────
    #  DropSens
    # ──────────────────────────────────────────────────────────

    def _dv_launch(self):
        self.dv_status.setText("● Starting...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("Launching DropView...")
        self.dv_ctrl.do_launch_dropview(log_fn=lambda msg: self.log_signal.emit(msg))

    def _dv_connect(self):
        self.dv_status.setText("● Connecting...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("Connecting DropSens...")
        com = self.port_dv.currentText()
        remember_value("dropsens_com", com)
        self.dv_ctrl.do_connect_dropsens(
            com_port=com,
            log_fn=lambda msg: self.log_signal.emit(msg)
        )

    def _dv_disconnect(self):
        self.dv_status.setText("● Disconnecting...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("Disconnecting DropSens...")
        self.dv_ctrl.do_disconnect_dropsens(log_fn=lambda msg: self.log_signal.emit(msg))

    def _dv_close(self):
        self.dv_status.setText("● Closing...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("Closing DropView...")
        self.dv_ctrl.do_exit_dropview(log_fn=lambda msg: self.log_signal.emit(msg))

    def _set_dv_btns(self, enabled: bool):
        for b in [self.dv_launch_btn, self.dv_close_btn,
                  self.dv_connect_btn, self.dv_disconnect_btn]:
            b.setEnabled(enabled)

    # ──────────────────────────────────────────────────────────
    #  Slot'lar
    # ──────────────────────────────────────────────────────────

    def _on_dv_action_done(self, action: str, success: bool):
        self._set_dv_btns(True)

    def _on_dv_status(self, connected: bool):
        if connected:
            self.dv_status.setText("● Connected")
            self.dv_status.setStyleSheet("color:#4CAF50;")
        else:
            self.dv_status.setText("● Disconnected")
            self.dv_status.setStyleSheet("color:#F44336;")

    def _update_summary(self, *_):
        lines = [
            f"Valve A (SV-01)   : "
            f"{'Connected - ' + self.port_a.currentText() if self.ctrl_a.is_connected() else 'Not connected'}",
            f"Valve B (SY-07B)  : "
            f"{'Connected - ' + self.port_b.currentText() if self.ctrl_b.is_connected() else 'Not connected'}",
            f"Pump (Nano)       : "
            f"{'Connected - ' + self.port_pump.currentText() if self.pump_ctrl.is_connected() else 'Not connected'}",
            f"DropSens          : "
            f"{'Connected (DropView 8400M)' if self.dv_ctrl._is_connected() else 'Not connected'}",
        ]
        self.summary_text.setPlainText("\n".join(lines))

    # ──────────────────────────────────────────────────────────
    #  Genel
    # ──────────────────────────────────────────────────────────

    def _stop_all(self):
        if self.ctrl_a.is_connected(): self.ctrl_a.stop()
        if self.ctrl_b.is_connected(): self.ctrl_b.stop()
        if self.pump_ctrl.is_connected():
            self.pump_ctrl.stop()
            self.pump_ctrl.set_speed_mv(0)
        self.status_a_lbl.setText("STOPPED")
        self.status_b_lbl.setText("STOPPED")
        self.pump_state_lbl.setText("Pump: STOPPED")
        self.log_signal.emit("EMERGENCY STOP ALL")

    def _query_all(self):
        self._query_a()
        self._query_b()
