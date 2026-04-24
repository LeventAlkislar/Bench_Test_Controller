import os
import time

import cv2
import numpy as np
import pyautogui
import win32con
import win32gui
import win32process
from PIL import Image

from bench_test.dropview.vision import (
    _grab_screen,
    find_on_screen,
    wait_for_image,
    wait_for_image_gone,
)


def _get_window_pid(hwnd) -> int | None:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def _get_owner_info(hwnd) -> tuple[str, int | None]:
    owner_hwnd = None
    owner_title = "unknown"
    try:
        owner_hwnd = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
    except Exception:
        owner_hwnd = None

    if owner_hwnd and win32gui.IsWindow(owner_hwnd):
        try:
            owner_title = win32gui.GetWindowText(owner_hwnd) or "(untitled)"
        except Exception:
            owner_title = "unknown"
    return owner_title, owner_hwnd


def _window_context(hwnd) -> str:
    title = "(invalid)"
    if hwnd and win32gui.IsWindow(hwnd):
        try:
            title = win32gui.GetWindowText(hwnd) or "(untitled)"
        except Exception:
            title = "(untitled)"
    pid = _get_window_pid(hwnd)
    owner_title, owner_hwnd = _get_owner_info(hwnd)
    return (
        f"target='{title}' (hwnd={hwnd}, pid={pid}, "
        f"owner='{owner_title}', owner_hwnd={owner_hwnd})"
    )


def _log_action(log_fn, action: str, hwnd, result: str, retry: int = 0, detail: str = ""):
    if not log_fn:
        return
    fg_title = "unknown"
    fg_hwnd = None
    try:
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg_hwnd) or "(untitled)"
    except Exception:
        pass
    suffix = f" | {detail}" if detail else ""
    log_fn(
        f"│  UI action: {action} | {_window_context(hwnd)} | "
        f"foreground='{fg_title}' (hwnd={fg_hwnd}) | retry={retry} | result={result}{suffix}"
    )


def is_foreground(hwnd) -> bool:
    try:
        return bool(hwnd) and win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def bring_to_front(hwnd, retries=3, log_fn=None) -> bool:
    if not hwnd or not win32gui.IsWindow(hwnd):
        _log_action(log_fn, "bring_to_front", hwnd, "fail", detail="invalid hwnd")
        return False

    for attempt in range(1, retries + 1):
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.2)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.15)
            rect = win32gui.GetWindowRect(hwnd)
            pyautogui.click((rect[0] + rect[2]) // 2, rect[1] + 10)
            time.sleep(0.15)
            if is_foreground(hwnd):
                _log_action(log_fn, "bring_to_front", hwnd, "ok", retry=attempt)
                return True
        except Exception as exc:
            _log_action(log_fn, "bring_to_front", hwnd, "retry", retry=attempt, detail=str(exc))
            time.sleep(0.15)
            continue
        _log_action(log_fn, "bring_to_front", hwnd, "retry", retry=attempt)
        time.sleep(0.15)

    _log_action(log_fn, "bring_to_front", hwnd, "fail", retry=retries)
    return False


def safe_click(hwnd, x, y, log_fn=None, retries=3, pre_delay=0.0, post_delay=0.0):
    for attempt in range(1, retries + 1):
        if bring_to_front(hwnd, retries=1, log_fn=log_fn):
            if pre_delay > 0:
                time.sleep(pre_delay)
            pyautogui.click(x, y)
            if post_delay > 0:
                time.sleep(post_delay)
            _log_action(log_fn, "click", hwnd, "ok", retry=attempt, detail=f"x={x}, y={y}")
            return
    raise RuntimeError(f"Click failed after {retries} retries: {_window_context(hwnd)}")


def safe_hotkey(hwnd, *keys, log_fn=None, retries=3, post_delay=0.0):
    for attempt in range(1, retries + 1):
        if bring_to_front(hwnd, retries=1, log_fn=log_fn):
            pyautogui.hotkey(*keys)
            if post_delay > 0:
                time.sleep(post_delay)
            _log_action(log_fn, "hotkey", hwnd, "ok", retry=attempt, detail="+".join(keys))
            return
    raise RuntimeError(f"Hotkey failed after {retries} retries: {_window_context(hwnd)}")


def safe_press(hwnd, key, log_fn=None, retries=3, post_delay=0.0):
    for attempt in range(1, retries + 1):
        if bring_to_front(hwnd, retries=1, log_fn=log_fn):
            pyautogui.press(key)
            if post_delay > 0:
                time.sleep(post_delay)
            _log_action(log_fn, "press", hwnd, "ok", retry=attempt, detail=str(key))
            return
    raise RuntimeError(f"Key press failed after {retries} retries: {_window_context(hwnd)}")


def safe_sequence(hwnd, steps, log_fn=None, retries=3):
    """
    Focuses the target window once, then executes a sequence of keyboard actions
    without refocusing between steps.

    Supported step formats:
      {"type": "hotkey", "keys": ("alt", "s"), "post_delay": 0.2}
      {"type": "press", "key": "s", "post_delay": 1.0}
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if not bring_to_front(hwnd, retries=1, log_fn=log_fn):
                raise RuntimeError("bring_to_front failed")
            for step in steps:
                step_type = step["type"]
                post_delay = float(step.get("post_delay", 0.0))
                if step_type == "hotkey":
                    keys = tuple(step["keys"])
                    pyautogui.hotkey(*keys)
                    _log_action(
                        log_fn,
                        "sequence_hotkey",
                        hwnd,
                        "ok",
                        retry=attempt,
                        detail="+".join(keys),
                    )
                elif step_type == "press":
                    key = step["key"]
                    pyautogui.press(key)
                    _log_action(
                        log_fn,
                        "sequence_press",
                        hwnd,
                        "ok",
                        retry=attempt,
                        detail=str(key),
                    )
                else:
                    raise ValueError(f"Unsupported sequence step type: {step_type}")
                if post_delay > 0:
                    time.sleep(post_delay)
            return
        except Exception as exc:
            last_error = exc
            _log_action(log_fn, "sequence", hwnd, "retry", retry=attempt, detail=str(exc))
            time.sleep(0.15)
    raise RuntimeError(f"Sequence failed after {retries} retries: {last_error}")


def _find_on_screen_in_region(image_path, hwnd, region, threshold):
    screen, offset_x, offset_y = _grab_screen(hwnd=hwnd, use_foreground_fallback=False)
    rx, ry, rw, rh = region
    cropped = screen[ry:ry + rh, rx:rx + rw]
    needle_pil = Image.open(image_path).convert("RGB")
    needle = cv2.cvtColor(np.array(needle_pil), cv2.COLOR_RGB2BGR)
    result = cv2.matchTemplate(cropped, needle, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        raise RuntimeError(
            f"Goruntu ekranda bulunamadi (eslesme: {max_val:.2f} < {threshold}). "
            f"Dosya: {os.path.basename(image_path)}"
        )
    h, w = needle.shape[:2]
    return offset_x + rx + max_loc[0] + w // 2, offset_y + ry + max_loc[1] + h // 2


def safe_find_on_screen(hwnd, image_path, threshold=0.7, log_fn=None, retries=3, region=None):
    last_error = None
    for attempt in range(1, retries + 1):
        bring_to_front(hwnd, retries=1, log_fn=log_fn)
        try:
            if region is not None:
                result = _find_on_screen_in_region(image_path, hwnd, region, threshold)
            else:
                result = find_on_screen(image_path, threshold=threshold, hwnd=hwnd, use_foreground_fallback=False)
            _log_action(log_fn, "find", hwnd, "ok", retry=attempt, detail=os.path.basename(image_path))
            return result
        except Exception as exc:
            last_error = exc
            _log_action(log_fn, "find", hwnd, "retry", retry=attempt, detail=os.path.basename(image_path))
            time.sleep(0.15)
    raise last_error


def safe_wait_for_image(
    hwnd,
    image_path,
    timeout=60,
    poll_interval=1.0,
    threshold=0.7,
    log_fn=None,
    region=None,
):
    bring_to_front(hwnd, retries=2, log_fn=log_fn)
    if region is None:
        wait_for_image(
            image_path,
            timeout=timeout,
            poll_interval=poll_interval,
            threshold=threshold,
            hwnd=hwnd,
            use_foreground_fallback=False,
        )
        _log_action(log_fn, "wait_image", hwnd, "ok", detail=os.path.basename(image_path))
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            safe_find_on_screen(
                hwnd,
                image_path,
                threshold=threshold,
                log_fn=log_fn,
                retries=1,
                region=region,
            )
            _log_action(log_fn, "wait_image", hwnd, "ok", detail=os.path.basename(image_path))
            return
        except Exception:
            time.sleep(poll_interval)
    raise TimeoutError(f"'{os.path.basename(image_path)}' {timeout}sn icinde ekranda bulunamadi.")


def safe_wait_for_image_gone(
    hwnd,
    image_path,
    timeout=60,
    poll_interval=1.0,
    threshold=0.7,
    log_fn=None,
):
    bring_to_front(hwnd, retries=2, log_fn=log_fn)
    wait_for_image_gone(
        image_path,
        timeout=timeout,
        poll_interval=poll_interval,
        threshold=threshold,
        hwnd=hwnd,
        use_foreground_fallback=False,
    )
    _log_action(log_fn, "wait_image_gone", hwnd, "ok", detail=os.path.basename(image_path))
