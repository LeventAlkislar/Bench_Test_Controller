# bench_test/dropview/automator.py
import os
import sys
import time
import subprocess

import pyautogui
import win32api
import win32clipboard
import win32con
import win32gui

from bench_test.config import (
    DROPVIEW_WINDOW_NAME, MULTISCRIPT_WINDOW, CONNECTING_DIALOG,
    WARNING_UNSAVED, THRESHOLD_HIGH, THRESHOLD_MID, THRESHOLD_LOW,
    SLEEP_AFTER_CLICK, SLEEP_AFTER_FOCUS, SLEEP_AFTER_COMMAND, SLEEP_AFTER_LAUNCH,
    TIMEOUT_WINDOW_OPEN, TIMEOUT_CONNECT, TIMEOUT_STOP_MEASURE, TIMEOUT_CLOSE_WINDOW,
    POLL_INTERVAL_NORMAL, POLL_INTERVAL_SLOW, DROPVIEW_EXE_CANDIDATES,
    PYAUTOGUI_FAILSAFE, PYAUTOGUI_PAUSE,
)
from bench_test.dropview.vision import (
    find_on_screen, match_score_on_screen,
    wait_for_image, wait_for_image_gone,
    _grab_region, _images_equal,
)
from bench_test.utils.paths import BASE_DIR, ASSETS_DIR, get_last, remember

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
    "connected":           "connected_status.png",
    "disconnected":        "disconnected_status.png",
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
        print("│    Silinecek script yok.")
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
        print("│  DropView zaten açık.")
        return
    print("│  DropView başlatılıyor ...")
    subprocess.Popen([_get_dropview_exe()])
    find_window(DROPVIEW_WINDOW_NAME, timeout=TIMEOUT_WINDOW_OPEN)
    time.sleep(SLEEP_AFTER_LAUNCH)
    print("│  DropView penceresi açıldı.")


def step_connect_dropsens():
    """Sadece Ctrl+C ile DropSens'e bağlanır."""
    if _is_dropview_connected():
        print("│  DropSens zaten bağlı.")
        return
    if not window_exists(DROPVIEW_WINDOW_NAME):
        raise RuntimeError("DropView penceresi açık değil.")

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)

    if window_exists(CONNECTING_DIALOG):
        print("│    Bağlanıyor, dialog bekleniyor ...")
        wait_for_window_close(CONNECTING_DIALOG, timeout=TIMEOUT_WINDOW_OPEN)

    try:
        wait_until_connected(timeout=TIMEOUT_CONNECT)
    except TimeoutError:
        raise RuntimeError(
            "DropSens bağlantısı kurulamadı: Cihazın fiziksel olarak bağlı "
            "olduğundan ve DropView'in hazır durumda olduğundan emin olun."
        )

    time.sleep(SLEEP_AFTER_FOCUS)
    if not _is_dropview_connected():
        raise RuntimeError(
            "DropSens bağlantı sinyali alındı ancak son doğrulama başarısız."
        )
    print("│  DropSens bağlandı.")


def step_disconnect_dropsens():
    """Sadece Ctrl+D ile DropSens bağlantısını keser."""
    if not window_exists(DROPVIEW_WINDOW_NAME):
        return
    if not _is_dropview_connected():
        print("│  DropSens zaten bağlı değil.")
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
    print("│  DropSens bağlantısı kesildi.")

def step_stop_measure():
    """Stop butonuna basar, ölçümün durmasını bekler, Exit ile Multiscript Editor'ü kapatır."""
    if not window_exists(MULTISCRIPT_WINDOW):
        print("│  Multiscript Editor açık değil — Stop Measure atlandı.")
        return

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=5)
    win32gui.SetForegroundWindow(ms_hwnd)
    time.sleep(SLEEP_AFTER_FOCUS)

    sx, sy = find_on_screen(_IMG["stop_btn"], threshold=THRESHOLD_HIGH)
    pyautogui.click(sx, sy)
    time.sleep(SLEEP_AFTER_COMMAND)

    print("│  Ölçümün durması bekleniyor (sarı nokta kaybolacak) ...")
    wait_for_image_gone(_IMG["yellow_dot_selected"], timeout=TIMEOUT_STOP_MEASURE,
                        poll_interval=POLL_INTERVAL_SLOW, threshold=THRESHOLD_HIGH)

    print("│  Yeşil nokta bekleniyor ...")
    wait_for_image(_IMG["green_dot_selected"], timeout=15,
                   poll_interval=POLL_INTERVAL_NORMAL, threshold=THRESHOLD_HIGH)

    ex, ey = find_on_screen(_IMG["exit_btn"], threshold=THRESHOLD_HIGH)
    pyautogui.click(ex, ey)
    time.sleep(SLEEP_AFTER_FOCUS)

    wait_for_window_close(MULTISCRIPT_WINDOW, timeout=TIMEOUT_CLOSE_WINDOW)
    print("│  Multiscript Editor kapatıldı.")

def step_exit_dropview(config: dict, log_fn=None):
    """Ctrl+D ile bağlantıyı kes + Alt+F4 ile DropView'i kapat."""
    def _log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    def _close_warning_if_exists():
        if not window_exists(WARNING_UNSAVED):
            return False
        hwnd = None
        def _find(h, _):
            nonlocal hwnd
            if win32gui.IsWindowVisible(h) and WARNING_UNSAVED in win32gui.GetWindowText(h):
                hwnd = h
        win32gui.EnumWindows(_find, None)
        if hwnd:
            try:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(SLEEP_AFTER_CLICK)
                pyautogui.press("enter")
                time.sleep(SLEEP_AFTER_FOCUS)
                return True
            except Exception as e:
                _log(f"│    HATA (uyarı kapatılırken): {e}")
                return False
        return False

    if not window_exists(DROPVIEW_WINDOW_NAME):
        _log("│  DropView zaten kapalı.")
        return

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)

    if _is_dropview_connected():
        win32gui.SetForegroundWindow(dv_hwnd)
        time.sleep(SLEEP_AFTER_CLICK)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord('D'), 0, 0, 0)
        win32api.keybd_event(ord('D'), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(SLEEP_AFTER_COMMAND)
        _close_warning_if_exists()
        wait_until_disconnected(timeout=TIMEOUT_CLOSE_WINDOW)
        _log("│  DropSens bağlantısı kesildi.")
    else:
        _log("│  DropSens zaten bağlı değil.")

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=5)
    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_F4, 0, 0, 0)
    win32api.keybd_event(win32con.VK_F4, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)
    _close_warning_if_exists()
    _log("│  DropView kapatıldı.")

def step_start_dropview(config: dict):
    if window_exists(DROPVIEW_WINDOW_NAME) and _is_dropview_connected():
        print("│  [Start DropView] DropView zaten açık ve Connected — atlandı.")
        return
    if not window_exists(DROPVIEW_WINDOW_NAME):
        print("│  DropView başlatılıyor ...")
        subprocess.Popen([_get_dropview_exe()])
        find_window(DROPVIEW_WINDOW_NAME, timeout=TIMEOUT_WINDOW_OPEN)
        time.sleep(SLEEP_AFTER_LAUNCH)
        print("│  DropView penceresi açıldı.")
    else:
        print("│  DropView zaten açık, bağlantı kurulmaya çalışılıyor ...")

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(SLEEP_AFTER_COMMAND)

    if window_exists(CONNECTING_DIALOG):
        print("│    Bağlanıyor, dialog bekleniyor ...")
        wait_for_window_close(CONNECTING_DIALOG, timeout=TIMEOUT_WINDOW_OPEN)
    else:
        print("│    Bağlantı popup'ı zaten kapandı, devam ediliyor ...")

    try:
        wait_until_connected(timeout=TIMEOUT_CONNECT)
    except TimeoutError:
        raise RuntimeError(
            "DropSens bağlantısı kurulamadı: Cihazın fiziksel olarak bağlı "
            "olduğundan ve DropView'in hazır durumda olduğundan emin olun."
        )

    time.sleep(SLEEP_AFTER_FOCUS)
    if not _is_dropview_connected():
        raise RuntimeError(
            "DropSens bağlantı sinyali alındı ancak son doğrulama başarısız. "
            "Lütfen tekrar deneyin."
        )
    print("│  DropSens bağlandı.")


def step_start_measure(config: dict):
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(SLEEP_AFTER_FOCUS)

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(SLEEP_AFTER_CLICK)
    rect    = win32gui.GetWindowRect(dv_hwnd)
    title_x = (rect[0] + rect[2]) // 2
    title_y = rect[1] + 10
    pyautogui.click(title_x, title_y)
    time.sleep(SLEEP_AFTER_FOCUS)

    wait_for_image(_IMG["scripts_menu"], timeout=TIMEOUT_WINDOW_OPEN, poll_interval=POLL_INTERVAL_SLOW)
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
        print("│    Uyari penceresi kapatılıyor ...")
        pyautogui.press("enter")
        time.sleep(SLEEP_AFTER_FOCUS)

    print("│  Ölçüm başlaması bekleniyor (sarı nokta) ...")
    wait_for_image(_IMG["yellow_dot_selected"], timeout=30,
                   poll_interval=POLL_INTERVAL_SLOW, threshold=THRESHOLD_HIGH)