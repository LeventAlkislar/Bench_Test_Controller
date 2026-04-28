# bench_test/measurement/legacy_session.py
# -*- coding: utf-8 -*-
"""
LegacySession
=============
session.json olmayan eski kayıt klasörlerini temsil eder.

MeasurementSession ile duck-type uyumludur:
ViewerTab bu iki tipi aynı interface üzerinden kullanabilir.
"""

import glob
import os
from datetime import datetime
from typing import Optional

from bench_test.measurement.session import SessionStatus


class LegacySession:
    """
    Eski kayıt klasörünü temsil eden hafif session nesnesi.

    Attributes
    ----------
    session_dir  : Seçilen klasörün tam yolu
    part_number  : Klasör adından türetilen parça numarası
    created_at   : Klasörün son değiştirilme zamanı
    """

    def __init__(
        self,
        session_dir: str,
        part_number: str,
        created_at: datetime,
        xlsx_path: Optional[str] = None,
        log_path: Optional[str] = None,
    ):
        self.session_dir = session_dir
        self.part_number = part_number
        self.created_at  = created_at
        self._xlsx_path  = xlsx_path
        self._log_path   = log_path

    # ── MeasurementSession uyumlu interface ───────────────────────

    @property
    def status(self) -> SessionStatus:
        """Legacy session'lar her zaman COMPLETED kabul edilir."""
        return SessionStatus.COMPLETED

    @property
    def measurements_dir(self) -> str:
        """CSV/xlsx doğrudan session_dir altında aranır."""
        return self.session_dir

    @property
    def logs_dir(self) -> str:
        """Log doğrudan session_dir altında aranır."""
        return self.session_dir

    def get_file_path(self, key: str) -> Optional[str]:
        if key == "xlsx":
            return self._xlsx_path
        if key == "log":
            return self._log_path
        return None

    # ── Keşif ─────────────────────────────────────────────────────

    @classmethod
    def discover(cls, folder: str) -> Optional["LegacySession"]:
        """
        Klasörde standart xlsx veya csv dosyası varsa LegacySession döner.
        Yoksa None döner.

        Aranan dosyalar:
          - *.xlsx  (standart olmayan xlsx'ler Faz 2'de filtrelenecek)
          - *.csv
        """
        if not os.path.isdir(folder):
            return None

        xlsx_files = sorted(glob.glob(os.path.join(folder, "*.xlsx")))
        csv_files  = sorted(glob.glob(os.path.join(folder, "*.csv")))

        if not xlsx_files and not csv_files:
            return None

        part_number = os.path.basename(folder) or "Legacy"

        try:
            mtime = os.path.getmtime(folder)
            created_at = datetime.fromtimestamp(mtime)
        except OSError:
            created_at = datetime.now()

        # En iyi xlsx adayı: en yeni dosya
        xlsx_path = xlsx_files[-1] if xlsx_files else None

        # Log dosyası ara: .log ve .txt, en yeni önce
        log_path = None
        log_candidates = sorted(
            glob.glob(os.path.join(folder, "*.log")) +
            glob.glob(os.path.join(folder, "*.txt")),
            key=os.path.getmtime,
            reverse=True,
        )
        if log_candidates:
            log_path = log_candidates[0]

        return cls(
            session_dir = folder,
            part_number = part_number,
            created_at  = created_at,
            xlsx_path   = xlsx_path,
            log_path    = log_path,
        )

    def __repr__(self) -> str:
        return (
            f"LegacySession("
            f"part={self.part_number!r}, "
            f"dir={self.session_dir!r})"
        )