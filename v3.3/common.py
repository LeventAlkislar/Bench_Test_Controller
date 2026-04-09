# -*- coding: utf-8 -*-
"""
common.py
=========
Her iki modülde de kullanılan ortak yardımcı fonksiyonlar.
- PyInstaller uyumlu BASE_DIR / ASSETS_DIR tespiti
- last_paths.json okuma/yazma
- Dosya/dizin hafızalı dialog yardımcıları (PyQt6)
"""

import os
import sys
import json
from datetime import datetime

from PyQt6.QtWidgets import QFileDialog

# ─────────────────────────────────────────────
#  Dizin tespiti (PyInstaller uyumlu)
# ─────────────────────────────────────────────

if getattr(sys, 'frozen', False):
    # Exe olarak çalışıyor
    BASE_DIR   = os.path.dirname(sys.executable)
    ASSETS_DIR = sys._MEIPASS
else:
    # Normal Python
    BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
    ASSETS_DIR = BASE_DIR

# ─────────────────────────────────────────────
#  last_paths.json yönetimi
# ─────────────────────────────────────────────

_LAST_PATHS_FILE = os.path.join(BASE_DIR, "last_paths.json")
_last_paths: dict = {}


def load_last_paths() -> dict:
    global _last_paths
    if os.path.exists(_LAST_PATHS_FILE):
        try:
            with open(_LAST_PATHS_FILE, "r", encoding="utf-8") as f:
                _last_paths = json.load(f)
        except (json.JSONDecodeError, OSError):
            _last_paths = {}
    return _last_paths


def save_last_paths():
    try:
        with open(_LAST_PATHS_FILE, "w", encoding="utf-8") as f:
            json.dump(_last_paths, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def get_last(key: str, fallback: str = None) -> str:
    return _last_paths.get(key) or fallback or BASE_DIR


def remember(key: str, path: str):
    """Dosya veya dizin yolunu hafızaya alır ve diske yazar."""
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    if directory:
        _last_paths[key] = directory
        save_last_paths()


# İlk yükleme
load_last_paths()

# ─────────────────────────────────────────────
#  Hafızalı PyQt6 dialog yardımcıları
# ─────────────────────────────────────────────

def open_file(parent, title: str, key: str, filetypes: str = "All Files (*.*)") -> str:
    """Son kullanılan dizinden başlayan dosya açma dialogu."""
    start = get_last(key)
    path, _ = QFileDialog.getOpenFileName(parent, title, start, filetypes)
    if path:
        path = os.path.normpath(path)
        remember(key, path)
    return path


def save_file(parent, title: str, key: str, filetypes: str = "All Files (*.*)",
              default_ext: str = "", initial_file: str = "") -> str:
    """Son kullanılan dizinden başlayan dosya kaydetme dialogu."""
    start = os.path.join(get_last(key), initial_file) if initial_file else get_last(key)
    path, _ = QFileDialog.getSaveFileName(parent, title, start, filetypes)
    if path:
        if default_ext and not path.endswith(default_ext):
            path += default_ext
        path = os.path.normpath(path)
        remember(key, path)
    return path


def open_dir(parent, title: str, key: str) -> str:
    """Son kullanılan dizinden başlayan dizin seçme dialogu."""
    start = get_last(key)
    path = QFileDialog.getExistingDirectory(parent, title, start)
    if path:
        path = os.path.normpath(path)
        remember(key, path)
    return path
