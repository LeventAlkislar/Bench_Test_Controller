# bench_test/ui/tabs/manual_tab.py
import threading
import time

import serial.tools.list_ports

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QGroupBox, QMessageBox, QScrollArea, QFrame, QTextEdit
)
from PyQt6.QtCore import pyqtSignal, Qt, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont

from bench_test.valve.multiport import ValveController, SV01Protocol
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.utils.paths import get_value, remember_value
from bench_test.ui.widgets import _btn, _lbl, _status_lbl


class ManualControlTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController, ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController):
        super().__init__()
        self.ctrl_a = ctrl_a
        self.ctrl_b = ctrl_b
        self.dv_ctrl = dv_ctrl
        self._build()
        self._restore_saved()
        self._refresh_ports()

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

        # Bağlantı ayarları
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
        fla.addLayout(row_a)

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
        fla.addLayout(btn_conn_a)

        fla.addWidget(self._separator())

        # Port butonları
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout()
        self.port_btns = []
        for i in range(1, 9):
            b = QPushButton(f"Port {i}")
            b.setMinimumSize(80, 40)
            b.clicked.connect(lambda checked, p=i: self._switch_port(p))
            grid.addWidget(b, (i - 1) // 4, (i - 1) % 4)
            self.port_btns.append(b)
        fla.addLayout(grid)

        cur_port_row = QHBoxLayout()
        cur_port_row.addWidget(QLabel("Current Port:"))
        self.cur_port = QLabel("Unknown")
        self.cur_port.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.cur_port.setStyleSheet("color:#2196F3;")
        cur_port_row.addWidget(self.cur_port)
        cur_port_row.addStretch()
        fla.addLayout(cur_port_row)

        ctrl_a_row = QHBoxLayout()
        ctrl_a_row.addWidget(_btn("Reset A", self._reset_a, "#FF9800"))
        ctrl_a_row.addWidget(_btn("Stop A",  self._stop_a,  "#F44336"))
        ctrl_a_row.addWidget(_btn("Query A", self._query_a, "#607D8B"))
        ctrl_a_row.addStretch()
        fla.addLayout(ctrl_a_row)

        self.status_a_lbl = QLabel("Ready")
        fla.addWidget(self.status_a_lbl)

        fla.addWidget(self._separator())

        # Hız kontrolü
        sr = QHBoxLayout()
        sr.addWidget(QLabel("Speed (5-350 rpm):"))
        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(5, 350)
        self.speed_spin.setValue(350)
        self.speed_spin.setFixedWidth(80)
        sr.addWidget(self.speed_spin)
        sr.addStretch()
        fla.addLayout(sr)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Presets:"))
        for v, label in [(50, "Slow"), (150, "Medium"), (250, "Fast"), (350, "Max")]:
            pr.addWidget(_btn(f"{label} ({v})", lambda checked, s=v: self.speed_spin.setValue(s)))
        pr.addStretch()
        fla.addLayout(pr)

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
        fla.addLayout(sb)
        fla.addWidget(_lbl("Not: 'Temporary' cihaz kapanınca sıfırlanır. 'Permanent' yeniden başlatma gerektirir.", color="#888"))

        return ga

    # ── Valve B ───────────────────────────────────────────────

    def _build_valve_b(self):
        gb = QGroupBox("Valve B: SY-07B Injector Valve (6-Port, 2-State)")
        flb = QVBoxLayout(gb)

        # Bağlantı ayarları
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
        flb.addLayout(row_b)

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
        flb.addLayout(btn_conn_b)
        flb.addWidget(_lbl("States: 1=Load (1-6, 2-3, 4-5) | 2=Inject (1-2, 3-4, 5-6)", color="#888"))

        flb.addWidget(self._separator())

        # Durum kontrolü
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
        flb.addLayout(sb_row)

        sr = QHBoxLayout()
        sr.addWidget(QLabel("Current State:"))
        self.cur_state = QLabel("Unknown")
        self.cur_state.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.cur_state.setStyleSheet("color:#9C27B0;")
        sr.addWidget(self.cur_state)
        sr.addStretch()
        flb.addLayout(sr)

        ctrl_b_row = QHBoxLayout()
        ctrl_b_row.addWidget(_btn("Reset B", self._reset_b, "#FF9800"))
        ctrl_b_row.addWidget(_btn("Stop B",  self._stop_b,  "#F44336"))
        ctrl_b_row.addWidget(_btn("Query B", self._query_b, "#607D8B"))
        ctrl_b_row.addStretch()
        flb.addLayout(ctrl_b_row)

        self.status_b_lbl = QLabel("Ready")
        flb.addWidget(self.status_b_lbl)

        return gb

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
            "Start DropView: DropView'i başlatır           |  Exit DropView: DropView'i kapatır (Alt+F4)\n"
            "Connect: DropSens bağlantısı kurar (Ctrl+C)  |  Disconnect: DropSens bağlantısını keser (Ctrl+D)",
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
        if saved_a:  self.port_a.setCurrentText(saved_a)
        if saved_b:  self.port_b.setCurrentText(saved_b)
        if saved_dv and self.port_dv.findText(saved_dv) >= 0:
            self.port_dv.setCurrentText(saved_dv)

    # ──────────────────────────────────────────────────────────
    #  Port yenileme
    # ──────────────────────────────────────────────────────────

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        for combo in [self.port_a, self.port_b]:
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
                self.log_signal.emit(f"HATA _switch_port: {e}")

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
    #  DropSens
    # ──────────────────────────────────────────────────────────

    def _dv_launch(self):
        self.dv_status.setText("● Başlatılıyor...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("DropView başlatılıyor...")
        self.dv_ctrl.do_launch_dropview(log_fn=lambda msg: self.log_signal.emit(msg))

    def _dv_connect(self):
        self.dv_status.setText("● Bağlanıyor...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("DropSens bağlanıyor...")
        com = self.port_dv.currentText()
        remember_value("dropsens_com", com)
        self.dv_ctrl.do_connect_dropsens(
            com_port=com,
            log_fn=lambda msg: self.log_signal.emit(msg)
        )

    def _dv_disconnect(self):
        self.dv_status.setText("● Bağlantı kesiliyor...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("DropSens bağlantısı kesiliyor...")
        self.dv_ctrl.do_disconnect_dropsens(log_fn=lambda msg: self.log_signal.emit(msg))

    def _dv_close(self):
        self.dv_status.setText("● Kapatılıyor...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("DropView kapatılıyor...")
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
        self.status_a_lbl.setText("STOPPED")
        self.status_b_lbl.setText("STOPPED")
        self.log_signal.emit("EMERGENCY STOP ALL")

    def _query_all(self):
        self._query_a()
        self._query_b()
