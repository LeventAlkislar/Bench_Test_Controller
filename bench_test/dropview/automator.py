# bench_test/dropview/automator.py
import os
import sys
import time
import threading
import subprocess

import psutil
import pyautogui
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
    MSG_NO_DEVICE, MSG_POTENTIOSTAT_NOT_FOUND,
)
from bench_test.dropview.vision import (
    find_on_screen, match_score_on_screen,
    wait_for_image, wait_for_image_gone,
    _grab_region, _images_equal,
)
from bench_test.utils.paths import BASE_DIR, ASSETS_DIR, get_last, remember, get_value, remember_value

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
    "manual_conn_com3":             "manual_connection_com3.png",
    "manual_conn_com10":            "manual_connection_com10.png",
    "manual_conn_connect_btn":      "manual_connection_connect_btn.png",
    "manual_conn_dropdown_arrow":   "manual_connection_dropdown_arrow.png",
    "error_no_device_text":         "No device connected.png",
    "error_potentiostat_text":      "Potentiostat not found.png",
}.items()}


_WATCHDOG_POLL_INTERVAL = 0.2
_dialog_watchdog_lock = threading.Lock()
_dialog_watchdog_thread = None
_dialog_watchdog_stop = None
_dialog_watchdog_log_fn = None
_dialog_watchdog_seen = {}
_dialog_watchdog_autodismiss = {WARNING_UNSAVED}


# ── Pencere yardımcıları ───────────────────────────────────────

def find_window(title_keyword, timeout=60, poll_interval=1.0):
    """Başlığında title_keyword geçen ilk görünür pencereyi bulur."""
    start = time.time()
    while time.time() - start < timeout:
        found = []
        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                if title_keyword in win32gui.GetWindowText(hwnd):
                    found.append(hwnd)
        win32gui.EnumWindows(_cb, None)
        if found:
            return found[0]
        time.sleep(poll_interval)
    raise TimeoutError(f"'{title_keyword}' penceresi {timeout}sn icinde bulunamadi.")


def window_exists(title_keyword) -> bool:
    """Başlığında title_keyword geçen görünür bir pencere var mı?"""
    found = []
    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            if title_keyword in win32gui.GetWindowText(hwnd):
                found.append(hwnd)
    win32gui.EnumWindows(_cb, None)
    return len(found) > 0


def wait_for_window_close(title_keyword, timeout=60, poll_interval=0.5):
    """Başlığında title_keyword geçen pencere kapanana kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        if not window_exists(title_keyword):
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
        f"DropView process'i {timeout}sn içinde kapanmadı."
    )

def _focus_window(hwnd, click_title=False, restore_if_iconic=True, sleep_after=SLEEP_AFTER_CLICK):
    """Bring a window to foreground with optional restore, title click and post-focus wait."""
    if restore_if_iconic and win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)
    win32gui.BringWindowToTop(hwnd)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(sleep_after)
    if win32gui.GetForegroundWindow() != hwnd:
        print(f"│  UYARI: SetForegroundWindow etkisiz (hwnd={hwnd}), devam ediliyor.")
    if click_title:
        rect = win32gui.GetWindowRect(hwnd)
        pyautogui.click((rect[0] + rect[2]) // 2, rect[1] + 10)
        time.sleep(SLEEP_AFTER_FOCUS)

def _is_dropview_ui_ready(dv_hwnd, timeout=3.0) -> bool:
    """
    Quick readiness check for DropView UI before trusting early return.
    """
    try:
        _focus_window(dv_hwnd, click_title=True)
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
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None

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
            if log_fn:
                log_fn(f"│    Dialog kapatıldı: '{title}'")
        except Exception as e:
            if log_fn:
                log_fn(f"│    HATA (dialog kapatılırken): {e}")
    return closed


def _watchdog_log(message: str):
    print(message)
    log_fn = _dialog_watchdog_log_fn
    if log_fn:
        log_fn(message)


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
        f"│  Dialog algılandı: '{title}' [{kind}] "
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
        f"│  Dialog kapandı: '{title}' "
        f"(hwnd={hwnd}, pid={pid}, owner='{owner_title}', owner_hwnd={owner_hwnd})"
    )


def _auto_dismiss_dialog(hwnd, title: str):
    try:
        pid = get_window_pid(hwnd)
        owner_title, owner_hwnd = _get_dialog_owner_info(hwnd)
        _focus_window(hwnd, restore_if_iconic=False)
        pyautogui.press("enter")
        _watchdog_log(
            f"│  Dialog otomatik kapatılıyor: '{title}' "
            f"(hwnd={hwnd}, pid={pid}, owner='{owner_title}', owner_hwnd={owner_hwnd})"
        )
    except Exception as e:
        _watchdog_log(f"│  UYARI: Dialog otomatik kapatılamadı ('{title}'): {e}")


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
        _watchdog_log("│  Dialog watchdog başlatıldı.")


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
    """
    Belirtilen başlıktaki uyarı penceresini kısa bir süre boyunca yoklar.
    Diyalog görünürse öne alır, Enter ile kapatır ve kapandığını doğrular.
    Bulamazsa sessizce geçer.
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        if window_exists(window_title):
            try:
                warn_hwnd = find_window(window_title, timeout=0.5, poll_interval=0.1)
                _focus_window(warn_hwnd, restore_if_iconic=False, sleep_after=0.3)
            except Exception:
                warn_hwnd = None

            time.sleep(0.3)
            pyautogui.press("enter")
            try:
                wait_for_window_close(window_title, timeout=2, poll_interval=0.1)
            except TimeoutError:
                time.sleep(SLEEP_AFTER_FOCUS)
            else:
                time.sleep(SLEEP_AFTER_FOCUS)
            return True
        time.sleep(0.2)
    return False


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


def _classify_error_dialog_by_template(log_fn=None, hwnd=None, use_foreground_fallback=False) -> str | None:
    """
    Error metni Win32 child text'ten okunamazsa, ekrandaki metin template'lerine
    bakarak hatayı sınıflandırır.
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

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
        "│  Error template skorları: "
        f"no_device={score_no_device:.2f}, potentiostat_not_found={score_pot_not_found:.2f}"
    )
    _log(
        "│  Error template eşik/delta: "
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


def _wait_for_connection_result(timeout=30, poll_interval=0.5, log_fn=None) -> str:
    """
    Bağlantı girişimi sonucunu bekler ve döner.
    Dönüş değerleri:
        'connected'              - Bağlantı başarılı
        'no_device_connected'    - Cihaz bulunamadı
        'potentiostat_not_found' - Potentiostat bulunamadı
        'unknown_error'          - Tanınmayan hata penceresi
        'timeout'                - Zaman aşımı
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    start = time.time()
    while time.time() - start < timeout:
        if not window_exists(CONNECTING_DIALOG):
            if window_exists(ERROR_WINDOW):
                err_hwnd = find_window(ERROR_WINDOW, timeout=3)
                text = _read_error_window_text(err_hwnd)
                shown_text = text if text else "(metin okunamadı)"
                _log(f"│  DropView Error dialog metni: {shown_text}")
                try:
                    _focus_window(err_hwnd, restore_if_iconic=False)
                    pyautogui.press("enter")
                    time.sleep(SLEEP_AFTER_FOCUS)
                except Exception:
                    pass
                if MSG_NO_DEVICE in text:
                    _log("│  DropView bağlantı hatası sınıflandırıldı: no_device_connected")
                    return "no_device_connected"
                if MSG_POTENTIOSTAT_NOT_FOUND in text:
                    _log("│  DropView bağlantı hatası sınıflandırıldı: potentiostat_not_found")
                    return "potentiostat_not_found"

                template_result = _classify_error_dialog_by_template(
                    log_fn=log_fn,
                    hwnd=err_hwnd,
                    use_foreground_fallback=False,
                )
                if template_result == "no_device_connected":
                    _log("│  DropView bağlantı hatası template ile sınıflandırıldı: no_device_connected")
                    return "no_device_connected"
                if template_result == "potentiostat_not_found":
                    _log("│  DropView bağlantı hatası template ile sınıflandırıldı: potentiostat_not_found")
                    return "potentiostat_not_found"

                _log(f"│  UYARI: Tanımsız DropView Error dialog metni: {shown_text}")
                return "unknown_error"
            if _is_dropview_connected():
                return "connected"
        time.sleep(poll_interval)
    return "timeout"


def _resolve_target_dropsens_ports(preferred_port=None) -> list[str]:
    """
    Denenecek COM portlarını öncelik sırasıyla döner.
    Sıralama:
        1. preferred_port (varsa)
        2. get_value("dropsens_com") ile kaydedilmiş port (varsa)
        3. DROPSENS_COM_PORTS listesindeki kalanlar
    Tekrarlar korunmaz.
    """
    seen = set()
    ports = []
    for p in [preferred_port, get_value("dropsens_com", "")] + DROPSENS_COM_PORTS:
        if p and p not in seen:
            seen.add(p)
            ports.append(p)
    return ports


def _connect_manual(dv_hwnd, target_com: str, log_fn=None) -> str:
    """
    Alt+D → M ile Manual Connection penceresini açar,
    hedef COM portunu seçer ve Connect butonuna basar.
    Dönüş değeri: 'connected' / 'no_device_connected' /
                  'potentiostat_not_found' / 'unknown_error' / 'timeout'
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    if target_com not in DROPSENS_COM_PORTS:
        _log(f"│  UYARI: Desteklenmeyen COM portu: {target_com}")
        return "timeout"

    _focus_window(dv_hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)
    pyautogui.hotkey("alt", "d")
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.press("m")
    time.sleep(SLEEP_AFTER_FOCUS)

    try:
        manual_hwnd = find_window(MANUAL_CONNECTION_WINDOW, timeout=10)
    except TimeoutError:
        _log("│  UYARI: Manual Connection penceresi açılmadı.")
        return "timeout"

    time.sleep(SLEEP_AFTER_FOCUS)

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
        _log(f"│  UYARI: COM port dropdown açılamadı: {e}")
        return "timeout"

    com_key = "manual_conn_com3" if target_com == "COM3" else "manual_conn_com10"
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
        _log(f"│  UYARI: {target_com} görseli bulunamadı: {e}")
        return "timeout"

    try:
        bx, by = find_on_screen(
            _IMG["manual_conn_connect_btn"],
            threshold=THRESHOLD_MID,
            hwnd=manual_hwnd,
            use_foreground_fallback=False,
        )
        pyautogui.click(bx, by)
        time.sleep(SLEEP_AFTER_COMMAND)
    except Exception as e:
        _log(f"│  UYARI: Connect butonu bulunamadı: {e}")
        return "timeout"

    try:
        wait_for_window_close(MANUAL_CONNECTION_WINDOW, timeout=10)
    except TimeoutError:
        _log("│  UYARI: Manual Connection penceresi kapanmadı.")
        return "timeout"

    return _wait_for_connection_result(timeout=TIMEOUT_CONNECT, log_fn=log_fn)

# ── Bağlantı durumu ────────────────────────────────────────────

def wait_until_connected(timeout=30, poll_interval=1.0):
    start = time.time()
    while time.time() - start < timeout:
        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05) if window_exists(DROPVIEW_WINDOW_NAME) else None
        conn_sc = match_score_on_screen(_IMG["connected"], hwnd=dv_hwnd, use_foreground_fallback=False)
        disc_sc = match_score_on_screen(_IMG["disconnected"], hwnd=dv_hwnd, use_foreground_fallback=False)
        if (conn_sc > THRESHOLD_LOW or disc_sc > THRESHOLD_LOW) and conn_sc > disc_sc:
            return
        time.sleep(poll_interval)
    raise TimeoutError("DropSens bağlantısı zaman aşımına uğradı.")


def wait_until_disconnected(timeout=10, poll_interval=1.0):
    start = time.time()
    while time.time() - start < timeout:
        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05) if window_exists(DROPVIEW_WINDOW_NAME) else None
        conn_sc = match_score_on_screen(_IMG["connected"], hwnd=dv_hwnd, use_foreground_fallback=False)
        disc_sc = match_score_on_screen(_IMG["disconnected"], hwnd=dv_hwnd, use_foreground_fallback=False)
        if (conn_sc > THRESHOLD_LOW or disc_sc > THRESHOLD_LOW) and disc_sc > conn_sc:
            return
        time.sleep(poll_interval)


def _is_dropview_connected() -> bool:
    if not window_exists(DROPVIEW_WINDOW_NAME):
        return False
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05)
    conn_sc = match_score_on_screen(_IMG["connected"], hwnd=dv_hwnd, use_foreground_fallback=False)
    disc_sc = match_score_on_screen(_IMG["disconnected"], hwnd=dv_hwnd, use_foreground_fallback=False)
    if conn_sc < THRESHOLD_LOW and disc_sc < THRESHOLD_LOW:
        return False
    return conn_sc > disc_sc


def get_dropview_connection_scores() -> dict:
    dv_open = window_exists(DROPVIEW_WINDOW_NAME)
    if not dv_open:
        return {"dv_open": False, "connected_score": 0.0, "disconnected_score": 0.0}
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=0.2, poll_interval=0.05)
    return {
        "dv_open": True,
        "connected_score":    match_score_on_screen(_IMG["connected"], hwnd=dv_hwnd, use_foreground_fallback=False),
        "disconnected_score": match_score_on_screen(_IMG["disconnected"], hwnd=dv_hwnd, use_foreground_fallback=False),
    }


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
    QMessageBox.information(None, "DropView Bulunamadı",
        "DropView.exe bulunamadı.\nLütfen DropView.exe dosyasını seçin.")
    path, _ = QFileDialog.getOpenFileName(None,
        "DropView.exe Dosyasını Seç", "",
        "Executable (*.exe);;Tüm dosyalar (*.*)")

    if not path:
        raise FileNotFoundError("DropView.exe seçilmedi, işlem iptal edildi.")
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Seçilen dosya bulunamadı: {path}")
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


def step_connect_dropsens(target_com: str = None, log_fn=None):
    """Manuel COM port seçimi ile DropSens'e bağlanır; başarısız olursa Ctrl+C fallback."""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    if _is_dropview_connected():
        return
    if not window_exists(DROPVIEW_WINDOW_NAME):
        raise RuntimeError("DropView penceresi açık değil.")

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    _focus_window(dv_hwnd)

    manual_attempts = []
    result = "timeout"
    for com in _resolve_target_dropsens_ports(preferred_port=target_com):
        _log(f"│  Manuel bağlantı deneniyor: {com}...")
        result = _connect_manual(dv_hwnd, com, log_fn=log_fn)
        manual_attempts.append((com, result))
        if result == "connected":
            _log(f"│  DropSens bağlandı (manuel: {com}).")
            remember_value("dropsens_com", com)
            return
        _log(f"│  Manuel bağlantı başarısız ({com}: {result}).")

    attempts_str = ", ".join(f"{p}={s}" for p, s in manual_attempts)
    _log(f"│  Manuel bağlantılar başarısız ({attempts_str}), Ctrl+C ile tekrar deneniyor...")
    _focus_window(dv_hwnd)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)

    fallback_result = _wait_for_connection_result(timeout=TIMEOUT_CONNECT, log_fn=log_fn)
    if fallback_result != "connected":
        raise RuntimeError(
            f"DropSens bağlantısı kurulamadı (manuel: {result}, "
            f"Ctrl+C: {fallback_result}). Cihazın bağlı ve "
            f"DropView'in hazır durumda olduğundan emin olun."
        )
    _log("│  DropSens bağlandı (Ctrl+C fallback).")

    time.sleep(SLEEP_AFTER_FOCUS)
    if not _is_dropview_connected():
        raise RuntimeError(
            "DropSens bağlantı sinyali alındı ancak son doğrulama başarısız."
        )


def step_disconnect_dropsens():
    """Sadece Ctrl+D ile DropSens bağlantısını keser."""
    if not window_exists(DROPVIEW_WINDOW_NAME):
        return
    if not _is_dropview_connected():
        return

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    _focus_window(dv_hwnd)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('D'), 0, 0, 0)
    win32api.keybd_event(ord('D'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)
    wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW)

def step_stop_measure(log_fn=None):
    """Stop butonuna basar, ölçümün durmasını bekler, Exit ile Multiscript Editor'ü kapatır."""
    ensure_dialog_watchdog(log_fn=log_fn)
    if not window_exists(MULTISCRIPT_WINDOW):
        return

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=5)
    _focus_window(ms_hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)

    sx, sy = find_on_screen(
        _IMG["stop_btn"],
        threshold=THRESHOLD_HIGH,
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )
    pyautogui.click(sx, sy)
    time.sleep(SLEEP_AFTER_COMMAND)

    wait_for_image_gone(
        _IMG["yellow_dot_selected"],
        timeout=TIMEOUT_STOP_MEASURE,
        poll_interval=POLL_INTERVAL_SLOW,
        threshold=THRESHOLD_HIGH,
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )

    wait_for_image(
        _IMG["green_dot_selected"],
        timeout=15,
        poll_interval=POLL_INTERVAL_NORMAL,
        threshold=THRESHOLD_HIGH,
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )

    ex, ey = find_on_screen(
        _IMG["exit_btn"],
        threshold=THRESHOLD_HIGH,
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )
    pyautogui.click(ex, ey)
    time.sleep(SLEEP_AFTER_FOCUS)

    wait_for_window_close(MULTISCRIPT_WINDOW, timeout=TIMEOUT_CLOSE_WINDOW)
    if log_fn:
        log_fn("│  Multiscript Editor kapandı.")


def _force_close_multiscript(log_fn=None) -> bool:
    """
    Multiscript Editor'u buton-gorsel bagimliligi olmadan kapatmayi dener.
    Basariliysa True, kapanmadiysa False doner.
    """
    def _log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

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
        _log("│  Multiscript Editor WM_CLOSE ile kapandı.")
        return True
    except Exception:
        pass

    # 2) Alt+F4
    try:
        _focus_window(ms_hwnd)
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_F4, 0, 0, 0)
        win32api.keybd_event(win32con.VK_F4, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(SLEEP_AFTER_COMMAND)
        wait_for_window_close(MULTISCRIPT_WINDOW, timeout=3, poll_interval=0.2)
        _log("│  Multiscript Editor Alt+F4 ile kapandı.")
        return True
    except Exception:
        pass

    _log("│  UYARI: Multiscript Editor hard-fallback ile kapatılamadı.")
    return False

def step_exit_dropview(config: dict, log_fn=None):
    """Ctrl+D ile bağlantıyı kes + Alt+F4 ile DropView'i kapat."""
    def _log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    try:
        # Process yoksa pencere de yoktur, erken çık
        if _get_dropview_pid() is None and not window_exists(DROPVIEW_WINDOW_NAME):
            _log("│  DropView zaten kapalı.")
            return

        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)

        # Multiscript Editor hâlâ açıksa Alt+F4 onu kapatır, DropView'i değil
        if window_exists(MULTISCRIPT_WINDOW):
            _log("│  Multiscript Editor hâlâ açık, önce kapatılıyor...")
            try:
                step_stop_measure(log_fn=_log)
            except Exception as e:
                _log(f"│  UYARI: Normal stop ile Multiscript kapanamadı: {e}")
                if not _force_close_multiscript(log_fn=_log):
                    _log("│  UYARI: Multiscript kapanamadı, DropView kapanışı yine de denenecek.")

        # Owned dialog'ları temizle (içeriğe bağımsız)
        closed = close_owned_dialogs(dv_hwnd, log_fn=_log)
        if closed:
            _log(f"│  {closed} dialog kapatıldı.")

        if _is_dropview_connected():
            _focus_window(dv_hwnd)
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            win32api.keybd_event(ord('D'), 0, 0, 0)
            win32api.keybd_event(ord('D'), 0, win32con.KEYEVENTF_KEYUP, 0)
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(SLEEP_AFTER_COMMAND)
            close_owned_dialogs(dv_hwnd, log_fn=_log)
            wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW)
            _log("│  DropSens bağlantısı kesildi.")
        else:
            _log("│  DropSens zaten bağlı değil.")

        # Alt+F4
        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=5)
        _focus_window(dv_hwnd)
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_F4, 0, 0, 0)
        win32api.keybd_event(win32con.VK_F4, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(SLEEP_AFTER_COMMAND)

        # Alt+F4 sonrası çıkabilecek dialog'ları kapat
        close_owned_dialogs(dv_hwnd, log_fn=_log)

        # Pencere kapandı mı doğrula
        window_closed = False
        try:
            wait_for_window_close(DROPVIEW_WINDOW_NAME, timeout=10)
            _log("│  DropView penceresi kapandı.")
            window_closed = True
        except TimeoutError:
            _log("│  UYARI: DropView penceresi kapanmadı.")

        # Process kapandı mı doğrula; pencere kapanmadıysa doğrudan kill'e geç
        process_exited = False
        if window_closed:
            try:
                _wait_for_dropview_process_exit(timeout=10)
                _log("│  DropView process'i kapandı.")
                process_exited = True
            except TimeoutError:
                _log("│  UYARI: Process kapanmadı.")

        if not process_exited:
            # Kill öncesi koşulsuz Ctrl+D — COM portu düzgün serbest bırakılsın
            _log("│  Kill öncesi Ctrl+D gönderiliyor...")
            try:
                dv_hwnd_kill = find_window(DROPVIEW_WINDOW_NAME, timeout=3)
                _focus_window(dv_hwnd_kill)
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(ord('D'), 0, 0, 0)
                win32api.keybd_event(ord('D'), 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(SLEEP_AFTER_COMMAND + 0.5)
                _log("│  Ctrl+D gönderildi.")
            except Exception as e:
                _log(f"│  UYARI: Kill öncesi Ctrl+D başarısız: {e}")

            pid = get_window_pid(dv_hwnd) or _get_dropview_pid()
            if pid:
                _log(f"│  DropView zorla kapatılıyor (PID={pid})...")
                try:
                    psutil.Process(pid).kill()
                    _wait_for_dropview_process_exit(timeout=5)
                    _log("│  DropView zorla kapatıldı.")
                except Exception as kill_err:
                    raise RuntimeError(
                        f"DropView kapatılamadı: {kill_err}"
                    ) from kill_err

                # Kill sonrası COM port / driver serbest bırakma payı
                _log("│  COM port serbest bırakılması bekleniyor...")
                time.sleep(3.0)

                # Process gerçekten gitti mi son doğrulama
                if _get_dropview_pid() is not None:
                    raise RuntimeError(
                        "DropView kill sonrası process hâlâ çalışıyor."
                    )
                _log("│  DropView process doğrulandı: kapalı.")
            else:
                if not window_closed:
                    raise RuntimeError(
                        "DropView penceresi kapanmadı ve PID bulunamadı."
                    )
                _log("│  DropView process zaten sonlanmış.")

        _log("│  DropView kapatıldı.")
        _log("│  COM port serbest bırakılması bekleniyor...")
        time.sleep(2.0)
    finally:
        stop_dialog_watchdog()

def step_start_dropview(config: dict, log_fn=None):
    def _log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    ensure_dialog_watchdog(log_fn=log_fn)

    # Pencere var mı kontrol et; varsa sağlıklı mı diye bak
    if window_exists(DROPVIEW_WINDOW_NAME):
        dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=5)
        is_enabled = win32gui.IsWindowEnabled(dv_hwnd)
        has_modal  = len(find_owned_dialogs(dv_hwnd)) > 0
        if not is_enabled or has_modal:
            _log("│  DropView bozuk/bloklu durumda tespit edildi, temizleniyor...")
            pid = get_window_pid(dv_hwnd)
            if pid:
                try:
                    psutil.Process(pid).kill()
                    _wait_for_dropview_process_exit(timeout=5)
                    _log("│  Eski DropView instance'ı kapatıldı.")
                except Exception as e:
                    _log(f"│  UYARI: Eski instance kapatılamadı: {e}")
        elif _is_dropview_connected():
            hwnd_pid = get_window_pid(dv_hwnd)
            if not hwnd_pid:
                _log("│  UYARI: Bağlı görünüyor ama pencere PID'si alınamadı, yeniden başlatılacak.")
                try:
                    win32gui.PostMessage(dv_hwnd, win32con.WM_CLOSE, 0, 0)
                    wait_for_window_close(DROPVIEW_WINDOW_NAME, timeout=3, poll_interval=0.2)
                    _log("│  Şüpheli DropView penceresi kapatıldı.")
                except Exception:
                    _log("│  UYARI: Şüpheli DropView penceresi kapatılamadı, yeniden başlatma denenecek.")
            else:
                try:
                    proc = psutil.Process(hwnd_pid)
                    if proc.is_running() and not _is_dropview_ui_ready(dv_hwnd, timeout=3.0):
                        _log(
                            f"â”‚  UYARI: DropView baÄŸlÄ± gÃ¶rÃ¼nÃ¼yor ama UI hazÄ±r deÄŸil "
                            f"(PID={hwnd_pid}), yeniden baÅŸlatÄ±lacak."
                        )
                        try:
                            psutil.Process(hwnd_pid).kill()
                            _wait_for_dropview_process_exit(timeout=5)
                            _log("â”‚  HazÄ±r olmayan DropView instance'Ä± kapatÄ±ldÄ±.")
                            proc = psutil.Process(hwnd_pid)
                        except psutil.NoSuchProcess:
                            pass
                        except Exception as e:
                            _log(f"â”‚  UYARI: HazÄ±r olmayan instance kapatÄ±lamadÄ±: {e}")
                    if not proc.is_running():
                        _log(f"│  UYARI: Bağlı görünüyor ama process çalışmıyor (PID={hwnd_pid}), yeniden başlatılacak.")
                    else:
                        _log(f"│  DropView sağlıklı ve bağlı (PID={hwnd_pid}), devam ediliyor.")
                        time.sleep(0.4)
                        if not _is_dropview_ui_ready(dv_hwnd, timeout=2.0) or not _is_dropview_connected():
                            _log(
                                f"│  UYARI: Erken sağlık kontrolü tutarsız (PID={hwnd_pid}), "
                                "yeniden başlatılacak."
                            )
                        else:
                            return
                except Exception as e:
                    _log(f"│  UYARI: DropView process sağlığı doğrulanamadı (PID={hwnd_pid}): {e}")
    else:
        # Pencere yok ama zombie process olabilir
        pid = _get_dropview_pid()
        if pid:
            try:
                psutil.Process(pid).kill()
                _wait_for_dropview_process_exit(timeout=5)
                _log("│  Zombie DropView process'i temizlendi.")
            except Exception as e:
                _log(f"│  UYARI: Zombie process kapatılamadı: {e}")

    if not window_exists(DROPVIEW_WINDOW_NAME):
        subprocess.Popen([_get_dropview_exe()])
        find_window(DROPVIEW_WINDOW_NAME, timeout=TIMEOUT_WINDOW_OPEN)
        time.sleep(SLEEP_AFTER_LAUNCH)

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    _focus_window(dv_hwnd)

    manual_attempts = []
    result = "timeout"
    for target_com in _resolve_target_dropsens_ports():
        _log(f"│  Manuel bağlantı deneniyor: {target_com}...")
        result = _connect_manual(dv_hwnd, target_com, log_fn=log_fn)
        manual_attempts.append((target_com, result))
        if result == "connected":
            _log(f"│  DropSens bağlandı (manuel: {target_com}).")
            remember_value("dropsens_com", target_com)
            break
        _log(f"│  Manuel bağlantı başarısız ({target_com}: {result}).")

    if result == "connected":
        pass
    elif result in ("no_device_connected", "potentiostat_not_found", "unknown_error", "timeout"):
        attempts_str = ", ".join(f"{port}={status}" for port, status in manual_attempts)
        _log(f"│  Manuel bağlantılar başarısız ({attempts_str}), Ctrl+C ile tekrar deneniyor...")
        _focus_window(dv_hwnd)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord('C'), 0, 0, 0)
        win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(SLEEP_AFTER_COMMAND)

        fallback_result = _wait_for_connection_result(timeout=TIMEOUT_CONNECT, log_fn=log_fn)
        if fallback_result != "connected":
            raise RuntimeError(
                f"DropSens bağlantısı kurulamadı (manuel: {attempts_str}, "
                f"Ctrl+C: {fallback_result}). Cihazın bağlı ve "
                f"DropView'in hazır durumda olduğundan emin olun."
            )
        _log("│  DropSens bağlandı (Ctrl+C fallback).")
    else:
        raise RuntimeError(
            "DropSens bağlantısı zaman aşımına uğradı. "
            "Cihazın bağlı olduğundan emin olun."
        )

    # Final doğrulama: render/race gecikmelerine karşı kısa polling + stabilite kontrolü.
    time.sleep(SLEEP_AFTER_FOCUS + 0.5)
    deadline = time.time() + 3.0
    stable_hits = 0
    while time.time() < deadline:
        if window_exists(CONNECTING_DIALOG):
            stable_hits = 0
            time.sleep(0.2)
            continue
        if _is_dropview_connected():
            stable_hits += 1
            if stable_hits >= 2:
                break
        else:
            stable_hits = 0
        time.sleep(0.2)

    if stable_hits < 2:
        raise RuntimeError(
            "DropSens bağlantı sinyali alındı ancak son doğrulama başarısız. "
            "Lütfen tekrar deneyin."
        )

def step_start_measure(config: dict, log_fn=None):
    ensure_dialog_watchdog(log_fn=log_fn)
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    _focus_window(dv_hwnd, click_title=True)

    # Alt+S S öncesi: DropView enabled ve modal yok mu doğrula
    if not win32gui.IsWindowEnabled(dv_hwnd):
        raise RuntimeError(
            "Start Measure hatası: DropView penceresi disabled durumda "
            "(modal dialog bloklıyor olabilir)."
        )
    owned = find_owned_dialogs(dv_hwnd)
    if owned:
        titles = [win32gui.GetWindowText(h) for h in owned]
        raise RuntimeError(
            f"Start Measure hatası: DropView'e ait açık dialog var: {titles}"
        )

    wait_for_image(
        _IMG["scripts_menu"],
        timeout=TIMEOUT_WINDOW_OPEN,
        poll_interval=POLL_INTERVAL_SLOW,
        hwnd=dv_hwnd,
        use_foreground_fallback=False,
    )
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.hotkey("alt", "s")
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.press("s")
    time.sleep(1.2)

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=15)
    if log_fn:
        log_fn(f"│  Multiscript Editor açıldı (hwnd={ms_hwnd}).")
    time.sleep(SLEEP_AFTER_FOCUS)

    lbl_x, lbl_y = find_on_screen(
        _IMG["loaded_scripts"],
        threshold=THRESHOLD_HIGH,
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )
    listbox_x = lbl_x
    listbox_y = lbl_y + 100

    lx, ly = find_on_screen(
        _IMG["load_btn"],
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )
    pyautogui.click(lx, ly)
    time.sleep(SLEEP_AFTER_FOCUS)

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
    pyautogui.click(fn_x, fn_y)
    time.sleep(0.4)

    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(SLEEP_AFTER_CLICK)
    pyautogui.press("enter")
    time.sleep(0.8)

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=10)
    _focus_window(ms_hwnd, restore_if_iconic=False, sleep_after=SLEEP_AFTER_FOCUS)
    before_count = _count_and_clear_scripts(ms_hwnd, listbox_x, listbox_y)
    _delete_scripts_above(ms_hwnd, listbox_x, listbox_y, before_count)

    rx, ry = find_on_screen(
        _IMG["run_btn"],
        threshold=THRESHOLD_MID,
        hwnd=ms_hwnd,
        use_foreground_fallback=False,
    )
    pyautogui.click(rx, ry)
    time.sleep(SLEEP_AFTER_COMMAND)

    # "Some curves have not been saved" diyalogu Run sonrası gecikmeli açılabiliyor.
    _dismiss_warning_if_present(WARNING_UNSAVED, wait=3.0)

    try:
        wait_for_image(
            _IMG["yellow_dot_selected"],
            timeout=30,
            poll_interval=POLL_INTERVAL_SLOW,
            threshold=THRESHOLD_HIGH,
            hwnd=ms_hwnd,
            use_foreground_fallback=False,
        )
    except Exception:
        if log_fn:
            fg_title = ""
            try:
                fg_hwnd = win32gui.GetForegroundWindow()
                fg_title = win32gui.GetWindowText(fg_hwnd) or "(untitled)"
            except Exception:
                fg_title = "(okunamadı)"
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
