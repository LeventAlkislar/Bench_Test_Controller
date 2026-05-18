# bench_test/dropview/controller.py
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bench_test.dropview import automator
from bench_test.dropview.vision import match_score_on_screen
from bench_test.config import (
    THRESHOLD_LOW,
    DROPVIEW_WINDOW_NAME,
)
from bench_test.utils.debug_log import debug_log


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

# ── Connection sekmesi için 4 ayrı aksiyon ─────────────────

    def do_launch_dropview(self, log_fn=None):
        """Sadece exe'yi başlatır, bağlanmaz."""
        def _run():
            try:
                automator.step_launch_dropview()
                if log_fn: log_fn("DropView launched.")
                self.force_poll()
            except Exception as e:
                if log_fn: log_fn(f"Launch error: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def do_connect_dropsens(self, com_port=None, log_fn=None):
        """Manuel COM port seçimi ile bağlanır; başarısız olursa Ctrl+C fallback."""
        def _run():
            try:
                automator.step_connect_dropsens(target_com=com_port, log_fn=log_fn)
                self._last_connected = True
                self.status_changed.emit(True)
                if log_fn: log_fn("DropSens connected.")
            except Exception as e:
                if log_fn: log_fn(f"Connect error: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def do_disconnect_dropsens(self, log_fn=None):
        """Sadece Ctrl+D ile bağlantıyı keser."""
        def _run():
            try:
                automator.step_disconnect_dropsens()
                self._last_connected = False
                self.status_changed.emit(False)
                if log_fn: log_fn("DropSens disconnected.")
            except Exception as e:
                if log_fn: log_fn(f"Disconnect error: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def do_start_dropview(self, log_fn=None) -> bool:
        """Recipe için: exe başlat + bağlan."""
        try:
            automator.step_start_dropview({}, log_fn=log_fn)
            self._last_connected = True
            self.status_changed.emit(True)
            if log_fn: log_fn("DropView launched and DropSens connected.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Start DropView error: {e}")
            return False

    def do_start_measure(self, scr_path: str, log_fn=None) -> bool:
        if not scr_path:
            if log_fn: log_fn("DropView: .scr file path is empty — measurement could not be started.")
            return False
        try:
            config = {"script_path": scr_path}
            debug_log(f"Loading script: {scr_path}")
            automator.step_start_measure(config, log_fn=log_fn)
            if log_fn: log_fn("Measurement started.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Start Measure error: {e}")
            return False

    def do_stop_measure(self, log_fn=None) -> bool:
        try:
            automator.step_stop_measure(log_fn=log_fn)
            if log_fn: log_fn("Measurement stopped.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Stop Measure error: {e}")
            return False

    def do_start_continuous_pad(
        self,
        tp_path: str,
        measurements_dir: str = "",
        part_number: str = "",
        log_fn=None,
    ) -> bool:
        if not tp_path:
            if log_fn: log_fn("DropView: .tp file path is empty - Continuous PAD could not be started.")
            return False
        if not measurements_dir:
            if log_fn: log_fn("DropView: measurements directory is empty - Continuous PAD could not be started.")
            return False
        if not part_number:
            if log_fn: log_fn("DropView: part number is empty - Continuous PAD could not be started.")
            return False
        try:
            automator.step_start_continuous_pad(
                {
                    "method_path": tp_path,
                    "measurements_dir": measurements_dir,
                    "part_number": part_number,
                },
                log_fn=log_fn,
            )
            if log_fn: log_fn("Continuous PAD segment started.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Continuous PAD start error: {e}")
            return False

    def do_stop_continuous_pad(self, log_fn=None) -> bool:
        try:
            automator.step_stop_continuous_pad(log_fn=log_fn)
            if log_fn: log_fn("Continuous PAD segment stopped.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Continuous PAD stop error: {e}")
            return False

    def do_run_cv(
        self,
        method_path: str,
        measurements_dir: str = "",
        part_number: str = "",
        log_fn=None,
    ) -> bool:
        if not method_path:
            if log_fn: log_fn("DropView: method file path is empty - CV could not be run.")
            return False
        if not measurements_dir:
            if log_fn: log_fn("DropView: measurements directory is empty - CV could not be run.")
            return False
        if not part_number:
            if log_fn: log_fn("DropView: part number is empty - CV could not be run.")
            return False
        try:
            path = automator.step_run_cv(
                {
                    "method_path": method_path,
                    "measurements_dir": measurements_dir,
                    "part_number": part_number,
                },
                log_fn=log_fn,
            )
            if log_fn: log_fn(f"CV measurement completed: {path}")
            return True
        except Exception as e:
            if log_fn: log_fn(f"CV run error: {e}")
            return False

    def do_exit_dropview(self, log_fn=None) -> bool:
        try:
            automator.step_exit_dropview({}, log_fn=log_fn)
            self._last_connected = False
            self.status_changed.emit(False)
            if log_fn: log_fn("DropView closed.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Exit DropView error: {e}")
            return False
