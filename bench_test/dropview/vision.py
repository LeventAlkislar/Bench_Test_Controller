# bench_test/dropview/vision.py
import base64
import ctypes
import io
import json
import os
import subprocess
import time

import cv2
import mss
import numpy as np
import pyautogui
import win32api
import win32con
import win32gui
from PIL import Image, ImageOps


_WINDOWS_OCR = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$stream = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]::new()
$writer = [Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]::new($stream)
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$ocrType = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$resultType = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Wait-Result($operation, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.Wait()
    $task.Result
}
$writer.WriteBytes([Convert]::FromBase64String([Console]::In.ReadToEnd()))
$null = Wait-Result ($writer.StoreAsync()) ([uint32])
$null = $writer.DetachStream()
$stream.Seek(0)
$decoder = Wait-Result ($decoderType::CreateAsync($stream)) $decoderType
$bitmap = Wait-Result ($decoder.GetSoftwareBitmapAsync()) $bitmapType
$language = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]::new('en-US')
$engine = $ocrType::TryCreateFromLanguage($language)
if ($null -eq $engine) { $engine = $ocrType::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { throw 'No Windows OCR language is available.' }
$lines = foreach ($line in (Wait-Result ($engine.RecognizeAsync($bitmap)) $resultType).Lines) {
    $word = $line.Words | Select-Object -First 1
    if ($null -eq $word) { continue }
    $rect = $word.BoundingRect
    @{ text = $line.Text; x = $rect.X + $rect.Width / 2; y = $rect.Y + $rect.Height / 2 }
}
ConvertTo-Json -InputObject @($lines | Where-Object { $null -ne $_ }) -Compress
"""


def read_label_positions(image):
    """Return OCR lines and their first word centers relative to a screenshot crop."""
    scale = 3
    padding = 12
    grayscale = ImageOps.grayscale(image)
    enlarged = grayscale.resize((image.width * scale, image.height * scale), Image.Resampling.BICUBIC)
    enlarged = ImageOps.expand(enlarged, border=padding, fill=255)
    with io.BytesIO() as png:
        enlarged.save(png, format="PNG")
        payload = base64.b64encode(png.getvalue()).decode("ascii")
    encoded_script = base64.b64encode(_WINDOWS_OCR.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
        input=payload, capture_output=True, encoding="utf-8", errors="replace", timeout=6, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return [(line["text"], round((line["x"] - padding) / scale), round((line["y"] - padding) / scale))
            for line in json.loads(result.stdout)]


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


def find_on_screen(image_path, threshold=0.7, hwnd=None, use_foreground_fallback=True, *, within_window=False):
    """
    Searches for image_path on screen and returns center (x, y).
    """
    needle_pil = Image.open(image_path).convert("RGB")
    needle = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
    screen, offset_x, offset_y = _grab_screen(
        hwnd=hwnd, use_foreground_fallback=use_foreground_fallback
    )
    if within_window:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        height, width = screen.shape[:2]
        x1, y1 = max(0, left - offset_x), max(0, top - offset_y)
        x2, y2 = min(width, right - offset_x), min(height, bottom - offset_y)
        if x2 - x1 < needle.shape[1] or y2 - y1 < needle.shape[0]:
            raise RuntimeError("The target window has no searchable area on its monitor.")
        screen = screen[y1:y2, x1:x2]
        offset_x += x1
        offset_y += y1

    result = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        raise RuntimeError(
            f"Image not found on screen (match: {max_val:.2f} < {threshold}). "
            f"File: {os.path.basename(image_path)}"
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


def connection_scores_on_window(hwnd, templates: dict, full_client=False, *, optional_templates=()) -> dict:
    """Compare templates in one visible client capture without changing focus."""
    result = {"connected_score": 0.0, "disconnected_score": 0.0,
              "image": None, "region": None, "error": ""}
    try:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            raise RuntimeError("DropView is hidden or minimized.")
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (left, top))
        right, bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
        try:
            scale = (ctypes.windll.user32.GetDpiForWindow(hwnd) or 96) / 96.0
        except AttributeError:
            scale = 1.0
        width = right - left if full_client else min(right - left, round(400 * scale))
        height = bottom - top if full_client else min(bottom - top, round(80 * scale))
        if width < 3 or height < 3:
            raise RuntimeError("DropView status region is empty.")
        top = bottom - height
        result["region"] = (left, top, width, height)
        with mss.mss() as sct:
            desktop = sct.monitors[0]
            if (left < desktop["left"] or top < desktop["top"]
                    or left + width > desktop["left"] + desktop["width"]
                    or bottom > desktop["top"] + desktop["height"]):
                raise RuntimeError("DropView status region is outside the desktop.")
            raw = sct.grab({"left": left, "top": top,
                            "width": width, "height": height})
            result["image"] = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Reject covered regions rather than matching another application's pixels.
        for x, y in ((left + 1, top + 1), (left + width - 2, top + 1),
                     (left + 1, bottom - 2), (left + width - 2, bottom - 2),
                     (left + width // 2, top + height // 2)):
            hit = win32gui.WindowFromPoint((x, y))
            if win32gui.GetAncestor(hit, win32con.GA_ROOT) != hwnd:
                raise RuntimeError("DropView status region is covered by another window.")
        screen = cv2.cvtColor(np.array(result["image"]), cv2.COLOR_RGB2BGR)
        for name, path in templates.items():
            try:
                with Image.open(path) as template:
                    needle = cv2.cvtColor(np.array(template.convert("RGB")), cv2.COLOR_RGB2BGR)
                if needle.shape[0] > height or needle.shape[1] > width:
                    raise RuntimeError("Connection template is larger than the status region.")
                scores = cv2.matchTemplate(screen, needle, cv2.TM_CCOEFF_NORMED)
                _, score, _, location = cv2.minMaxLoc(scores)
                result[f"{name}_score"] = float(score)
                if score >= 0.8:
                    result[f"{name}_center_y"] = location[1] + needle.shape[0] / 2
                    x, y = left + location[0], top + location[1]
                    for point in ((x, y), (x + needle.shape[1] - 1, y + needle.shape[0] - 1),
                                  (x + needle.shape[1] // 2, y + needle.shape[0] // 2)):
                        hit = win32gui.WindowFromPoint(point)
                        if win32gui.GetAncestor(hit, win32con.GA_ROOT) != hwnd:
                            raise RuntimeError("Measurement indicator is covered by another window.")
            except (OSError, RuntimeError, cv2.error) as exc:
                if name not in optional_templates:
                    raise
                result["optional_error"] = str(exc)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def match_cropped_image_score_on_window(image_path, crop_box, hwnd) -> float:
    """Return the best score for a cropped template inside the target window."""
    try:
        needle_pil = Image.open(image_path).convert("RGB").crop(crop_box)
        needle = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
        screen, offset_x, offset_y = _grab_screen(
            hwnd=hwnd,
            use_foreground_fallback=False,
        )
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        x1 = max(0, left - offset_x)
        y1 = max(0, top - offset_y)
        x2 = min(screen.shape[1], right - offset_x)
        y2 = min(screen.shape[0], bottom - offset_y)
        window = screen[y1:y2, x1:x2]
        if window.shape[0] < needle.shape[0] or window.shape[1] < needle.shape[1]:
            return 0.0
        result = cv2.matchTemplate(window, needle, cv2.TM_CCOEFF_NORMED)
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
        f"'{os.path.basename(image_path)}' was not found on screen within {timeout}s."
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
