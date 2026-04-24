# bench_test/dropview/vision.py
import os
import time

import cv2
import mss
import numpy as np
from PIL import Image


def _grab_screen() -> np.ndarray:
    """DPI-aware tam ekran görüntüsü alır (BGR)."""
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # tum sanal masaustu
        raw = sct.grab(monitor)
        img = np.array(raw)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def find_on_screen(image_path, threshold=0.7):
    """
    Ekranda image_path görüntüsünü arar.
    Bulunan konumun merkez (x, y) koordinatını döndürür.
    """
    needle_pil = Image.open(image_path).convert("RGB")
    needle     = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
    screen = _grab_screen()

    result = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        raise RuntimeError(
            f"Görüntü ekranda bulunamadı (eslesme: {max_val:.2f} < {threshold}). "
            f"Dosya: {os.path.basename(image_path)}"
        )

    h, w = needle.shape[:2]
    return max_loc[0] + w // 2, max_loc[1] + h // 2


def match_score_on_screen(image_path) -> float:
    """
    Ekranda image_path görüntüsünün en yüksek korelasyon skorunu döndürür.
    """
    try:
        needle_pil = Image.open(image_path).convert("RGB")
        needle     = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
        screen     = _grab_screen()
        result     = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return float(max_val)
    except Exception:
        return 0.0


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
