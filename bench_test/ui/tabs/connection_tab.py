# bench_test/ui/tabs/connection_tab.py
import serial.tools.list_ports

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QGroupBox, QMessageBox
)
from PyQt6.QtCore import pyqtSignal, QTimer
from PyQt6.QtGui import QColor

from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.utils.paths import get_last, remember


def _btn(label, slot):
    b = QPushButton(label)
    b.clicked.connect(slot)
    return b


class ConnectionTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController,
                 ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController):
        super().__init__()
        self.ctrl_a  = ctrl_a
        self.ctrl_b  = ctrl_b
        self.dv_ctrl = dv_ctrl
        self._build_ui()
        self._connect_signals()
        self.refresh_ports()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Valve A ───────────────────────────────────────────
        grp_a = QGroupBox("Valve A — SV-01 Multiport (8-port)")
        form_a = QFormLayout(grp_a)

        self.port_combo_a = QComboBox()
        refresh_a = _btn("Refresh", self.refresh_ports)
        row_a = QHBoxLayout()
        row_a.addWidget(self.port_combo_a)
        row_a.addWidget(refresh_a)
        form_a.addRow("COM Port:", row_a)

        self.baud_combo_a = QComboBox()
        self.baud_combo_a.addItems(["9600", "19200", "38400", "115200"])
        form_a.addRow("Baudrate:", self.baud_combo_a)

        btn_row_a = QHBoxLayout()
        self.conn_btn_a   = _btn("Connect",    self._connect_a)
        self.disconn_btn_a = _btn("Disconnect", self._disconnect_a)
        self.test_btn_a   = _btn("Test",        self._test_a)
        btn_row_a.addWidget(self.conn_btn_a)
        btn_row_a.addWidget(self.disconn_btn_a)
        btn_row_a.addWidget(self.test_btn_a)
        form_a.addRow("", btn_row_a)

        self.status_label_a = QLabel("Disconnected")
        self.status_label_a.setStyleSheet("color: red; font-weight: bold;")
        form_a.addRow("Status:", self.status_label_a)

        layout.addWidget(grp_a)

        # ── Valve B ───────────────────────────────────────────
        grp_b = QGroupBox("Valve B — SY-07B Injector (6-port)")
        form_b = QFormLayout(grp_b)

        self.port_combo_b = QComboBox()
        refresh_b = _btn("Refresh", self.refresh_ports)
        row_b = QHBoxLayout()
        row_b.addWidget(self.port_combo_b)
        row_b.addWidget(refresh_b)
        form_b.addRow("COM Port:", row_b)

        self.baud_combo_b = QComboBox()
        self.baud_combo_b.addItems(["9600", "19200", "38400", "115200"])
        form_b.addRow("Baudrate:", self.baud_combo_b)

        btn_row_b = QHBoxLayout()
        self.conn_btn_b    = _btn("Connect",    self._connect_b)
        self.disconn_btn_b = _btn("Disconnect", self._disconnect_b)
        self.test_btn_b    = _btn("Test",        self._test_b)
        btn_row_b.addWidget(self.conn_btn_b)
        btn_row_b.addWidget(self.disconn_btn_b)
        btn_row_b.addWidget(self.test_btn_b)
        form_b.addRow("", btn_row_b)

        self.status_label_b = QLabel("Disconnected")
        self.status_label_b.setStyleSheet("color: red; font-weight: bold;")
        form_b.addRow("Status:", self.status_label_b)

        layout.addWidget(grp_b)

        # ── DropView ──────────────────────────────────────────
        grp_dv = QGroupBox("DropView 8400M")
        form_dv = QFormLayout(grp_dv)

        btn_row_dv = QHBoxLayout()
        self.start_dv_btn  = _btn("Start DropView",  self._start_dropview)
        self.stop_dv_btn   = _btn("Exit DropView",   self._exit_dropview)
        btn_row_dv.addWidget(self.start_dv_btn)
        btn_row_dv.addWidget(self.stop_dv_btn)
        form_dv.addRow("", btn_row_dv)

        self.status_label_dv = QLabel("Unknown")
        self.status_label_dv.setStyleSheet("color: gray; font-weight: bold;")
        form_dv.addRow("Status:", self.status_label_dv)

        layout.addWidget(grp_dv)
        layout.addStretch()

    def _connect_signals(self):
        self.dv_ctrl.status_changed.connect(self._on_dv_status)

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        for combo in [self.port_combo_a, self.port_combo_b]:
            current = combo.currentText()
            combo.clear()
            combo.addItems(ports)
            if current in ports:
                combo.setCurrentText(current)

    def _connect_a(self):
        port = self.port_combo_a.currentText()
        baud = int(self.baud_combo_a.currentText())
        if not port:
            QMessageBox.warning(self, "Uyarı", "COM port seçilmedi.")
            return
        try:
            self.ctrl_a.connect(port, baud)
            remember("valve_a_port", port)
            self.status_label_a.setText("Connected")
            self.status_label_a.setStyleSheet("color: green; font-weight: bold;")
            self.log_signal.emit(f"Valve A bağlandı: {port}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    def _disconnect_a(self):
        self.ctrl_a.disconnect()
        self.status_label_a.setText("Disconnected")
        self.status_label_a.setStyleSheet("color: red; font-weight: bold;")
        self.log_signal.emit("Valve A bağlantısı kesildi.")

    def _test_a(self):
        r = self.ctrl_a.test_connection()
        if r.get("success"):
            self.log_signal.emit(f"Valve A test OK: {r.get('status_message')}")
        else:
            self.log_signal.emit(f"Valve A test FAILED: {r.get('error')}")

    def _connect_b(self):
        port = self.port_combo_b.currentText()
        baud = int(self.baud_combo_b.currentText())
        if not port:
            QMessageBox.warning(self, "Uyarı", "COM port seçilmedi.")
            return
        try:
            self.ctrl_b.connect(port, baud)
            remember("valve_b_port", port)
            self.status_label_b.setText("Connected")
            self.status_label_b.setStyleSheet("color: green; font-weight: bold;")
            self.log_signal.emit(f"Valve B bağlandı: {port}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", str(e))

    def _disconnect_b(self):
        self.ctrl_b.disconnect()
        self.status_label_b.setText("Disconnected")
        self.status_label_b.setStyleSheet("color: red; font-weight: bold;")
        self.log_signal.emit("Valve B bağlantısı kesildi.")

    def _test_b(self):
        r = self.ctrl_b.test_connection()
        if r.get("success"):
            self.log_signal.emit(f"Valve B test OK: {r.get('status_message')}")
        else:
            self.log_signal.emit(f"Valve B test FAILED: {r.get('error')}")

    def _start_dropview(self):
        self.log_signal.emit("DropView başlatılıyor...")
        self.dv_ctrl.launch_dropview()

    def _exit_dropview(self):
        self.log_signal.emit("DropView kapatılıyor...")
        self.dv_ctrl.exit_dropview()

    def _on_dv_status(self, connected: bool):
        if connected:
            self.status_label_dv.setText("Connected")
            self.status_label_dv.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.status_label_dv.setText("Disconnected")
            self.status_label_dv.setStyleSheet("color: red; font-weight: bold;")