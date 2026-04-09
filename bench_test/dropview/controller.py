# bench_test/dropview/controller.py
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bench_test.dropview import automator
from bench_test.dropview.vision import match_score_on_screen
from bench_test.config import (
    THRESHOLD_LOW,
    DROPVIEW_WINDOW_NAME,
)


class DropViewController(QObject):
    """
    DropView 8400M yazilimini automator.py uzerinden yonetir.
    Tum islemler arka plan thread'lerinde calisir, GUI'yi bloke etmez.
    """
    status_changed = pyqtSignal(bool)
    log_message    = pyqtSignal(str)
    action_done    = pyqtSignal(str, bool)

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
                scores = automator.get_dropview_connection_scores()
                dv_open = scores["dv_open"]
                conn_sc = scores["connected_score"]
                disc_sc = scores["disconnected_score"]

                if not dv_open:
                    connected = False
                elif conn_sc < THRESHOLD_LOW and disc_sc < THRESHOLD_LOW:
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
            return automator._is_dropview_connected()
        except Exception:
            return False

    # ── 4 ana aksiyon ─────────────────────────────────────────

    def do_start_dropview(self, log_fn=None) -> bool:
        try:
            automator.step_start_dropview({})
            self._last_connected = True
            self.status_changed.emit(True)
            if log_fn:
                log_fn("DropView başlatıldı ve DropSens bağlandı.")
            return True
        except Exception as e:
            if log_fn:
                log_fn(f"Start DropView hatası: {e}")
            return False

    def do_start_measure(self, scr_path: str, log_fn=None) -> bool:
        if not scr_path:
            if log_fn:
                log_fn("DropView: .scr dosya yolu boş — ölçüm başlatılamadı.")
            return False
        try:
            config = {"script_path": scr_path}
            if log_fn:
                log_fn(f"Script yükleniyor: {scr_path}")
            automator.step_start_measure(config)
            if log_fn:
                log_fn("Ölçüm başlatıldı.")
            return True
        except Exception as e:
            if log_fn:
                log_fn(f"Start Measure hatası: {e}")
            return False

    def do_stop_measure(self, log_fn=None) -> bool:
        try:
            automator.step_stop_measure()
            if log_fn:
                log_fn("Ölçüm durduruldu.")
            return True
        except Exception as e:
            if log_fn:
                log_fn(f"Stop Measure hatası: {e}")
            return False

    def do_exit_dropview(self, log_fn=None) -> bool:
        try:
            automator.step_exit_dropview({}, log_fn=log_fn)
            self._last_connected = False
            self.status_changed.emit(False)
            if log_fn:
                log_fn("DropView kapatıldı.")
            return True
        except Exception as e:
            if log_fn:
                log_fn(f"Exit DropView hatası: {e}")
            return False

    # ── Geriye dönük uyumluluk ────────────────────────────────

    def start_dropview(self):
        threading.Thread(
            target=self.do_start_dropview,
            daemon=True
        ).start()

    def start_measurement(self, scr_path: str = ""):
        threading.Thread(
            target=self.do_start_measure,
            args=(scr_path,),
            daemon=True
        ).start()

    def stop_measurement(self):
        threading.Thread(
            target=self.do_stop_measure,
            daemon=True
        ).start()