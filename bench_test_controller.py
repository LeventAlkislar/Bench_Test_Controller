# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Bench Test Controller v1.0
==========================
Dual Valve Controller + DropSens/DropView entegrasyonu.

Cihazlar:
  - Valve A : SV-01 Multiport Valve (8-port selector)
  - Valve B : SY-07B Injector Valve (6-port, 2-state)
  - DropSens: Metrohm DropView 8400M yazilimi uzerinden kontrol

Kurulum:
    pip install PyQt6 pyserial
    pip install pywin32 pyautogui numpy opencv-python Pillow  (DropView icin)
"""

import os
import sys
import json
import serial
import serial.tools.list_ports
import threading
import time
import traceback
import queue
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QHBoxLayout, QVBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QDoubleSpinBox, QTextEdit, QGroupBox, QFileDialog,
    QMessageBox, QSplitter, QScrollArea, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialog, QDialogButtonBox, QCheckBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QObject, pyqtSlot
)
from PyQt6.QtGui import QFont, QColor, QPalette, QBrush

from common import BASE_DIR, ASSETS_DIR, get_last, remember, open_file, save_file, open_dir

# ═══════════════════════════════════════════════════════════════
#  VERİ YAPILARI
# ═══════════════════════════════════════════════════════════════

from bench_test.recipe.models import StepLoop, RecipeStep, Recipe  # noqa — asıl kod oraya taşındı
@dataclass
class StepLoop:  # TODO: bench_test/recipe/models.py'ye taşındı, ileride silinecek
    start_step: int
    end_step: int
    loop_count: int


@dataclass
class RecipeStep:  # TODO: bench_test/recipe/models.py'ye taşındı, ileride silinecek
    port: int                    # Valve A portu (1-8), 0 = degisiklik yok
    duration_minutes: float
    description: str = ""
    valve_b_state: int = 0       # 0=yok, 1=Load, 2=Inject
    dropview_action: str = "none"  # "none" | "start_dropview" | "start_measure" | "stop_measure" | "exit_dropview"
    dropview_scr: str = ""         # .scr dosya yolu (bos = mevcut)


@dataclass
class Recipe:  # TODO: bench_test/recipe/models.py'ye taşındı, ileride silinecek
    name: str
    steps: List[RecipeStep]
    loop_count: int = 1
    step_loops: List[StepLoop] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  PROTOKOL
# ═══════════════════════════════════════════════════════════════

from bench_test.valve.protocol import SV01Protocol  # noqa — asıl kod oraya taşındı
class SV01Protocol:  # TODO: bu blok bench_test/valve/protocol.py'ye taşındı, silinecek
    START_CODE = 0xCC
    END_CODE   = 0xDD
    PASSWORD   = [0xFF, 0xEE, 0xBB, 0xAA]

    CMD_SWITCH_PORT      = 0x44
    CMD_RESET            = 0x45
    CMD_STRONG_STOP      = 0x49
    CMD_POLL_STATUS      = 0x4A
    CMD_SET_SPEED_DYNAMIC = 0x4B
    CMD_QUERY_PORT       = 0x3E
    CMD_QUERY_MAX_SPEED  = 0x27
    CMD_QUERY_RESET_SPEED = 0x2B
    CMD_SET_MAX_SPEED    = 0x07
    CMD_SET_RESET_SPEED  = 0x0B

    STATUS_NORMAL          = 0x00
    STATUS_FRAME_ERROR     = 0x01
    STATUS_PARAM_ERROR     = 0x02
    STATUS_OPTOCOUPLER_ERROR = 0x03
    STATUS_MOTOR_BUSY      = 0x04
    STATUS_TASK_SUSPENDED  = 0xFE
    STATUS_UNKNOWN_ERROR   = 0xFF

    STATUS_MESSAGES = {
        0x00: "Normal", 0x01: "Frame Error", 0x02: "Parameter Error",
        0x03: "Optocoupler Error", 0x04: "Motor Busy",
        0xFE: "Task Suspended (Motor Working)", 0xFF: "Unknown Error"
    }

    @staticmethod
    def calculate_checksum(data: bytes) -> int:
        return sum(data) & 0xFFFF

    @classmethod
    def build_command(cls, address, command, param1=0, param2=0):
        frame = bytes([cls.START_CODE, address, command, param1, param2, cls.END_CODE])
        cs = cls.calculate_checksum(frame)
        return frame + bytes([cs & 0xFF, (cs >> 8) & 0xFF])

    @classmethod
    def build_factory_command(cls, address, command, p1=0, p2=0, p3=0, p4=0):
        frame = bytes([cls.START_CODE, address, command,
                       cls.PASSWORD[0], cls.PASSWORD[1], cls.PASSWORD[2], cls.PASSWORD[3],
                       p1, p2, p3, p4, cls.END_CODE])
        cs = cls.calculate_checksum(frame)
        return frame + bytes([cs & 0xFF, (cs >> 8) & 0xFF])

    @classmethod
    def parse_response(cls, response: bytes) -> dict:
        if len(response) < 8:
            return {"success": False, "error": f"Response too short ({len(response)} bytes)"}
        if response[0] != cls.START_CODE or response[5] != cls.END_CODE:
            return {"success": False, "error": "Invalid frame markers"}
        expected = cls.calculate_checksum(response[:6])
        actual   = response[6] | (response[7] << 8)
        if expected != actual:
            return {"success": False, "error": f"Checksum mismatch"}
        status = response[2]
        return {
            "success": True, "address": response[1],
            "status": status, "status_message": cls.STATUS_MESSAGES.get(status, "Unknown"),
            "param1": response[3], "param2": response[4],
            "param_value": response[3] | (response[4] << 8),
            "raw": response.hex().upper()
        }


# ═══════════════════════════════════════════════════════════════
#  VALF KONTROLCÜLER
# ═══════════════════════════════════════════════════════════════

from bench_test.valve.multiport import ValveController  # noqa — asıl kod oraya taşındı
class ValveController:  # TODO: bench_test/valve/multiport.py'ye taşındı, ileride silinecek
    MIN_SPEED = 5; MAX_SPEED = 350; DEFAULT_SPEED = 200; SERIAL_TIMEOUT = 5.0

    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self.address = 0x00
        self.lock = threading.Lock()
        self.current_speed = self.DEFAULT_SPEED

    def connect(self, port, baudrate=9600, timeout=None):
        if timeout is None: timeout = self.SERIAL_TIMEOUT
        try:
            self.serial_port = serial.Serial(port=port, baudrate=baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=timeout)
            return True
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to open serial port: {e}")

    def disconnect(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None

    def is_connected(self):
        return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command, param1=0, param2=0, is_movement_cmd=False):
        if not self.is_connected(): return {"success": False, "error": "Not connected"}
        with self.lock:
            try:
                cmd = SV01Protocol.build_command(self.address, command, param1, param2)
                self.serial_port.reset_input_buffer()
                self.serial_port.write(cmd)
                response = self.serial_port.read(8)
                if len(response) == 0:
                    if is_movement_cmd:
                        return {"success": True, "status": SV01Protocol.STATUS_TASK_SUSPENDED,
                                "status_message": "Command sent (motor busy)", "timeout": True, "raw": "N/A"}
                    return {"success": False, "error": "No response (timeout)"}
                return SV01Protocol.parse_response(response)
            except serial.SerialException as e:
                return {"success": False, "error": f"Serial error: {e}"}

    def send_factory_command(self, command, p1=0, p2=0, p3=0, p4=0):
        if not self.is_connected(): return {"success": False, "error": "Not connected"}
        with self.lock:
            try:
                cmd = SV01Protocol.build_factory_command(self.address, command, p1, p2, p3, p4)
                self.serial_port.reset_input_buffer()
                self.serial_port.write(cmd)
                response = self.serial_port.read(8)
                if len(response) == 0: return {"success": False, "error": "No response"}
                return SV01Protocol.parse_response(response)
            except serial.SerialException as e:
                return {"success": False, "error": f"Serial error: {e}"}

    def test_connection(self): return self.send_command(SV01Protocol.CMD_POLL_STATUS)
    def switch_port(self, port): return self.send_command(SV01Protocol.CMD_SWITCH_PORT, port, 0, True)
    def reset(self): return self.send_command(SV01Protocol.CMD_RESET, is_movement_cmd=True)
    def stop(self): return self.send_command(SV01Protocol.CMD_STRONG_STOP)
    def get_current_port(self): return self.send_command(SV01Protocol.CMD_QUERY_PORT)
    def get_status(self): return self.send_command(SV01Protocol.CMD_POLL_STATUS)

    def get_max_speed(self):
        r = self.send_command(SV01Protocol.CMD_QUERY_MAX_SPEED)
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            r["speed_rpm"] = r.get("param_value", 0)
        return r

    def set_max_speed(self, speed_rpm):
        if not self.MIN_SPEED <= speed_rpm <= self.MAX_SPEED:
            return {"success": False, "error": f"Speed must be {self.MIN_SPEED}-{self.MAX_SPEED}"}
        p1 = speed_rpm & 0xFF; p2 = (speed_rpm >> 8) & 0xFF
        r = self.send_factory_command(SV01Protocol.CMD_SET_MAX_SPEED, p1, p2)
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            self.current_speed = speed_rpm
        return r

    def set_speed_dynamic(self, speed_rpm):
        if not self.MIN_SPEED <= speed_rpm <= self.MAX_SPEED:
            return {"success": False, "error": f"Speed must be {self.MIN_SPEED}-{self.MAX_SPEED}"}
        p1 = speed_rpm & 0xFF; p2 = (speed_rpm >> 8) & 0xFF
        r = self.send_command(SV01Protocol.CMD_SET_SPEED_DYNAMIC, p1, p2)
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            self.current_speed = speed_rpm
        return r

    def wait_for_completion(self, timeout=20.0):
        start = time.time()
        while time.time() - start < timeout:
            r = self.get_status()
            if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL: return True
            if r.get("status") in [SV01Protocol.STATUS_TASK_SUSPENDED, SV01Protocol.STATUS_MOTOR_BUSY]:
                time.sleep(0.2); continue
            time.sleep(0.1)
        return False


from bench_test.valve.injector import InjectorValveController  # noqa — asıl kod oraya taşındı
class InjectorValveController:  # TODO: bench_test/valve/injector.py'ye taşındı, ileride silinecek
    STATE_LOAD = 1; STATE_INJECT = 2
    STATE_NAMES = {1: "Load (1-6, 2-3, 4-5)", 2: "Inject (1-2, 3-4, 5-6)"}
    SERIAL_TIMEOUT = 5.0

    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self.address = 0x00
        self.lock = threading.Lock()
        self.current_state = 0

    def connect(self, port, baudrate=9600, timeout=None):
        if timeout is None: timeout = self.SERIAL_TIMEOUT
        try:
            self.serial_port = serial.Serial(port=port, baudrate=baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=timeout)
            return True
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to open serial port: {e}")

    def disconnect(self):
        if self.serial_port and self.serial_port.is_open: self.serial_port.close()
        self.serial_port = None; self.current_state = 0

    def is_connected(self): return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command, param1=0, param2=0, is_movement_cmd=False):
        if not self.is_connected(): return {"success": False, "error": "Not connected"}
        with self.lock:
            try:
                cmd = SV01Protocol.build_command(self.address, command, param1, param2)
                self.serial_port.reset_input_buffer(); self.serial_port.write(cmd)
                response = self.serial_port.read(8)
                if len(response) == 0:
                    if is_movement_cmd:
                        return {"success": True, "status": SV01Protocol.STATUS_TASK_SUSPENDED,
                                "status_message": "Command sent", "timeout": True, "raw": "N/A"}
                    return {"success": False, "error": "No response"}
                return SV01Protocol.parse_response(response)
            except serial.SerialException as e:
                return {"success": False, "error": f"Serial error: {e}"}

    def test_connection(self): return self.send_command(SV01Protocol.CMD_POLL_STATUS)

    def switch_state(self, state):
        if state not in [self.STATE_LOAD, self.STATE_INJECT]:
            return {"success": False, "error": "State must be 1 or 2"}
        r = self.send_command(SV01Protocol.CMD_SWITCH_PORT, state, 0, True)
        if r.get("success"): self.current_state = state
        return r

    def set_load_position(self):   return self.switch_state(self.STATE_LOAD)
    def set_inject_position(self): return self.switch_state(self.STATE_INJECT)

    def reset(self):
        r = self.send_command(SV01Protocol.CMD_RESET, is_movement_cmd=True)
        if r.get("success"): self.current_state = self.STATE_INJECT
        return r

    def stop(self): return self.send_command(SV01Protocol.CMD_STRONG_STOP)
    def get_status(self): return self.send_command(SV01Protocol.CMD_POLL_STATUS)

    def get_current_state(self):
        r = self.send_command(SV01Protocol.CMD_QUERY_PORT)
        if r.get("success"):
            s = r.get("param1", 0); r["state"] = s
            r["state_name"] = self.STATE_NAMES.get(s, "Unknown"); self.current_state = s
        return r

    def wait_for_completion(self, timeout=20.0):
        start = time.time()
        while time.time() - start < timeout:
            r = self.get_status()
            if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL: return True
            if r.get("status") in [SV01Protocol.STATUS_TASK_SUSPENDED, SV01Protocol.STATUS_MOTOR_BUSY]:
                time.sleep(0.2); continue
            time.sleep(0.1)
        return False


# ═══════════════════════════════════════════════════════════════
#  DROPVIEW KONTROLCÜ
# ═══════════════════════════════════════════════════════════════

from bench_test.dropview.controller import DropViewController  # noqa — asıl kod oraya taşındı
class DropViewController(QObject):  # TODO: bench_test/dropview/controller.py'ye taşındı, ileride silinecek
    """
    DropView 8400M yazilimini orchestrator.py uzerinden yonetir.
    Tum islemler arka plan thread'lerinde calisir, GUI'yi bloke etmez.
    """
    status_changed = pyqtSignal(bool)   # True = bagli
    log_message    = pyqtSignal(str)
    action_done    = pyqtSignal(str, bool)  # (action_name, success)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(3000)
        self._poll_timer.timeout.connect(self._poll_connection)
        self._last_connected = None

    def start_polling(self):
        self._poll_timer.start()

    def stop_polling(self):
        self._poll_timer.stop()

    def _poll_connection(self):
        def _check():
            try:
                import orchestrator as orch
                scores = orch.get_dropview_connection_scores()
                dv_open = scores["dv_open"]
                conn_sc = scores["connected_score"]
                disc_sc = scores["disconnected_score"]

                if not dv_open:
                    connected = False
                elif conn_sc < 0.4 and disc_sc < 0.4:
                    connected = False
                else:
                    connected = conn_sc > disc_sc

                if connected != self._last_connected:
                    self._last_connected = connected
                    self.status_changed.emit(connected)
            except Exception:
                pass
        threading.Thread(target=_check, daemon=True).start()

    def force_poll(self):
        def _check():
            connected = self._is_connected()
            self._last_connected = connected
            self.status_changed.emit(connected)
        threading.Thread(target=_check, daemon=True).start()

    @staticmethod
    def _is_connected() -> bool:
        try:
            import orchestrator as orch
            return orch._is_dropview_connected()
        except Exception:
            return False

    # ── Ayrıştırılmış 4 aksiyonlu GUI API ────────────────────

    def launch_dropview(self):
        """Sadece DropView.exe'yi başlatır (arka plan)."""
        def _run():
            try:
                import orchestrator as orch
                orch.step_launch_dropview({})
                self.log_message.emit("DropView: Program başlatıldı.")
                self.action_done.emit("launch", True)
            except Exception as e:
                self.log_message.emit(f"DropView: Başlatma hatası: {e}")
                self.action_done.emit("launch", False)
        threading.Thread(target=_run, daemon=True).start()

    def connect_dropsens(self):
        """Sadece DropSens'e bağlanır (arka plan)."""
        def _run():
            try:
                import orchestrator as orch
                orch.step_connect_dropsens({})
                self._last_connected = True
                self.log_message.emit("DropSens: Bağlantı kuruldu.")
                self.status_changed.emit(True)
                self.action_done.emit("connect", True)
            except Exception as e:
                self._last_connected = False
                self.log_message.emit(f"DropSens: Bağlantı hatası: {e}")
                self.status_changed.emit(False)
                self.action_done.emit("connect", False)
        threading.Thread(target=_run, daemon=True).start()

    def disconnect_dropsens(self):
        """DropSens bağlantısını keser (arka plan)."""
        def _run():
            try:
                import orchestrator as orch
                orch.step_disconnect_dropsens({})
                self._last_connected = False
                self.log_message.emit("DropSens: Bağlantı kesildi.")
                self.status_changed.emit(False)
                self.action_done.emit("disconnect", True)
            except Exception as e:
                self.log_message.emit(f"DropSens: Bağlantı kesme hatası: {e}")
                self.action_done.emit("disconnect", False)
        threading.Thread(target=_run, daemon=True).start()

    def close_dropview(self):
        """DropView programını Alt+F4 ile kapatır (arka plan)."""
        def _run():
            try:
                import orchestrator as orch
                orch.step_close_dropview({})
                self._last_connected = False
                self.log_message.emit("DropView: Program kapatıldı.")
                self.status_changed.emit(False)
                self.action_done.emit("close", True)
            except Exception as e:
                self.log_message.emit(f"DropView: Kapatma hatası: {e}")
                self.action_done.emit("close", False)
        threading.Thread(target=_run, daemon=True).start()

    # ── Recipe Runner tarafından çağrılan senkron adımlar ────

    def do_start_dropview(self, log_fn=None):
        """
        Recipe: Start DropView aksiyonu.
        DropView başlat + DropSens bağlan. Connected olana kadar bloke eder.
        """
        try:
            import orchestrator as orch
            orch.step_start_dropview({})
            self._last_connected = True
            self.status_changed.emit(True)
            if log_fn: log_fn("DropView başlatıldı ve DropSens bağlandı.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Start DropView hatası: {e}")
            return False

    def do_start_measure(self, scr_path: str, log_fn=None):
        """
        Recipe: Start Measure aksiyonu.
        Multiscript Editor'ü açar, script yükler, ölçümü başlatır.
        Sarı nokta görününce (ölçüm başladı) return eder.
        """
        if not scr_path:
            if log_fn: log_fn("Start Measure: .scr dosyası belirtilmedi.")
            return False
        try:
            import orchestrator as orch
            if log_fn: log_fn(f"Ölçüm başlatılıyor: {os.path.basename(scr_path)}")
            orch.step_start_measure({"script_path": scr_path})
            if log_fn: log_fn("Ölçüm başladı (sarı nokta görüldü).")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Start Measure hatası: {e}")
            return False

    def do_stop_measure(self, log_fn=None):
        """
        Recipe: Stop Measure aksiyonu.
        Stop butonuna basar, yeşil nokta gelince Exit'e basar.
        """
        try:
            import orchestrator as orch
            if log_fn: log_fn("Ölçüm durduruluyor...")
            orch.step_stop_measure()
            if log_fn: log_fn("Ölçüm durduruldu, Multiscript Editor kapatıldı.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Stop Measure hatası: {e}")
            return False

    def do_exit_dropview(self, log_fn=None):
        """
        Recipe: Exit DropView aksiyonu.
        Disconnect + DropView kapat.
        """
        try:
            import orchestrator as orch
            orch.step_exit_dropview({}, log_fn=log_fn)  # ← log_fn eklendi
            self._last_connected = False
            self.status_changed.emit(False)
            if log_fn: log_fn("DropView kapatıldı.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Exit DropView hatası: {e}")
            return False

    # ── Geriye dönük uyumluluk ────────────────────────────────

    def start_dropview(self):
        def _run():
            try:
                import orchestrator as orch
                orch.step_start_dropview({})
                self.log_message.emit("DropView: Bağlantı tamamlandı.")
                self.status_changed.emit(True)
                self._last_connected = True
            except Exception as e:
                self.log_message.emit(f"DropView: Başlama hatası: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def start_measurement(self, scr_path: str = ""):
        if not scr_path:
            self.log_message.emit("DropView: .scr dosya yolu boş — ölçüm başlatılamadı.")
            return
        def _run():
            try:
                import orchestrator as orch
                config = {"script_path": scr_path}
                self.log_message.emit(f"DropView: Script yükleniyor: {os.path.basename(scr_path)}")
                orch.step_start_measure(config)
                self.log_message.emit("DropView: Ölçüm başlatıldı.")
            except Exception as e:
                self.log_message.emit(f"DropView: Ölçüm başlatılamadı: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def stop_measurement(self):
        def _run():
            try:
                import orchestrator as orch
                orch.step_stop_measure()
                self.log_message.emit("DropView: Ölçüm durduruldu.")
            except Exception as e:
                self.log_message.emit(f"DropView: Durdurma hatası: {e}")
        threading.Thread(target=_run, daemon=True).start()


# ═══════════════════════════════════════════════════════════════
#  RECIPE RUNNER
# ═══════════════════════════════════════════════════════════════

# DropView aksiyon sabitleri
DROPVIEW_ACTIONS = ["none", "start_dropview", "start_measure", "stop_measure", "exit_dropview"]
DROPVIEW_LABELS  = {
    "none":           "None",
    "start_dropview": "Start DropView",
    "start_measure":  "Start Measure",
    "stop_measure":   "Stop Measure",
    "exit_dropview":  "Exit DropView",
}
# Süre 0'a izin verilen aksiyonlar (valve ayarına gerek kalmayan saf DropView adımları)
DROPVIEW_ZERO_DURATION_OK = {"start_dropview", "start_measure", "stop_measure", "exit_dropview"}


class RecipeRunner(threading.Thread):
    def __init__(self, controller_a, controller_b, recipe: Recipe,
                 status_queue: queue.Queue, stop_event: threading.Event,
                 dropview_ctrl: Optional[DropViewController] = None):
        super().__init__(daemon=True)
        self.controller_a   = controller_a
        self.controller_b   = controller_b
        self.recipe         = recipe
        self.status_queue   = status_queue
        self.stop_event     = stop_event
        self.dropview_ctrl  = dropview_ctrl
        self.pause_event    = threading.Event()
        self.pause_event.set()

    def _log(self, msg):
        self.status_queue.put(("log", msg))

    def _check_dropview_connected(self) -> bool:
        if self.dropview_ctrl is None:
            return True
        try:
            import orchestrator as orch
            return orch._is_dropview_connected()
        except Exception:
            return False

    def _execute_step(self, step: RecipeStep, step_num, total_steps, loop_info=""):
        if self.stop_event.is_set(): return False

        # ── DropView aksiyonu ──────────────────────────────────
        action = step.dropview_action
        dv = self.dropview_ctrl

        if action == "start_dropview" and dv:
            self._log("DropView başlatılıyor ve DropSens bağlanıyor...")
            ok = dv.do_start_dropview(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error",
                    f"Adım {step_num}: Start DropView başarısız."))
                return False

        elif action == "start_measure" and dv:
            if not self._check_dropview_connected():
                self.status_queue.put(("error",
                    f"Adım {step_num}: DropView bağlantısı yok — ölçüm başlatılamadı. "
                    "Lütfen önce Start DropView adımı ekleyin."))
                return False
            ok = dv.do_start_measure(step.dropview_scr, log_fn=self._log)
            if not ok:
                self.status_queue.put(("error",
                    f"Adım {step_num}: Start Measure başarısız."))
                return False

        elif action == "stop_measure" and dv:
            self._log("Ölçüm durduruluyor...")
            ok = dv.do_stop_measure(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error",
                    f"Adım {step_num}: Stop Measure başarısız."))
                return False

        elif action == "exit_dropview" and dv:
            self._log("DropView kapatılıyor...")
            ok = dv.do_exit_dropview(log_fn=self._log)
            if not ok:
                self.status_queue.put(("error",
                    f"Adım {step_num}: Exit DropView başarısız."))
                return False

        # ── Valf A ────────────────────────────────────────────
        if step.port > 0 and self.controller_a and self.controller_a.is_connected():
            self.status_queue.put(("switching", f"{loop_info}Valve A -> Port {step.port}"))
            r = self.controller_a.switch_port(step.port)
            if not r.get("success"):
                err = r.get("error", "")
                if "timeout" not in err.lower():
                    self.status_queue.put(("error", f"Valve A port switch failed: {err}")); return False
            time.sleep(0.3)
            self.controller_a.wait_for_completion(timeout=20.0)

        # ── Valf B ────────────────────────────────────────────
        if step.valve_b_state > 0 and self.controller_b and self.controller_b.is_connected():
            state_name = InjectorValveController.STATE_NAMES.get(step.valve_b_state, "")
            self.status_queue.put(("switching", f"{loop_info}Valve B -> {state_name}"))
            r = self.controller_b.switch_state(step.valve_b_state)
            if not r.get("success"):
                err = r.get("error", "")
                if "timeout" not in err.lower():
                    self.status_queue.put(("error", f"Valve B switch failed: {err}")); return False
            time.sleep(0.3)
            self.controller_b.wait_for_completion(timeout=20.0)

        # ── Süre 0 ise bekleme döngüsüne girilmez ─────────────
        if step.duration_minutes <= 0:
            return True

        # ── Süre bekleme döngüsü ──────────────────────────────
        parts = []
        if step.port > 0: parts.append(f"A:Port {step.port}")
        if step.valve_b_state > 0:
            parts.append(f"B:{'Load' if step.valve_b_state==1 else 'Inject'}")
        if action != "none": parts.append(f"DV:{DROPVIEW_LABELS.get(action, action)}")
        valve_str = ", ".join(parts) if parts else "No valve change"

        running_msg = f"{loop_info}Step {step_num}/{total_steps}: {valve_str} for {step.duration_minutes:.1f} min"
        if step.description:
            running_msg += f"  |  {step.description}"
        self.status_queue.put(("running", running_msg))

        duration_sec = step.duration_minutes * 60
        start_t = time.time()
        while time.time() - start_t < duration_sec:
            if self.stop_event.is_set(): return False
            self.pause_event.wait()
            elapsed   = time.time() - start_t
            remaining = (duration_sec - elapsed) / 60
            self.status_queue.put(("progress", {
                "step": step_num, "total_steps": total_steps,
                "port": step.port, "valve_b_state": step.valve_b_state,
                "remaining_minutes": remaining, "total_minutes": step.duration_minutes,
                "description": step.description, "loop_info": loop_info
            }))
            time.sleep(1.0)
        return True

    def run(self):
        try:
            # start_measure öncesinde start_dropview var mı kontrol et
            if self.dropview_ctrl:
                for i, step in enumerate(self.recipe.steps):
                    if step.dropview_action == "start_measure":
                        # Bu adımdan önce start_dropview var mı?
                        has_start_before = any(
                            s.dropview_action == "start_dropview"
                            for s in self.recipe.steps[:i]
                        )
                        # Yoksa şu an bağlı mı?
                        already_connected = self._check_dropview_connected()
                        if not has_start_before and not already_connected:
                            self.status_queue.put(("error",
                                                   "Recipe başlatılamadı: Bu recipe 'Start Measure' adımı içeriyor "
                                                   "ancak DropSens bağlantısı yok. "
                                                   "Lütfen önce bir 'Start DropView' adımı ekleyin veya "
                                                   "Connection sekmesinden DropSens'e bağlanın."))
                            return

            total = len(self.recipe.steps)
            for recipe_loop in range(self.recipe.loop_count):
                linfo = f"Recipe Loop {recipe_loop+1}/{self.recipe.loop_count}: " if self.recipe.loop_count > 1 else ""
                step_idx = 0
                while step_idx < total:
                    if self.stop_event.is_set():
                        self.status_queue.put(("stopped", "Recipe stopped by user")); return
                    step_loop = self._find_loop_starting_at(step_idx + 1)
                    if step_loop:
                        ls = step_loop.start_step - 1; le = step_loop.end_step
                        for it in range(step_loop.loop_count):
                            li = f"{linfo}Step Loop {it+1}/{step_loop.loop_count} (Steps {step_loop.start_step}-{step_loop.end_step}): "
                            for idx in range(ls, le):
                                if self.stop_event.is_set():
                                    self.status_queue.put(("stopped", "Recipe stopped")); return
                                if not self._execute_step(self.recipe.steps[idx], idx+1, total, li): return
                        step_idx = le
                    else:
                        if not self._execute_step(self.recipe.steps[step_idx], step_idx+1, total, linfo): return
                        step_idx += 1
            self.status_queue.put(("completed", "Recipe completed successfully!"))
        except Exception as e:
            self.status_queue.put(("error", f"Recipe error: {e}"))

    def _find_loop_starting_at(self, step_num):
        for loop in self.recipe.step_loops:
            if loop.start_step == step_num: return loop
        return None

    def pause(self):  self.pause_event.clear()
    def resume(self): self.pause_event.set()


# ═══════════════════════════════════════════════════════════════
#  YARDIMCI WIDGET'LAR
# ═══════════════════════════════════════════════════════════════

def _btn(text, callback=None, color=None, min_w=None):
    b = QPushButton(text)
    if callback: b.clicked.connect(callback)
    if color: b.setStyleSheet(f"QPushButton{{background:{color};color:white;border-radius:4px;padding:4px 8px;}}"
                               f"QPushButton:hover{{background:{color}dd;}}"
                               f"QPushButton:disabled{{background:#555;color:#888;}}")
    if min_w: b.setMinimumWidth(min_w)
    return b


def _lbl(text, bold=False, color=None, size=None):
    l = QLabel(text)
    f = l.font()
    if bold: f.setBold(True)
    if size: f.setPointSize(size)
    l.setFont(f)
    if color: l.setStyleSheet(f"color:{color};")
    return l


def _status_lbl(text="● Disconnected"):
    l = QLabel(text)
    l.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
    l.setStyleSheet("color:#F44336;")
    return l


def _group(title, layout=None):
    g = QGroupBox(title)
    if layout:
        g.setLayout(layout)
    return g


# ═══════════════════════════════════════════════════════════════
#  STEP LOOP DİALOG
# ═══════════════════════════════════════════════════════════════

class StepLoopDialog(QDialog):
    def __init__(self, parent, total_steps, existing_loops, selected_steps=None):
        super().__init__(parent)
        self.setWindowTitle("Set Step Loop Range")
        self.setFixedSize(400, 260)
        self.result_loop = None
        self.existing_loops = existing_loops
        self.total_steps = total_steps

        layout = QVBoxLayout(self)

        layout.addWidget(_lbl("Create a loop range for specific steps:", bold=True))
        layout.addWidget(_lbl("Steps within this range will repeat the specified number of times.", color="#888"))

        form = QFormLayout()
        self.start_spin = QSpinBox(); self.start_spin.setRange(1, total_steps)
        self.end_spin   = QSpinBox(); self.end_spin.setRange(1, total_steps)
        self.loop_spin  = QSpinBox(); self.loop_spin.setRange(1, 999); self.loop_spin.setValue(2)

        if selected_steps:
            self.start_spin.setValue(selected_steps[0])
            self.end_spin.setValue(selected_steps[-1])

        form.addRow("From Step:", self.start_spin)
        form.addRow("To Step:",   self.end_spin)
        form.addRow("Loop Count:", self.loop_spin)
        layout.addLayout(form)

        self.preview = QLabel("")
        self.preview.setStyleSheet("color:#4CAF50;")
        layout.addWidget(self.preview)

        self.start_spin.valueChanged.connect(self._update_preview)
        self.end_spin.valueChanged.connect(self._update_preview)
        self.loop_spin.valueChanged.connect(self._update_preview)
        self._update_preview()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _update_preview(self):
        s = self.start_spin.value(); e = self.end_spin.value(); n = self.loop_spin.value()
        if s > e:
            self.preview.setText("Warning: Start must be <= End"); self.preview.setStyleSheet("color:#F44336;")
        else:
            self.preview.setText(f"Steps {s} through {e} will repeat {n} times")
            self.preview.setStyleSheet("color:#4CAF50;")

    def _on_ok(self):
        s = self.start_spin.value(); e = self.end_spin.value(); n = self.loop_spin.value()
        if s > e:
            QMessageBox.critical(self, "Error", "Start step must be <= End step"); return
        for ex in self.existing_loops:
            if not (e < ex.start_step or s > ex.end_step):
                QMessageBox.critical(self, "Error",
                    f"Overlaps with existing loop (Steps {ex.start_step}-{ex.end_step})"); return
        self.result_loop = StepLoop(s, e, n)
        self.accept()


# ═══════════════════════════════════════════════════════════════
#  CONNECTION TAB
# ═══════════════════════════════════════════════════════════════

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
        self.speed_spin = QSpinBox(); self.speed_spin.setRange(5, 350); self.speed_spin.setValue(200)
        sr.addWidget(self.speed_spin)
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
        self.dv_close_btn  = _btn("Stop DropView",  self._dv_close,  "#9C27B0", 120)
        row_prog.addWidget(self.dv_launch_btn)
        row_prog.addWidget(self.dv_close_btn)
        row_prog.addStretch()
        fld.addLayout(row_prog)

        row_conn = QHBoxLayout()
        self.dv_connect_btn    = _btn("Connect",    self._dv_connect,    "#4CAF50", 90)
        self.dv_disconnect_btn = _btn("Disconnect", self._dv_disconnect, "#F44336", 100)
        self.dv_status         = _status_lbl("● Disconnected")
        row_conn.addWidget(self.dv_connect_btn)
        row_conn.addWidget(self.dv_disconnect_btn)
        row_conn.addWidget(self.dv_status)
        row_conn.addStretch()
        fld.addLayout(row_conn)

        fld.addWidget(_lbl(
            "Start DropView: Programı başlatır  |  Connect: DropSens'e bağlanır (Ctrl+C)\n"
            "Disconnect: Bağlantıyı keser (Ctrl+D)  |  Stop DropView: Programı kapatır (Alt+F4)",
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
        self.dv_status.setText("● Başlatılıyor..."); self.dv_status.setStyleSheet("color:#FF9800;")
        self._set_dv_btns(False)
        self.dv_ctrl.launch_dropview()

    def _dv_close(self):
        self.dv_status.setText("● Kapatılıyor..."); self.dv_status.setStyleSheet("color:#FF9800;")
        self._set_dv_btns(False)
        self.dv_ctrl.close_dropview()

    def _dv_connect(self):
        self.dv_status.setText("● Bağlanıyor..."); self.dv_status.setStyleSheet("color:#FF9800;")
        self._set_dv_btns(False)
        self.dv_ctrl.connect_dropsens()

    def _dv_disconnect(self):
        self.dv_status.setText("● Kesiliyor..."); self.dv_status.setStyleSheet("color:#FF9800;")
        self._set_dv_btns(False)
        self.dv_ctrl.disconnect_dropsens()

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
        lines.append(f"Valve A (SV-01):  {'Connected - ' + self.port_a.currentText() if self.ctrl_a.is_connected() else 'Not connected'}")
        lines.append(f"Valve B (SY-07B): {'Connected - ' + self.port_b.currentText() if self.ctrl_b.is_connected() else 'Not connected'}")
        dv_connected = self.dv_ctrl._is_connected()
        lines.append(f"DropSens:         {'Connected (DropView 8400M)' if dv_connected else 'Not connected'}")
        self.summary_text.setPlainText("\n".join(lines))

    def _on_dv_status_summary(self, connected):
        self._update_summary()


# ═══════════════════════════════════════════════════════════════
#  MANUAL CONTROL TAB
# ═══════════════════════════════════════════════════════════════

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
            QMessageBox.warning(self, "Warning", "Valve A not connected"); return
        self.status_a_lbl.setText(f"Switching to Port {port}...")
        self._log(f"Valve A: Switching to port {port}...")
        def _run():
            r = self.ctrl_a.switch_port(port)
            if r.get("success"):
                time.sleep(0.3); self.ctrl_a.wait_for_completion(20.0)
                self.cur_port.setText(f"Port {port}")
                self.status_a_lbl.setText(f"At Port {port}")
                self._log(f"Valve A: At port {port}")
            else:
                self.status_a_lbl.setText(f"Failed: {r.get('error','')}")
                self._log(f"Valve A switch failed: {r.get('error','')}")
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


# ═══════════════════════════════════════════════════════════════
#  RECIPE TAB
# ═══════════════════════════════════════════════════════════════

VALVE_B_LABELS   = {0: "-", 1: "Load", 2: "Inject"}
COL_STEP, COL_VA, COL_VB, COL_DUR, COL_DESC, COL_DV, COL_SCR, COL_LOOP = range(8)

# Aksiyonlar için combo box etiket listesi (sırayla)
_DV_COMBO_LABELS = [
    "None",
    "Start DropView",
    "Start Measure",
    "Stop Measure",
    "Exit DropView",
]
# Combo index → aksiyon anahtarı
_DV_IDX_TO_KEY = {
    0: "none",
    1: "start_dropview",
    2: "start_measure",
    3: "stop_measure",
    4: "exit_dropview",
}
_DV_KEY_TO_IDX = {v: k for k, v in _DV_IDX_TO_KEY.items()}


class RecipeTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController, ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController,
                 script_tab: "ScriptEditorTab" = None):
        super().__init__()
        self.ctrl_a = ctrl_a; self.ctrl_b = ctrl_b
        self.dv_ctrl = dv_ctrl
        self.script_tab = script_tab
        self.recipe_steps: List[RecipeStep] = []
        self.step_loops:   List[StepLoop]   = []
        self.recipe_runner: Optional[RecipeRunner] = None
        self.stop_event    = threading.Event()
        self.status_queue  = queue.Queue()
        self._ignoring_changes = False

        self._build()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_queue)
        self._poll_timer.start()

    def _build(self):
        layout = QVBoxLayout(self)

        # ── Recipe settings ───────────────────────────────────
        gs = QGroupBox("Recipe Settings")
        fls = QHBoxLayout(gs)
        fls.addWidget(QLabel("Recipe Name:"))
        self.name_edit = QLineEdit("New Recipe"); self.name_edit.setMaximumWidth(200)
        fls.addWidget(self.name_edit)
        fls.addWidget(QLabel("  Recipe Loops:"))
        self.loop_spin = QSpinBox(); self.loop_spin.setRange(1, 999); self.loop_spin.setValue(1)
        self.loop_spin.valueChanged.connect(self._update_total_time)
        fls.addWidget(self.loop_spin); fls.addStretch()
        layout.addWidget(gs)

        # ── Add step ──────────────────────────────────────────
        ga = QGroupBox("Add Recipe Step")
        fla = QHBoxLayout(ga)

        fla.addWidget(QLabel("Valve A:"))
        self.step_port = QSpinBox(); self.step_port.setRange(0, 8); self.step_port.setMaximumWidth(55)
        self.step_port.setToolTip("0 = no change, 1-8 = port")
        fla.addWidget(self.step_port)

        fla.addWidget(QLabel("Valve B:"))
        self.step_vb = QComboBox()
        self.step_vb.addItems(["0 - No change", "1 - Load (1-6,2-3,4-5)", "2 - Inject (1-2,3-4,5-6)"])
        fla.addWidget(self.step_vb)

        fla.addWidget(QLabel("Duration (min):"))
        self.step_dur = QDoubleSpinBox()
        self.step_dur.setRange(0, 9999)   # 0'a izin var
        self.step_dur.setValue(60)
        self.step_dur.setMaximumWidth(80)
        fla.addWidget(self.step_dur)

        fla.addWidget(QLabel("Desc:"))
        self.step_desc = QLineEdit(); self.step_desc.setMaximumWidth(120)
        fla.addWidget(self.step_desc)

        fla.addWidget(QLabel("DropView:"))
        self.step_dv = QComboBox()
        self.step_dv.addItems(_DV_COMBO_LABELS)
        fla.addWidget(self.step_dv)

        fla.addWidget(QLabel(".scr:"))
        self.step_scr = QLineEdit(); self.step_scr.setMaximumWidth(120)
        self.step_scr.setPlaceholderText("Boş = Script Editor sekmesindeki dosya")
        fla.addWidget(self.step_scr)
        fla.addWidget(_btn("...", self._browse_step_scr))
        fla.addWidget(_btn("Add Step", self._add_step, "#4CAF50"))
        layout.addWidget(ga)

        # ── Steps table ───────────────────────────────────────
        gt = QGroupBox("Recipe Steps (Double-click to edit | Select rows for step loops)")
        gtl = QVBoxLayout(gt)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Step #", "Valve A", "Valve B", "Duration (min)", "Description", "DropView Action", "Script (.scr)", "Loop Info"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        gtl.addWidget(self.table)
        layout.addWidget(gt, 1)

        # ── Step buttons ──────────────────────────────────────
        sb = QHBoxLayout()
        sb.addWidget(_btn("Remove Selected", self._remove_step))
        sb.addWidget(_btn("Move Up",         self._move_up))
        sb.addWidget(_btn("Move Down",       self._move_down))
        sb.addWidget(_btn("Duplicate",       self._duplicate))
        sb.addWidget(_btn("Clear All",       self._clear_all))
        sb.addWidget(QFrame())
        sb.addWidget(_btn("Set Step Loop",   self._set_loop,   "#9C27B0"))
        sb.addWidget(_btn("Clear Loops",     self._clear_loops,"#607D8B"))
        sb.addStretch()
        layout.addLayout(sb)

        # ── Loops display ─────────────────────────────────────
        gl = QGroupBox("Active Step Loops")
        gll = QVBoxLayout(gl)
        self.loops_lbl = QLabel("No step loops defined")
        self.loops_lbl.setStyleSheet("color:#9C27B0;")
        gll.addWidget(self.loops_lbl)
        layout.addWidget(gl)

        # ── File ops + total time ─────────────────────────────
        fb = QHBoxLayout()
        fb.addWidget(_btn("Save Recipe", self._save_recipe))
        fb.addWidget(_btn("Load Recipe", self._load_recipe))
        fb.addStretch()
        self.total_time_lbl = QLabel("Total Time: 0 min (0h 0m)")
        self.total_time_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        fb.addWidget(self.total_time_lbl)
        layout.addLayout(fb)

        # ── Execution ─────────────────────────────────────────
        ge = QGroupBox("Recipe Execution")
        gel = QVBoxLayout(ge)

        eb = QHBoxLayout()
        self.start_btn = _btn("Start Recipe", self._start_recipe, "#4CAF50", 130)
        self.pause_btn = _btn("Pause",        self._pause_recipe, "#FF9800", 80)
        self.stop_btn  = _btn("Stop Recipe",  self._stop_recipe,  "#F44336", 130)
        self.pause_btn.setEnabled(False); self.stop_btn.setEnabled(False)
        eb.addWidget(self.start_btn); eb.addWidget(self.pause_btn); eb.addWidget(self.stop_btn); eb.addStretch()
        gel.addLayout(eb)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setFont(QFont("Segoe UI", 11))
        gel.addWidget(self.status_lbl)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Progress:"))
        self.progress = QProgressBar(); self.progress.setMaximum(100); self.progress.setMinimumWidth(300)
        pr.addWidget(self.progress)
        self.progress_lbl = QLabel("0%")
        pr.addWidget(self.progress_lbl); pr.addStretch()
        gel.addLayout(pr)
        layout.addWidget(ge)

    # ── Step management ───────────────────────────────────────
    def _add_step(self):
        port     = self.step_port.value()
        duration = self.step_dur.value()
        desc     = self.step_desc.text()
        vb_str   = self.step_vb.currentText()
        vb_state = int(vb_str[0]) if vb_str and vb_str[0].isdigit() else 0
        dv_idx   = self.step_dv.currentIndex()
        dv_action = _DV_IDX_TO_KEY.get(dv_idx, "none")
        dv_scr = self.step_scr.text().strip()

        # Süre 0 sadece DropView aksiyonu olduğunda geçerli
        if duration <= 0 and dv_action == "none":
            QMessageBox.warning(self, "Uyarı",
                "Süre 0 yalnızca bir DropView aksiyonu seçildiğinde kullanılabilir.\n"
                "None aksiyonunda süre 0 girilemeez.")
            return

        # start_measure için .scr dosyası zorunlu
        if dv_action == "start_measure":
            if not dv_scr:
                editor_path = self.script_tab.get_current_scr_path() if self.script_tab else ""
                if editor_path:
                    dv_scr = editor_path
                    self.step_scr.setText(dv_scr)
                else:
                    last = get_last("scr_last_used", "")
                    if last and os.path.isfile(last):
                        dv_scr = last
                        self.step_scr.setText(dv_scr)
                    else:
                        QMessageBox.warning(self, "Uyarı",
                            "DropView 'Start Measure' için .scr dosyası gerekli.\n"
                            "Lütfen Script Editor'de bir dosya açın veya .scr kutusuna yol girin.")
                        return

        # En az bir aksiyon olmalı
        if port == 0 and vb_state == 0 and dv_action == "none":
            QMessageBox.warning(self, "Warning", "At least one action must be specified"); return

        step = RecipeStep(port=port, duration_minutes=duration, description=desc,
                          valve_b_state=vb_state, dropview_action=dv_action, dropview_scr=dv_scr)
        self.recipe_steps.append(step)
        self._refresh_table(); self._update_total_time()
        log_msg = (f"Adım eklendi: A=Port{port}, B={VALVE_B_LABELS[vb_state]}, "
                   f"DV={DROPVIEW_LABELS.get(dv_action, dv_action)}, {duration}min")
        if desc:
            log_msg += f"  |  {desc}"
        self.log_signal.emit(log_msg)

    def _browse_step_scr(self):
        p = open_file(self, "Script Dosyası Seç", "scr_open_dir",
                       "Script dosyası (*.scr);;Tüm dosyalar (*.*)")
        if p: self.step_scr.setText(p)

    def _refresh_table(self):
        self._ignoring_changes = True
        self.table.setRowCount(len(self.recipe_steps))
        for i, step in enumerate(self.recipe_steps):
            loop_info = self._get_loop_info(i + 1)
            vals = [
                str(i + 1),
                f"Port {step.port}" if step.port > 0 else "-",
                VALVE_B_LABELS.get(step.valve_b_state, "-"),
                str(step.duration_minutes),
                step.description,
                DROPVIEW_LABELS.get(step.dropview_action, "-"),
                os.path.basename(step.dropview_scr) if (step.dropview_action == "start_measure" and step.dropview_scr) else "--",
                loop_info
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j in (0, 7):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, item)
        self._ignoring_changes = False

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._ignoring_changes: return
        row = item.row(); col = item.column()
        if row >= len(self.recipe_steps): return
        step = self.recipe_steps[row]
        val  = item.text().strip()
        try:
            if col == COL_VA:
                p = int(val.replace("Port ", "")) if val != "-" else 0
                if 0 <= p <= 8: step.port = p
                else: raise ValueError()
            elif col == COL_VB:
                if val.strip().isdigit():
                    s = int(val.strip())
                    if 0 <= s <= 2: step.valve_b_state = s
                    else: raise ValueError()
                else:
                    rev = {v: k for k, v in VALVE_B_LABELS.items()}
                    step.valve_b_state = rev.get(val, 0)
            elif col == COL_DUR:
                d = float(val)
                if d >= 0:
                    # Süre 0 kontrolü: None aksiyonunda izin verme
                    if d <= 0 and step.dropview_action == "none":
                        pass  # Hata vermeden eski değeri koru
                    else:
                        step.duration_minutes = d
                else:
                    raise ValueError()
            elif col == COL_DESC:
                step.description = val
            elif col == COL_DV:
                _dv_map = {
                    "none": "none", "0": "none", "-": "none",
                    "start dropview": "start_dropview", "start_dropview": "start_dropview", "1": "start_dropview",
                    "start measure": "start_measure",   "start_measure": "start_measure",   "2": "start_measure",
                    "stop measure": "stop_measure",     "stop_measure": "stop_measure",     "3": "stop_measure",
                    "exit dropview": "exit_dropview",   "exit_dropview": "exit_dropview",   "4": "exit_dropview",
                }
                step.dropview_action = _dv_map.get(val.lower(), "none")
            elif col == COL_SCR:
                pass  # Çift tıkla ile güncellenir
        except (ValueError, TypeError):
            pass
        self._refresh_table(); self._update_total_time()

    def _on_cell_double_clicked(self, row: int, col: int):
        if col != COL_SCR: return
        if row >= len(self.recipe_steps): return
        step = self.recipe_steps[row]
        if step.dropview_action != "start_measure":
            return
        path = open_file(self, "Script Dosyası Seç", "scr_open_dir",
                         "Script dosyası (*.scr);;Tüm dosyalar (*.*)")
        if path:
            step.dropview_scr = path
            self._refresh_table()

    def _remove_step(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for r in rows:
            if r < len(self.recipe_steps): del self.recipe_steps[r]
        self.step_loops = [l for l in self.step_loops if l.end_step <= len(self.recipe_steps)]
        self._refresh_table(); self._update_loops_display(); self._update_total_time()

    def _move_up(self):
        rows = [i.row() for i in self.table.selectedItems()]
        if not rows: return
        r = rows[0]
        if r > 0:
            self.recipe_steps[r], self.recipe_steps[r-1] = self.recipe_steps[r-1], self.recipe_steps[r]
            self._refresh_table(); self.table.selectRow(r-1)

    def _move_down(self):
        rows = [i.row() for i in self.table.selectedItems()]
        if not rows: return
        r = rows[0]
        if r < len(self.recipe_steps) - 1:
            self.recipe_steps[r], self.recipe_steps[r+1] = self.recipe_steps[r+1], self.recipe_steps[r]
            self._refresh_table(); self.table.selectRow(r+1)

    def _duplicate(self):
        rows = [i.row() for i in self.table.selectedItems()]
        if not rows: return
        r = rows[0]
        if r < len(self.recipe_steps):
            s = self.recipe_steps[r]
            ns = RecipeStep(s.port, s.duration_minutes, s.description + " (copy)",
                            s.valve_b_state, s.dropview_action, s.dropview_scr)
            self.recipe_steps.insert(r+1, ns)
            self._refresh_table(); self._update_total_time()

    def _clear_all(self):
        if self.recipe_steps and QMessageBox.question(self, "Confirm", "Clear all steps and loops?") \
                == QMessageBox.StandardButton.Yes:
            self.recipe_steps.clear(); self.step_loops.clear()
            self._refresh_table(); self._update_loops_display(); self._update_total_time()

    def _set_loop(self):
        if not self.recipe_steps:
            QMessageBox.warning(self, "Warning", "Add steps first"); return
        rows = sorted({i.row() for i in self.table.selectedItems()})
        sel  = [r+1 for r in rows] if rows else None
        dlg  = StepLoopDialog(self, len(self.recipe_steps), self.step_loops, sel)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_loop:
            self.step_loops.append(dlg.result_loop)
            self._update_loops_display(); self._refresh_table(); self._update_total_time()

    def _clear_loops(self):
        if self.step_loops and QMessageBox.question(self, "Confirm", "Clear all step loops?") \
                == QMessageBox.StandardButton.Yes:
            self.step_loops.clear()
            self._update_loops_display(); self._refresh_table(); self._update_total_time()

    def _get_loop_info(self, step_num):
        for loop in self.step_loops:
            if loop.start_step <= step_num <= loop.end_step:
                return f"Loop x{loop.loop_count}"
        return ""

    def _update_loops_display(self):
        if not self.step_loops:
            self.loops_lbl.setText("No step loops defined"); return
        parts = [f"Steps {l.start_step}-{l.end_step} x{l.loop_count}"
                 for l in sorted(self.step_loops, key=lambda x: x.start_step)]
        self.loops_lbl.setText("  |  ".join(parts))

    def _update_total_time(self):
        if not self.recipe_steps:
            self.total_time_lbl.setText("Total Time: 0 min (0h 0m)"); return
        times = [s.duration_minutes for s in self.recipe_steps]
        processed = set()
        total = 0
        for loop in self.step_loops:
            t = sum(times[loop.start_step-1:loop.end_step]) * loop.loop_count
            total += t
            for i in range(loop.start_step-1, loop.end_step): processed.add(i)
        for i, t in enumerate(times):
            if i not in processed: total += t
        total *= self.loop_spin.value()
        h = int(total // 60); m = int(total % 60)
        self.total_time_lbl.setText(f"Total Time: {total:.1f} min ({h}h {m}m)")

    def _save_recipe(self):
        if not self.recipe_steps:
            QMessageBox.warning(self, "Uyarı", "Kaydedilecek adım yok"); return
        path = save_file(self, "Recipe Kaydet", "recipe_dir",
                          "JSON (*.json);;Tüm dosyalar (*.*)", ".json",
                          self.name_edit.text().replace(" ", "_") + ".json")
        if path:
            data = {"name": self.name_edit.text(), "loop_count": self.loop_spin.value(),
                    "steps": [asdict(s) for s in self.recipe_steps],
                    "step_loops": [asdict(l) for l in self.step_loops]}
            with open(path, "w") as f: json.dump(data, f, indent=2)
            self.log_signal.emit(f"Recipe saved: {path}")

    def _load_recipe(self):
        path = open_file(self, "Recipe Yükle", "recipe_dir",
                          "JSON (*.json);;Tüm dosyalar (*.*)")
        if path:
            try:
                with open(path) as f: data = json.load(f)
                self.name_edit.setText(data.get("name", ""))
                self.loop_spin.setValue(data.get("loop_count", 1))
                self.recipe_steps = [RecipeStep(**s) for s in data.get("steps", [])]
                self.step_loops   = [StepLoop(**l) for l in data.get("step_loops", [])]
                self._refresh_table(); self._update_loops_display(); self._update_total_time()
                self.log_signal.emit(f"Recipe loaded: {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load: {e}")

    def _start_recipe(self):
        if not self.ctrl_a.is_connected() and not self.ctrl_b.is_connected():
            QMessageBox.warning(self, "Warning", "No valves connected"); return
        if not self.recipe_steps:
            QMessageBox.warning(self, "Warning", "No recipe steps"); return

        self.stop_event.clear()
        recipe = Recipe(self.name_edit.text(), self.recipe_steps.copy(),
                        self.loop_spin.value(), self.step_loops.copy())
        self.recipe_runner = RecipeRunner(
            self.ctrl_a, self.ctrl_b, recipe, self.status_queue, self.stop_event,
            self.dv_ctrl)
        self.recipe_runner.start()

        self.start_btn.setEnabled(False); self.pause_btn.setEnabled(True); self.stop_btn.setEnabled(True)
        self.log_signal.emit(f"Recipe started: {recipe.name}")

    def _pause_recipe(self):
        if self.recipe_runner:
            if self.pause_btn.text() == "Pause":
                self.recipe_runner.pause(); self.pause_btn.setText("Resume")
                self.status_lbl.setText("PAUSED"); self.log_signal.emit("Recipe paused")
            else:
                self.recipe_runner.resume(); self.pause_btn.setText("Pause")
                self.log_signal.emit("Recipe resumed")

    def _stop_recipe(self):
        self.stop_event.set()
        self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False); self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(False); self.status_lbl.setText("Stopped")
        self.progress.setValue(0); self.progress_lbl.setText("0%")
        self.log_signal.emit("Recipe stopped")

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self.status_queue.get_nowait()
                if msg_type == "progress":
                    rem   = data.get("remaining_minutes", 0)
                    total = data.get("total_minutes", 1)
                    pct   = max(0, min(100, ((total - rem) / total) * 100)) if total > 0 else 100
                    parts = []
                    if data.get("port", 0) > 0:        parts.append(f"A:Port {data['port']}")
                    if data.get("valve_b_state", 0) > 0:
                        parts.append(f"B:{'Load' if data['valve_b_state']==1 else 'Inject'}")
                    li = data.get("loop_info", "")
                    st = f"{li}Step {data['step']}/{data['total_steps']}: {', '.join(parts) or 'No change'} - {rem:.1f} min remaining"
                    if data.get("description"): st += f" ({data['description']})"
                    self.status_lbl.setText(st)
                    self.progress.setValue(int(pct)); self.progress_lbl.setText(f"{pct:.0f}%")
                elif msg_type in ("switching", "running", "log"):
                    self.status_lbl.setText(str(data)); self.log_signal.emit(str(data))
                elif msg_type == "completed":
                    self.status_lbl.setText(f"COMPLETED: {data}")
                    self.progress.setValue(100); self.progress_lbl.setText("100%")
                    self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False)
                    self.stop_btn.setEnabled(False)
                    QMessageBox.information(self, "Recipe Complete", str(data))
                    self.log_signal.emit(str(data))
                elif msg_type == "stopped":
                    self.status_lbl.setText(f"Stopped: {data}"); self.log_signal.emit(str(data))
                elif msg_type == "error":
                    self.status_lbl.setText(f"ERROR: {data}")
                    self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False)
                    self.stop_btn.setEnabled(False)
                    QMessageBox.critical(self, "Recipe Error", str(data))
                    self.log_signal.emit(f"Recipe error: {data}")
                elif msg_type == "warning":
                    self.log_signal.emit(f"Warning: {data}")
        except queue.Empty:
            pass


# ═══════════════════════════════════════════════════════════════
#  SCRIPT EDITOR TAB
# ═══════════════════════════════════════════════════════════════

class ScriptEditorTab(QWidget):
    log_signal    = pyqtSignal(str)
    scr_changed   = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._current_path: str = ""
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        gf = QGroupBox("Script Dosyası")
        gfl = QVBoxLayout(gf)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Dosya:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Henüz kaydedilmedi...")
        self.path_edit.setReadOnly(True)
        path_row.addWidget(self.path_edit)
        gfl.addLayout(path_row)

        btn_row = QHBoxLayout()
        btn_row.addWidget(_btn("Yeni",           self._new,          "#607D8B"))
        btn_row.addWidget(_btn("Aç / Yükle",     self._load,         "#2196F3"))
        btn_row.addWidget(_btn("Kaydet",         self._save,         "#4CAF50"))
        btn_row.addWidget(_btn("Farklı Kaydet",  self._save_as,      "#FF9800"))
        btn_row.addStretch()
        gfl.addLayout(btn_row)
        layout.addWidget(gf)

        gp = QGroupBox("DropView Script Parametreleri")
        form = QFormLayout(gp)
        form.setSpacing(10)

        method_row = QWidget()
        mrl = QHBoxLayout(method_row); mrl.setContentsMargins(0,0,0,0)
        self.method_edit = QLineEdit()
        self.method_edit.setPlaceholderText("...PAD 5 Sample.tp")
        mrl.addWidget(self.method_edit)
        mrl.addWidget(_btn("...", self._browse_method))
        form.addRow("Method dosyası (.tp):", method_row)

        csv_row = QWidget()
        crl = QHBoxLayout(csv_row); crl.setContentsMargins(0,0,0,0)
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("...measurements.csv")
        crl.addWidget(self.csv_edit)
        crl.addWidget(_btn("...", self._browse_csv))
        form.addRow("CSV çıktı dosyası:", csv_row)

        repeat_row = QWidget()
        rrl = QHBoxLayout(repeat_row); rrl.setContentsMargins(0,0,0,0)
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 99999)
        self.repeat_spin.setValue(1440)
        self.repeat_spin.setMaximumWidth(100)
        rrl.addWidget(self.repeat_spin)
        rrl.addWidget(QLabel("tekrar"))
        rrl.addStretch()
        form.addRow("Tekrar sayısı:", repeat_row)

        wait_row = QWidget()
        wrl = QHBoxLayout(wait_row); wrl.setContentsMargins(0,0,0,0)
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(1.0, 3600.0)
        self.wait_spin.setValue(47.0)
        self.wait_spin.setDecimals(1)
        self.wait_spin.setMaximumWidth(100)
        wrl.addWidget(self.wait_spin)
        wrl.addWidget(QLabel("sn"))
        wrl.addStretch()
        form.addRow("Bekleme süresi:", wait_row)

        self.duration_lbl = QLabel("")
        self.duration_lbl.setStyleSheet("color:#888; font-style:italic;")
        form.addRow("Tahmini süre:", self.duration_lbl)
        self.repeat_spin.valueChanged.connect(self._update_duration)
        self.wait_spin.valueChanged.connect(self._update_duration)
        self._update_duration()

        self.method_edit.textChanged.connect(self._mark_modified)
        self.csv_edit.textChanged.connect(self._mark_modified)
        self.repeat_spin.valueChanged.connect(self._mark_modified)
        self.wait_spin.valueChanged.connect(self._mark_modified)
        self._modified = False

        layout.addWidget(gp)

        gx = QGroupBox("XML Önizleme")
        gxl = QVBoxLayout(gx)
        self.xml_preview = QTextEdit()
        self.xml_preview.setReadOnly(True)
        self.xml_preview.setFont(QFont("Consolas", 9))
        self.xml_preview.setMaximumHeight(200)
        gxl.addWidget(self.xml_preview)
        pb = QHBoxLayout()
        pb.addWidget(_btn("Önizlemeyi Güncelle", self._update_preview, "#607D8B"))
        pb.addStretch()
        gxl.addLayout(pb)
        layout.addWidget(gx)
        layout.addStretch()

    def _update_duration(self):
        r = self.repeat_spin.value()
        w = self.wait_spin.value()
        h = r * w / 3600
        self.duration_lbl.setText(f"~{h:.1f} saat  ({r} x {w} sn)")

    def _update_preview(self):
        try:
            from dropview_script_generator import generate_dropview_script
            import tempfile, os
            tmp = tempfile.NamedTemporaryFile(suffix=".scr", delete=False)
            tmp.close()
            generate_dropview_script(
                method_file=self.method_edit.text() or "METHOD_PATH",
                output_csv=self.csv_edit.text() or "OUTPUT.csv",
                repeat_times=self.repeat_spin.value(),
                wait_ms=int(self.wait_spin.value() * 1000),
                output_script_path=tmp.name
            )
            with open(tmp.name, "r", encoding="utf-8") as f:
                self.xml_preview.setPlainText(f.read())
            os.unlink(tmp.name)
        except Exception as e:
            self.xml_preview.setPlainText(f"Önizleme hatası: {e}")

    def _mark_modified(self):
        self._modified = True
        if self._current_path:
            self.path_edit.setText(f"{self._current_path}  ⚠ Kaydedilmemiş değişiklik")

    def _restore_last_scr(self):
        last = get_last("scr_last_used", "")
        if last and os.path.isfile(last):
            try:
                self._load_from_path(last)
                self.log_signal.emit(f"Son script yüklendi: {last}")
            except Exception:
                pass

    def _load_from_path(self, path: str):
        """Verilen yoldan .scr dosyasını yükler (iç kullanım)."""
        import xml.etree.ElementTree as ET
        tree = ET.parse(path)
        root = tree.getroot()
        actions = root.find("actions")

        load = actions.find(".//action[@type='LOADMETHOD']/file")
        if load is not None: self.method_edit.setText(load.text or "")

        exp = actions.find(".//action[@type='EXPORTCURVES']/file")
        if exp is not None: self.csv_edit.setText(exp.text or "")

        times = actions.find(".//action[@type='REPEAT']/times")
        if times is not None: self.repeat_spin.setValue(int(times.text))

        wait = actions.find(".//action[@type='WAIT']")
        if wait is not None:
            ms = int(wait.get("timeMS", 47000))
            self.wait_spin.setValue(ms / 1000)

        self._current_path = path
        self._modified = False
        self.path_edit.setText(path)
        remember("scr_last_used", path)
        self.scr_changed.emit(path)
        self._update_preview()

    def _new(self):
        self._current_path = ""
        self._modified = False
        self.path_edit.setText("")
        self.method_edit.setText("")
        self.csv_edit.setText("")
        self.repeat_spin.setValue(1440)
        self.wait_spin.setValue(47.0)
        self.xml_preview.clear()
        self.log_signal.emit("Yeni script formu açıldı.")

    def _load(self):
        path = open_file(self, "Script Dosyası Aç", "scr_open_dir",
                          "Script dosyası (*.scr);;Tüm dosyalar (*.*)")
        if not path: return
        try:
            self._load_from_path(path)
            if hasattr(self, 'method_edit') and self.method_edit.text():
                remember("method_dir", self.method_edit.text())
            if hasattr(self, 'csv_edit') and self.csv_edit.text():
                remember("csv_output_dir", self.csv_edit.text())
            self.log_signal.emit(f"Script yüklendi: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Dosya yüklenemedi: {e}")

    def _save(self):
        if not self._current_path:
            self._save_as(); return
        self._write_scr(self._current_path)

    def _save_as(self):
        path = save_file(self, "Script Dosyasını Kaydet", "scr_save_dir",
                          "Script dosyası (*.scr);;Tüm dosyalar (*.*)", ".scr")
        if not path: return
        self._current_path = path
        self.path_edit.setText(path)
        self._write_scr(path)

    def _write_scr(self, path: str):
        if not self.method_edit.text():
            QMessageBox.warning(self, "Uyarı", "Method dosyası seçilmedi."); return
        if not self.csv_edit.text():
            QMessageBox.warning(self, "Uyarı", "CSV çıktı yolu girilmedi."); return
        try:
            from dropview_script_generator import generate_dropview_script
            generate_dropview_script(
                method_file=self.method_edit.text(),
                output_csv=self.csv_edit.text(),
                repeat_times=self.repeat_spin.value(),
                wait_ms=int(self.wait_spin.value() * 1000),
                output_script_path=path
            )
            self._modified = False
            self.path_edit.setText(path)
            remember("scr_last_used", path)
            self.scr_changed.emit(path)
            self.log_signal.emit(f"Script kaydedildi: {path}")
            self._update_preview()
            QMessageBox.information(self, "Başarıldı", f"Script kaydedildi:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Kaydetme hatası: {e}")

    def _browse_method(self):
        p = open_file(self, "Method Dosyası Seç", "method_dir",
                       "DropView Method (*.tp);;Tüm dosyalar (*.*)")
        if p: self.method_edit.setText(p)

    def _browse_csv(self):
        p = save_file(self, "CSV Çıktı Dosyası", "csv_output_dir",
                       "CSV dosyası (*.csv)", ".csv")
        if p: self.csv_edit.setText(p)

    def get_current_scr_path(self) -> str:
        return self._current_path


# ═══════════════════════════════════════════════════════════════
#  LOG TAB
# ═══════════════════════════════════════════════════════════════

class LogTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.log = QTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log, 1)
        btns = QHBoxLayout()
        btns.addWidget(_btn("Clear Log",     self.log.clear))
        btns.addWidget(_btn("Save Log",      self._save))
        btns.addWidget(_btn("Zaman Damgasi", self._insert_timestamp))
        btns.addStretch()
        layout.addLayout(btns)

    def append(self, msg: str, desc: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        if desc:
            line += f"  |  {desc}"
        self.log.append(line)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _insert_timestamp(self):
        self.log.setReadOnly(False)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log.append(f"[{ts}] ")
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log.setTextCursor(cursor)
        self.log.setFocus()

    def _save(self):
        path = save_file(self, "Logu Kaydet", "log_dir", "Text (*.txt)", ".txt")
        if path:
            with open(path, "w", encoding="utf-8") as f: f.write(self.log.toPlainText())


# ═══════════════════════════════════════════════════════════════
#  ANA PENCERE
# ═══════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bench Test Controller v1.0")
        self.setMinimumSize(1200, 900)

        self.ctrl_a  = ValveController()
        self.ctrl_b  = InjectorValveController()
        self.dv_ctrl = DropViewController(self)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self.conn_tab   = ConnectionTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.manual_tab = ManualControlTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.script_tab = ScriptEditorTab()
        self.recipe_tab = RecipeTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl,
                                    self.script_tab)
        self.log_tab    = LogTab()

        tabs.addTab(self.conn_tab,    "Connection")
        tabs.addTab(self.manual_tab,  "Manual Control")
        tabs.addTab(self.script_tab,  "Script Editor")
        tabs.addTab(self.recipe_tab,  "Recipe Control")
        tabs.addTab(self.log_tab,     "Full Log")

        self.script_tab._restore_last_scr()

        for tab in [self.conn_tab, self.manual_tab, self.script_tab, self.recipe_tab]:
            tab.log_signal.connect(self.log_tab.append)

        self.dv_ctrl.start_polling()

    def closeEvent(self, event):
        if self.recipe_tab.recipe_runner and self.recipe_tab.recipe_runner.is_alive():
            reply = QMessageBox.question(self, "Confirm", "Recipe is running. Stop and exit?")
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore(); return
            self.recipe_tab.stop_event.set()
        self.dv_ctrl.stop_polling()
        self.ctrl_a.disconnect()
        self.ctrl_b.disconnect()
        event.accept()


# ═══════════════════════════════════════════════════════════════
#  GİRİŞ NOKTASI
# ═══════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
