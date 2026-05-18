# bench_test/measurement/session.py
# -*- coding: utf-8 -*-
"""
MeasurementSession
==================
Bir ölçüm oturumunu temsil eder.

Dizin yapısı:
    {package_root}/
    └── {part_number}/
        └── {YYYYMMDD_HHMMSS}/
            ├── session.json
            ├── measurement_parameters/
            │   ├── {part_number}.tp
            │   ├── {part_number}.scr   (varsa)
            │   └── recipe.json         (varsa)
            ├── measurements/           ← CSV çıktıları buraya
            └── logs/

Kullanım:
    session = MeasurementSession.create(package_root, part_number)
    session.save()

    # Sonradan yüklemek için:
    session = MeasurementSession.load(session_dir)
"""

import json
import os
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionStatus(str, Enum):
    PENDING    = "pending"       # Oluşturuldu, henüz başlamadı
    IN_PROGRESS = "in_progress"  # Recipe çalışıyor
    COMPLETED  = "completed"     # Başarıyla tamamlandı
    ABORTED    = "aborted"       # Kullanıcı durdurdu
    ERROR      = "error"         # Hata ile sonlandı


# ─────────────────────────────────────────────────────────────────
#  Alt dizin sabitleri
# ─────────────────────────────────────────────────────────────────
DIR_PARAMS       = "measurement_parameters"
DIR_MEASUREMENTS = "measurements"
DIR_LOGS         = "logs"
SESSION_FILE     = "session.json"

MEASUREMENT_MODE_SCRIPT_PAD = "script_pad"
MEASUREMENT_MODE_CONTINUOUS_PAD = "continuous_pad"
MEASUREMENT_MODE_CV = "cv"


class MeasurementSession:
    """
    Tek bir ölçüm oturumunu temsil eder.

    Attributes
    ----------
    session_dir : str
        Oturuma ait dizinin tam yolu.
    part_number : str
        Ölçülen sensörün parça numarası.
    created_at : datetime
        Oturum oluşturulma zamanı.
    status : SessionStatus
        Oturumun mevcut durumu.
    files : dict
        Kopyalanan statik dosyaların session_dir'e göre göreli yolları.
        Örnek: {"tp": "measurement_parameters/Sil.tp", "script": ..., "recipe": ...}
    notes : str
        Serbest metin notlar.
    """

    def __init__(
        self,
        session_dir: str,
        part_number: str,
        created_at: datetime,
        status: SessionStatus = SessionStatus.PENDING,
        files: Optional[dict] = None,
        notes: str = "",
        experiment_params: Optional[dict] = None,
        measurement_mode: str = MEASUREMENT_MODE_SCRIPT_PAD,
    ):
        self.session_dir  = session_dir
        self.part_number  = part_number
        self.created_at   = created_at
        self.status       = status
        self.files        = files or {}
        self.notes        = notes
        self.experiment_params = experiment_params or {}
        self.measurement_mode = measurement_mode or MEASUREMENT_MODE_SCRIPT_PAD

    # ── Fabrika metotları ─────────────────────────────────────────

    @classmethod
    def create(cls, package_root: str, part_number: str) -> "MeasurementSession":
        """
        Yeni bir oturum dizini oluşturur ve MeasurementSession döndürür.
        Dizinler hemen yaratılır; session.json henüz yazılmaz.
        session.save() çağrısıyla diske yazılır.
        """
        if not part_number or not part_number.strip():
            raise ValueError("Part Number cannot be empty.")

        part_number = part_number.strip()
        created_at  = datetime.now()
        timestamp   = created_at.strftime("%Y%m%d_%H%M%S")

        session_dir = os.path.join(package_root, part_number, timestamp)

        # Alt dizinleri oluştur
        for sub in (DIR_PARAMS, DIR_MEASUREMENTS, DIR_LOGS):
            os.makedirs(os.path.join(session_dir, sub), exist_ok=True)

        return cls(
            session_dir  = session_dir,
            part_number  = part_number,
            created_at   = created_at,
            status       = SessionStatus.PENDING,
        )

    @classmethod
    def load(cls, session_dir: str) -> "MeasurementSession":
        """Mevcut bir session.json dosyasından oturum yükler."""
        path = os.path.join(session_dir, SESSION_FILE)
        if not os.path.exists(path):
            raise FileNotFoundError(f"session.json not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(
            session_dir  = session_dir,
            part_number  = data["part_number"],
            created_at   = datetime.fromisoformat(data["created_at"]),
            status       = SessionStatus(data.get("status", SessionStatus.PENDING)),
            files        = data.get("files", {}),
            notes        = data.get("notes", ""),
            experiment_params = data.get("experiment_params") or {},
            measurement_mode = data.get(
                "measurement_mode",
                MEASUREMENT_MODE_SCRIPT_PAD,
            ),
        )

    # ── Durum güncelleme ──────────────────────────────────────────

    def set_status(self, status: SessionStatus):
        """Durumu günceller ve diske yazar."""
        self.status = status
        self.save()

    def register_file(self, key: str, absolute_path: str):
        """
        Kopyalanan bir dosyayı kayıt altına alır.
        Yolu session_dir'e göre göreceli olarak saklar.

        Parameters
        ----------
        key : str
            Dosya tipi anahtarı. Örnek: "tp", "script", "recipe"
        absolute_path : str
            Dosyanın tam yolu (session_dir altında olmalı).
        """
        rel = os.path.relpath(absolute_path, self.session_dir)
        self.files[key] = rel.replace("\\", "/")   # platform bağımsız

    def get_file_path(self, key: str) -> Optional[str]:
        """
        Kayıtlı bir dosyanın tam yolunu döndürür.
        Dosya yoksa None döner.
        """
        rel = self.files.get(key)
        if rel is None:
            return None
        full = os.path.normpath(os.path.join(self.session_dir, rel))
        return full if os.path.exists(full) else None

    # ── Hesaplanan özellikler ─────────────────────────────────────

    @property
    def params_dir(self) -> str:
        """measurement_parameters/ dizininin tam yolu."""
        return os.path.join(self.session_dir, DIR_PARAMS)

    @property
    def measurements_dir(self) -> str:
        """measurements/ dizininin tam yolu. csv_output_dir olarak kullanılır."""
        return os.path.join(self.session_dir, DIR_MEASUREMENTS)

    @property
    def logs_dir(self) -> str:
        """logs/ dizininin tam yolu."""
        return os.path.join(self.session_dir, DIR_LOGS)

    # ── Serileştirme ──────────────────────────────────────────────

    def save(self):
        """Oturum meta verisini session.json olarak diske yazar."""
        data = {
            "part_number" : self.part_number,
            "session_id"  : os.path.basename(self.session_dir),
            "created_at"  : self.created_at.isoformat(),
            "status"      : self.status.value,
            "files"       : self.files,
            "notes"       : self.notes,
            "experiment_params": self.experiment_params,
            "measurement_mode": self.measurement_mode,
        }
        path = os.path.join(self.session_dir, SESSION_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def to_dict(self) -> dict:
        """GUI veya loglama için okunabilir dict döndürür."""
        return {
            "part_number"     : self.part_number,
            "created_at"      : self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "status"          : self.status.value,
            "session_dir"     : self.session_dir,
            "measurements_dir": self.measurements_dir,
            "files"           : self.files,
            "notes"           : self.notes,
            "experiment_params": self.experiment_params,
            "measurement_mode" : self.measurement_mode,
        }

    def __repr__(self) -> str:
        return (
            f"MeasurementSession("
            f"part={self.part_number!r}, "
            f"status={self.status.value}, "
            f"dir={self.session_dir!r})"
        )
