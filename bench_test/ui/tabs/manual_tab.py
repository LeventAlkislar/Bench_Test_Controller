# bench_test/ui/tabs/manual_tab.py
import threading
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QSplitter,
    QDoubleSpinBox, QGroupBox, QMessageBox, QFrame, QSizePolicy,
    QTextEdit, QScrollArea, QAbstractItemView
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont, QColor
from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.ui.widgets import _btn, _lbl
from datetime import datetime

class ManualControlTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController, ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController):
        super().__init__()
        self.ctrl_a = ctrl_a; self.ctrl_b = ctrl_b; self.dv_ctrl = dv_ctrl
        self._build()
        dv_ctrl.status_changed.connect(self._on_dv_status)

    def _on_dv_status(self, connected):
        if connected:
            self.dv_state_lbl.setText("Connected")
            self.dv_state_lbl.setStyleSheet("color:#4CAF50;")
        else:
            self.dv_state_lbl.setText("Disconnected")
            self.dv_state_lbl.setStyleSheet("color:#F44336;")

    def _build(self):
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        outer.addWidget(splitter)

        left = QWidget()
        ll = QVBoxLayout(left); ll.setSpacing(8)

        # Valve A
        ga = QGroupBox("Valve A: SV-01 (8-Port Selector)")
        gal = QVBoxLayout(ga)
        grid = QGridLayout()
        self.port_btns = []
        for i in range(1, 9):
            b = QPushButton(f"Port {i}")
            b.setMinimumSize(80, 40)
            b.clicked.connect(lambda checked, p=i: self._switch_port(p))
            grid.addWidget(b, (i-1)//4, (i-1)%4)
            self.port_btns.append(b)
        gal.addLayout(grid)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Current Port:"))
        self.cur_port = QLabel("Unknown")
        self.cur_port.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.cur_port.setStyleSheet("color:#2196F3;")
        pr.addWidget(self.cur_port); pr.addStretch()
        gal.addLayout(pr)

        ctrl_a = QHBoxLayout()
        ctrl_a.addWidget(_btn("Reset A",  self._reset_a,  "#FF9800"))
        ctrl_a.addWidget(_btn("Stop A",   self._stop_a,   "#F44336"))
        ctrl_a.addWidget(_btn("Query A",  self._query_a,  "#607D8B"))
        ctrl_a.addStretch()
        gal.addLayout(ctrl_a)

        self.status_a_lbl = QLabel("Ready")
        self.speed_a_lbl  = QLabel("Speed: Unknown"); self.speed_a_lbl.setStyleSheet("color:#4CAF50;")
        gal.addWidget(self.status_a_lbl); gal.addWidget(self.speed_a_lbl)
        ll.addWidget(ga)

        # Valve B
        gb = QGroupBox("Valve B: SY-07B (6-Port Injector)")
        gbl = QVBoxLayout(gb)
        sb_row = QHBoxLayout()
        self.load_btn   = QPushButton("Load\n(1-6, 2-3, 4-5)"); self.load_btn.setMinimumSize(140, 60)
        self.inject_btn = QPushButton("Inject\n(1-2, 3-4, 5-6)"); self.inject_btn.setMinimumSize(140, 60)
        self.load_btn.clicked.connect(self._set_load)
        self.inject_btn.clicked.connect(self._set_inject)
        sb_row.addWidget(self.load_btn); sb_row.addWidget(self.inject_btn); sb_row.addStretch()
        gbl.addLayout(sb_row)

        sr = QHBoxLayout()
        sr.addWidget(QLabel("Current State:"))
        self.cur_state = QLabel("Unknown")
        self.cur_state.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.cur_state.setStyleSheet("color:#9C27B0;")
        sr.addWidget(self.cur_state); sr.addStretch()
        gbl.addLayout(sr)

        ctrl_b = QHBoxLayout()
        ctrl_b.addWidget(_btn("Reset B", self._reset_b, "#FF9800"))
        ctrl_b.addWidget(_btn("Stop B",  self._stop_b,  "#F44336"))
        ctrl_b.addWidget(_btn("Query B", self._query_b, "#607D8B"))
        ctrl_b.addStretch()
        gbl.addLayout(ctrl_b)

        self.status_b_lbl = QLabel("Ready")
        gbl.addWidget(self.status_b_lbl)
        ll.addWidget(gb)

        # DropSens
        gdv = QGroupBox("DropSens - DropView 8400M")
        gdvl = QVBoxLayout(gdv)
        dv_state_row = QHBoxLayout()
        dv_state_row.addWidget(QLabel("Current State:"))
        self.dv_state_lbl = QLabel("Disconnected")
        self.dv_state_lbl.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.dv_state_lbl.setStyleSheet("color:#F44336;")
        dv_state_row.addWidget(self.dv_state_lbl)
        dv_state_row.addStretch()
        gdvl.addLayout(dv_state_row)
        ll.addWidget(gdv)

        # Combined
        gc = QGroupBox("Combined Controls")
        gcl = QVBoxLayout(gc)
        gcl.addWidget(_btn("Emergency Stop ALL", self._stop_all, "#B71C1C", 200))
        gcl.addWidget(_btn("Query All Status", self._query_all, "#455A64"))
        ll.addWidget(gc)
        ll.addStretch()

        # Log
        right = QWidget()
        rl = QVBoxLayout(right)

        lg = QGroupBox("Activity Log")
        lgl = QVBoxLayout(lg)
        self.manual_log = QTextEdit(); self.manual_log.setReadOnly(True)
        self.manual_log.setFont(QFont("Consolas", 9))
        lgl.addWidget(self.manual_log)
        rl.addWidget(lg, 1)

        lb = QHBoxLayout()
        lb.addWidget(_btn("Clear Log", self.manual_log.clear))
        lb.addWidget(_btn("Save Log",  self._save_log))
        lb.addStretch()
        rl.addLayout(lb)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([500, 380])

    def _switch_port(self, port):
        if not self.ctrl_a.is_connected():
            QMessageBox.warning(self, "Warning", "Valve A not connected")
            return
        self.status_a_lbl.setText(f"Switching to Port {port}...")
        self._log(f"Valve A: Switching to port {port}...")

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
                    self._log(f"Valve A: At port {port}")
                else:
                    QMetaObject.invokeMethod(self.status_a_lbl, "setText",
                                             Qt.ConnectionType.QueuedConnection,
                                             Q_ARG(str, f"Failed: {r.get('error', '')}"))
                    self._log(f"Valve A switch failed: {r.get('error', '')}")
            except Exception as e:
                self._log(f"HATA _switch_port: {e}")
                import traceback
                self._log(traceback.format_exc())

        threading.Thread(target=_run, daemon=True).start()


    def _reset_a(self):
        if not self.ctrl_a.is_connected(): return
        self._log("Valve A: Resetting...")
        def _run():
            r = self.ctrl_a.reset()
            if r.get("success"):
                time.sleep(0.3); self.ctrl_a.wait_for_completion(20.0)
                self.cur_port.setText("Home"); self.status_a_lbl.setText("At Home")
                self._log("Valve A: Reset complete")
        threading.Thread(target=_run, daemon=True).start()

    def _stop_a(self):
        if self.ctrl_a.is_connected():
            self.ctrl_a.stop(); self.status_a_lbl.setText("STOPPED"); self._log("Valve A: STOP")

    def _query_a(self):
        if not self.ctrl_a.is_connected(): return
        r = self.ctrl_a.get_current_port()
        if r.get("success"):
            p = r.get("param1", 0)
            self.cur_port.setText("Home" if p == 0xFF else f"Port {p}")
            self._log(f"Valve A: port={p}")

    def _set_load(self):
        if not self.ctrl_b.is_connected():
            QMessageBox.warning(self, "Warning", "Valve B not connected"); return
        self._log("Valve B: Setting Load...")
        def _run():
            r = self.ctrl_b.set_load_position()
            if r.get("success"):
                time.sleep(0.3); self.ctrl_b.wait_for_completion(20.0)
                self.cur_state.setText("Load (1-6, 2-3, 4-5)")
                self.status_b_lbl.setText("Load Position"); self._log("Valve B: Load position")
        threading.Thread(target=_run, daemon=True).start()

    def _set_inject(self):
        if not self.ctrl_b.is_connected():
            QMessageBox.warning(self, "Warning", "Valve B not connected"); return
        self._log("Valve B: Setting Inject...")
        def _run():
            r = self.ctrl_b.set_inject_position()
            if r.get("success"):
                time.sleep(0.3); self.ctrl_b.wait_for_completion(20.0)
                self.cur_state.setText("Inject (1-2, 3-4, 5-6)")
                self.status_b_lbl.setText("Inject Position"); self._log("Valve B: Inject position")
        threading.Thread(target=_run, daemon=True).start()

    def _reset_b(self):
        if not self.ctrl_b.is_connected(): return
        def _run():
            r = self.ctrl_b.reset()
            if r.get("success"):
                time.sleep(0.3); self.ctrl_b.wait_for_completion(20.0)
                self.cur_state.setText("Inject (1-2, 3-4, 5-6)")
                self.status_b_lbl.setText("Inject Position"); self._log("Valve B: Reset to Inject")
        threading.Thread(target=_run, daemon=True).start()

    def _stop_b(self):
        if self.ctrl_b.is_connected():
            self.ctrl_b.stop(); self.status_b_lbl.setText("STOPPED"); self._log("Valve B: STOP")

    def _query_b(self):
        if not self.ctrl_b.is_connected(): return
        r = self.ctrl_b.get_current_state()
        if r.get("success"):
            self.cur_state.setText(r.get("state_name","Unknown"))
            self._log(f"Valve B: {r.get('state_name','')}")

    def _stop_all(self):
        if self.ctrl_a.is_connected(): self.ctrl_a.stop()
        if self.ctrl_b.is_connected(): self.ctrl_b.stop()
        self.status_a_lbl.setText("STOPPED"); self.status_b_lbl.setText("STOPPED")
        self._log("EMERGENCY STOP ALL")

    def _query_all(self):
        self._query_a(); self._query_b()

    def _log(self, msg):
        self.log_signal.emit(msg)
        ts = datetime.now().strftime("%H:%M:%S")
        self.manual_log.append(f"[{ts}] {msg}")

    def _save_log(self):
        path = save_file(self, "Logu Kaydet", "log_dir", "Text (*.txt)", ".txt")
        if path:
            with open(path, "w", encoding="utf-8") as f: f.write(self.manual_log.toPlainText())

    def update_port_display(self, port_text):
        self.cur_port.setText(port_text)

    def update_state_display(self, state_text):
        self.cur_state.setText(state_text)
