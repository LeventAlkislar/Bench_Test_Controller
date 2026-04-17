# bench_test/ui/tabs/connection_tab.py
import serial.tools.list_ports

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QDoubleSpinBox, QGroupBox, QMessageBox, QScrollArea,
    QFrame, QSizePolicy, QTextEdit
)

from PyQt6.QtCore import pyqtSignal, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont

from bench_test.valve.multiport import ValveController, SV01Protocol
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.utils.paths import get_last, remember

from bench_test.ui.widgets import _btn, _lbl, _status_lbl


class ConnectionTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController, ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController):
        super().__init__()
        self.ctrl_a = ctrl_a; self.ctrl_b = ctrl_b; self.dv_ctrl = dv_ctrl
        self._build()
        self._refresh_ports()
        dv_ctrl.status_changed.connect(self._on_dv_status)
        dv_ctrl.status_changed.connect(self._on_dv_status_summary)
        dv_ctrl.action_done.connect(self._on_dv_action_done)
        dv_ctrl.log_message.connect(self.log_signal)

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

        # ── Valve A ───────────────────────────────────────────
        ga = QGroupBox("Valve A: SV-01 Multiport Valve (8-Port Selector)")
        fla = QVBoxLayout(ga)

        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("COM Port:"))
        self.port_a = QComboBox(); self.port_a.setMinimumWidth(100)
        row_a.addWidget(self.port_a)
        row_a.addWidget(QLabel("Baud:"))
        self.baud_a = QComboBox()
        self.baud_a.addItems(["9600","19200","38400","57600","115200"])
        row_a.addWidget(self.baud_a)
        row_a.addWidget(QLabel("Addr (hex):"))
        self.addr_a = QLineEdit("00"); self.addr_a.setMaximumWidth(40)
        row_a.addWidget(self.addr_a)
        row_a.addStretch()
        fla.addLayout(row_a)

        btn_a = QHBoxLayout()
        self.conn_a_btn  = _btn("Connect A",    self._connect_a,    "#4CAF50")
        self.disc_a_btn  = _btn("Disconnect A", self._disconnect_a, "#F44336")
        self.test_a_btn  = _btn("Test A",       self._test_a,       "#2196F3")
        self.disc_a_btn.setEnabled(False); self.test_a_btn.setEnabled(False)
        self.status_a    = _status_lbl()
        btn_a.addWidget(self.conn_a_btn); btn_a.addWidget(self.disc_a_btn)
        btn_a.addWidget(self.test_a_btn); btn_a.addWidget(self.status_a); btn_a.addStretch()
        fla.addLayout(btn_a)
        layout.addWidget(ga)

        # ── Valve B ───────────────────────────────────────────
        gb = QGroupBox("Valve B: SY-07B Injector Valve (6-Port, 2-State)")
        flb = QVBoxLayout(gb)

        row_b = QHBoxLayout()
        row_b.addWidget(QLabel("COM Port:"))
        self.port_b = QComboBox(); self.port_b.setMinimumWidth(100)
        row_b.addWidget(self.port_b)
        row_b.addWidget(QLabel("Baud:"))
        self.baud_b = QComboBox()
        self.baud_b.addItems(["9600","19200","38400","57600","115200"])
        row_b.addWidget(self.baud_b)
        row_b.addWidget(QLabel("Addr (hex):"))
        self.addr_b = QLineEdit("00"); self.addr_b.setMaximumWidth(40)
        row_b.addWidget(self.addr_b)
        row_b.addStretch()
        flb.addLayout(row_b)

        btn_b = QHBoxLayout()
        self.conn_b_btn  = _btn("Connect B",    self._connect_b,    "#4CAF50")
        self.disc_b_btn  = _btn("Disconnect B", self._disconnect_b, "#F44336")
        self.test_b_btn  = _btn("Test B",       self._test_b,       "#2196F3")
        self.disc_b_btn.setEnabled(False); self.test_b_btn.setEnabled(False)
        self.status_b    = _status_lbl()
        btn_b.addWidget(self.conn_b_btn); btn_b.addWidget(self.disc_b_btn)
        btn_b.addWidget(self.test_b_btn); btn_b.addWidget(self.status_b); btn_b.addStretch()
        flb.addLayout(btn_b)
        flb.addWidget(_lbl("States: 1=Load (1-6, 2-3, 4-5) | 2=Inject (1-2, 3-4, 5-6)", color="#888"))
        layout.addWidget(gb)

        # ── Refresh ports ─────────────────────────────────────
        layout.addWidget(_btn("Refresh COM Ports", self._refresh_ports))

        # ── Valve A Speed ─────────────────────────────────────
        gs = QGroupBox("Valve A Speed Control (Port Transition Speed)")
        fls = QVBoxLayout(gs)
        sr  = QHBoxLayout()
        sr.addWidget(QLabel("Speed (5-350 rpm):"))
        self.speed_spin = QSpinBox(); self.speed_spin.setRange(5, 350); self.speed_spin.setValue(350)
        self.speed_spin.setFixedWidth(80)
        sr.addWidget(self.speed_spin)
        sr.addStretch()
        fls.addLayout(sr)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Presets:"))
        for v, label in [(50,"Slow"),(150,"Medium"),(250,"Fast"),(350,"Max")]:
            pr.addWidget(_btn(f"{label} ({v})", lambda checked, s=v: self.speed_spin.setValue(s)))
        pr.addStretch()
        fls.addLayout(pr)

        sb = QHBoxLayout()
        self.set_spd_btn  = _btn("Set Temporary",  self._set_speed_dynamic,  "#FF9800")
        self.set_spd_btn.setEnabled(False)
        self.set_perm_btn = _btn("Set Permanent",  self._set_speed_permanent,"#795548")
        self.set_perm_btn.setEnabled(False)
        self.qry_spd_btn  = _btn("Query Speed",    self._query_speed,        "#607D8B")
        self.qry_spd_btn.setEnabled(False)
        self.speed_lbl = QLabel("Valve A Speed: Unknown")
        self.speed_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        sb.addWidget(self.set_spd_btn); sb.addWidget(self.set_perm_btn)
        sb.addWidget(self.qry_spd_btn); sb.addWidget(self.speed_lbl); sb.addStretch()
        fls.addLayout(sb)
        fls.addWidget(_lbl("Note: 'Temporary' resets on power cycle. 'Permanent' requires restart.", color="#888"))
        layout.addWidget(gs)

        # ── DropView / DropSens ───────────────────────────────
        gd = QGroupBox("DropSens - DropView 8400M")
        fld = QVBoxLayout(gd)

        row_prog = QHBoxLayout()
        self.dv_launch_btn = _btn("Start DropView", self._dv_launch, "#3F51B5", 130)
        self.dv_connect_btn    = _btn("Connect",    self._dv_connect,    "#4CAF50", 130)
        self.dv_disconnect_btn = _btn("Disconnect", self._dv_disconnect, "#F44336", 130)
        self.dv_close_btn  = _btn("Exit DropView",  self._dv_close,  "#9C27B0", 130)
        self.dv_status         = _status_lbl("● Disconnected")

        row_prog.addWidget(self.dv_launch_btn)
        row_prog.addWidget(self.dv_connect_btn)
        row_prog.addWidget(self.dv_disconnect_btn)
        row_prog.addWidget(self.dv_close_btn)
        row_prog.addWidget(self.dv_status)
        row_prog.addStretch()
        fld.addLayout(row_prog)

        fld.addWidget(_lbl(
            "Start DropView: Initiates DropView           |  Exit DropView: Terminates DropView (Alt+F4)\n"
            "Connect: Connects to DropSens (Ctrl+C)  |  Disconnect: Disconnects from DropSens (Ctrl+D)",
            color="#888"))

        layout.addWidget(gd)

        # ── Connection summary ────────────────────────────────
        gc = QGroupBox("Connection Summary")
        flc = QVBoxLayout(gc)
        self.summary_text = QTextEdit(); self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(100)
        flc.addWidget(self.summary_text)
        layout.addWidget(gc)
        layout.addStretch()

        self._update_summary()

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        for combo in [self.port_a, self.port_b]:
            current = combo.currentText()
            combo.clear()
            combo.addItems(ports)
            if current in ports: combo.setCurrentText(current)

    def _connect_a(self):
        try:
            self.ctrl_a.address = int(self.addr_a.text(), 16)
            self.ctrl_a.connect(self.port_a.currentText(), int(self.baud_a.currentText()))
            self.status_a.setText("● Connected"); self.status_a.setStyleSheet("color:#4CAF50;")
            self.conn_a_btn.setEnabled(False); self.disc_a_btn.setEnabled(True)
            self.test_a_btn.setEnabled(True);  self.set_spd_btn.setEnabled(True)
            self.set_perm_btn.setEnabled(True); self.qry_spd_btn.setEnabled(True)
            self.log_signal.emit(f"Valve A connected to {self.port_a.currentText()}")
            self._update_summary(); self._query_speed()
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Valve A: {e}")
            self.log_signal.emit(f"Valve A connection failed: {e}")

    def _disconnect_a(self):
        self.ctrl_a.disconnect()
        self.status_a.setText("● Disconnected"); self.status_a.setStyleSheet("color:#F44336;")
        self.conn_a_btn.setEnabled(True); self.disc_a_btn.setEnabled(False)
        self.test_a_btn.setEnabled(False); self.set_spd_btn.setEnabled(False)
        self.set_perm_btn.setEnabled(False); self.qry_spd_btn.setEnabled(False)
        self.speed_lbl.setText("Valve A Speed: Unknown")
        self.log_signal.emit("Valve A disconnected"); self._update_summary()

    def _test_a(self):
        r = self.ctrl_a.test_connection()
        if r.get("success"):
            msg = f"Connection OK. Status: {r.get('status_message','')}"
            QMessageBox.information(self, "Valve A Test", msg)
            self.log_signal.emit(f"Valve A test: {msg}")
        else:
            QMessageBox.critical(self, "Valve A Test", r.get("error", "Unknown"))

    def _set_speed_dynamic(self):
        r = self.ctrl_a.set_speed_dynamic(self.speed_spin.value())
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            self.speed_lbl.setText(f"Valve A Speed: {self.speed_spin.value()} rpm (temp)")
            self.log_signal.emit(f"Valve A speed set to {self.speed_spin.value()} rpm (temp)")
        else:
            QMessageBox.critical(self, "Error", r.get("error", r.get("status_message", "Failed")))

    def _set_speed_permanent(self):
        spd = self.speed_spin.value()
        if QMessageBox.question(self, "Confirm", f"Set permanent speed to {spd} rpm?\n(Device restart required)") \
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

    def _connect_b(self):
        try:
            self.ctrl_b.address = int(self.addr_b.text(), 16)
            self.ctrl_b.connect(self.port_b.currentText(), int(self.baud_b.currentText()))
            self.status_b.setText("● Connected"); self.status_b.setStyleSheet("color:#4CAF50;")
            self.conn_b_btn.setEnabled(False); self.disc_b_btn.setEnabled(True)
            self.test_b_btn.setEnabled(True)
            self.log_signal.emit(f"Valve B connected to {self.port_b.currentText()}")
            self._update_summary()
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", f"Valve B: {e}")

    def _disconnect_b(self):
        self.ctrl_b.disconnect()
        self.status_b.setText("● Disconnected"); self.status_b.setStyleSheet("color:#F44336;")
        self.conn_b_btn.setEnabled(True); self.disc_b_btn.setEnabled(False)
        self.test_b_btn.setEnabled(False)
        self.log_signal.emit("Valve B disconnected"); self._update_summary()

    def _test_b(self):
        r = self.ctrl_b.test_connection()
        msg = f"Connection OK. Status: {r.get('status_message','')}" if r.get("success") else r.get("error","")
        (QMessageBox.information if r.get("success") else QMessageBox.critical)(self, "Valve B Test", msg)

    def _dv_launch(self):
        self.dv_status.setText("● Başlatılıyor...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("DropView başlatılıyor...")
        self.dv_ctrl.do_launch_dropview(log_fn=lambda msg: self.log_signal.emit(msg))

    def _dv_connect(self):
        self.dv_status.setText("● Bağlanıyor...")
        self.dv_status.setStyleSheet("color:#FF9800;")
        self.log_signal.emit("DropSens bağlanıyor...")
        self.dv_ctrl.do_connect_dropsens(log_fn=lambda msg: self.log_signal.emit(msg))

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

    @pyqtSlot(str, bool)
    def _on_dv_action_done(self, action: str, success: bool):
        self._set_dv_btns(True)

    @pyqtSlot(bool)
    def _on_dv_status(self, connected):
        if connected:
            self.dv_status.setText("● Connected"); self.dv_status.setStyleSheet("color:#4CAF50;")
        else:
            self.dv_status.setText("● Disconnected"); self.dv_status.setStyleSheet("color:#F44336;")

    def _update_summary(self):
        lines = []
        lines.append(f"Valve A (SV-01)    : {'Connected - ' + self.port_a.currentText() if self.ctrl_a.is_connected() else 'Not connected'}")
        lines.append(f"Valve B (SY-07B)  : {'Connected - ' + self.port_b.currentText() if self.ctrl_b.is_connected() else 'Not connected'}")
        dv_connected = self.dv_ctrl._is_connected()
        lines.append(f"DropSens             : {'Connected (DropView 8400M)' if dv_connected else 'Not connected'}")
        self.summary_text.setPlainText("\n".join(lines))

    def _on_dv_status_summary(self, connected):
        self._update_summary()
