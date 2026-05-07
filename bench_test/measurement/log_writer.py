# bench_test/measurement/log_writer.py
# -*- coding: utf-8 -*-
"""
LogWriter
=========
Recipe çalışırken log satırlarını session dizinindeki
logs/session.log dosyasına gerçek zamanlı yazar.

Kullanım:
    writer = LogWriter(session)
    writer.open()

    # Her log satırı için:
    writer.write("Step 1/8: A:Port 1, B:Load for 105.0 min")

    # Recipe bitince:
    writer.close()

PackagePanel.build_package() başarılı olunca açılır,
on_recipe_completed() / on_recipe_aborted() çağrısında kapanır.
"""

import os
import sys
from datetime import datetime
from typing import Optional

from bench_test.measurement.session import MeasurementSession


class LogWriter:
    """
    Aktif oturumun logs/session.log dosyasına satır yazar.

    Parameters
    ----------
    session : MeasurementSession
        Aktif oturum. logs_dir buradan alınır.
    """

    LOG_FILENAME = "session.log"

    def __init__(self, session: MeasurementSession):
        self.session  = session
        self._path    = os.path.join(session.logs_dir, self.LOG_FILENAME)
        self._file    = None
        self._closed  = False

    # ── Yaşam döngüsü ─────────────────────────────────────────────

    def open(self):
        """
        Log dosyasını açar. Dizin yoksa oluşturur.
        Zaten açıksa tekrar açmaz.
        """
        if self._file is not None:
            return
        os.makedirs(self.session.logs_dir, exist_ok=True)
        # "a" modu: önceki çalışma varsa üstüne ekler (restart senaryosu)
        self._file   = open(self._path, "a", encoding="utf-8", buffering=1)
        self._closed = False
        # Oturum başlangıç ayracı
        from bench_test.version import APP_NAME, APP_VERSION
        self._write_raw(f"\n{'─' * 60}")
        self._write_raw(f"{APP_NAME}  v{APP_VERSION}")
        self._write_raw(f"Program file: {os.path.basename(sys.executable)}")
        self._write_raw(f"Session opened: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._write_raw(f"Part: {self.session.part_number}  |  Dir: {self.session.session_dir}")
        self._write_raw(f"{'─' * 60}")

    def write(self, msg: str):
        """
        Tek bir log satırı yazar.
        Zaman damgası yoksa otomatik ekler.

        Parameters
        ----------
        msg : str
            Log satırı. Başında [YYYY-MM-DD HH:MM:SS] varsa dokunmaz,
            yoksa otomatik ekler.
        """
        if self._file is None or self._closed:
            return

        msg = msg.strip()
        if not msg:
            return

        # Zaten timestamp'li mi?
        if msg.startswith("[") and len(msg) > 21 and msg[20] == "]":
            line = msg
        else:
            ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] {msg}"

        self._write_raw(line)

    def close(self):
        """Log dosyasını kapatır. Kapanış kaydı yazar."""
        if self._file is None or self._closed:
            return
        self._write_raw(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"Session closed: {self.session.status.value}")
        self._file.flush()
        self._file.close()
        self._file   = None
        self._closed = True

        # session.json'a log yolunu kaydet
        self.session.register_file("log", self._path)
        self.session.save()

    # ── Özellikler ────────────────────────────────────────────────

    @property
    def path(self) -> str:
        """Log dosyasının tam yolu."""
        return self._path

    @property
    def is_open(self) -> bool:
        return self._file is not None and not self._closed

    # ── İç yardımcı ───────────────────────────────────────────────

    def _write_raw(self, line: str):
        """Zaman damgası eklemeden ham satır yazar."""
        if self._file:
            self._file.write(line + "\n")

    # ── Context manager desteği ───────────────────────────────────

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()
