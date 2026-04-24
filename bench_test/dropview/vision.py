# bench_test/dropview/vision.py
import os
import time

import cv2
import mss
import numpy as np
import pyautogui
import win32api
import win32con
import win32gui
from PIL import Image


def _get_active_monitor(sct) -> dict:
    """
    Returns the monitor where the mouse cursor is located.
    Falls back to the primary monitor (monitors[1]) if no match is found.
    """
    cx, cy = pyautogui.position()
    for mon in sct.monitors[1:]:
        if mon["left"] <= cx < mon["left"] + mon["width"] and mon["top"] <= cy < mon["top"] + mon["height"]:
            return mon
    return sct.monitors[1]


def _match_monitor_by_rect(sct, rect) -> dict | None:
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    for mon in sct.monitors[1:]:
        if (
            mon["left"] == left
            and mon["top"] == top
            and mon["width"] == width
            and mon["height"] == height
        ):
            return mon
    return None


def _get_monitor_for_hwnd(sct, hwnd) -> dict | None:
    """
    Returns the MSS monitor corresponding to the given window handle.
    Returns None on any lookup/mapping failure.
    """
    try:
        if not hwnd or not win32gui.IsWindow(hwnd):
            return None
        hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(hmon)
        return _match_monitor_by_rect(sct, info["Monitor"])
    except Exception:
        return None


def _grab_screen(hwnd=None, use_foreground_fallback=True) -> tuple[np.ndarray, int, int]:
    """Grabs a DPI-aware monitor screenshot and returns (BGR image, left, top)."""
    with mss.mss() as sct:
        monitor = _get_monitor_for_hwnd(sct, hwnd)
        if monitor is None and use_foreground_fallback:
            fg_hwnd = None
            try:
                fg_hwnd = win32gui.GetForegroundWindow()
            except Exception:
                fg_hwnd = None
            monitor = _get_monitor_for_hwnd(sct, fg_hwnd)
        if monitor is None:
            monitor = _get_active_monitor(sct)

        raw = sct.grab(monitor)
        screen = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
        return screen, int(monitor["left"]), int(monitor["top"])


def find_on_screen(image_path, threshold=0.7, hwnd=None, use_foreground_fallback=True):
    """
    Searches for image_path on screen and returns center (x, y).
    """
    needle_pil = Image.open(image_path).convert("RGB")
    needle = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
    screen, offset_x, offset_y = _grab_screen(
        hwnd=hwnd, use_foreground_fallback=use_foreground_fallback
    )

    result = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        raise RuntimeError(
            f"Goruntu ekranda bulunamadi (eslesme: {max_val:.2f} < {threshold}). "
            f"Dosya: {os.path.basename(image_path)}"
        )

    h, w = needle.shape[:2]
    return offset_x + max_loc[0] + w // 2, offset_y + max_loc[1] + h // 2


def match_score_on_screen(image_path, hwnd=None, use_foreground_fallback=True) -> float:
    """
    Returns best template match score for image_path on screen.
    """
    try:
        needle_pil = Image.open(image_path).convert("RGB")
        needle = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
        screen, _, _ = _grab_screen(hwnd=hwnd, use_foreground_fallback=use_foreground_fallback)
        result = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return float(max_val)
    except Exception:
        return 0.0


def wait_for_image(
    image_path,
    timeout=60,
    poll_interval=1.0,
    threshold=0.7,
    hwnd=None,
    use_foreground_fallback=True,
):
    """Waits until image_path appears on screen."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            find_on_screen(
                image_path,
                threshold=threshold,
                hwnd=hwnd,
                use_foreground_fallback=use_foreground_fallback,
            )
            return
        except RuntimeError:
            time.sleep(poll_interval)
    raise TimeoutError(
        f"'{os.path.basename(image_path)}' {timeout}sn icinde ekranda bulunamadi."
    )


def wait_for_image_gone(
    image_path,
    timeout=60,
    poll_interval=1.0,
    threshold=0.7,
    hwnd=None,
    use_foreground_fallback=True,
):
    """Waits until image_path disappears from screen."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            find_on_screen(
                image_path,
                threshold=threshold,
                hwnd=hwnd,
                use_foreground_fallback=use_foreground_fallback,
            )
            time.sleep(poll_interval)
        except RuntimeError:
            return
    raise TimeoutError(
        f"'{os.path.basename(image_path)}' {timeout}sn icinde kaybolmadi."
    )


def _grab_region(x, y, w, h):
    with mss.mss() as sct:
        raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _images_equal(img1, img2):
    a = np.array(img1)
    b = np.array(img2)
    return a.shape == b.shape and np.array_equal(a, b)
