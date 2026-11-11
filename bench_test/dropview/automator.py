# bench_test/dropview/automator.py
import os
import sys
import time
import threading
import subprocess
import ctypes
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import psutil
from serial.tools import list_ports
import pyautogui
from PIL import ImageOps
import win32api
import win32clipboard
import win32con
import win32gui
import win32process

from bench_test.config import (
    DROPVIEW_WINDOW_NAME, MULTISCRIPT_WINDOW, CONNECTING_DIALOG,
    WARNING_UNSAVED, THRESHOLD_HIGH, THRESHOLD_MID, THRESHOLD_LOW,
    SLEEP_AFTER_CLICK, SLEEP_AFTER_FOCUS, SLEEP_AFTER_COMMAND, SLEEP_AFTER_LAUNCH,
    TIMEOUT_WINDOW_OPEN, TIMEOUT_CONNECT, TIMEOUT_STOP_MEASURE, TIMEOUT_CLOSE_WINDOW,
    POLL_INTERVAL_NORMAL, POLL_INTERVAL_SLOW, DROPVIEW_EXE_CANDIDATES,
    PYAUTOGUI_FAILSAFE, PYAUTOGUI_PAUSE,
    DROPSENS_COM_PORTS,
    MANUAL_CONNECTION_WINDOW, ERROR_WINDOW,
    MSG_NO_DEVICE, MSG_POTENTIOSTAT_NOT_FOUND, MSG_POTENTIOSTAT_NULL,
)
from bench_test.dropview.vision import (
    find_on_screen, match_score_on_screen, match_cropped_image_score_on_window,
    connection_scores_on_window,
    wait_for_image, wait_for_image_gone,
    _grab_region, _images_equal, read_label_positions,
)
from bench_test.dropview.window_actions import (
    bring_to_front,
    safe_click,
    safe_find_on_screen,
    safe_hotkey,
    safe_press,
    safe_sequence,
    safe_wait_for_image,
    safe_wait_for_image_gone,
)
from bench_test.utils.paths import BASE_DIR, ASSETS_DIR, get_last, remember, get_value, remember_value
from bench_test.utils.debug_log import debug_log, _debug_log_path

# pyautogui ayarları
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE
pyautogui.PAUSE    = PYAUTOGUI_PAUSE

# Görüntü dosyaları
_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")

_IMG = {k: os.path.join(_IMG_DIR, v) for k, v in {
    "scripts_menu":        "scripts_menu.png",
    "load_btn":            "load_btn.png",
    "delete_btn":          "delete_btn.png",
    "run_btn":             "run_btn.png",
    "stop_btn":            "stop_btn.png",
    "exit_btn":            "exit_btn.png",
    "loaded_scripts":      "loaded_scripts_label.png",
    "yellow_dot_selected": "yellow_dot_selected.png",
    "green_dot_selected":  "green_dot_selected.png",
    "connected":                    "connected_status.png",
    "disconnected":                 "disconnected_status.png",
    "pad_status_run":              "Run.png",
    "pad_status_stopped":          "Stopped.png",
    "manual_conn_com3":             "manual_connection_com3.png",
    "manual_conn_com4":             "manual_connection_com4.png",
    "manual_conn_com10":            "manual_connection_com10.png",
    "manual_conn_connect_btn":      "manual_connection_connect_btn.png",
    "manual_conn_dropdown_arrow":   "manual_connection_dropdown_arrow.png",
    "error_no_device_text":         "No device connected.png",
    "error_potentiostat_text":      "Potentiostat not found.png",
    "autosave_as_menu_item":        "autosave_as_menu_item.png",
    "select_nodes_unchecked":       "select_nodes_to_apply_dialog.png",
    "select_nodes_checked":         "select_nodes_to_apply_checked_dialog.png",
    "cv_select_nodes_unchecked":    "Node 1 (CV) Unchecked.png",
    "cv_select_nodes_checked":      "Node 1 (CV) Checked.png",
    "accept_btn":                   "accept_btn.png",
    "save_as_node_dialog":          "save_as_node_dialog.png",
    "save_btn":                     "save_btn.png",
    "load_method_confirm_dialog":   "load_method_confirm_dialog.png",
    "yes_btn":                      "yes_btn.png",
    "load_method_open_dialog":      "load_method_open_dialog.png",
    "open_btn":                     "open_btn.png",
    "method_select_nodes_unchecked": "method_select_nodes_unchecked_dialog.png",
    "method_select_nodes_checked":  "method_select_nodes_checked_dialog.png",
    "method_select_nodes_accept":   "method_select_nodes_accept_btn.png",
    "select_all_btn":               "select_all_btn.png",
    "run_experiment_confirm_dialog": "run_experiment_confirm_dialog.png",
}.items()}


_WATCHDOG_POLL_INTERVAL = 0.2
_CONTINUOUS_PAD_FILE_TIMEOUT = 90.0
_CONTINUOUS_PAD_SEND_TIMEOUT = 90.0
_CONTINUOUS_PAD_STABLE_TIMEOUT = 90.0
_CONTINUOUS_PAD_STABLE_SECONDS = 2.0
_continuous_pad_autosave_key = None
_continuous_pad_method_path = None
_continuous_pad_measurements_dir = ""
_continuous_pad_active_file = ""
_continuous_pad_last_snapshot = set()
_cv_autosave_key = None
_cv_method_path = None
_dialog_watchdog_lock = threading.Lock()
_dialog_watchdog_thread = None
_dialog_watchdog_stop = None
_dialog_watchdog_log_fn = None
_dialog_watchdog_seen = {}
_dialog_watchdog_autodismiss = {WARNING_UNSAVED}
_warning_ack_lock = threading.Lock()
_connection_error_lock = threading.Lock()
_connection_error_waiters = {}
_POTENTIOSTAT_NULL = "potentiostat_null"
_USB_DISCONNECTED = "device_usb_disconnected"
_connection_faults = {}
_CONNECTION_ERRORS = {
    MSG_POTENTIOSTAT_NULL.casefold(): _POTENTIOSTAT_NULL,
    "device usb cable disconnected": _USB_DISCONNECTED,
}


def connection_fault(expected_owner, *, since=0):
    """Retain a confirmed link loss after its dialog has been acknowledged."""
    if not expected_owner:
        return None
    with _connection_error_lock:
        fault = _connection_faults.get(expected_owner["pid"])
        if fault and fault["owner"] == expected_owner and fault["sampled_at"] >= since:
            return dict(fault)
    return None


# ── Pencere yardımcıları ───────────────────────────────────────

def find_window(title_keyword, timeout=60, poll_interval=1.0, *, pid=None, include_hidden=False):
    """Başlığında title_keyword geçen ilk görünür pencereyi bulur."""
    start = time.time()
    while time.time() - start < timeout:
        found = []
        def _cb(hwnd, _):
            if ((include_hidden or win32gui.IsWindowVisible(hwnd))
                    and title_keyword in win32gui.GetWindowText(hwnd)
                    and (pid is None or get_window_pid(hwnd) == pid)):
                found.append(hwnd)
        win32gui.EnumWindows(_cb, None)
        if found:
            return found[0]
        time.sleep(poll_interval)
    raise TimeoutError(f"'{title_keyword}' window was not found within {timeout}s.")


def window_exists(title_keyword, include_hidden=False, *, pid=None) -> bool:
    """Başlığında title_keyword geçen görünür bir pencere var mı?"""
    found = []
    def _cb(hwnd, _):
        if include_hidden or win32gui.IsWindowVisible(hwnd):
            if (title_keyword in win32gui.GetWindowText(hwnd)
                    and (pid is None or get_window_pid(hwnd) == pid)):
                found.append(hwnd)
    win32gui.EnumWindows(_cb, None)
    return len(found) > 0


def wait_for_window_close(title_keyword, timeout=60, poll_interval=0.5, *, hwnd=None):
    """Başlığında title_keyword geçen pencere kapanana kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        visible = win32gui.IsWindowVisible(hwnd) if hwnd is not None else window_exists(title_keyword)
        if not visible:
            return
        time.sleep(poll_interval)
    raise TimeoutError(f"'{title_keyword}' penceresi {timeout}sn icinde kapanmadi.")

def _get_dropview_pid() -> int | None:
    """Çalışan DropView.exe process PID'ini döner, yoksa None."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if "dropview" in proc.info["name"].lower():
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

def _wait_for_dropview_process_exit(timeout=15, poll_interval=0.5):
    """DropView.exe process'i tamamen kapanana kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        if _get_dropview_pid() is None:
            return
        time.sleep(poll_interval)
    raise TimeoutError(
        f"DropView process did not exit within {timeout}s."
    )

def _focus_window(hwnd, click_title=False, restore_if_iconic=True, sleep_after=SLEEP_AFTER_CLICK,
                  *, require_foreground=True):
    focused = bring_to_front(hwnd, retries=1, click_title=click_title, restore_if_iconic=restore_if_iconic)
    if require_foreground and not focused:
        raise RuntimeError(f"Could not give keyboard focus to window hwnd={hwnd}.")
    time.sleep(sleep_after)

def _is_dropview_ui_ready(dv_hwnd, timeout=3.0) -> bool:
    """
    Quick readiness check for DropView UI before trusting early return.
    """
    try:
        wait_for_image(
            _IMG["scripts_menu"],
            timeout=timeout,
            poll_interval=POLL_INTERVAL_NORMAL,
            threshold=THRESHOLD_MID,
            hwnd=dv_hwnd,
            use_foreground_fallback=False,
        )
        return True
    except Exception:
        return False


def get_window_pid(hwnd) -> int | None:
    """Pencere handle'ından process PID'ini döner."""
    try:
        thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid if thread_id > 0 and pid > 0 else None
    except Exception:
        return None


def dropview_process_identity(hwnd):
    pid = get_window_pid(hwnd)
    if not pid:
        return None
    try:
        process = psutil.Process(pid)
        if _is_dropview_process(process):
            return {"pid": pid, "created": process.create_time()}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None


def _is_dropview_process(process):
    name = process.name().lower()
    return "dropview" in name or (name in ("java.exe", "javaw.exe") and _is_dropview_java_process(process))


def find_owned_dialogs(owner_hwnd) -> list[int]:
    """
    owner_hwnd'ye ait görünür modal/owned dialog pencerelerini döner.
    Kriter: doğrudan owner eşleşmesi VEYA (#32770 sınıfı + aynı process PID).
    Başka uygulamaların dialog'larını yakalamaz.
    """
    owner_pid = get_window_pid(owner_hwnd)
    dialogs = []
    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if hwnd == owner_hwnd:
            return
        direct_owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        if direct_owner == owner_hwnd:
            dialogs.append(hwnd)
            return
        if owner_pid and win32gui.GetClassName(hwnd) == "#32770":
            if get_window_pid(hwnd) == owner_pid:
                dialogs.append(hwnd)
    win32gui.EnumWindows(_cb, None)
    return dialogs

def close_owned_dialogs(owner_hwnd, log_fn=None) -> int:
    """
    owner_hwnd'ye ait tüm modal/owned dialog'ları Enter ile kapatır.
    Kapatılan dialog sayısını döner.
    """
    closed = 0
    for dlg_hwnd in find_owned_dialogs(owner_hwnd):
        try:
            title = win32gui.GetWindowText(dlg_hwnd)
            _focus_window(dlg_hwnd, restore_if_iconic=False)
            pyautogui.press("enter")
            time.sleep(SLEEP_AFTER_FOCUS)
            closed += 1
            debug_log(f"Dialog closed: '{title}'")
        except Exception as e:
            debug_log(f"ERROR while closing dialog: {e}", level="warning")
    return closed


def _watchdog_log(message: str):
    debug_log(message)


def _describe_dialog(hwnd) -> tuple[str, str]:
    title = win32gui.GetWindowText(hwnd) or "(untitled)"
    detail = ""
    if title == ERROR_WINDOW:
        detail = _read_error_window_text(hwnd)
    return title, detail


def _get_dialog_owner_info(hwnd) -> tuple[str, int | None]:
    owner_title = "unknown"
    owner_hwnd = None
    try:
        owner_hwnd = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
    except Exception:
        owner_hwnd = None

    if owner_hwnd and win32gui.IsWindow(owner_hwnd):
        try:
            owner_title = win32gui.GetWindowText(owner_hwnd) or "(untitled)"
        except Exception:
            owner_title = "unknown"
    else:
        try:
            dialog_pid = get_window_pid(hwnd)
            for candidate in (DROPVIEW_WINDOW_NAME, MULTISCRIPT_WINDOW):
                if not window_exists(candidate):
                    continue
                candidate_hwnd = find_window(candidate, timeout=0.2, poll_interval=0.05)
                if get_window_pid(candidate_hwnd) == dialog_pid:
                    owner_hwnd = candidate_hwnd
                    owner_title = win32gui.GetWindowText(candidate_hwnd) or candidate
                    break
        except Exception:
            pass

    return owner_title, owner_hwnd


def _iter_dropview_dialogs() -> list[int]:
    hwnds = set()
    if window_exists(DROPVIEW_WINDOW_NAME):
        try:
            dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05)
            hwnds.update(find_owned_dialogs(dv_hwnd))
        except Exception:
            pass
    if window_exists(MULTISCRIPT_WINDOW):
        try:
            ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=0.2, poll_interval=0.05)
            hwnds.update(find_owned_dialogs(ms_hwnd))
        except Exception:
            pass
    return list(hwnds)


def _log_dialog_detected(hwnd, title: str, detail: str):
    kind = "error" if title == ERROR_WINDOW else "warning"
    pid = get_window_pid(hwnd)
    owner_title, owner_hwnd = _get_dialog_owner_info(hwnd)
    suffix = f" | {detail}" if detail else ""
    _watchdog_log(
        f"│  Dialog detected: '{title}' [{kind}] "
        f"(hwnd={hwnd}, pid={pid}, owner='{owner_title}', owner_hwnd={owner_hwnd}){suffix}"
    )
    _dialog_watchdog_seen[hwnd] = {
        "title": title,
        "detail": detail,
        "pid": pid,
        "owner_title": owner_title,
        "owner_hwnd": owner_hwnd,
    }


def _log_dialog_closed(hwnd):
    info = _dialog_watchdog_seen.pop(hwnd, None)
    if not info:
        return
    title = info.get("title", "(untitled)")
    pid = info.get("pid")
    owner_title = info.get("owner_title", "unknown")
    owner_hwnd = info.get("owner_hwnd")
    _watchdog_log(
        f"│  Dialog closed: '{title}' "
        f"(hwnd={hwnd}, pid={pid}, owner='{owner_title}', owner_hwnd={owner_hwnd})"
    )


def _auto_dismiss_dialog(hwnd, title: str):
    """Acknowledge only the known DropView warning, then verify that it closed."""
    with _warning_ack_lock:
        try:
            owner = dropview_process_identity(hwnd)
            if title != WARNING_UNSAVED or owner is None or win32gui.GetWindowText(hwnd).strip() != title:
                return False
            for attempt in range(2):
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if dropview_process_identity(hwnd) != owner or win32gui.GetWindowText(hwnd).strip() != title:
                    return False
                _focus_window(hwnd, restore_if_iconic=False)
                if (win32gui.GetForegroundWindow() != hwnd or dropview_process_identity(hwnd) != owner
                        or win32gui.GetWindowText(hwnd).strip() != title):
                    _watchdog_log(f"│  WARNING: Dialog could not be brought to the foreground ('{title}').")
                    return False
                pyautogui.press("enter")
                _watchdog_log(f"│  Acknowledging dialog: '{title}' (hwnd={hwnd}, PID={owner['pid']}, attempt={attempt + 1}).")
                try:
                    wait_for_window_close(title, timeout=1.5, poll_interval=0.1, hwnd=hwnd)
                except TimeoutError:
                    continue
                _watchdog_log(f"│  Dialog closure verified: '{title}' (hwnd={hwnd}, PID={owner['pid']}).")
                return True
            _watchdog_log(f"│  WARNING: Dialog is still open after acknowledgement ('{title}', hwnd={hwnd}).")
            return False
        except Exception as exc:
            _watchdog_log(f"│  WARNING: Dialog could not be auto-dismissed ('{title}'): {exc}")
            return False


def acknowledge_discrete_unsaved_warning(expected_owner):
    """Accept the known warning only in the process that owns the PAD run."""
    if not expected_owner:
        return False
    scope = {"pid": expected_owner["pid"]}
    try:
        if not window_exists(WARNING_UNSAVED, **scope):
            return False
        hwnd = find_window(WARNING_UNSAVED, timeout=0.2, poll_interval=0.05, **scope)
        if (win32gui.GetWindowText(hwnd).strip() == WARNING_UNSAVED
                and dropview_process_identity(hwnd) == expected_owner):
            return _auto_dismiss_dialog(hwnd, WARNING_UNSAVED)
    except Exception as exc:
        debug_log(f"DropView unsaved-curves warning could not be verified: {exc}", level="warning")
    return False


def _dialog_watchdog_loop():
    while not _dialog_watchdog_stop.is_set():
        try:
            current_hwnds = set(_iter_dropview_dialogs())
            for hwnd in list(_dialog_watchdog_seen):
                if hwnd not in current_hwnds or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    _log_dialog_closed(hwnd)

            for hwnd in current_hwnds:
                if hwnd not in _dialog_watchdog_seen:
                    title, detail = _describe_dialog(hwnd)
                    _log_dialog_detected(hwnd, title, detail)
                    if title in _dialog_watchdog_autodismiss:
                        _auto_dismiss_dialog(hwnd, title)
        except Exception:
            pass
        time.sleep(_WATCHDOG_POLL_INTERVAL)


def ensure_dialog_watchdog(log_fn=None):
    global _dialog_watchdog_thread, _dialog_watchdog_stop, _dialog_watchdog_log_fn
    with _dialog_watchdog_lock:
        if log_fn is not None:
            _dialog_watchdog_log_fn = log_fn
        if _dialog_watchdog_thread and _dialog_watchdog_thread.is_alive():
            return
        _dialog_watchdog_seen.clear()
        _dialog_watchdog_stop = threading.Event()
        _dialog_watchdog_thread = threading.Thread(
            target=_dialog_watchdog_loop,
            name="dropview-dialog-watchdog",
            daemon=True,
        )
        _dialog_watchdog_thread.start()
        _watchdog_log("│  Dialog watchdog started.")


def stop_dialog_watchdog():
    global _dialog_watchdog_thread, _dialog_watchdog_stop
    with _dialog_watchdog_lock:
        thread = _dialog_watchdog_thread
        stop_event = _dialog_watchdog_stop
        if not thread or not thread.is_alive():
            return
        stop_event.set()
    thread.join(timeout=1.0)
    with _dialog_watchdog_lock:
        for hwnd in list(_dialog_watchdog_seen):
            _log_dialog_closed(hwnd)
        _dialog_watchdog_thread = None
        _dialog_watchdog_stop = None
    _watchdog_log("│  Dialog watchdog durduruldu.")


def _dismiss_warning_if_present(window_title: str, wait: float = 3.0) -> bool:
    """Use the same verified acknowledgement for warnings during measurement start."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if window_exists(window_title):
            try:
                hwnd = find_window(window_title, timeout=0.5, poll_interval=0.1)
                return _auto_dismiss_dialog(hwnd, window_title)
            except TimeoutError:
                continue
        time.sleep(0.2)
    return False


def _require_image(key: str) -> str:
    path = _IMG[key]
    if not os.path.isfile(path):
        raise RuntimeError(
            f"DropView automation image is missing: {os.path.basename(path)}"
        )
    return path


def _log(log_fn, msg: str):
    if log_fn:
        log_fn(msg)
    debug_log(msg)


def _ensure_dropview_ready(action_name: str, log_fn=None):
    ensure_dialog_watchdog(log_fn=log_fn)
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    _focus_window(dv_hwnd, click_title=True)
    if not win32gui.IsWindowEnabled(dv_hwnd):
        raise RuntimeError(
            f"{action_name}: DropView window is disabled "
            "(a modal dialog may be blocking it)."
        )
    owned = find_owned_dialogs(dv_hwnd)
    if owned:
        titles = [win32gui.GetWindowText(h) for h in owned]
        raise RuntimeError(f"{action_name}: DropView has an open dialog: {titles}")
    return dv_hwnd


def _paste_path_into_file_dialog(
    hwnd,
    file_path: str,
    log_fn=None,
    submit: bool = False,
    submit_delay: float = 0.8,
):
    _focus_window(hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(file_path)
    finally:
        win32clipboard.CloseClipboard()
    time.sleep(0.2)

    rect = win32gui.GetWindowRect(hwnd)
    dlg_x = rect[0]
    dlg_y = rect[1]
    dlg_w = rect[2] - rect[0]
    dlg_h = rect[3] - rect[1]
    fn_x = dlg_x + int(dlg_w * 0.45)
    fn_y = dlg_y + int(dlg_h * 0.70)
    safe_click(hwnd, fn_x, fn_y, log_fn=log_fn, post_delay=0.4)
    safe_sequence(
        hwnd,
        [
            {"type": "hotkey", "keys": ("ctrl", "a"), "post_delay": 0.1},
            {"type": "hotkey", "keys": ("ctrl", "v"), "post_delay": SLEEP_AFTER_CLICK},
            *(
                [{"type": "press", "key": "enter", "post_delay": submit_delay}]
                if submit
                else []
            ),
        ],
        log_fn=log_fn,
    )


def _submit_file_dialog_or_fallback(
    hwnd,
    title: str,
    button_text: str,
    image_key: str,
    log_fn=None,
    timeout: float = 2.0,
) -> None:
    try:
        _wait_for_hwnd_close(hwnd, title, timeout=timeout)
        return
    except TimeoutError:
        _click_button(hwnd, button_text, image_key, log_fn=log_fn, post_delay=SLEEP_AFTER_COMMAND)


def _dialog_belongs_to_owner(hwnd, owner_hwnd) -> bool:
    if not owner_hwnd:
        return True
    try:
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) == owner_hwnd:
            return True
    except Exception:
        pass
    owner_pid = get_window_pid(owner_hwnd)
    return bool(owner_pid and get_window_pid(hwnd) == owner_pid and win32gui.GetClassName(hwnd) == "#32770")


def _find_dialog_by_title(
    title_keyword: str,
    timeout=10,
    owner_hwnd=None,
    image_key: str | None = None,
    exact: bool = False,
):
    wanted = title_keyword.strip().lower()
    start = time.time()
    last_titles = []
    while time.time() - start < timeout:
        found = []
        visible_titles = []

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            if not _dialog_belongs_to_owner(hwnd, owner_hwnd):
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if title:
                visible_titles.append(title)
            title_key = title.lower()
            if (title_key == wanted) if exact else (wanted in title_key):
                found.append(hwnd)

        win32gui.EnumWindows(_cb, None)
        if found:
            return found[0]
        if image_key and owner_hwnd:
            for dlg_hwnd in find_owned_dialogs(owner_hwnd):
                if _is_image_present(dlg_hwnd, image_key, threshold=THRESHOLD_LOW):
                    return dlg_hwnd
        last_titles = visible_titles
        time.sleep(0.2)
    raise TimeoutError(
        f"'{title_keyword}' dialog was not found within {timeout}s."
        f" Visible dialogs: {last_titles}"
    )


def _wait_for_hwnd_close(hwnd, title: str = "dialog", timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return
        time.sleep(0.1)
    raise TimeoutError(f"'{title}' did not close within {timeout:.1f}s.")


def _wait_for_optional_dialog_close(
    title_keyword: str,
    owner_hwnd=None,
    appear_wait=2.0,
    close_timeout=30.0,
    log_fn=None,
    exact: bool = True,
) -> bool:
    try:
        hwnd = _find_dialog_by_title(
            title_keyword,
            timeout=appear_wait,
            owner_hwnd=owner_hwnd,
            exact=exact,
        )
    except TimeoutError:
        return False
    debug_log(f"Waiting for DropView dialog to close: {win32gui.GetWindowText(hwnd) or title_keyword}")
    _wait_for_hwnd_close(hwnd, title_keyword, timeout=close_timeout)
    return True


def _click_image(hwnd, image_key: str, log_fn=None, threshold=THRESHOLD_MID, post_delay=SLEEP_AFTER_CLICK):
    x, y = safe_find_on_screen(
        hwnd,
        _require_image(image_key),
        threshold=threshold,
        log_fn=log_fn,
    )
    safe_click(hwnd, x, y, log_fn=log_fn, post_delay=post_delay)


def _click_popup_image(image_key: str, log_fn=None, threshold=THRESHOLD_LOW, post_delay=SLEEP_AFTER_CLICK):
    """Click an image in an open popup/menu without refocusing the parent window."""
    image_path = _require_image(image_key)
    try:
        x, y = find_on_screen(
            image_path,
            threshold=threshold,
            hwnd=None,
            use_foreground_fallback=True,
        )
    except Exception as exc:
        score = match_score_on_screen(
            image_path,
            hwnd=None,
            use_foreground_fallback=True,
        )
        raise RuntimeError(
            f"Popup image not found (match: {score:.2f} < {threshold:.2f}). "
            f"File: {os.path.basename(image_path)}"
        ) from exc
    pyautogui.click(x, y)
    if post_delay > 0:
        time.sleep(post_delay)
    debug_log(f"Popup image clicked: {os.path.basename(image_path)}")


def _find_child_by_text(hwnd, text: str, exact: bool = False) -> int | None:
    wanted = text.strip().lower()
    found = []

    def _cb(child_hwnd, _):
        try:
            child_text = (win32gui.GetWindowText(child_hwnd) or "").strip()
        except Exception:
            return
        compare = child_text.lower()
        if (compare == wanted) if exact else (wanted in compare):
            found.append(child_hwnd)

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        return None
    return found[0] if found else None


def _click_child_text(hwnd, text: str, log_fn=None, exact: bool = False, post_delay=SLEEP_AFTER_CLICK) -> bool:
    child = _find_child_by_text(hwnd, text, exact=exact)
    if not child:
        return False
    left, top, right, bottom = win32gui.GetWindowRect(child)
    safe_click(
        hwnd,
        (left + right) // 2,
        (top + bottom) // 2,
        log_fn=log_fn,
        post_delay=post_delay,
    )
    return True


def _is_checkbox_checked(hwnd, text: str) -> bool | None:
    child = _find_child_by_text(hwnd, text)
    if not child:
        return None
    try:
        return bool(win32gui.SendMessage(child, win32con.BM_GETCHECK, 0, 0))
    except Exception:
        return None


_NODE_CHECKBOX_CHECKED_DARK_RATIO = 0.08
_NODE_CHECKBOX_UNCHECKED_DARK_RATIO = 0.03
_CV_NODE_CHECKBOX_CROP = (0, 27, 18, 49)
_CV_NODE_CHECKBOX_MATCH_THRESHOLD = 0.75
_CV_NODE_CHECKBOX_MATCH_MARGIN = 0.10


def _node_checked_by_pixels(hwnd) -> bool | None:
    try:
        child = _find_child_by_text(hwnd, "Node 1")
        if not child:
            return None
        left, top, right, bottom = win32gui.GetWindowRect(child)
        width = right - left
        height = bottom - top
        checkbox_width = min(width, height, 20)
        if checkbox_width < 8 or height < 8:
            return None

        checkbox = _grab_region(left, top, checkbox_width, height).convert("L")
        margin = max(2, min(checkbox.size) // 5)
        inner = checkbox.crop(
            (margin, margin, checkbox.width - margin, checkbox.height - margin)
        )
        histogram = inner.histogram()
        dark_pixels = sum(histogram[:110])
        pixel_count = inner.width * inner.height
        dark_ratio = dark_pixels / pixel_count if pixel_count else 0.0
        debug_log(
            f"Node 1 checkbox dark pixel ratio: {dark_ratio:.3f} "
            f"(control_rect={left},{top},{right},{bottom})"
        )

        if dark_ratio >= _NODE_CHECKBOX_CHECKED_DARK_RATIO:
            return True
        if dark_ratio <= _NODE_CHECKBOX_UNCHECKED_DARK_RATIO:
            return False
    except Exception as exc:
        debug_log(f"Node 1 checkbox pixel check failed: {exc}", level="warning")
    return None


def _is_dropview_java_process(process):
    executable = os.path.normcase(os.path.realpath(process.exe()))
    for launcher in DROPVIEW_EXE_CANDIDATES:
        install_dir = os.path.dirname(os.path.realpath(launcher))
        expected = os.path.join(install_dir, "jre", "bin", os.path.basename(executable))
        if executable != os.path.normcase(expected):
            continue
        arguments = process.cmdline()
        for option, value in zip(arguments, arguments[1:]):
            if option == "-jar":
                jar = value if os.path.isabs(value) else os.path.join(process.cwd(), value)
                return os.path.normcase(os.path.realpath(jar)) == os.path.normcase(
                    os.path.join(install_dir, "dropview.jar"))
    return False


def _cv_node_checked_by_template(hwnd) -> bool | None:
    checked_score = match_cropped_image_score_on_window(
        _require_image("cv_select_nodes_checked"),
        _CV_NODE_CHECKBOX_CROP,
        hwnd,
    )
    unchecked_score = match_cropped_image_score_on_window(
        _require_image("cv_select_nodes_unchecked"),
        _CV_NODE_CHECKBOX_CROP,
        hwnd,
    )
    debug_log(
        "CV Node 1 checkbox template scores: "
        f"checked={checked_score:.3f}, unchecked={unchecked_score:.3f}"
    )
    if (
        checked_score >= _CV_NODE_CHECKBOX_MATCH_THRESHOLD
        and checked_score >= unchecked_score + _CV_NODE_CHECKBOX_MATCH_MARGIN
    ):
        return True
    if (
        unchecked_score >= _CV_NODE_CHECKBOX_MATCH_THRESHOLD
        and unchecked_score >= checked_score + _CV_NODE_CHECKBOX_MATCH_MARGIN
    ):
        return False
    return None


def _click_node_checkbox_area(hwnd, log_fn=None):
    child = _find_child_by_text(hwnd, "Node 1")
    if child:
        left, top, _right, bottom = win32gui.GetWindowRect(child)
        safe_click(
            hwnd,
            left + 8,
            (top + bottom) // 2,
            log_fn=log_fn,
            post_delay=SLEEP_AFTER_CLICK,
        )
        return
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    safe_click(
        hwnd,
        left + 10,
        top + 40,
        log_fn=log_fn,
        post_delay=SLEEP_AFTER_CLICK,
    )


def _ensure_node_checked(
    hwnd,
    log_fn=None,
    context: str = "DropView",
    visual_state_fn=None,
):
    checked_by_control = _is_checkbox_checked(hwnd, "Node 1")
    if checked_by_control is True:
        return
    visual_state_fn = visual_state_fn or _node_checked_by_pixels
    checked_by_pixels = visual_state_fn(hwnd)
    if checked_by_pixels is True:
        if checked_by_control is False:
            debug_log(f"{context}: Node 1 is visually checked; skipping checkbox click.")
        return

    _click_node_checkbox_area(hwnd, log_fn=log_fn)
    checked_by_control = _is_checkbox_checked(hwnd, "Node 1")
    if checked_by_control is True:
        return
    checked_by_pixels = visual_state_fn(hwnd)
    if checked_by_pixels is True:
        return
    if checked_by_control is False or checked_by_pixels is False:
        raise RuntimeError(f"{context}: Node 1 checkbox could not be selected.")


def _click_button(hwnd, text: str, image_key: str, log_fn=None, post_delay=SLEEP_AFTER_CLICK):
    if _click_child_text(hwnd, text, log_fn=log_fn, exact=False, post_delay=post_delay):
        return
    _click_image(hwnd, image_key, log_fn=log_fn, post_delay=post_delay)


def _is_image_present(hwnd, image_key: str, threshold=THRESHOLD_MID) -> bool:
    try:
        match_score_on_screen(
            _require_image(image_key),
            hwnd=hwnd,
            use_foreground_fallback=False,
        )
        safe_find_on_screen(
            hwnd,
            _require_image(image_key),
            threshold=threshold,
            retries=1,
        )
        return True
    except Exception:
        return False


def _accept_if_dialog_present(
    title_keyword: str,
    wait=1.5,
    log_fn=None,
    owner_hwnd=None,
    image_key: str | None = None,
    exact: bool = False,
) -> bool:
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            hwnd = _find_dialog_by_title(
                title_keyword,
                timeout=0.3,
                owner_hwnd=owner_hwnd,
                image_key=image_key,
                exact=exact,
            )
        except TimeoutError:
            time.sleep(0.1)
            continue
        _focus_window(hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)
        try:
            safe_sequence(
                hwnd,
                [{"type": "hotkey", "keys": ("alt", "y"), "post_delay": 0.3}],
                log_fn=log_fn,
            )
            _wait_for_hwnd_close(hwnd, title_keyword, timeout=1.0)
        except Exception:
            _click_button(hwnd, "Yes", "yes_btn", log_fn=log_fn, post_delay=SLEEP_AFTER_COMMAND)
        _wait_for_hwnd_close(hwnd, title_keyword, timeout=5.0)
        return True
    return False


def _snapshot_measurement_files(measurements_dir: str, extension: str) -> set[str]:
    extension = extension.lower()
    try:
        return {
            os.path.join(measurements_dir, name)
            for name in os.listdir(measurements_dir)
            if name.lower().endswith(extension)
            and os.path.isfile(os.path.join(measurements_dir, name))
        }
    except OSError:
        return set()


def _snapshot_mtp_files(measurements_dir: str) -> set[str]:
    return _snapshot_measurement_files(measurements_dir, ".mtp")


def _snapshot_mtc_files(measurements_dir: str) -> set[str]:
    return _snapshot_measurement_files(measurements_dir, ".mtc")


def _latest_mtp_file(measurements_dir: str) -> str:
    files = list(_snapshot_mtp_files(measurements_dir))
    if not files:
        return ""
    return max(files, key=lambda path: os.path.getmtime(path))


def _wait_for_new_file(
    measurements_dir: str,
    before: set[str],
    extension: str,
    timeout: float,
    label: str,
) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = _snapshot_measurement_files(measurements_dir, extension)
        new_files = sorted(current - before, key=lambda path: os.path.getmtime(path))
        if new_files:
            return new_files[-1]
        time.sleep(0.5)
    raise RuntimeError(
        f"{label} did not create a new {extension} file within {timeout:.0f}s: "
        f"{measurements_dir}"
    )


def _wait_for_new_mtp(measurements_dir: str, before: set[str], timeout: float) -> str:
    return _wait_for_new_file(
        measurements_dir,
        before,
        ".mtp",
        timeout,
        "Continuous PAD",
    )


def _wait_for_new_mtc(measurements_dir: str, before: set[str], timeout: float) -> str:
    return _wait_for_new_file(
        measurements_dir,
        before,
        ".mtc",
        timeout,
        "CV",
    )


def _xml_has_root_close(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            if os.path.getsize(path) > 4096:
                f.seek(-4096, os.SEEK_END)
            tail = f.read().decode("utf-8", errors="ignore")
        return "</root>" in tail
    except OSError:
        return False


def _wait_for_stable_xml(
    path: str,
    timeout: float,
    label: str,
) -> str:
    deadline = time.time() + timeout
    last_size = -1
    stable_since = None
    while time.time() < deadline:
        if not path or not os.path.isfile(path):
            time.sleep(0.5)
            continue
        size = os.path.getsize(path)
        if size > 0 and size == last_size:
            if stable_since is None:
                stable_since = time.time()
            if (
                time.time() - stable_since >= _CONTINUOUS_PAD_STABLE_SECONDS
                and _xml_has_root_close(path)
            ):
                return path
        else:
            stable_since = None
            last_size = size
        time.sleep(0.5)
    raise RuntimeError(
        f"{label} file did not become stable or complete: {path}"
    )


def _wait_for_stable_mtp(path: str, timeout: float = _CONTINUOUS_PAD_STABLE_TIMEOUT) -> str:
    return _wait_for_stable_xml(path, timeout, "Continuous PAD .mtp")


def _wait_for_stable_mtc(path: str, timeout: float = _CONTINUOUS_PAD_STABLE_TIMEOUT) -> str:
    return _wait_for_stable_xml(path, timeout, "CV .mtc")


def _cv_parameter_float(technic, section: str, param_id: str, fallback: float = 0.0) -> float:
    param = technic.find(f"{section}/parameter[@id='{param_id}']")
    if param is None or param.text is None:
        return fallback
    try:
        return float(param.text)
    except (TypeError, ValueError):
        return fallback


def _estimate_cv_duration_seconds(method_path: str) -> float:
    """Estimate finite CV runtime from the .tc method so Run CV can wait long enough."""
    try:
        tree = ET.parse(method_path)
        technic = tree.getroot().find("technic")
        if technic is None or technic.get("id", "") != "CV":
            return 0.0

        srate = _cv_parameter_float(technic, "commonUserParameters", "Srate")
        nscans = _cv_parameter_float(technic, "commonUserParameters", "nscans", 1.0)
        if srate <= 0.0 or nscans <= 0.0:
            return 0.0

        channel = technic.find("channelUserParameters/channel[@id='0']")
        if channel is None:
            return 0.0

        def channel_float(param_id: str, fallback: float = 0.0) -> float:
            param = channel.find(f"parameter[@id='{param_id}']")
            if param is None or param.text is None:
                return fallback
            try:
                return float(param.text)
            except (TypeError, ValueError):
                return fallback

        ebeg = channel_float("Ebegin")
        ev1 = channel_float("Evtx1", ebeg)
        ev2 = channel_float("Evtx2", ev1)
        scan_span = abs(ev1 - ebeg) + abs(ev2 - ev1)
        scan_seconds = (scan_span / srate) * nscans
        pretreatment_seconds = sum(
            _cv_parameter_float(technic, "pretreatmentUserParameters", param_id)
            for param_id in ("tcond", "tdep", "tequil")
        )
        return max(0.0, scan_seconds + pretreatment_seconds)
    except Exception as exc:
        debug_log(f"CV duration estimate skipped: {exc}", level="warning")
        return 0.0


def _cv_file_timeout(method_path: str, configured_timeout: float | None = None) -> float:
    if configured_timeout is not None:
        return float(configured_timeout)
    estimate = _estimate_cv_duration_seconds(method_path)
    if estimate <= 0.0:
        return _CONTINUOUS_PAD_FILE_TIMEOUT
    timeout = max(_CONTINUOUS_PAD_FILE_TIMEOUT, estimate + 60.0, estimate * 1.35)
    debug_log(f"CV estimated duration: {estimate:.1f}s; file timeout: {timeout:.1f}s")
    return timeout


def _read_error_window_text(hwnd) -> str:
    """
    Error penceresinin içindeki static text child'ını okur.
    Pencere içeriğine göre hata türünü ayırt etmek için kullanılır.
    """
    texts = []

    def _cb(child_hwnd, _):
        cls = win32gui.GetClassName(child_hwnd)
        if cls == "Static":
            text = win32gui.GetWindowText(child_hwnd)
            if text:
                texts.append(text)

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return " ".join(texts)


@contextmanager
def _track_connection_error(hwnd):
    """Keep a dismissed error visible to the connection attempt that raised it."""
    owner = dropview_process_identity(hwnd)
    seen = threading.Event()
    if owner is None:
        yield seen
        return
    key = (owner["pid"], owner["created"])
    with _connection_error_lock:
        _connection_error_waiters.setdefault(key, set()).add(seen)
    try:
        yield seen
    finally:
        with _connection_error_lock:
            waiters = _connection_error_waiters[key]
            waiters.remove(seen)
            if not waiters:
                del _connection_error_waiters[key]


def _read_connection_error_labels(hwnd):
    capture = connection_scores_on_window(hwnd, {}, full_client=True)
    if capture["error"]:
        return "", None
    left, top, _, _ = capture["region"]
    lines = []
    ok_point = None
    for text, x, y in read_label_positions(capture["image"]):
        if text.strip().casefold() in ("ok", "0k"):
            ok_point = (left + x, top + y)
        else:
            lines.append(text)
    return " ".join(lines), ok_point


def _dismiss_connection_error_dialog(hwnd):
    """Acknowledge the exact DropView error and report a match even if OK needs a retry."""
    with _connection_error_lock:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindowText(hwnd).strip() != ERROR_WINDOW:
            return False
        owner = dropview_process_identity(hwnd)
        if owner is None:
            return False
        text = _read_error_window_text(hwnd)
        message = " ".join(text.split()).rstrip(".")
        reason = _CONNECTION_ERRORS.get(message.casefold())
        if text.strip() and reason is None:
            return False
        button = _find_child_by_text(hwnd, "OK", exact=True)
        if button and win32gui.GetClassName(button) != "Button":
            button = None
        ok_point = None
        dialog_rect = None
        if not text.strip() or not button:
            try:
                dialog_rect = win32gui.GetWindowRect(hwnd)
                visual_text, ok_point = _read_connection_error_labels(hwnd)
            except (OSError, RuntimeError, subprocess.SubprocessError, TypeError, ValueError) as exc:
                debug_log(f"DropView Error dialog OCR could not be read: {exc}", level="warning")
                visual_text = ""
            if not text.strip():
                message = " ".join(visual_text.split()).rstrip(".")
                reason = _CONNECTION_ERRORS.get(message.casefold())
            if reason is None:
                return False
        if dropview_process_identity(hwnd) != owner or win32gui.GetWindowText(hwnd).strip() != ERROR_WINDOW:
            return False

        key = (owner["pid"], owner["created"])
        if reason == _USB_DISCONNECTED:
            _connection_faults[owner["pid"]] = {
                "owner": owner, "reason": reason, "sampled_at": time.monotonic(), "message": message}
        for seen in _connection_error_waiters.get(key, ()):
            seen.reason = reason
            seen.set()
        debug_log(f"DropView Error: {message} (PID={owner['pid']}, hwnd={hwnd}).", level="warning")
        try:
            if not button and ok_point is not None and win32gui.GetForegroundWindow() != hwnd:
                _focus_window(hwnd, restore_if_iconic=False)
            if dropview_process_identity(hwnd) != owner or win32gui.GetWindowText(hwnd).strip() != ERROR_WINDOW:
                return True
            if button:
                if (win32gui.IsWindowEnabled(button) and win32gui.GetAncestor(button, win32con.GA_ROOT) == hwnd
                        and win32gui.GetWindowText(button).strip().casefold() == "ok"):
                    win32gui.PostMessage(button, win32con.BM_CLICK, 0, 0)
            elif (ok_point is not None and win32gui.GetForegroundWindow() == hwnd
                  and win32gui.GetWindowRect(hwnd) == dialog_rect):
                hit = win32gui.WindowFromPoint(ok_point)
                if hit and win32gui.GetAncestor(hit, win32con.GA_ROOT) == hwnd:
                    pyautogui.click(*ok_point)
        except Exception as exc:
            debug_log(f"DropView Error OK could not be clicked: {exc}", level="warning")
        return True


def dismiss_connection_error_dialogs():
    """Acknowledge known connection errors even without the main DropView window."""
    def inspect(hwnd, _):
        try:
            _dismiss_connection_error_dialog(hwnd)
        except Exception as exc:
            debug_log(f"DropView Error dialog inspection failed: {exc}", level="warning")

    try:
        win32gui.EnumWindows(inspect, None)
    except Exception as exc:
        debug_log(f"DropView Error dialog scan failed: {exc}", level="warning")


def _classify_error_dialog_by_template(log_fn=None, hwnd=None, use_foreground_fallback=False) -> str | None:
    """
    Error metni Win32 child text'ten okunamazsa, ekrandaki metin template'lerine
    bakarak hatayı sınıflandırır.
    """
    def _log(msg):
        debug_log(msg)

    score_no_device = match_score_on_screen(
        _IMG["error_no_device_text"],
        hwnd=hwnd,
        use_foreground_fallback=use_foreground_fallback,
    )
    score_pot_not_found = match_score_on_screen(
        _IMG["error_potentiostat_text"],
        hwnd=hwnd,
        use_foreground_fallback=use_foreground_fallback,
    )
    min_score = max(THRESHOLD_LOW, 0.55)
    min_delta = 0.08

    _log(
        "│  Error template scores: "
        f"no_device={score_no_device:.2f}, potentiostat_not_found={score_pot_not_found:.2f}"
    )
    _log(
        "│  Error template threshold/delta: "
        f"min_score={min_score:.2f}, min_delta={min_delta:.2f}"
    )

    if score_no_device >= score_pot_not_found:
        best_label = "no_device_connected"
        best_score = score_no_device
        delta = score_no_device - score_pot_not_found
    else:
        best_label = "potentiostat_not_found"
        best_score = score_pot_not_found
        delta = score_pot_not_found - score_no_device

    if best_score >= min_score and delta >= min_delta:
        return best_label
    return None


def _wait_for_connection_result(timeout=30, poll_interval=0.5, log_fn=None, *, hwnd=None, connection_error=None) -> str:
    """
    Bağlantı girişimi sonucunu bekler ve döner.
    Dönüş değerleri:
        'connected'              - Bağlantı başarılı
        'no_device_connected'    - Cihaz bulunamadı
        'potentiostat_not_found' - Potentiostat bulunamadı
        'potentiostat_null'      - DropView null potentiostat hatası
        'unknown_error'          - Tanınmayan hata penceresi
        'timeout'                - Zaman aşımı
    """
    def _log(msg):
        debug_log(msg)

    scope = {"pid": get_window_pid(hwnd)} if hwnd is not None else {}
    start = time.time()
    while time.time() - start < timeout:
        if connection_error is not None and connection_error.is_set():
            return getattr(connection_error, "reason", _POTENTIOSTAT_NULL)
        if not window_exists(CONNECTING_DIALOG, **scope):
            if window_exists(ERROR_WINDOW, **scope):
                try:
                    err_hwnd = find_window(ERROR_WINDOW, timeout=3, **scope)
                except TimeoutError:
                    if connection_error is not None and connection_error.is_set():
                        return getattr(connection_error, "reason", _POTENTIOSTAT_NULL)
                    raise
                if connection_error is not None and (
                        _dismiss_connection_error_dialog(err_hwnd) or connection_error.is_set()):
                    return getattr(connection_error, "reason", _POTENTIOSTAT_NULL)
                text = _read_error_window_text(err_hwnd)
                shown_text = text if text else "(text could not be read)"
                _log(f"│  DropView Error dialog text: {shown_text}")
                classification = None
                if MSG_NO_DEVICE in text:
                    classification = "no_device_connected"
                elif MSG_POTENTIOSTAT_NOT_FOUND in text:
                    classification = "potentiostat_not_found"
                try:
                    _focus_window(err_hwnd, restore_if_iconic=False)
                    if win32gui.GetForegroundWindow() == err_hwnd:
                        if classification is None:
                            classification = _classify_error_dialog_by_template(
                                log_fn=log_fn, hwnd=err_hwnd, use_foreground_fallback=False)
                        if win32gui.GetForegroundWindow() == err_hwnd:
                            pyautogui.press("enter")
                            time.sleep(SLEEP_AFTER_FOCUS)
                except Exception as exc:
                    _log(f"│  WARNING: DropView Error dialog could not be handled: {exc}")
                if classification is not None:
                    _log(f"│  DropView connection error classified: {classification}")
                    return classification

                _log(f"│  WARNING: Unclassified DropView Error dialog text: {shown_text}")
                return "unknown_error"
            connected = (_connection_snapshot(hwnd=hwnd)["state"] == "connected"
                         if hwnd is not None else _is_dropview_connected())
            if connected:
                return getattr(connection_error, "reason", _POTENTIOSTAT_NULL) if connection_error is not None and connection_error.is_set() else "connected"
        time.sleep(poll_interval)
    return getattr(connection_error, "reason", _POTENTIOSTAT_NULL) if connection_error is not None and connection_error.is_set() else "timeout"


def _resolve_target_dropsens_ports(preferred_port=None) -> list[str]:
    """
    Denenecek COM portlarını öncelik sırasıyla döner.
    Sıralama:
        1. preferred_port (varsa)
        2. Son başarılı manuel port, ardından kayıtlı port
        3. DROPSENS_COM_PORTS listesindeki mevcut diğer portlar
    Tekrarlar korunmaz.
    """
    seen = set()
    ports = []
    available = {port.device.upper() for port in list_ports.comports()}
    from bench_test.dropview.windows_devices import list_dropsens_devices
    selection = get_value("dropsens_named_selection")
    mapped = []
    if isinstance(selection, dict) and selection.get("address"):
        mapped = [port for address, _, port in list_dropsens_devices()
                  if address == selection["address"]]
    route = get_value("dropsens_connection_route", {})
    last_route_port = route.get("port") if isinstance(route, dict) and route.get("kind") == "manual" else None
    candidates = [preferred_port, *mapped, get_value("dropsens_last_successful_port"), last_route_port,
                  get_value("dropsens_com", ""), *DROPSENS_COM_PORTS]
    for p in candidates:
        if p and (p in DROPSENS_COM_PORTS or p in mapped) and p.upper() in available and p not in seen:
            seen.add(p)
            ports.append(p)
    return ports


def dropview_responding(hwnd):
    from ctypes import wintypes
    function = ctypes.windll.user32.SendMessageTimeoutW
    function.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    function.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    return bool(function(hwnd, 0, 0, 0, 2, 500, ctypes.byref(result)))


def _normalize_ocr_com_label(label):
    label = "".join(label.upper().split())
    # Windows OCR can split the M in these small Swing labels into "TV1".
    if label.startswith("COTV1"):
        label = "COM" + label[5:]
    if not label.startswith("COM"):
        return ""
    digits = label[3:].translate(str.maketrans({"O": "0", "I": "1", "L": "1", "B": "8"}))
    return f"COM{digits}" if digits.isascii() and digits.isdigit() else ""


def _select_named_connection_port(dialog, port, port_count):
    """Read the Swing popup and click only an exact COM label."""
    arrow_x, arrow_y = find_on_screen(
        _IMG["manual_conn_dropdown_arrow"], threshold=THRESHOLD_MID,
        hwnd=dialog, use_foreground_fallback=False,
    )
    hit = win32gui.WindowFromPoint((arrow_x, arrow_y))
    if not hit or win32gui.GetAncestor(hit, win32con.GA_ROOT) != dialog:
        return False
    safe_click(dialog, arrow_x, arrow_y, post_delay=SLEEP_AFTER_CLICK)
    # Swing opens the list as a separate popup above or below the field.
    for offset_y in (22, -22):
        hit = win32gui.WindowFromPoint((arrow_x - 24, arrow_y + offset_y))
        popup = win32gui.GetAncestor(hit, win32con.GA_ROOT) if hit else None
        if popup and win32gui.GetWindow(popup, win32con.GW_OWNER) == dialog:
            break
    else:
        debug_log(f"Manual port {port}: DropView dropdown popup could not be located.", level="warning")
        return False

    left, top, right, bottom = win32gui.GetWindowRect(popup)
    pyautogui.moveTo(left + (right - left) // 2, top + (bottom - top) // 2)
    if port_count > 8:
        pyautogui.scroll(port_count)
        time.sleep(SLEEP_AFTER_CLICK)
    previous = None
    for _ in range(port_count):
        if not win32gui.IsWindowVisible(popup):
            return False
        snapshot = _grab_region(left, top, right - left, bottom - top)
        if previous is not None and _images_equal(previous, snapshot):
            return False
        labels = read_label_positions(snapshot)
        debug_log(f"Manual port {port}: dropdown OCR read {[label for label, _, _ in labels]!r}.")
        matches = [(left + x, top + y) for label, x, y in labels
                   if _normalize_ocr_com_label(label) == port]
        if len(matches) > 1:
            return False
        if matches:
            point = matches[0]
            hit = win32gui.WindowFromPoint(point)
            if (hit and win32gui.GetAncestor(hit, win32con.GA_ROOT) == popup
                    and _images_equal(snapshot, _grab_region(left, top, right - left, bottom - top))):
                pyautogui.click(*point)
                time.sleep(SLEEP_AFTER_CLICK)
                if win32gui.IsWindowVisible(popup):
                    return False
                return _verify_named_connection_port(dialog, port, left, arrow_x, arrow_y)
            return False
        previous = snapshot
        pyautogui.scroll(-3)
        time.sleep(SLEEP_AFTER_CLICK)
    return False


def _verify_named_connection_port(dialog, port, left, arrow_x, arrow_y):
    # Crop the collapsed field immediately to the left of the arrow.
    dpi = ctypes.windll.user32.GetDpiForWindow(dialog) or 96
    half_height = round(9 * dpi / 96)
    border = round(2 * dpi / 96)
    left += border
    right = arrow_x - half_height - border
    hit = win32gui.WindowFromPoint(((left + right) // 2, arrow_y)) if right > left else None
    if not hit or win32gui.GetAncestor(hit, win32con.GA_ROOT) != dialog:
        debug_log(f"Manual port {port}: selected field is not visible.", level="warning")
        return False
    field = _grab_region(left, arrow_y - half_height, right - left, 2 * half_height)
    # Remove the gray Swing background and preserve dark glyphs for OCR.
    field = ImageOps.grayscale(field).point(lambda value: 255 if value >= 160 else 0)
    labels = [label for label, _, _ in read_label_positions(field)]
    selected = [_normalize_ocr_com_label(label) for label in labels]
    verified = selected == [port]
    debug_log(f"Manual port {port}: selected field OCR read {labels!r}; verified={verified}.",
              level="debug" if verified else "warning")
    return verified


def _connect_manual(dv_hwnd, target_com: str, log_fn=None, *, exact_port=False) -> str:
    """
    Alt+D → M ile Manual Connection penceresini açar,
    hedef COM portunu seçer ve Connect butonuna basar.
    Dönüş değeri: 'connected' / 'no_device_connected' /
                  'potentiostat_not_found' / 'unknown_error' / 'timeout'
    """
    def _log(msg):
        debug_log(msg, level="warning")

    if not exact_port and target_com not in DROPSENS_COM_PORTS:
        _log(f"│  WARNING: Unsupported COM port: {target_com}")
        return "timeout"
    available = {port.device.upper() for port in list_ports.comports()}
    if target_com.upper() not in available:
        _log(f"Manual connection skipped: {target_com} is not present in Windows.")
        return "port_unavailable"

    safe_sequence(
        dv_hwnd,
        [
            {"type": "hotkey", "keys": ("alt", "d"), "post_delay": SLEEP_AFTER_FOCUS},
            {"type": "press", "key": "m", "post_delay": SLEEP_AFTER_FOCUS},
        ],
        log_fn=log_fn,
    )

    try:
        manual_hwnd = find_window(MANUAL_CONNECTION_WINDOW, timeout=10, pid=get_window_pid(dv_hwnd))
    except TimeoutError:
        _log("│  WARNING: Manual Connection window did not open.")
        return "timeout"

    time.sleep(SLEEP_AFTER_FOCUS)

    if exact_port:
        try:
            if not _select_named_connection_port(manual_hwnd, target_com, len(available)):
                return "port_not_selectable"
        except subprocess.CalledProcessError as exc:
            _log(f"Windows OCR failed: {exc.stderr.strip()}")
            return "port_not_selectable"
        except subprocess.TimeoutExpired:
            _log("Windows OCR did not respond in time.")
            return "port_not_selectable"
        except Exception as exc:
            _log(f"Could not select {target_com} in DropView: {exc}")
            return "port_not_selectable"
    elif not _select_manual_port_image(manual_hwnd, target_com, _log):
        return "timeout"

    try:
        bx, by = find_on_screen(
            _IMG["manual_conn_connect_btn"],
            threshold=THRESHOLD_MID,
            hwnd=manual_hwnd,
            use_foreground_fallback=False,
        )
        if exact_port:
            hit = win32gui.WindowFromPoint((bx, by))
            if not hit or win32gui.GetAncestor(hit, win32con.GA_ROOT) != manual_hwnd:
                return "connect_button_unavailable"
    except Exception as e:
        _log(f"│  WARNING: Connect button not found: {e}")
        return "timeout"

    with _track_connection_error(dv_hwnd) as connection_error:
        try:
            pyautogui.click(bx, by)
            time.sleep(SLEEP_AFTER_COMMAND)
            wait_for_window_close(MANUAL_CONNECTION_WINDOW, timeout=10, hwnd=manual_hwnd)
        except TimeoutError:
            _log("│  WARNING: Manual Connection window did not close.")
            return getattr(connection_error, "reason", _POTENTIOSTAT_NULL) if connection_error.is_set() else "timeout"
        except Exception as exc:
            _log(f"│  WARNING: Manual connection could not be submitted: {exc}")
            return getattr(connection_error, "reason", _POTENTIOSTAT_NULL) if connection_error.is_set() else "timeout"

        return _wait_for_connection_result(
            timeout=TIMEOUT_CONNECT, log_fn=log_fn, hwnd=dv_hwnd, connection_error=connection_error)


def _select_manual_port_image(manual_hwnd, target_com, _log):
    try:
        dx, dy = find_on_screen(
            _IMG["manual_conn_dropdown_arrow"],
            threshold=THRESHOLD_MID,
            hwnd=manual_hwnd,
            use_foreground_fallback=False,
        )
        pyautogui.click(dx, dy)
        time.sleep(SLEEP_AFTER_CLICK)
    except Exception as e:
        _log(f"│  WARNING: COM port dropdown could not be opened: {e}")
        return False

    com_key = {
        "COM3": "manual_conn_com3",
        "COM4": "manual_conn_com4",
        "COM10": "manual_conn_com10",
    }[target_com]
    try:
        cx, cy = find_on_screen(
            _IMG[com_key],
            threshold=THRESHOLD_MID,
            hwnd=manual_hwnd,
            use_foreground_fallback=False,
        )
        pyautogui.click(cx, cy)
        time.sleep(SLEEP_AFTER_CLICK)
    except Exception as e:
        _log(f"│  WARNING: {target_com} image not found: {e}")
        return False
    return True

# ── Bağlantı durumu ────────────────────────────────────────────

_CONNECTION_MIN_DELTA = 0.08
_indicator_capture_lock = threading.Lock()
_CONNECTION_VERIFY_TIMEOUT = 5.0


def _connection_snapshot(should_focus=None, *, hwnd=None, measurement_status=False) -> dict:
    started = time.monotonic()
    result = {"dv_open": False, "connected_score": 0.0,
              "disconnected_score": 0.0, "state": "unknown",
              "image": None, "region": None, "error": ""}
    try:
        result["dv_open"] = win32gui.IsWindow(hwnd) if hwnd else window_exists(DROPVIEW_WINDOW_NAME)
        if not result["dv_open"]:
            result["state"] = "disconnected"
            result["error"] = "DropView window is not open."
        else:
            hwnd = hwnd or find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05)
            with _indicator_capture_lock:
                if should_focus is not None and should_focus():
                    _focus_window(hwnd, sleep_after=SLEEP_AFTER_FOCUS, require_foreground=False)
                result["sampled_at"] = time.monotonic()
                templates = {
                    "connected": _IMG["connected"],
                    "disconnected": _IMG["disconnected"],
                }
                status_keys = ("pad_status_run", "pad_status_stopped")
                if measurement_status:
                    templates.update((key, _IMG[key]) for key in status_keys)
                    result.update(connection_scores_on_window(hwnd, templates, optional_templates=status_keys))
                else:
                    result.update(connection_scores_on_window(hwnd, templates))
            conn = result["connected_score"]
            disc = result["disconnected_score"]
            if not result["error"]:
                if conn >= THRESHOLD_LOW and conn - disc >= _CONNECTION_MIN_DELTA:
                    result["state"] = "connected"
                elif disc >= THRESHOLD_LOW and disc - conn >= _CONNECTION_MIN_DELTA:
                    result["state"] = "disconnected"
    except Exception as exc:
        result["error"] = str(exc)
    result["elapsed_s"] = time.monotonic() - started
    debug_log(
        "DropView connection: "
        f"state={result['state']}, connected={result['connected_score']:.3f}, "
        f"disconnected={result['disconnected_score']:.3f}, "
        f"elapsed={result['elapsed_s']:.3f}s, region={result['region']}, "
        f"error={result['error'] or 'none'}"
    )
    return result


def _discrete_pad_activity(snapshot):
    if snapshot.get("optional_error"):
        return "unknown"
    if snapshot["state"] != "connected":
        return "disconnected" if snapshot["state"] == "disconnected" else "unknown"
    connection_y = snapshot.get("connected_center_y")
    if connection_y is None:
        return "unknown"

    def status_row_score(key):
        center_y = snapshot.get(f"{key}_center_y")
        # A Run label elsewhere in the client is not the status field.
        if center_y is None or abs(center_y - connection_y) > 12:
            return 0.0
        return snapshot[f"{key}_score"]

    running = status_row_score("pad_status_run")
    stopped = status_row_score("pad_status_stopped")
    if running >= 0.9 and running - stopped >= 0.08:
        return "running"
    if stopped >= 0.9 and stopped - running >= 0.08:
        return "stopped"
    return "unknown"


def observe_dropview(*, expected_owner=None, should_focus=None) -> dict:
    """Read link and PAD status from the same window and timestamp the sample."""
    observation = {"sampled_at": time.monotonic(), "dv_open": None, "state": "unknown",
                   "pad_activity": "unknown", "pad_observed": expected_owner is not None,
                   "responding": None, "process_identity": None}
    scope = {"pid": expected_owner["pid"]} if expected_owner else {}
    try:
        hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05, **scope)
    except TimeoutError:
        return {**observation, "dv_open": False, "state": "disconnected", "process_identity": expected_owner}

    identity = dropview_process_identity(hwnd)
    observation["dv_open"] = True
    if expected_owner and identity != expected_owner:
        if identity is not None:
            observation.update(dv_open=False, state="disconnected", process_identity=expected_owner)
        return observation

    options = {"hwnd": hwnd, "measurement_status": expected_owner is not None}
    sampled_at = time.monotonic()
    snapshot = _connection_snapshot(**options)
    if (snapshot["state"] == "unknown" and should_focus
            and any(reason in snapshot.get("error", "").lower() for reason in ("covered", "hidden", "minimized"))
            and should_focus()):
        sampled_at = time.monotonic()
        snapshot = _connection_snapshot(should_focus, **options)
    responding = dropview_responding(hwnd)
    if identity is not None and dropview_process_identity(hwnd) != identity:
        return observation

    snapshot.pop("image", None)
    observation.update(snapshot, sampled_at=snapshot.get("sampled_at", sampled_at),
                       process_identity=identity, responding=responding)
    if expected_owner and snapshot["dv_open"]:
        observation["pad_activity"] = _discrete_pad_activity(snapshot)
    return observation


def _save_connection_failure(snapshot: dict):
    image = snapshot.get("image")
    if image is None:
        debug_log("DropView failure image unavailable: " + snapshot.get("error", ""),
                  level="warning")
        return
    try:
        directory = os.path.join(os.path.dirname(_debug_log_path()), "dropview_diagnostics")
        os.makedirs(directory, exist_ok=True)
        filename = f"connection_failure_{time.time_ns()}.png"
        path = os.path.join(directory, filename)
        image.save(path)
        debug_log(f"DropView connection failure image: {path}", level="warning")
    except Exception as exc:
        debug_log(f"Could not save DropView failure image: {exc}", level="warning")


def _verify_dropview_connection(*, hwnd=None):
    started = time.monotonic()
    deadline = started + _CONNECTION_VERIFY_TIMEOUT
    stable_hits = 0
    snapshot = {}
    scope = {"pid": get_window_pid(hwnd)} if hwnd is not None else {}
    while time.monotonic() < deadline:
        snapshot = _connection_snapshot(hwnd=hwnd) if hwnd is not None else _connection_snapshot()
        connecting = window_exists(CONNECTING_DIALOG, **scope)
        stable_hits = stable_hits + 1 if snapshot["state"] == "connected" and not connecting else 0
        debug_log(f"DropView final verification: stable_hits={stable_hits}, "
                  f"connecting={connecting}, elapsed={time.monotonic() - started:.3f}s")
        if stable_hits >= 2 and time.monotonic() <= deadline:
            return
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.2, remaining))
    _save_connection_failure(snapshot)
    raise RuntimeError(
        "DropSens connection signal was received, but final verification failed "
        f"within {_CONNECTION_VERIFY_TIMEOUT:.1f}s "
        f"(state={snapshot.get('state', 'unknown')}, "
        f"connected={snapshot.get('connected_score', 0.0):.3f}, "
        f"disconnected={snapshot.get('disconnected_score', 0.0):.3f}). "
        "See bench_test_debug.log for details."
    )


def wait_until_connected(timeout=30, poll_interval=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _connection_snapshot()["state"] == "connected":
            return
        time.sleep(poll_interval)
    raise TimeoutError("DropSens connection timed out.")


def wait_until_disconnected(timeout=10, poll_interval=1.0, *, hwnd=None):
    deadline = time.monotonic() + timeout
    # Leave a bounded observation window after a late modal acknowledgement.
    final_deadline = deadline + 4
    if hwnd is None and window_exists(DROPVIEW_WINDOW_NAME):
        hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.5, poll_interval=0.1)
    owner = dropview_process_identity(hwnd) if hwnd is not None else None
    options = {"hwnd": hwnd} if hwnd is not None else {}
    while time.monotonic() < deadline:
        if owner:
            if dropview_process_identity(hwnd) != owner:
                raise RuntimeError("The DropView process changed while waiting for disconnection.")
            if acknowledge_discrete_unsaved_warning(owner):
                deadline = min(final_deadline, max(deadline, time.monotonic() + poll_interval))
            if window_exists(WARNING_UNSAVED, pid=owner["pid"]):
                time.sleep(poll_interval)
                continue
            if dropview_process_identity(hwnd) != owner:
                raise RuntimeError("The DropView process changed while acknowledging the disconnect warning.")
        if _connection_snapshot(**options)["state"] == "disconnected":
            return
        time.sleep(poll_interval)
    raise TimeoutError("DropSens disconnection could not be verified; the link or a blocking warning is still unresolved.")


def _is_dropview_connected() -> bool:
    return _connection_snapshot()["state"] == "connected"


def get_dropview_connection_scores(should_focus=None) -> dict:
    result = _connection_snapshot(should_focus) if should_focus is not None else _connection_snapshot()
    result.pop("image", None)
    return result


# ── DropView exe ───────────────────────────────────────────────

def _get_dropview_exe() -> str:
    saved = get_last("dropview_exe", "")
    if saved and os.path.isfile(saved):
        return saved
    for candidate in DROPVIEW_EXE_CANDIDATES:
        if os.path.isfile(candidate):
            remember("dropview_exe", candidate)
            return candidate

    from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
    QMessageBox.information(None, "DropView Not Found",
        "DropView.exe was not found.\nPlease select the DropView.exe file.")
    path, _ = QFileDialog.getOpenFileName(None,
        "Select DropView.exe File", "",
        "Executable (*.exe);;All files (*.*)")

    if not path:
        raise FileNotFoundError("DropView.exe was not selected; operation cancelled.")
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Selected file not found: {path}")
    remember("dropview_exe", path)
    return path

# ── Multiscript Editor yardımcıları ───────────────────────────

def _count_and_clear_scripts(ms_hwnd, listbox_x, listbox_y):
    rect = win32gui.GetWindowRect(ms_hwnd)
    region_x = rect[0] + 5
    region_y = rect[1] + 60
    region_w = 240
    region_h = 300

    pyautogui.click(listbox_x, listbox_y)
    time.sleep(SLEEP_AFTER_CLICK)
    pyautogui.hotkey("ctrl", "home")
    time.sleep(0.2)

    count = 0
    for _ in range(30):
        before = _grab_region(region_x, region_y, region_w, region_h)
        pyautogui.press("down")
        time.sleep(0.2)
        after = _grab_region(region_x, region_y, region_w, region_h)
        if _images_equal(before, after):
            break
        count += 1
    return count


def _delete_scripts_above(ms_hwnd, listbox_x, listbox_y, count):
    if count == 0:
        return
    pyautogui.click(listbox_x, listbox_y)
    time.sleep(SLEEP_AFTER_CLICK)
    for i in range(count):
        pyautogui.press("up")
        time.sleep(SLEEP_AFTER_CLICK)
        dx, dy = find_on_screen(
            _IMG["delete_btn"],
            threshold=THRESHOLD_MID,
            hwnd=ms_hwnd,
            use_foreground_fallback=False,
        )
        pyautogui.click(dx, dy)
        time.sleep(0.4)


# ── Ana adım fonksiyonları ─────────────────────────────────────

def step_launch_dropview():
    """Sadece DropView.exe'yi başlatır, bağlanmaz."""
    if window_exists(DROPVIEW_WINDOW_NAME):
        return
    subprocess.Popen([_get_dropview_exe()])
    find_window(DROPVIEW_WINDOW_NAME, timeout=TIMEOUT_WINDOW_OPEN)
    time.sleep(SLEEP_AFTER_LAUNCH)


def _verified_dropview_process(owner):
    process = psutil.Process(owner["pid"])
    if abs(process.create_time() - owner["created"]) >= 0.01 or not process.is_running():
        return None
    if not _is_dropview_process(process):
        raise RuntimeError("The recorded process could not be verified as DropView.")
    return process


def _check_restart_cancelled(cancelled):
    if cancelled and cancelled():
        raise RuntimeError("DropView restart was cancelled.")


def _wait_for_dropview_close(owner, *, cancelled=None, timeout=8):
    """Wait for process exit while servicing only its verified unsaved-curves warning."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _check_restart_cancelled(cancelled)
        process = _verified_dropview_process(owner)
        if process is None:
            return
        acknowledge_discrete_unsaved_warning(owner)
        _check_restart_cancelled(cancelled)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            process.wait(timeout=min(0.25, remaining))
            return
        except psutil.TimeoutExpired:
            continue
    if _verified_dropview_process(owner) is not None:
        raise psutil.TimeoutExpired(timeout, pid=owner["pid"])


def _close_verified_dropview(owner, hwnd=None, *, allow_terminate, hung_only=False,
                             service_dialogs=False, cancelled=None, log_fn=None):
    """Close a verified process using the caller's escalation policy."""
    _check_restart_cancelled(cancelled)
    try:
        process = _verified_dropview_process(owner)
        if process is None:
            return
        pid = owner["pid"]
        if hung_only or service_dialogs:
            hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.5, poll_interval=0.1, pid=pid)
        if hwnd is not None:
            if dropview_process_identity(hwnd) != owner:
                raise RuntimeError("The DropView window no longer belongs to the recorded process.")
            if hung_only:
                acknowledge_discrete_unsaved_warning(owner)
                if dropview_responding(hwnd):
                    raise RuntimeError("DropView is responding again; a forced restart was withheld.")
            elif service_dialogs:
                acknowledge_discrete_unsaved_warning(owner)
                if dropview_process_identity(hwnd) != owner:
                    if _verified_dropview_process(owner) is None:
                        return
                    raise RuntimeError("The DropView window changed before normal closure.")
            _check_restart_cancelled(cancelled)
            _log(log_fn, f"Requesting a normal close of DropView (PID={pid}).")
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            try:
                if service_dialogs:
                    _wait_for_dropview_close(owner, cancelled=cancelled)
                else:
                    process.wait(timeout=5)
                return
            except psutil.TimeoutExpired:
                _check_restart_cancelled(cancelled)
                if service_dialogs and allow_terminate:
                    _log(log_fn, "Normal DropView closure timed out; verifying the original process before forced termination.")

        if not allow_terminate:
            raise RuntimeError("DropView did not close normally; forced termination is unavailable in this recovery.")
        if hung_only:
            acknowledge_discrete_unsaved_warning(owner)
        process = _verified_dropview_process(owner)
        if process is None:
            return
        if hung_only and (dropview_process_identity(hwnd) != owner or dropview_responding(hwnd)):
            raise RuntimeError("The recorded DropView window is no longer confirmed unresponsive.")
        _check_restart_cancelled(cancelled)
        _log(log_fn, f"Terminating verified DropView (PID={pid}).")
        process.terminate()
        process.wait(timeout=5)
    except psutil.NoSuchProcess:
        return


def stop_unresponsive_dropview(expected_owner, *, cancelled, log_fn=None):
    """Escalate a confirmed hang only while the original window is unresponsive."""
    _close_verified_dropview(expected_owner, allow_terminate=True, hung_only=True, cancelled=cancelled, log_fn=log_fn)


def close_dropview_for_recovery(expected_owner, *, cancelled, log_fn=None):
    """Close a failed link's original session, escalating after normal-close timeout."""
    _close_verified_dropview(expected_owner, allow_terminate=True, service_dialogs=True,
                             cancelled=cancelled, log_fn=log_fn)


def restore_dropview_control(previous_owner=None, log_fn=None, *, allow_terminate=False, cancelled=None):
    """Reconnect or relaunch; never treat a new editor as proof of old device state."""
    _check_restart_cancelled(cancelled)
    hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.5, poll_interval=0.1) if window_exists(DROPVIEW_WINDOW_NAME) else None
    current = dropview_process_identity(hwnd) if hwnd else None
    if hwnd and current is None:
        raise RuntimeError("The DropView window process could not be verified.")
    if hwnd and current and previous_owner and current != previous_owner:
        step_connect_dropsens(log_fn=log_fn, hwnd=hwnd)
        _verify_dropview_connection(hwnd=hwnd)
        return False
    if hwnd and dropview_responding(hwnd):
        try:
            step_connect_dropsens(log_fn=log_fn, hwnd=hwnd)
            _verify_dropview_connection(hwnd=hwnd)
            if current == previous_owner and window_exists(MULTISCRIPT_WINDOW, pid=current["pid"]):
                return True
        except RuntimeError as exc:
            if log_fn:
                log_fn(f"DropView reconnect before restart failed: {exc}")

    identity = current or previous_owner
    if identity:
        _close_verified_dropview(identity, hwnd, allow_terminate=allow_terminate, cancelled=cancelled, log_fn=log_fn)
    if _get_dropview_pid() is not None:
        raise RuntimeError("A DropView process is still present; a duplicate will not be launched.")
    _check_restart_cancelled(cancelled)
    step_launch_dropview()
    _check_restart_cancelled(cancelled)
    step_connect_dropsens(log_fn=log_fn)
    _verify_dropview_connection()
    return False


def step_connect_dropsens(target_com: str = None, log_fn=None, *, hwnd=None, exact_port=False, force_reconnect=False):
    """Connect via DropView, with fallback only for the regular COM selection."""
    def _log(msg):
        debug_log(msg)
        if log_fn:
            log_fn(msg)

    options = {"hwnd": hwnd} if hwnd is not None else {}
    connected = _connection_snapshot(**options)["state"] == "connected" if options else _is_dropview_connected()
    if connected and not force_reconnect:
        if exact_port:
            raise RuntimeError("DropView is already connected. Disconnect it before selecting a DropSens device.")
        _verify_dropview_connection(**options)
        return
    if hwnd is None and not window_exists(DROPVIEW_WINDOW_NAME):
        raise RuntimeError("DropView window is not open.")
    dv_hwnd = hwnd if hwnd is not None else find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if force_reconnect:
        owner = dropview_process_identity(dv_hwnd)
        if owner:
            acknowledge_discrete_unsaved_warning(owner)
            if window_exists(WARNING_UNSAVED, pid=owner["pid"]):
                raise RuntimeError("The unsaved-curves warning is still open; reconnection cannot continue.")
            if dropview_process_identity(dv_hwnd) != owner:
                raise RuntimeError("The DropView process changed before reconnection.")
    _focus_window(dv_hwnd)
    if force_reconnect:
        # A cable-loss dialog invalidates the old green indicator.
        safe_sequence(dv_hwnd, [{"type": "hotkey", "keys": ("ctrl", "d"),
                                 "post_delay": SLEEP_AFTER_COMMAND}], log_fn=log_fn)
        wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW, hwnd=dv_hwnd)

    manual_attempts = []
    result = "timeout"
    ports = [target_com] if exact_port else _resolve_target_dropsens_ports(preferred_port=target_com)
    for com in ports:
        _log(f"│  Trying manual connection: {com}...")
        manual_options = {"exact_port": True} if exact_port else {}
        result = _connect_manual(dv_hwnd, com, log_fn=log_fn, **manual_options)
        manual_attempts.append((com, result))
        if result == "connected":
            _verify_dropview_connection(**options)
            _log(f"│  DropSens connected (manual: {com}).")
            if exact_port:
                return
            remember_value("dropsens_com", com)
            remember_value("dropsens_last_successful_port", com)
            remember_value("dropsens_connection_route", {"kind": "manual", "port": com})
            return
        _log(f"│  Manual connection failed ({com}: {result}).")
        pid = get_window_pid(dv_hwnd)
        if pid and window_exists(MANUAL_CONNECTION_WINDOW, pid=pid):
            dialog = find_window(MANUAL_CONNECTION_WINDOW, timeout=0.5, poll_interval=0.1, pid=pid)
            win32gui.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
            wait_for_window_close(MANUAL_CONNECTION_WINDOW, timeout=3, hwnd=dialog)

    if exact_port:
        raise RuntimeError(f"DropView could not connect on {target_com} ({result}).")

    attempts_str = ", ".join(f"{p}={s}" for p, s in manual_attempts)
    _log(f"│  Manual connections failed ({attempts_str}); retrying with Ctrl+C...")
    with _track_connection_error(dv_hwnd) as connection_error:
        safe_sequence(
            dv_hwnd,
            [
                {
                    "type": "hotkey",
                    "keys": ("ctrl", "c"),
                    "post_delay": SLEEP_AFTER_COMMAND,
                },
            ],
            log_fn=log_fn,
        )
        fallback_result = _wait_for_connection_result(
            timeout=TIMEOUT_CONNECT, log_fn=log_fn, hwnd=dv_hwnd, connection_error=connection_error)
    if fallback_result != "connected":
        raise RuntimeError(
            f"DropSens connection could not be established (manual: {result}, "
            f"Ctrl+C: {fallback_result}). Make sure the device is connected "
            f"and DropView is ready."
        )
    _verify_dropview_connection(**options)
    _log("│  DropSens connected (Ctrl+C fallback).")
    remember_value("dropsens_connection_route", {"kind": "automatic", "port": None})


def step_disconnect_dropsens():
    """Sadece Ctrl+D ile DropSens bağlantısını keser."""
    if not window_exists(DROPVIEW_WINDOW_NAME):
        return
    if not _is_dropview_connected():
        return

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    safe_sequence(
        dv_hwnd,
        [
            {
                "type": "hotkey",
                "keys": ("ctrl", "d"),
                "post_delay": SLEEP_AFTER_COMMAND,
            },
        ],
        log_fn=None,
    )
    wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW)

def get_discrete_measurement_state(*, expected_owner=None, refocus_if_covered=True, hwnd=None):
    """Read the visible editor before attempting to change focus."""
    result = {"state": "unknown", "sampled_at": time.monotonic(), "error": ""}
    try:
        scope = {"pid": expected_owner["pid"]} if expected_owner else {}
        if hwnd is None and not window_exists(MULTISCRIPT_WINDOW, **scope):
            result["editor_missing"] = not window_exists(MULTISCRIPT_WINDOW, include_hidden=True, **scope)
            result["editor_hidden"] = not result["editor_missing"]
            result["error"] = "Multiscript Editor is not open; measurement state is unverified."
            return result
        hwnd = hwnd or find_window(MULTISCRIPT_WINDOW, timeout=0.2, poll_interval=0.05, **scope)
        if expected_owner and dropview_process_identity(hwnd) != expected_owner:
            result["editor_missing"] = True
            result["error"] = "The editor does not belong to the measurement's DropView process."
            return result
        templates = {
            "running": _IMG["yellow_dot_selected"], "stopped": _IMG["green_dot_selected"],
        }
        with _indicator_capture_lock:
            scores = connection_scores_on_window(hwnd, templates, full_client=True)
            visibility_error = scores.get("error", "").lower()
            if refocus_if_covered and any(
                    reason in visibility_error for reason in ("covered", "hidden", "minimized")):
                result["error"] = scores["error"]
                _focus_window(hwnd, sleep_after=SLEEP_AFTER_FOCUS, require_foreground=False)
                scores = connection_scores_on_window(hwnd, templates, full_client=True)
        result.update({key: value for key, value in scores.items() if key != "image"})
        if not scores["error"]:
            running, stopped = scores["running_score"], scores["stopped_score"]
            if running >= 0.8 and running - stopped >= 0.08:
                result["state"] = "running"
            elif stopped >= 0.8 and stopped - running >= 0.08:
                result["state"] = "stopped"
    except Exception as exc:
        result["error"] = "; ".join(part for part in (result["error"], str(exc)) if part)
    return result


def _find_ready_discrete_editor(expected_owner, *, timeout=0, refocus_if_covered=False):
    deadline = time.monotonic() + timeout
    while True:
        candidates = []

        def collect(hwnd, _):
            if (win32gui.IsWindowVisible(hwnd) and MULTISCRIPT_WINDOW in win32gui.GetWindowText(hwnd)
                    and (not expected_owner or get_window_pid(hwnd) == expected_owner["pid"])):
                candidates.append(hwnd)

        win32gui.EnumWindows(collect, None)
        idle_editor = None
        for hwnd in candidates:
            observation = get_discrete_measurement_state(
                expected_owner=expected_owner, hwnd=hwnd,
                refocus_if_covered=refocus_if_covered and bool(expected_owner))
            if observation["state"] == "running":
                return hwnd, "running"
            if observation["state"] == "stopped" and idle_editor is None:
                idle_editor = hwnd
            if refocus_if_covered and observation["state"] == "unknown":
                error = observation.get("error") or "Run/Stop indicator unreadable"
                debug_log(f"Discrete PAD Stop could not read existing Multiscript hwnd={hwnd}: {error}.")
        if idle_editor is not None:
            return idle_editor, "stopped"
        if time.monotonic() >= deadline:
            return None, "unknown"
        time.sleep(0.1)


def _acquire_discrete_editor(expected_owner, *, reopen_if_needed=False, for_stop=False, log_fn=None):
    """Select a readable editor and reopen it only in the verified original session."""
    dv_hwnd = None if for_stop else verify_discrete_session_connection(expected_owner)
    editor, state = _find_ready_discrete_editor(expected_owner, refocus_if_covered=reopen_if_needed)
    if editor is None:
        if not reopen_if_needed or not expected_owner:
            raise RuntimeError("No Multiscript window with a verified run or idle indicator is available.")
        dv_hwnd = dv_hwnd or verify_discrete_session_connection(expected_owner)
        _open_multiscript_menu(dv_hwnd, log_fn=log_fn, timeout=3, expected_owner=expected_owner)
        editor, state = _find_ready_discrete_editor(expected_owner, timeout=3)
        if editor is None:
            raise RuntimeError("The reopened Multiscript window did not show a readable run or idle indicator.")
    return editor, state


def step_stop_measure(log_fn=None, require_editor=False, *, expected_owner=None, reopen_if_needed=False):
    """Stop butonuna basar, ölçümün durmasını bekler, Exit ile Multiscript Editor'ü kapatır."""
    ensure_dialog_watchdog(log_fn=log_fn)
    focus = not require_editor
    if require_editor:
        ms_hwnd, state = _acquire_discrete_editor(
            expected_owner, reopen_if_needed=reopen_if_needed, for_stop=True, log_fn=log_fn)
        debug_log(f"Discrete PAD Stop selected Multiscript hwnd={ms_hwnd}, state={state}.")
    else:
        if not window_exists(MULTISCRIPT_WINDOW):
            return
        ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=5)
        _focus_window(ms_hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)
        state = get_discrete_measurement_state()["state"]

    # An external Stop can leave the editor open with its Stop button disabled.
    # Still verify idle and close the editor through the normal path below.
    if state != "stopped":
        sx, sy = safe_find_on_screen(
            ms_hwnd,
            _IMG["stop_btn"],
            threshold=THRESHOLD_HIGH,
            log_fn=log_fn,
            focus=focus,
        )
        if expected_owner and dropview_process_identity(ms_hwnd) != expected_owner:
            raise RuntimeError("The Multiscript process changed before Stop.")
        safe_click(ms_hwnd, sx, sy, log_fn=log_fn, post_delay=SLEEP_AFTER_COMMAND, focus=focus)

    safe_wait_for_image_gone(
        ms_hwnd,
        _IMG["yellow_dot_selected"],
        timeout=TIMEOUT_STOP_MEASURE,
        poll_interval=POLL_INTERVAL_SLOW,
        threshold=THRESHOLD_HIGH,
        log_fn=log_fn,
        focus=focus,
    )

    safe_wait_for_image(
        ms_hwnd,
        _IMG["green_dot_selected"],
        timeout=15,
        poll_interval=POLL_INTERVAL_NORMAL,
        threshold=THRESHOLD_HIGH,
        log_fn=log_fn,
        focus=focus,
    )
    if require_editor and get_discrete_measurement_state(
            expected_owner=expected_owner, hwnd=ms_hwnd, refocus_if_covered=False)["state"] != "stopped":
        raise RuntimeError("The old Multiscript Editor did not confirm an idle measurement.")

    ex, ey = safe_find_on_screen(
        ms_hwnd,
        _IMG["exit_btn"],
        threshold=THRESHOLD_HIGH,
        log_fn=log_fn,
        focus=focus,
    )
    if expected_owner and dropview_process_identity(ms_hwnd) != expected_owner:
        raise RuntimeError("The Multiscript process changed before Exit.")
    safe_click(ms_hwnd, ex, ey, log_fn=log_fn, post_delay=SLEEP_AFTER_FOCUS, focus=focus)

    close_options = {"hwnd": ms_hwnd} if require_editor else {}
    wait_for_window_close(MULTISCRIPT_WINDOW, timeout=TIMEOUT_CLOSE_WINDOW, **close_options)
    debug_log("Multiscript Editor closed.")


def step_start_continuous_pad(config: dict, log_fn=None):
    """Configure AutoSave/method if needed, then start a Continuous PAD segment."""
    global _continuous_pad_active_file, _continuous_pad_last_snapshot, _continuous_pad_measurements_dir

    config = config or {}
    measurements_dir = config.get("measurements_dir", "")
    if not measurements_dir or not os.path.isdir(measurements_dir):
        raise RuntimeError(f"Continuous PAD measurements directory not found: {measurements_dir}")

    step_configure_continuous_pad_autosave(config, log_fn=log_fn)
    step_load_continuous_pad_method(config, log_fn=log_fn)

    dv_hwnd = _ensure_dropview_ready("Continuous PAD start", log_fn=log_fn)
    before = _snapshot_mtp_files(measurements_dir)
    _continuous_pad_last_snapshot = before
    _continuous_pad_measurements_dir = measurements_dir
    _continuous_pad_active_file = ""
    debug_log("Continuous PAD: starting segment with Ctrl+R...")
    try:
        safe_sequence(
            dv_hwnd,
            [{"type": "hotkey", "keys": ("ctrl", "r"), "post_delay": SLEEP_AFTER_COMMAND}],
            log_fn=log_fn,
        )
    except Exception as exc:
        debug_log(f"Continuous PAD: Ctrl+R failed ({exc}); trying Alt+D -> R.")
        safe_sequence(
            dv_hwnd,
            [
                {"type": "hotkey", "keys": ("alt", "d"), "post_delay": SLEEP_AFTER_FOCUS},
                {"type": "press", "key": "r", "post_delay": SLEEP_AFTER_COMMAND},
            ],
            log_fn=log_fn,
        )

    _accept_if_dialog_present(
        "Run experiment",
        wait=2.5,
        log_fn=log_fn,
        owner_hwnd=dv_hwnd,
        image_key="run_experiment_confirm_dialog",
        exact=True,
    )
    _wait_for_optional_dialog_close(
        "Send",
        owner_hwnd=dv_hwnd,
        appear_wait=2.0,
        close_timeout=float(config.get("send_timeout", _CONTINUOUS_PAD_SEND_TIMEOUT)),
        log_fn=log_fn,
        exact=True,
    )
    _accept_if_dialog_present(
        "Run experiment",
        wait=3.0,
        log_fn=log_fn,
        owner_hwnd=dv_hwnd,
        image_key="run_experiment_confirm_dialog",
        exact=True,
    )

    blocking = find_owned_dialogs(dv_hwnd)
    if blocking:
        titles = [win32gui.GetWindowText(h) for h in blocking]
        raise RuntimeError(f"Continuous PAD start left an unexpected dialog open: {titles}")

    debug_log("Continuous PAD segment started; .mtp file will be verified after Stop/AutoSave closes it.")


def step_stop_continuous_pad(log_fn=None):
    """Stop the active Continuous PAD segment and wait until AutoSave closes the .mtp."""
    global _continuous_pad_active_file

    dv_hwnd = _ensure_dropview_ready("Continuous PAD stop", log_fn=log_fn)
    debug_log("Continuous PAD: stopping segment with Ctrl+S...")
    try:
        safe_sequence(
            dv_hwnd,
            [{"type": "hotkey", "keys": ("ctrl", "s"), "post_delay": SLEEP_AFTER_COMMAND}],
            log_fn=log_fn,
        )
    except Exception as exc:
        debug_log(f"Continuous PAD: Ctrl+S failed ({exc}); trying Alt+D -> S.")
        safe_sequence(
            dv_hwnd,
            [
                {"type": "hotkey", "keys": ("alt", "d"), "post_delay": SLEEP_AFTER_FOCUS},
                {"type": "press", "key": "s", "post_delay": SLEEP_AFTER_COMMAND},
            ],
            log_fn=log_fn,
        )

    # Stop should not show a dialog. If one appears, fail clearly instead of
    # advancing recipe state with an unknown DropView condition.
    owned = find_owned_dialogs(dv_hwnd)
    if owned:
        titles = [win32gui.GetWindowText(h) for h in owned]
        raise RuntimeError(f"Continuous PAD stop left an unexpected dialog open: {titles}")

    stable_target = _continuous_pad_active_file
    if not stable_target and _continuous_pad_measurements_dir:
        stable_target = _wait_for_new_mtp(
            _continuous_pad_measurements_dir,
            _continuous_pad_last_snapshot,
            timeout=_CONTINUOUS_PAD_FILE_TIMEOUT,
        )
    if not stable_target:
        raise RuntimeError(
            "Continuous PAD stop could not find an active .mtp file to verify."
        )
    stable_path = _wait_for_stable_mtp(stable_target)
    _continuous_pad_active_file = ""
    debug_log(f"Continuous PAD segment file closed: {stable_path}")


def step_configure_continuous_pad_autosave(config: dict, log_fn=None):
    """Set DropView AutoSave As to the current session measurements directory."""
    global _continuous_pad_autosave_key

    config = config or {}
    measurements_dir = config.get("measurements_dir", "")
    part_number = config.get("part_number", "")
    if not measurements_dir or not os.path.isdir(measurements_dir):
        raise RuntimeError(f"Continuous PAD AutoSave directory not found: {measurements_dir}")
    if not part_number:
        raise RuntimeError("Continuous PAD AutoSave part number is empty.")

    save_base = os.path.join(measurements_dir, part_number)
    dv_hwnd = _ensure_dropview_ready("Continuous PAD AutoSave", log_fn=log_fn)
    autosave_key = (
        get_window_pid(dv_hwnd),
        os.path.normcase(os.path.abspath(measurements_dir)),
        part_number,
    )
    if _continuous_pad_autosave_key == autosave_key:
        return
    debug_log(f"Continuous PAD: configuring AutoSave As -> {save_base}")
    safe_sequence(
        dv_hwnd,
        [{"type": "hotkey", "keys": ("alt", "f"), "post_delay": SLEEP_AFTER_FOCUS}],
        log_fn=log_fn,
    )
    _click_popup_image("autosave_as_menu_item", log_fn=log_fn, threshold=THRESHOLD_LOW)

    nodes_hwnd = _find_dialog_by_title(
        "Select nodes to apply",
        timeout=10,
        owner_hwnd=dv_hwnd,
        image_key="select_nodes_unchecked",
    )
    _ensure_node_checked(nodes_hwnd, log_fn=log_fn)
    _click_button(nodes_hwnd, "Accept", "accept_btn", log_fn=log_fn, post_delay=SLEEP_AFTER_COMMAND)

    save_hwnd = _find_dialog_by_title(
        "Save as",
        timeout=12,
        owner_hwnd=dv_hwnd,
        image_key="save_as_node_dialog",
    )
    _paste_path_into_file_dialog(save_hwnd, save_base, log_fn=log_fn, submit=True)
    _submit_file_dialog_or_fallback(
        save_hwnd,
        "Save as",
        "Save",
        "save_btn",
        log_fn=log_fn,
    )
    _continuous_pad_autosave_key = autosave_key


def step_load_continuous_pad_method(config: dict, log_fn=None):
    """Load the Continuous PAD .tp method and apply it to Node 1."""
    global _continuous_pad_method_path

    config = config or {}
    method_path = config.get("method_path", "")
    if not method_path or not os.path.isfile(method_path):
        raise RuntimeError(f"Continuous PAD method file not found: {method_path}")

    dv_hwnd = _ensure_dropview_ready("Continuous PAD method load", log_fn=log_fn)
    method_key = (get_window_pid(dv_hwnd), os.path.normcase(os.path.abspath(method_path)))
    if _continuous_pad_method_path == method_key:
        return

    debug_log(f"Continuous PAD: loading method -> {method_path}")
    safe_sequence(
        dv_hwnd,
        [
            {"type": "hotkey", "keys": ("alt", "f"), "post_delay": SLEEP_AFTER_FOCUS},
            {"type": "press", "key": "m", "post_delay": SLEEP_AFTER_COMMAND},
        ],
        log_fn=log_fn,
    )
    _accept_if_dialog_present(
        "Load method",
        wait=5.0,
        log_fn=log_fn,
        owner_hwnd=dv_hwnd,
        image_key="load_method_confirm_dialog",
        exact=True,
    )

    open_hwnd = _find_dialog_by_title(
        "Load method...",
        timeout=10,
        owner_hwnd=dv_hwnd,
        image_key="load_method_open_dialog",
        exact=True,
    )
    _paste_path_into_file_dialog(open_hwnd, method_path, log_fn=log_fn, submit=True)
    _submit_file_dialog_or_fallback(
        open_hwnd,
        "Load method...",
        "Open",
        "open_btn",
        log_fn=log_fn,
    )

    nodes_hwnd = _find_dialog_by_title(
        "Select nodes to apply",
        timeout=10,
        owner_hwnd=dv_hwnd,
        image_key="method_select_nodes_unchecked",
    )
    _ensure_node_checked(
        nodes_hwnd,
        log_fn=log_fn,
        context="Continuous PAD method load",
    )
    _click_button(
        nodes_hwnd,
        "Accept",
        "method_select_nodes_accept",
        log_fn=log_fn,
        post_delay=SLEEP_AFTER_COMMAND,
    )
    _continuous_pad_method_path = method_key


def step_run_cv(config: dict, log_fn=None) -> str:
    """Load CV, configure AutoSave, run, and wait until a stable .mtc is written."""
    config = config or {}
    method_path = config.get("method_path", "")
    if not method_path or not os.path.isfile(method_path):
        raise RuntimeError(f"CV method file not found: {method_path}")
    measurements_dir = config.get("measurements_dir", "")
    if not measurements_dir or not os.path.isdir(measurements_dir):
        raise RuntimeError(f"CV measurements directory not found: {measurements_dir}")

    step_load_cv_method(config, log_fn=log_fn)
    step_configure_cv_autosave(config, log_fn=log_fn)

    dv_hwnd = _ensure_dropview_ready("CV run", log_fn=log_fn)
    before = _snapshot_mtc_files(measurements_dir)
    debug_log("CV: running measurement with Ctrl+R...")
    try:
        safe_sequence(
            dv_hwnd,
            [{"type": "hotkey", "keys": ("ctrl", "r"), "post_delay": SLEEP_AFTER_COMMAND}],
            log_fn=log_fn,
        )
    except Exception as exc:
        debug_log(f"CV: Ctrl+R failed ({exc}); trying Alt+D -> R.")
        safe_sequence(
            dv_hwnd,
            [
                {"type": "hotkey", "keys": ("alt", "d"), "post_delay": SLEEP_AFTER_FOCUS},
                {"type": "press", "key": "r", "post_delay": SLEEP_AFTER_COMMAND},
            ],
            log_fn=log_fn,
        )

    _accept_if_dialog_present(
        "Run experiment",
        wait=2.5,
        log_fn=log_fn,
        owner_hwnd=dv_hwnd,
        image_key="run_experiment_confirm_dialog",
        exact=True,
    )
    _wait_for_optional_dialog_close(
        "Send",
        owner_hwnd=dv_hwnd,
        appear_wait=2.0,
        close_timeout=float(config.get("send_timeout", _CONTINUOUS_PAD_SEND_TIMEOUT)),
        log_fn=log_fn,
        exact=True,
    )
    _accept_if_dialog_present(
        "Run experiment",
        wait=3.0,
        log_fn=log_fn,
        owner_hwnd=dv_hwnd,
        image_key="run_experiment_confirm_dialog",
        exact=True,
    )

    mtc_path = _wait_for_new_mtc(
        measurements_dir,
        before,
        timeout=_cv_file_timeout(method_path, config.get("file_timeout")),
    )
    stable_path = _wait_for_stable_mtc(
        mtc_path,
        timeout=float(config.get("stable_timeout", _CONTINUOUS_PAD_STABLE_TIMEOUT)),
    )
    debug_log(f"CV measurement file closed: {stable_path}")
    return stable_path


def step_configure_cv_autosave(config: dict, log_fn=None):
    """Set DropView AutoSave As for CV .mtc output."""
    global _cv_autosave_key

    config = config or {}
    measurements_dir = config.get("measurements_dir", "")
    part_number = config.get("part_number", "")
    if not measurements_dir or not os.path.isdir(measurements_dir):
        raise RuntimeError(f"CV AutoSave directory not found: {measurements_dir}")
    if not part_number:
        raise RuntimeError("CV AutoSave part number is empty.")

    save_base = os.path.join(measurements_dir, part_number)
    dv_hwnd = _ensure_dropview_ready("CV AutoSave", log_fn=log_fn)
    autosave_key = (
        get_window_pid(dv_hwnd),
        os.path.normcase(os.path.abspath(measurements_dir)),
        part_number,
    )
    if _cv_autosave_key == autosave_key:
        return
    debug_log(f"CV: configuring AutoSave As -> {save_base}")
    safe_sequence(
        dv_hwnd,
        [{"type": "hotkey", "keys": ("alt", "f"), "post_delay": SLEEP_AFTER_FOCUS}],
        log_fn=log_fn,
    )
    _click_popup_image("autosave_as_menu_item", log_fn=log_fn, threshold=THRESHOLD_LOW)

    nodes_hwnd = _find_dialog_by_title(
        "Select nodes to apply",
        timeout=10,
        owner_hwnd=dv_hwnd,
        image_key="select_nodes_unchecked",
    )
    _ensure_node_checked(
        nodes_hwnd,
        log_fn=log_fn,
        context="CV AutoSave",
        visual_state_fn=_cv_node_checked_by_template,
    )
    _click_button(nodes_hwnd, "Accept", "accept_btn", log_fn=log_fn, post_delay=SLEEP_AFTER_COMMAND)

    save_hwnd = _find_dialog_by_title(
        "Save as",
        timeout=12,
        owner_hwnd=dv_hwnd,
        image_key="save_as_node_dialog",
    )
    _paste_path_into_file_dialog(save_hwnd, save_base, log_fn=log_fn, submit=True)
    _submit_file_dialog_or_fallback(
        save_hwnd,
        "Save as",
        "Save",
        "save_btn",
        log_fn=log_fn,
    )
    _cv_autosave_key = autosave_key


def step_load_cv_method(config: dict, log_fn=None):
    """Load the CV .tc method and apply it to Node 1."""
    global _cv_method_path

    config = config or {}
    method_path = config.get("method_path", "")
    if not method_path or not os.path.isfile(method_path):
        raise RuntimeError(f"CV method file not found: {method_path}")

    dv_hwnd = _ensure_dropview_ready("CV method load", log_fn=log_fn)
    method_key = (get_window_pid(dv_hwnd), os.path.normcase(os.path.abspath(method_path)))
    if _cv_method_path == method_key:
        return

    debug_log(f"CV: loading method -> {method_path}")
    safe_sequence(
        dv_hwnd,
        [
            {"type": "hotkey", "keys": ("alt", "f"), "post_delay": SLEEP_AFTER_FOCUS},
            {"type": "press", "key": "m", "post_delay": SLEEP_AFTER_COMMAND},
        ],
        log_fn=log_fn,
    )
    _accept_if_dialog_present(
        "Load method",
        wait=5.0,
        log_fn=log_fn,
        owner_hwnd=dv_hwnd,
        image_key="load_method_confirm_dialog",
        exact=True,
    )

    open_hwnd = _find_dialog_by_title(
        "Load method...",
        timeout=10,
        owner_hwnd=dv_hwnd,
        image_key="load_method_open_dialog",
        exact=True,
    )
    _paste_path_into_file_dialog(open_hwnd, method_path, log_fn=log_fn, submit=True)
    _submit_file_dialog_or_fallback(
        open_hwnd,
        "Load method...",
        "Open",
        "open_btn",
        log_fn=log_fn,
    )

    nodes_hwnd = _find_dialog_by_title(
        "Select nodes to apply",
        timeout=10,
        owner_hwnd=dv_hwnd,
        image_key="method_select_nodes_unchecked",
    )
    _ensure_node_checked(
        nodes_hwnd,
        log_fn=log_fn,
        context="CV method load",
    )
    _click_button(
        nodes_hwnd,
        "Accept",
        "method_select_nodes_accept",
        log_fn=log_fn,
        post_delay=SLEEP_AFTER_COMMAND,
    )
    _cv_method_path = method_key


def _force_close_multiscript(log_fn=None) -> bool:
    """
    Multiscript Editor'u buton-gorsel bagimliligi olmadan kapatmayi dener.
    Basariliysa True, kapanmadiysa False doner.
    """
    def _log(msg):
        debug_log(msg)

    if not window_exists(MULTISCRIPT_WINDOW):
        return True

    try:
        ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=2)
    except Exception:
        return not window_exists(MULTISCRIPT_WINDOW)

    # 1) WM_CLOSE
    try:
        win32gui.PostMessage(ms_hwnd, win32con.WM_CLOSE, 0, 0)
        wait_for_window_close(MULTISCRIPT_WINDOW, timeout=2, poll_interval=0.2)
        _log("│  Multiscript Editor closed via WM_CLOSE.")
        return True
    except Exception:
        pass

    # 2) Alt+F4
    try:
        safe_sequence(ms_hwnd, [{"type": "hotkey", "keys": ("alt", "f4"), "post_delay": SLEEP_AFTER_COMMAND}], log_fn=log_fn)
        wait_for_window_close(MULTISCRIPT_WINDOW, timeout=3, poll_interval=0.2)
        _log("│  Multiscript Editor closed via Alt+F4.")
        return True
    except Exception:
        pass

    _log("│  WARNING: Multiscript Editor could not be closed with hard fallback.")
    return False

def step_exit_dropview(config: dict, log_fn=None):
    """Ctrl+D ile bağlantıyı kes + Alt+F4 ile DropView'i kapat."""
    def _log(msg):
        debug_log(msg)

    try:
        # Process yoksa pencere de yoktur, erken çık
        if _get_dropview_pid() is None and not window_exists(DROPVIEW_WINDOW_NAME):
            _log("│  DropView is already closed.")
            return

        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)

        # Multiscript Editor hâlâ açıksa Alt+F4 onu kapatır, DropView'i değil
        if window_exists(MULTISCRIPT_WINDOW):
            _log("│  Multiscript Editor is still open; closing it first...")
            try:
                step_stop_measure(log_fn=_log)
            except Exception as e:
                _log(f"│  WARNING: Multiscript could not be closed with normal stop: {e}")
                if not _force_close_multiscript(log_fn=_log):
                    _log("│  WARNING: Multiscript could not be closed; DropView shutdown will still be attempted.")

        # Owned dialog'ları temizle (içeriğe bağımsız)
        closed = close_owned_dialogs(dv_hwnd, log_fn=_log)
        if closed:
            _log(f"│  {closed} dialog(s) closed.")

        if _is_dropview_connected():
            safe_sequence(
                dv_hwnd,
                [
                    {
                        "type": "hotkey",
                        "keys": ("ctrl", "d"),
                        "post_delay": SLEEP_AFTER_COMMAND,
                    },
                ],
                log_fn=_log,
            )
            close_owned_dialogs(dv_hwnd, log_fn=_log)
            wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW)
            _log("│  DropSens disconnected.")
        else:
            _log("│  DropSens is already disconnected.")

        # Alt+F4
        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=5)
        safe_sequence(
            dv_hwnd,
            [
                {
                    "type": "hotkey",
                    "keys": ("alt", "f4"),
                    "post_delay": SLEEP_AFTER_COMMAND,
                },
            ],
            log_fn=_log,
        )

        # Alt+F4 sonrası çıkabilecek dialog'ları kapat
        close_owned_dialogs(dv_hwnd, log_fn=_log)

        # Pencere kapandı mı doğrula
        window_closed = False
        try:
            wait_for_window_close(DROPVIEW_WINDOW_NAME, timeout=10)
            _log("│  DropView window closed.")
            window_closed = True
        except TimeoutError:
            _log("│  WARNING: DropView window did not close.")

        # Process kapandı mı doğrula; pencere kapanmadıysa doğrudan kill'e geç
        process_exited = False
        if window_closed:
            try:
                _wait_for_dropview_process_exit(timeout=10)
                _log("│  DropView process exited.")
                process_exited = True
            except TimeoutError:
                _log("│  WARNING: Process did not exit.")

        if not process_exited:
            # Kill öncesi koşulsuz Ctrl+D — COM portu düzgün serbest bırakılsın
            _log("│  Sending Ctrl+D before kill...")
            try:
                dv_hwnd_kill = find_window(DROPVIEW_WINDOW_NAME, timeout=3)
                safe_sequence(dv_hwnd_kill, [{"type": "hotkey", "keys": ("ctrl", "d"), "post_delay": SLEEP_AFTER_COMMAND + 0.5}], log_fn=_log)
                _log("│  Ctrl+D sent.")
            except Exception as e:
                _log(f"│  WARNING: Ctrl+D before kill failed: {e}")

            pid = get_window_pid(dv_hwnd) or _get_dropview_pid()
            if pid is not None and pid <= 0:
                pid = _get_dropview_pid()
            if pid:
                _log(f"│  Force-closing DropView (PID={pid})...")
                try:
                    psutil.Process(pid).kill()
                    _wait_for_dropview_process_exit(timeout=5)
                    _log("│  DropView force-closed.")
                except Exception as kill_err:
                    raise RuntimeError(
                        f"DropView could not be closed: {kill_err}"
                    ) from kill_err

                # Kill sonrası COM port / driver serbest bırakma payı
                _log("│  Waiting for COM port release...")
                time.sleep(3.0)

                # Process gerçekten gitti mi son doğrulama
                if _get_dropview_pid() is not None:
                    raise RuntimeError(
                        "DropView process is still running after kill."
                    )
                _log("│  DropView process verified: closed.")
            else:
                if not window_closed:
                    raise RuntimeError(
                        "DropView window did not close and PID was not found."
                    )
                _log("│  DropView process has already exited.")

        _log("│  DropView closed.")
        _log("│  Waiting for COM port release...")
        time.sleep(2.0)
    finally:
        stop_dialog_watchdog()

def step_start_dropview(config: dict, log_fn=None, *, allow_cleanup=True, cancelled=None):
    _check_restart_cancelled(cancelled)
    ensure_dialog_watchdog(log_fn=log_fn)

    if window_exists(DROPVIEW_WINDOW_NAME):
        hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=5)
        owner = dropview_process_identity(hwnd)
        if owner is None:
            raise RuntimeError("The DropView window process could not be verified.")
        blocked = not win32gui.IsWindowEnabled(hwnd) or bool(find_owned_dialogs(hwnd))
        if not blocked and _connection_snapshot(hwnd=hwnd)["state"] == "connected":
            blocked = not _is_dropview_ui_ready(hwnd, timeout=3.0)
            if not blocked:
                time.sleep(0.4)
                if (_is_dropview_ui_ready(hwnd, timeout=2.0)
                        and _connection_snapshot(hwnd=hwnd)["state"] == "connected"
                        and dropview_process_identity(hwnd) == owner):
                    return
                _log(log_fn, "DropView startup health checks were inconsistent; verifying the connection again.")
        if blocked:
            if not allow_cleanup:
                raise RuntimeError("The existing DropView window is blocked during recovery startup.")
            _close_verified_dropview(owner, hwnd, allow_terminate=True, cancelled=cancelled, log_fn=log_fn)
    else:
        pid = _get_dropview_pid()
        if pid is not None:
            if not allow_cleanup:
                raise RuntimeError("A DropView process without a window is still present during recovery startup.")
            try:
                owner = {"pid": pid, "created": psutil.Process(pid).create_time()}
                _close_verified_dropview(owner, allow_terminate=True, cancelled=cancelled, log_fn=log_fn)
            except psutil.NoSuchProcess:
                pass

    _check_restart_cancelled(cancelled)
    if not window_exists(DROPVIEW_WINDOW_NAME) and _get_dropview_pid() is not None:
        raise RuntimeError("A DropView process is still present; a duplicate will not be launched.")
    step_launch_dropview()
    _check_restart_cancelled(cancelled)
    step_connect_dropsens(log_fn=log_fn)


def _open_multiscript_menu(dv_hwnd, log_fn=None, *, timeout=TIMEOUT_WINDOW_OPEN, expected_owner=None):
    # Alt+S S öncesi: DropView enabled ve modal yok mu doğrula
    if not win32gui.IsWindowEnabled(dv_hwnd):
        raise RuntimeError(
            "Start Measure error: DropView window is disabled "
            "(a modal dialog may be blocking it)."
        )
    owned = find_owned_dialogs(dv_hwnd)
    if owned:
        titles = [win32gui.GetWindowText(h) for h in owned]
        raise RuntimeError(
            f"Start Measure error: DropView has an open dialog: {titles}"
        )

    safe_wait_for_image(
        dv_hwnd,
        _IMG["scripts_menu"],
        timeout=timeout,
        poll_interval=POLL_INTERVAL_SLOW,
        threshold=THRESHOLD_HIGH,
        log_fn=log_fn,
    )
    time.sleep(SLEEP_AFTER_FOCUS)
    if expected_owner:
        if dropview_process_identity(dv_hwnd) != expected_owner:
            raise RuntimeError("The DropView process changed before opening Multiscript.")
    safe_sequence(
        dv_hwnd,
        [
            {"type": "hotkey", "keys": ("alt", "s"), "post_delay": SLEEP_AFTER_FOCUS},
            {"type": "press", "key": "s", "post_delay": 1.2},
        ],
        log_fn=log_fn,
        retries=1 if expected_owner else 3,
    )


def verify_discrete_session_connection(expected_owner):
    if not expected_owner:
        raise RuntimeError("The measurement's DropView process was not recorded.")
    hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.5, poll_interval=0.1, pid=expected_owner["pid"])
    if dropview_process_identity(hwnd) != expected_owner or not dropview_responding(hwnd):
        raise RuntimeError("The original DropView process could not be verified.")
    connection = _connection_snapshot(hwnd=hwnd)
    if connection["state"] == "unknown":
        connection = _connection_snapshot(lambda: True, hwnd=hwnd)
    if (connection["state"] != "connected"
            or dropview_process_identity(hwnd) != expected_owner):
        raise RuntimeError("DropSens connection in the original DropView process could not be verified.")
    return hwnd


def reopen_discrete_editor(expected_owner, log_fn=None):
    """Restore only the editor belonging to the running measurement."""
    editor, state = _acquire_discrete_editor(expected_owner, reopen_if_needed=True, log_fn=log_fn)
    debug_log(f"Multiscript control restored: hwnd={editor}, state={state}.")
    return editor


def step_start_measure(config: dict, log_fn=None):
    ensure_dialog_watchdog(log_fn=log_fn)
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    _focus_window(dv_hwnd, click_title=True)
    _open_multiscript_menu(dv_hwnd, log_fn=log_fn)

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=15)
    debug_log(f"Multiscript Editor opened (hwnd={ms_hwnd}).")
    time.sleep(SLEEP_AFTER_FOCUS)

    lbl_x, lbl_y = safe_find_on_screen(
        ms_hwnd,
        _IMG["loaded_scripts"],
        threshold=THRESHOLD_HIGH,
        log_fn=log_fn,
    )
    listbox_x = lbl_x
    listbox_y = lbl_y + 100

    lx, ly = safe_find_on_screen(
        ms_hwnd,
        _IMG["load_btn"],
        log_fn=log_fn,
    )
    safe_click(ms_hwnd, lx, ly, log_fn=log_fn, post_delay=SLEEP_AFTER_FOCUS)

    script_path = config["script_path"]
    open_hwnd = find_window("Open", timeout=10)
    time.sleep(SLEEP_AFTER_FOCUS)

    _focus_window(open_hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(script_path)
    win32clipboard.CloseClipboard()
    time.sleep(0.2)

    open_rect = win32gui.GetWindowRect(open_hwnd)
    dlg_x = open_rect[0]
    dlg_y = open_rect[1]
    dlg_w = open_rect[2] - open_rect[0]
    dlg_h = open_rect[3] - open_rect[1]

    fn_x = dlg_x + int(dlg_w * 0.45)
    fn_y = dlg_y + int(dlg_h * 0.70)
    safe_click(open_hwnd, fn_x, fn_y, log_fn=log_fn, post_delay=0.4)

    safe_sequence(
        open_hwnd,
        [
            {"type": "hotkey", "keys": ("ctrl", "a"), "post_delay": 0.1},
            {"type": "hotkey", "keys": ("ctrl", "v"), "post_delay": SLEEP_AFTER_CLICK},
            {"type": "press", "key": "enter", "post_delay": 0.8},
        ],
        log_fn=log_fn,
    )

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=10)
    _focus_window(ms_hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)
    before_count = _count_and_clear_scripts(ms_hwnd, listbox_x, listbox_y)
    _delete_scripts_above(ms_hwnd, listbox_x, listbox_y, before_count)

    rx, ry = safe_find_on_screen(
        ms_hwnd,
        _IMG["run_btn"],
        threshold=THRESHOLD_MID,
        log_fn=log_fn,
    )
    safe_click(ms_hwnd, rx, ry, log_fn=log_fn, post_delay=SLEEP_AFTER_COMMAND)

    # "Some curves have not been saved" diyalogu Run sonrası gecikmeli açılabiliyor.
    _dismiss_warning_if_present(WARNING_UNSAVED, wait=3.0)

    try:
        safe_wait_for_image(
            ms_hwnd,
            _IMG["yellow_dot_selected"],
            timeout=30,
            poll_interval=POLL_INTERVAL_SLOW,
            threshold=THRESHOLD_HIGH,
            log_fn=log_fn,
        )
    except Exception:
        if log_fn:
            fg_title = ""
            try:
                fg_hwnd = win32gui.GetForegroundWindow()
                fg_title = win32gui.GetWindowText(fg_hwnd) or "(untitled)"
            except Exception:
                fg_title = "(unreadable)"
            try:
                green_score = match_score_on_screen(
                    _IMG["green_dot_selected"],
                    hwnd=ms_hwnd,
                    use_foreground_fallback=False,
                )
            except Exception:
                green_score = -1.0
            try:
                run_score = match_score_on_screen(
                    _IMG["run_btn"],
                    hwnd=ms_hwnd,
                    use_foreground_fallback=False,
                )
            except Exception:
                run_score = -1.0
            log_fn(
                f"│  TANI: yellow_dot timeout | fg='{fg_title}' | "
                f"dv_open={window_exists(DROPVIEW_WINDOW_NAME)} | "
                f"ms_open={window_exists(MULTISCRIPT_WINDOW)} | "
                f"green_score={green_score:.2f} | run_btn_score={run_score:.2f}"
            )
        raise
    stop_dialog_watchdog()
    return dropview_process_identity(ms_hwnd)
