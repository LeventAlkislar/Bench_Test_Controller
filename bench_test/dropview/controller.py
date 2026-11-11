# bench_test/dropview/controller.py
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bench_test.dropview import automator
from bench_test.dropview.windows_devices import list_dropsens_devices
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
        self.last_health = {}
        self.last_process_identity = None
        self.discrete_measurement_owner = None
        self.health_focus_requested = None
        self._poll_lock = threading.Lock()

    def start_polling(self):
        self._poll_timer.start()

    def stop_polling(self):
        self._poll_timer.stop()

    def _read_health(self, *, expected_owner=None, should_focus=None):
        health = automator.observe_dropview(expected_owner=expected_owner, should_focus=should_focus)
        identity = health["process_identity"]
        if health["dv_open"] is True and identity:
            self.last_process_identity = identity
        elif health["dv_open"] is False and identity is None:
            health["process_identity"] = self.last_process_identity
        self.last_health = health
        return health

    def observe_discrete_pad_activity(self, expected_owner, *, since, max_age):
        """Reuse a fresh observation of this measurement or take a passive sample."""
        if not expected_owner or not self._poll_lock.acquire(timeout=max_age):
            return "unknown"
        try:
            health = self.last_health
            sampled_at = health.get("sampled_at", 0)
            if (health.get("process_identity") == expected_owner and health.get("pad_observed")
                    and sampled_at >= since and 0 <= time.monotonic() - sampled_at < max_age):
                return health["pad_activity"]
            return self._read_health(expected_owner=expected_owner)["pad_activity"]
        except Exception as exc:
            debug_log(f"Discrete PAD observation failed: {exc}", level="warning")
            return "unknown"
        finally:
            self._poll_lock.release()

    def _poll_connection(self, *, force=False):
        if not force and not self._poll_lock.acquire(blocking=False):
            return

        def _check():
            if force:
                self._poll_lock.acquire()
            try:
                automator.dismiss_connection_error_dialogs()
                focus = self.health_focus_requested
                owner = self.discrete_measurement_owner if focus and focus() else None
                health = self._read_health(expected_owner=owner, should_focus=None if force else focus)
                if health["state"] == "unknown":
                    return
                connected = health["state"] == "connected"

                if force or connected != self._last_connected:
                    self._last_connected = connected
                    self.status_changed.emit(connected)
            except Exception as exc:
                debug_log(f"DropView connection polling failed: {exc}", level="warning")
            finally:
                self._poll_lock.release()
        threading.Thread(target=_check, daemon=True).start()

    def force_poll(self):
        self._poll_connection(force=True)

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

    def do_connect_dropsens(self, com_port=None, log_fn=None, *, device_address=None):
        """Connect by COM as usual, or resolve a named Bluetooth device first."""
        def _run():
            try:
                if device_address:
                    matches = [device for device in list_dropsens_devices() if device[0] == device_address]
                    if not matches:
                        raise RuntimeError("The selected DropSens has no Bluetooth COM port in Windows.")
                    if len(matches) != 1:
                        raise RuntimeError("Windows lists multiple COM ports for the selected DropSens.")
                    _, name, port = matches[0]
                    if log_fn:
                        log_fn(f"Connecting to {name} on {port}.")
                    automator.step_connect_dropsens(target_com=port, log_fn=log_fn, exact_port=True)
                else:
                    automator.step_connect_dropsens(target_com=com_port, log_fn=log_fn)
                self._last_connected = True
                self.status_changed.emit(True)
                if log_fn: log_fn("DropSens connected.")
            except Exception as e:
                if log_fn: log_fn(f"Connect error: {e}")
                if device_address:
                    self.action_done.emit("connect_named", False)
            else:
                if device_address:
                    self.action_done.emit("connect_named", True)
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

    def do_start_dropview(self, log_fn=None, *, allow_cleanup=True, cancelled=None) -> bool:
        """Recipe için: exe başlat + bağlan."""
        try:
            automator.step_start_dropview({}, log_fn=log_fn, allow_cleanup=allow_cleanup, cancelled=cancelled)
            self._last_connected = True
            self.status_changed.emit(True)
            if log_fn: log_fn("DropView launched and DropSens connected.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Start DropView error: {e}")
            return False

    def do_start_measure(self, scr_path: str, log_fn=None) -> bool:
        self.discrete_measurement_owner = None
        if not scr_path:
            if log_fn: log_fn("DropView: .scr file path is empty — measurement could not be started.")
            return False
        try:
            config = {"script_path": scr_path}
            debug_log(f"Loading script: {scr_path}")
            self.discrete_measurement_owner = automator.step_start_measure(config, log_fn=log_fn)
            if log_fn: log_fn("Measurement started.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Start Measure error: {e}")
            return False

    def do_reconnect_dropsens(self, expected_owner, log_fn=None) -> bool:
        """Restore the link in the original DropView process."""
        try:
            hwnd = automator.find_window(automator.DROPVIEW_WINDOW_NAME, timeout=3, pid=expected_owner["pid"])
            if automator.dropview_process_identity(hwnd) != expected_owner or not automator.dropview_responding(hwnd):
                raise RuntimeError("The original DropView session is unavailable.")
            automator.dismiss_connection_error_dialogs()
            automator.step_connect_dropsens(log_fn=log_fn, hwnd=hwnd, force_reconnect=True)
            if automator.dropview_process_identity(hwnd) != expected_owner:
                raise RuntimeError("The original DropView process changed during reconnection.")
            automator.step_stop_measure(log_fn=log_fn, require_editor=True,
                                       expected_owner=expected_owner, reopen_if_needed=True)
            self._last_connected = True
            self.status_changed.emit(True)
            if log_fn:
                log_fn("DropSens reconnected in the original DropView session.")
            return True
        except Exception as exc:
            if log_fn:
                log_fn(f"DropSens reconnect error: {exc}")
            return False

    def do_stop_measure(self, log_fn=None, require_editor=False, *, expected_owner=None, reopen_if_needed=False) -> bool:
        try:
            owner = (expected_owner or self.discrete_measurement_owner) if require_editor else None
            automator.step_stop_measure(log_fn=log_fn, require_editor=require_editor, expected_owner=owner,
                                        reopen_if_needed=reopen_if_needed)
            if log_fn: log_fn("Measurement stopped.")
            return True
        except Exception as e:
            if log_fn: log_fn(f"Stop Measure error: {e}")
            return False

    def do_reconcile_discrete_recovery(self, measurement_may_be_running: bool, log_fn=None, expected_owner=None) -> bool:
        """Do not interpret an absent editor as a verified stopped measurement."""
        try:
            if measurement_may_be_running and expected_owner:
                automator.verify_discrete_session_connection(expected_owner)
                return self.do_stop_measure(
                    log_fn=log_fn, require_editor=True, expected_owner=expected_owner, reopen_if_needed=True)

            editor_open = automator.window_exists(automator.MULTISCRIPT_WINDOW)
            if editor_open:
                if not self.do_stop_measure(log_fn=log_fn, require_editor=True, expected_owner=expected_owner):
                    return False
            elif measurement_may_be_running:
                raise RuntimeError(
                    "The previous measurement may still be active, but its Multiscript Editor "
                    "is unavailable. A stopped device cannot be verified automatically."
                )
            return True
        except Exception as exc:
            if log_fn:
                log_fn(f"Discrete PAD recovery blocked: {exc}")
            return False

    def do_restore_discrete_control(self, previous_owner=None, log_fn=None, *, allow_terminate=False, cancelled=None):
        return automator.restore_dropview_control(
            previous_owner, log_fn=log_fn, allow_terminate=allow_terminate, cancelled=cancelled)

    def do_stop_unresponsive_dropview(self, expected_owner, *, cancelled, log_fn=None) -> bool:
        try:
            automator.stop_unresponsive_dropview(expected_owner, cancelled=cancelled, log_fn=log_fn)
        except Exception as exc:
            if log_fn:
                log_fn(f"DropView hang recovery error: {exc}")
            return False
        return True

    def do_close_dropview_for_recovery(self, expected_owner, *, cancelled, log_fn=None) -> bool:
        try:
            automator.close_dropview_for_recovery(expected_owner, cancelled=cancelled, log_fn=log_fn)
        except Exception as exc:
            if log_fn:
                log_fn(f"DropView recovery close error: {exc}")
            return False
        if log_fn:
            log_fn("The original DropView process has exited; a fresh session can be started.")
        return True

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
