# bench_test/dropview/automator.py
import os
import sys
import time
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
}.items()}


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
            win32gui.SetForegroundWindow(dlg_hwnd)
            time.sleep(SLEEP_AFTER_CLICK)
            pyautogui.press("enter")
            time.sleep(SLEEP_AFTER_FOCUS)
            closed += 1
            if log_fn:
                log_fn(f"│    Dialog kapatıldı: '{title}'")
        except Exception as e:
            if log_fn:
                log_fn(f"│    HATA (dialog kapatılırken): {e}")
    return closed


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


def _wait_for_connection_result(timeout=30, poll_interval=0.5) -> str:
    """
    Bağlantı girişimi sonucunu bekler ve döner.
    Dönüş değerleri:
        'connected'              - Bağlantı başarılı
        'no_device_connected'    - Cihaz bulunamadı
        'potentiostat_not_found' - Potentiostat bulunamadı
        'unknown_error'          - Tanınmayan hata penceresi
        'timeout'                - Zaman aşımı
    """
    start = time.time()
    while time.time() - start < timeout:
        if not window_exists(CONNECTING_DIALOG):
            if window_exists(ERROR_WINDOW):
                err_hwnd = find_window(ERROR_WINDOW, timeout=3)
                text = _read_error_window_text(err_hwnd)
                try:
                    win32gui.SetForegroundWindow(err_hwnd)
                    time.sleep(SLEEP_AFTER_CLICK)
                    pyautogui.press("enter")
                    time.sleep(SLEEP_AFTER_FOCUS)
                except Exception:
                    pass
                if MSG_NO_DEVICE in text:
                    return "no_device_connected"
                if MSG_POTENTIOSTAT_NOT_FOUND in text:
                    return "potentiostat_not_found"
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

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.hotkey("alt", "d")
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.press("m")
    time.sleep(SLEEP_AFTER_FOCUS)

    try:
        find_window(MANUAL_CONNECTION_WINDOW, timeout=10)
    except TimeoutError:
        _log("│  UYARI: Manual Connection penceresi açılmadı.")
        return "timeout"

    time.sleep(SLEEP_AFTER_FOCUS)

    try:
        dx, dy = find_on_screen(_IMG["manual_conn_dropdown_arrow"], threshold=THRESHOLD_MID)
        pyautogui.click(dx, dy)
        time.sleep(SLEEP_AFTER_CLICK)
    except Exception as e:
        _log(f"│  UYARI: COM port dropdown açılamadı: {e}")
        return "timeout"

    com_key = "manual_conn_com3" if target_com == "COM3" else "manual_conn_com10"
    try:
        cx, cy = find_on_screen(_IMG[com_key], threshold=THRESHOLD_MID)
        pyautogui.click(cx, cy)
        time.sleep(SLEEP_AFTER_CLICK)
    except Exception as e:
        _log(f"│  UYARI: {target_com} görseli bulunamadı: {e}")
        return "timeout"

    try:
        bx, by = find_on_screen(_IMG["manual_conn_connect_btn"], threshold=THRESHOLD_MID)
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

    return _wait_for_connection_result(timeout=TIMEOUT_CONNECT)

# ── Bağlantı durumu ────────────────────────────────────────────

def wait_until_connected(timeout=30, poll_interval=1.0):
    start = time.time()
    while time.time() - start < timeout:
        conn_sc = match_score_on_screen(_IMG["connected"])
        disc_sc = match_score_on_screen(_IMG["disconnected"])
        if (conn_sc > THRESHOLD_LOW or disc_sc > THRESHOLD_LOW) and conn_sc > disc_sc:
            return
        time.sleep(poll_interval)
    raise TimeoutError("DropSens bağlantısı zaman aşımına uğradı.")


def wait_until_disconnected(timeout=10, poll_interval=1.0):
    start = time.time()
    while time.time() - start < timeout:
        conn_sc = match_score_on_screen(_IMG["connected"])
        disc_sc = match_score_on_screen(_IMG["disconnected"])
        if (conn_sc > THRESHOLD_LOW or disc_sc > THRESHOLD_LOW) and disc_sc > conn_sc:
            return
        time.sleep(poll_interval)


def _is_dropview_connected() -> bool:
    if not window_exists(DROPVIEW_WINDOW_NAME):
        return False
    conn_sc = match_score_on_screen(_IMG["connected"])
    disc_sc = match_score_on_screen(_IMG["disconnected"])
    if conn_sc < THRESHOLD_LOW and disc_sc < THRESHOLD_LOW:
        return False
    return conn_sc > disc_sc


def get_dropview_connection_scores() -> dict:
    dv_open = window_exists(DROPVIEW_WINDOW_NAME)
    if not dv_open:
        return {"dv_open": False, "connected_score": 0.0, "disconnected_score": 0.0}
    return {
        "dv_open": True,
        "connected_score":    match_score_on_screen(_IMG["connected"]),
        "disconnected_score": match_score_on_screen(_IMG["disconnected"]),
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
        dx, dy = find_on_screen(_IMG["delete_btn"], threshold=THRESHOLD_MID)
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
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)

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

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)

    fallback_result = _wait_for_connection_result(timeout=TIMEOUT_CONNECT)
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
    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('D'), 0, 0, 0)
    win32api.keybd_event(ord('D'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)
    wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW)

def step_stop_measure():
    """Stop butonuna basar, ölçümün durmasını bekler, Exit ile Multiscript Editor'ü kapatır."""
    if not window_exists(MULTISCRIPT_WINDOW):
        return

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=5)
    win32gui.SetForegroundWindow(ms_hwnd)
    time.sleep(SLEEP_AFTER_FOCUS)

    sx, sy = find_on_screen(_IMG["stop_btn"], threshold=THRESHOLD_HIGH)
    pyautogui.click(sx, sy)
    time.sleep(SLEEP_AFTER_COMMAND)

    wait_for_image_gone(_IMG["yellow_dot_selected"], timeout=TIMEOUT_STOP_MEASURE,
                        poll_interval=POLL_INTERVAL_SLOW, threshold=THRESHOLD_HIGH)

    wait_for_image(_IMG["green_dot_selected"], timeout=15,
                   poll_interval=POLL_INTERVAL_NORMAL, threshold=THRESHOLD_HIGH)

    ex, ey = find_on_screen(_IMG["exit_btn"], threshold=THRESHOLD_HIGH)
    pyautogui.click(ex, ey)
    time.sleep(SLEEP_AFTER_FOCUS)

    wait_for_window_close(MULTISCRIPT_WINDOW, timeout=TIMEOUT_CLOSE_WINDOW)

def step_exit_dropview(config: dict, log_fn=None):
    """Ctrl+D ile bağlantıyı kes + Alt+F4 ile DropView'i kapat."""
    def _log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    # Process yoksa pencere de yoktur, erken çık
    if _get_dropview_pid() is None and not window_exists(DROPVIEW_WINDOW_NAME):
        _log("│  DropView zaten kapalı.")
        return

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)

    # Multiscript Editor hâlâ açıksa Alt+F4 onu kapatır, DropView'i değil
    if window_exists(MULTISCRIPT_WINDOW):
        _log("│  Multiscript Editor hâlâ açık, önce kapatılıyor...")
        try:
            step_stop_measure()
        except Exception as e:
            raise RuntimeError(
                f"DropView kapatılamadı: Multiscript Editor önce kapatılamadı: {e}"
            ) from e

    # Owned dialog'ları temizle (içeriğe bağımsız)
    closed = close_owned_dialogs(dv_hwnd, log_fn=_log)
    if closed:
        _log(f"│  {closed} dialog kapatıldı.")

    if _is_dropview_connected():
        win32gui.SetForegroundWindow(dv_hwnd)
        time.sleep(SLEEP_AFTER_CLICK)
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
    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_F4, 0, 0, 0)
    win32api.keybd_event(win32con.VK_F4, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)

    # Alt+F4 sonrası çıkabilecek dialog'ları kapat
    close_owned_dialogs(dv_hwnd, log_fn=_log)

    # Pencere kapandı mı doğrula
    try:
        wait_for_window_close(DROPVIEW_WINDOW_NAME, timeout=10)
        _log("│  DropView penceresi kapandı.")
    except TimeoutError:
        _log("│  UYARI: DropView penceresi kapanmadı, zorla kapatılıyor...")

    # Process kapandı mı doğrula
    try:
        _wait_for_dropview_process_exit(timeout=10)
        _log("│  DropView kapatıldı.")
    except TimeoutError:
        _log("│  UYARI: Process kapanmadı, zorla öldürülüyor...")
        pid = _get_dropview_pid()
        if pid:
            try:
                psutil.Process(pid).kill()
                _wait_for_dropview_process_exit(timeout=5)
                _log("│  DropView zorla kapatıldı.")
            except Exception as kill_err:
                raise RuntimeError(
                    f"DropView kapatılamadı: {kill_err}"
                ) from kill_err
        else:
            raise RuntimeError(
                "DropView process'i kapanmadı ve PID bulunamadı."
            )

def step_start_dropview(config: dict, log_fn=None):
    def _log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

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
            return  # Sağlıklı ve bağlı, devam et
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
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)

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
        win32gui.SetForegroundWindow(dv_hwnd)
        time.sleep(SLEEP_AFTER_CLICK)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord('C'), 0, 0, 0)
        win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(SLEEP_AFTER_COMMAND)

        fallback_result = _wait_for_connection_result(timeout=TIMEOUT_CONNECT)
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

    time.sleep(SLEEP_AFTER_FOCUS)
    if not _is_dropview_connected():
        raise RuntimeError(
            "DropSens bağlantı sinyali alındı ancak son doğrulama başarısız. "
            "Lütfen tekrar deneyin."
        )

def step_start_measure(config: dict):
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    rect = win32gui.GetWindowRect(dv_hwnd)
    title_x = (rect[0] + rect[2]) // 2
    title_y = rect[1] + 10
    pyautogui.click(title_x, title_y)
    time.sleep(SLEEP_AFTER_FOCUS)

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

    wait_for_image(_IMG["scripts_menu"], timeout=TIMEOUT_WINDOW_OPEN, poll_interval=POLL_INTERVAL_SLOW)
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.hotkey("alt", "s")
    time.sleep(SLEEP_AFTER_FOCUS)
    pyautogui.press("s")
    time.sleep(1.2)

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=15)
    time.sleep(SLEEP_AFTER_FOCUS)

    lbl_x, lbl_y = find_on_screen(_IMG["loaded_scripts"], threshold=THRESHOLD_HIGH)
    listbox_x = lbl_x
    listbox_y = lbl_y + 100

    lx, ly = find_on_screen(_IMG["load_btn"])
    pyautogui.click(lx, ly)
    time.sleep(SLEEP_AFTER_FOCUS)

    script_path = config["script_path"]
    open_hwnd = find_window("Open", timeout=10)
    time.sleep(SLEEP_AFTER_FOCUS)

    win32gui.SetForegroundWindow(open_hwnd)
    time.sleep(SLEEP_AFTER_FOCUS)

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
    win32gui.SetForegroundWindow(ms_hwnd)
    time.sleep(SLEEP_AFTER_FOCUS)
    before_count = _count_and_clear_scripts(ms_hwnd, listbox_x, listbox_y)
    _delete_scripts_above(ms_hwnd, listbox_x, listbox_y, before_count)

    rx, ry = find_on_screen(_IMG["run_btn"], threshold=THRESHOLD_MID)
    pyautogui.click(rx, ry)
    time.sleep(SLEEP_AFTER_COMMAND)

    if window_exists(WARNING_UNSAVED):
        pyautogui.press("enter")
        time.sleep(SLEEP_AFTER_FOCUS)

    wait_for_image(_IMG["yellow_dot_selected"], timeout=30,
                   poll_interval=POLL_INTERVAL_SLOW, threshold=THRESHOLD_HIGH)
