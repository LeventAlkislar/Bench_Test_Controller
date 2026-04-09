"""
Bench Test Orkestratörü
=======================
Tüm test sürecini tek noktadan yönetir.

Adımlar:
    1. [✓] DropView ölçüm scriptini üret (veya mevcut .scr kullan)
    2. [✓] DropView'i başlat ve cihaza bağlan
    3. [✓] Scripti yüklet ve çalıştır (GUI otomasyonu)
    4. [ ] Vana kontrol senaryosunu başlat (paralel)
    5. [ ] Test bitince verileri birleştir ve görselleştir

Gerekli görüntü dosyaları (orchestrator.py ile aynı dizinde):
    scripts_menu.png        — DropView menü çubuğundaki "Scripts" yazısı
    script_item.png         — Multiscript Editor listesindeki script satırı
    delete_btn.png          — Multiscript Editor "Delete" butonu
    connected_status.png    — DropView durum çubuğundaki "Connected" yazısı
    disconnected_status.png — DropView durum çubuğundaki "Disconnected" yazısı
"""

import os
import sys
import time
import subprocess
import win32clipboard
import win32gui
import win32con
import win32api
import pyautogui
import numpy as np
import cv2
from PIL import ImageGrab, Image

# common.py'dan ortak yardımcılar
from common import BASE_DIR, ASSETS_DIR, get_last, remember, load_last_paths, save_last_paths

# dropview_script_generator: ASSETS_DIR'e bak (exe modunda _MEIPASS)
sys.path.insert(0, ASSETS_DIR)
from dropview_script_generator import generate_dropview_script


# ─────────────────────────────────────────────
#  Sabitler
# ─────────────────────────────────────────────

DROPVIEW_WINDOW_NAME = "DropView 8400M"

# DropView exe olası kurulum yolları
_DROPVIEW_EXE_CANDIDATES = [
    r"C:\Program Files\DropView 8400M\Dropview.exe",
    r"C:\Program Files (x86)\DropView 8400M\Dropview.exe",
]


def _get_dropview_exe() -> str:
    """
    DropView.exe yolunu döndürür.
    1. last_paths.json'daki kayıtlı yolu dener
    2. Bilinen kurulum dizinlerini tarar
    3. Hiçbirinde bulamazsa kullanıcıdan sorar ve kaydeder.
    """
    # 1. Kaydedilmiş yol var mı?
    saved = get_last("dropview_exe", "")
    if saved and os.path.isfile(saved):
        return saved

    # 2. Bilinen kurulum yollarını tara
    for candidate in _DROPVIEW_EXE_CANDIDATES:
        if os.path.isfile(candidate):
            _remember_exe(candidate)
            return candidate

    # 3. Kullanıcıdan sor — PyQt6 veya tkinter hangisi uygunsa
    try:
        from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
        app = QApplication.instance()
        QMessageBox.information(None, "DropView Bulunamadı",
            "DropView.exe bulunamadı.\nLütfen DropView.exe dosyasını seçin.")
        path, _ = QFileDialog.getOpenFileName(None,
            "DropView.exe Dosyasını Seç", "",
            "Executable (*.exe);;Tüm dosyalar (*.*)")
    except ImportError:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        messagebox.showinfo("DropView Bulunamadı",
            "DropView.exe bulunamadı.\nLütfen DropView.exe dosyasını seçin.")
        path = filedialog.askopenfilename(
            title="DropView.exe Dosyasını Seç",
            filetypes=[("Executable", "*.exe"), ("Tüm dosyalar", "*.*")])
        root.destroy()

    if not path:
        raise FileNotFoundError("DropView.exe seçilmedi, işlem iptal edildi.")

    path = os.path.normpath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Seçilen dosya bulunamadı: {path}")

    _remember_exe(path)
    return path


def _remember_exe(path: str):
    """DropView.exe yolunu last_paths.json'a kaydeder."""
    remember("dropview_exe", path)
CONNECTING_DIALOG    = "Connecting..."
MULTISCRIPT_WINDOW   = "Multiscript Editor"

# pyautogui global ayarları — otomasyon için gerekli
pyautogui.FAILSAFE = False
pyautogui.PAUSE    = 0.05


# Görüntü dosyaları
_IMG = {k: os.path.join(os.path.dirname(__file__), v) for k, v in {
    "scripts_menu":        "scripts_menu.png",
    "script_item":         "script_item.png",
    "red_dot":             "red_dot.png",
    "load_btn":            "load_btn.png",
    "delete_btn":          "delete_btn.png",
    "run_all_btn":         "run_all_btn.png",
    "run_btn":             "run_btn.png",
    "stop_btn":            "stop_btn.png",
    "loaded_scripts":      "loaded_scripts_label.png",
    "connected":           "connected_status.png",
    "disconnected":        "disconnected_status.png",
}.items()}



# ─────────────────────────────────────────────
#  Ekran görüntüsü yardımcıları
# ─────────────────────────────────────────────

def find_on_screen(image_path, threshold=0.7):
    """
    Ekranda image_path görüntüsünü arar.
    Pillow + numpy + opencv kullanır — yoldaki boşluklardan etkilenmez.
    Bulunan konumun merkez (x, y) koordinatını döndürür.
    """
    needle_pil = Image.open(image_path).convert("RGB")
    needle     = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
    screen_pil = ImageGrab.grab()
    screen     = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2BGR)

    result = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        raise RuntimeError(
            f"Görüntü ekranda bulunamadı (eslesme: {max_val:.2f} < {threshold}). "
            f"Dosya: {os.path.basename(image_path)}"
        )

    h, w = needle.shape[:2]
    return max_loc[0] + w // 2, max_loc[1] + h // 2


def wait_for_image(image_path, timeout=60, poll_interval=1.0, threshold=0.7):
    """Ekranda image_path görüntüsü görünene kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            find_on_screen(image_path, threshold=threshold)
            return
        except RuntimeError:
            time.sleep(poll_interval)
    raise TimeoutError(
        f"'{os.path.basename(image_path)}' {timeout}sn icinde ekranda bulunamadi."
    )


def wait_for_image_gone(image_path, timeout=60, poll_interval=1.0, threshold=0.7):
    """Ekrandaki image_path görüntüsü kaybolana kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            find_on_screen(image_path, threshold=threshold)
            time.sleep(poll_interval)  # Hâlâ var
        except RuntimeError:
            return  # Kayboldu
    raise TimeoutError(
        f"'{os.path.basename(image_path)}' {timeout}sn icinde kaybolmadi."
    )


def wait_until_connected(timeout=30, poll_interval=1.0):
    """connected_score > disconnected_score olana kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        conn_sc = match_score_on_screen(_IMG["connected"])
        disc_sc = match_score_on_screen(_IMG["disconnected"])
        if (conn_sc > 0.4 or disc_sc > 0.4) and conn_sc > disc_sc:
            return
        time.sleep(poll_interval)
    raise TimeoutError("DropSens bağlantısı zaman aşımına uğradı.")


def wait_until_disconnected(timeout=10, poll_interval=1.0):
    """disconnected_score > connected_score olana kadar bekler."""
    start = time.time()
    while time.time() - start < timeout:
        conn_sc = match_score_on_screen(_IMG["connected"])
        disc_sc = match_score_on_screen(_IMG["disconnected"])
        if (conn_sc > 0.4 or disc_sc > 0.4) and disc_sc > conn_sc:
            return
        time.sleep(poll_interval)
    # Timeout'ta hata fırlatmıyoruz — disconnect yine de gerçekleşmiş olabilir


# ─────────────────────────────────────────────
#  win32 pencere yardımcıları
# ─────────────────────────────────────────────

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


def window_exists(title_keyword):
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


def find_child_button(parent_hwnd, button_text):
    """Bir pencerenin child elemanları arasında belirtilen metne sahip butonu bulur."""
    found = []
    def _cb(hwnd, _):
        if win32gui.GetWindowText(hwnd) == button_text:
            found.append(hwnd)
    win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    if not found:
        raise RuntimeError(
            f"'{button_text}' butonu '{win32gui.GetWindowText(parent_hwnd)}' "
            f"penceresinde bulunamadi."
        )
    return found[0]


def click_button(hwnd):
    """Bir buton hwnd'sine BM_CLICK mesajı göndererek tıklar."""
    win32api.SendMessage(hwnd, win32con.BM_CLICK, 0, 0)
    time.sleep(0.3)


# ─────────────────────────────────────────────
#  Adım 2: DropView'i başlat ve cihaza bağlan
# ─────────────────────────────────────────────

def match_score_on_screen(image_path) -> float:
    """
    Ekranda image_path görüntüsünün en yüksek korelasyon skorunu döndürür.
    Eşleşme bulunamazsa 0.0 döner.
    """
    try:
        needle_pil = Image.open(image_path).convert("RGB")
        needle     = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
        screen_pil = ImageGrab.grab()
        screen     = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2BGR)
        result     = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return float(max_val)
    except Exception:
        return 0.0


def _is_dropview_connected():
    """
    DropView açık ve cihaz bağlı mı kontrol eder.
    İki görüntüyü karşılaştırır — hangisi daha yüksek skorsa onu seçer.
    """
    if not window_exists(DROPVIEW_WINDOW_NAME):
        return False
    conn_sc = match_score_on_screen(_IMG["connected"])
    disc_sc = match_score_on_screen(_IMG["disconnected"])
    if conn_sc < 0.4 and disc_sc < 0.4:
        return False  # Görüntü tanınamadı
    return conn_sc > disc_sc


def get_dropview_connection_scores() -> dict:
    """
    Hem connected hem disconnected görüntülerinin korelasyon skorlarını döndürür.
    Tanı amaçlıdır — GUI log'unda neyin ne kadar eşleştiğini görmek için.
    """
    dv_open = window_exists(DROPVIEW_WINDOW_NAME)
    if not dv_open:
        return {"dv_open": False, "connected_score": 0.0, "disconnected_score": 0.0}
    return {
        "dv_open": True,
        "connected_score":    match_score_on_screen(_IMG["connected"]),
        "disconnected_score": match_score_on_screen(_IMG["disconnected"]),
    }


def step_launch_dropview(config: dict):
    """
    Sadece DropView.exe'yi başlatır ve ana pencere açılana kadar bekler.
    Zaten açıksa sessizce geçer.
    """
    if window_exists(DROPVIEW_WINDOW_NAME):
        print("│  DropView zaten açık.")
        return

    print("│  DropView başlatılıyor ...")
    subprocess.Popen([_get_dropview_exe()])
    find_window(DROPVIEW_WINDOW_NAME, timeout=60)
    time.sleep(2)
    print("│  DropView penceresi açıldı.")


def step_connect_dropsens(config: dict):
    """
    Sadece DropSens'e bağlanır (Ctrl+C).
    Zaten bağlıysa sessizce geçer.
    Bağlantı kurulamazsa RuntimeError fırlatır (GUI'ye iletilir).
    """
    if _is_dropview_connected():
        print("│  DropSens zaten bağlı.")
        return

    if not window_exists(DROPVIEW_WINDOW_NAME):
        raise RuntimeError(
            "DropView penceresi açık değil. Önce 'Start DropView' butonuna basın."
        )

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(0.5)

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(0.3)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, 0, 0)
    win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    time.sleep(1)
    if window_exists(CONNECTING_DIALOG):
        print("│    Bağlanıyor, dialog bekleniyor ...")
        wait_for_window_close(CONNECTING_DIALOG, timeout=60)
    else:
        print("│    Bağlantı popup'ı zaten kapandı, devam ediliyor ...")

    # Bağlantı durumu: skor karşılaştırmasıyla Connected görünene kadar bekle
    try:
        wait_until_connected(timeout=30)
    except TimeoutError:
        raise RuntimeError(
            "DropSens bağlantısı kurulamadı: Cihazın fiziksel olarak bağlı "
            "olduğundan ve DropView'in hazır durumda olduğundan emin olun."
        )

    # Son doğrulama
    time.sleep(0.5)
    if not _is_dropview_connected():
        raise RuntimeError(
            "DropSens bağlantı sinyali alındı ancak son doğrulama başarısız. "
            "Lütfen tekrar deneyin."
        )
    print("│  DropSens bağlandı.")


def step_disconnect_dropsens(config: dict):
    """
    DropSens bağlantısını keser (Ctrl+D).
    DropView açık değilse sessizce geçer.
    """
    if not window_exists(DROPVIEW_WINDOW_NAME):
        print("│  DropView açık değil, disconnect atlandı.")
        return

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(0.3)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('D'), 0, 0, 0)
    win32api.keybd_event(ord('D'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    # Disconnected yazısının ekrana gelmesini skor karşılaştırmasıyla bekle
    time.sleep(1)
    wait_until_disconnected(timeout=10)
    print("│  DropSens bağlantısı kesildi.")


def step_close_dropview(config: dict):
    """
    DropView penceresini Alt+F4 ile kapatır.
    Zaten kapalıysa sessizce geçer.
    """
    if not window_exists(DROPVIEW_WINDOW_NAME):
        print("│  DropView zaten kapalı.")
        return

    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(0.3)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)       # Alt bas
    win32api.keybd_event(win32con.VK_F4, 0, 0, 0)         # F4 bas
    win32api.keybd_event(win32con.VK_F4, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(1)
    print("│  DropView kapatıldı.")


def step_start_dropview(config: dict):
    """
    Geriye dönük uyumluluk için korundu.
    Hem exe'yi başlatır hem de DropSens'e bağlanır.
    """
    step_launch_dropview(config)
    step_connect_dropsens(config)



# ─────────────────────────────────────────────
#  Adım 3: Multiscript Editor'ü aç, scripti yükle, çalıştır
# ─────────────────────────────────────────────

def _grab_region(x, y, w, h):
    """Ekranın belirli bir bölgesinin görüntüsünü alır."""
    return ImageGrab.grab(bbox=(x, y, x + w, y + h))


def _images_equal(img1, img2):
    """İki PIL görüntüsü aynı mı?"""
    a = np.array(img1)
    b = np.array(img2)
    return a.shape == b.shape and np.array_equal(a, b)


def _count_and_clear_scripts(ms_hwnd, listbox_x, listbox_y):
    """
    Yükleme öncesi listede kaç script var sayar.
    Sonra yükleme sonrası Up + Delete ile hepsini temizler.

    listbox_x, listbox_y: liste kutusunun tıklanacak koordinatı
    """
    # Liste kutusunun bölgesini kaydet (değişim tespiti için)
    rect = win32gui.GetWindowRect(ms_hwnd)
    region_x = rect[0] + 5
    region_y = rect[1] + 60
    region_w = 240
    region_h = 300

    # 1. Listeye tıkla, en yukarı git
    pyautogui.click(listbox_x, listbox_y)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "home")
    time.sleep(0.2)

    # 2. Down tuşuyla aşağı ilerleyerek script sayısını bul
    count = 0
    for _ in range(30):
        before = _grab_region(region_x, region_y, region_w, region_h)
        pyautogui.press("down")
        time.sleep(0.2)
        after = _grab_region(region_x, region_y, region_w, region_h)
        if _images_equal(before, after):
            break  # En alttayız
        count += 1

    return count


def _delete_scripts_above(ms_hwnd, listbox_x, listbox_y, count):
    """
    Yükleme sonrası bizim scriptimiz seçili (en altta).
    Up tuşuyla yukarı çıkıp her seferinde Delete butonuna basar.
    """
    if count == 0:
        print("│    Silinecek script yok.")
        return

    # Liste kutusuna tıkla (bizim script seçili olmalı)
    pyautogui.click(listbox_x, listbox_y)
    time.sleep(0.3)

    for i in range(count):
        # Yukarı git
        pyautogui.press("up")
        time.sleep(0.3)

        # Delete butonuna tıkla
        dx, dy = find_on_screen(_IMG["delete_btn"], threshold=0.6)
        pyautogui.click(dx, dy)
        time.sleep(0.4)



def step_load_and_run_script(config: dict):

    # ── 3a. DropView öne al ───────────────────────────────────
    dv_hwnd = find_window(DROPVIEW_WINDOW_NAME, timeout=10)
    if win32gui.IsIconic(dv_hwnd):
        win32gui.ShowWindow(dv_hwnd, win32con.SW_RESTORE)
        time.sleep(0.5)

    win32gui.SetForegroundWindow(dv_hwnd)
    time.sleep(0.3)
    rect    = win32gui.GetWindowRect(dv_hwnd)
    title_x = (rect[0] + rect[2]) // 2
    title_y = rect[1] + 10
    pyautogui.click(title_x, title_y)
    time.sleep(0.5)

    # ── 3b. Scripts menüsü aktif olana kadar bekle, sonra aç ──
    wait_for_image(_IMG["scripts_menu"], timeout=60, poll_interval=2.0)
    pyautogui.hotkey("alt", "s")
    time.sleep(0.5)
    pyautogui.press("s")
    time.sleep(1.2)

    # ── 3c. Multiscript Editor penceresini bekle ──────────────
    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=15)
    time.sleep(0.5)

    # ── 3d. Liste kutusunun koordinatını görüntü tabanlı bul ─
    # "Loaded scripts" başlığını bul, altındaki listeye tıkla
    lbl_x, lbl_y = find_on_screen(_IMG["loaded_scripts"], threshold=0.7)
    listbox_x = lbl_x          # Başlıkla aynı x
    listbox_y = lbl_y + 100    # Başlığın 100px altı — liste ortası

    # ── 3e. Kendi scriptimizi yükle ──────────────────────────
    lx, ly = find_on_screen(_IMG["load_btn"])
    pyautogui.click(lx, ly)
    time.sleep(0.3)
    time.sleep(0.5)

    # Dosya dialoguna script tam yolunu yapıştır
    script_path = config["script_path"]

    # "Open" dialogu açılana kadar bekle
    open_hwnd = find_window("Open", timeout=10)
    time.sleep(0.5)

    # Dialog'u öne al
    win32gui.SetForegroundWindow(open_hwnd)
    time.sleep(0.5)

    # Tam yolu clipboard'a yaz
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(script_path)
    win32clipboard.CloseClipboard()
    time.sleep(0.2)

    # File Name kutusunun koordinatını hesapla
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
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.8)

    # ── 3f. Diğer scriptleri sil ─────────────────────────────
    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=10)
    win32gui.SetForegroundWindow(ms_hwnd)
    time.sleep(0.5)
    # Yükleme sonrası script sayısını say, bizimki hariç sil
    before_count = _count_and_clear_scripts(ms_hwnd, listbox_x, listbox_y)
    # Bizim scriptimiz en altta, sayıdan 1 çıkar
    _delete_scripts_above(ms_hwnd, listbox_x, listbox_y, before_count)

    # ── 3g. Run (seçili scripti çalıştır) ───────────────────
    rx, ry = find_on_screen(_IMG["run_btn"], threshold=0.6)
    pyautogui.click(rx, ry)
    time.sleep(1.0)

    # "Some curves have not been saved" uyarısı çıkarsa Enter ile geç
    if window_exists("Some curves have not been saved"):
        print("│    Uyari penceresi kapatılıyor ...")
        pyautogui.press("enter")
        time.sleep(0.5)



# ─────────────────────────────────────────────
#  Yer tutucu adımlar (ileride doldurulacak)
# ─────────────────────────────────────────────








# ─────────────────────────────────────────────
#  Adım: DropView Ölçümü Durdur
# ─────────────────────────────────────────────

def step_stop_measurement():
    """
    Multiscript Editor penceresinde Stop butonunu görüntü tabanlı bulup tıklar.
    """
    if not window_exists(MULTISCRIPT_WINDOW):
        return  # Pencere yoksa sessizce geç

    ms_hwnd = find_window(MULTISCRIPT_WINDOW, timeout=5)
    win32gui.SetForegroundWindow(ms_hwnd)
    time.sleep(0.5)

    sx, sy = find_on_screen(_IMG["stop_btn"], threshold=0.7)
    pyautogui.click(sx, sy)
    time.sleep(0.5)
# ─────────────────────────────────────────────
#  Ana akış
# ─────────────────────────────────────────────

