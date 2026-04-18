# bench_test/ui/tabs/package_panel.py
# -*- coding: utf-8 -*-
"""
PackagePanel
============
MeasurementSetupTab içindeki Kolon 1.
Part Number + Package Root girişi, dosya referansları,
oturum durumu ve aggregate butonu.

Mevcut PackageTab mantığını taşır; script_tab / recipe_tab
referansları yerine MeasurementSetupTab sinyalleriyle beslenir.
"""

import os
import shutil

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QGroupBox, QMessageBox, QTextEdit
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont

from bench_test.measurement.session import MeasurementSession
from bench_test.measurement.packager import Packager, PackagerError
from bench_test.measurement.aggregator import Aggregator
from bench_test.measurement.log_writer import LogWriter
from bench_test.measurement.state_machine import SessionStateMachine, AGGREGATING, IDLE
from bench_test.utils.paths import get_last, remember, open_dir
from bench_test.ui.widgets import _btn

_COLOR_NONE    = "#888888"
_COLOR_READY   = "#4CAF50"
_COLOR_RUNNING = "#2196F3"
_COLOR_DONE    = "#4CAF50"
_COLOR_ERROR   = "#F44336"


class PackagePanel(QWidget):
    log_signal = pyqtSignal(str)

    # Diğer panellerin dinleyeceği sinyaller
    part_number_changed = pyqtSignal(str)   # → MethodEditorPanel.set_sensor_sample
    csv_path_changed    = pyqtSignal(str)   # → ScriptParamsPanel.set_csv_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session    : MeasurementSession = None
        self._log_writer : LogWriter           = None
        self.sm = SessionStateMachine(self)
        self.sm.log_signal.connect(self._log)
        self.sm.state_changed.connect(self._on_state_changed)
        self._build_ui()

    def _close_log_writer(self):
        """Açık session.log dosya handle'ını güvenli biçimde kapat."""
        if self._log_writer and self._log_writer.is_open:
            try:
                self._log_writer.close()
            except Exception as e:
                self._log(f"⚠ LogWriter kapatılamadı: {e}")

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Session Identity ──────────────────────────────────────
        id_grp = QGroupBox("Session Identity")
        id_form = QFormLayout(id_grp)

        self.part_number_edit = QLineEdit()
        self.part_number_edit.setPlaceholderText("e.g. UNAM-XXX-YYY-ZZZ")
        self.part_number_edit.textChanged.connect(self._on_part_number_changed)
        id_form.addRow("Part Number:", self.part_number_edit)

        self.session_id_edit = QLineEdit()
        self.session_id_edit.setReadOnly(True)
        self.session_id_edit.setPlaceholderText("Session başlatıldığında atanır...")
        id_form.addRow("Session ID:", self.session_id_edit)

        pkg_row = QHBoxLayout()
        self.package_root_edit = QLineEdit()
        self.package_root_edit.setReadOnly(True)
        self.package_root_edit.setPlaceholderText("Paket kök dizini seçilmemiş...")
        self.package_root_edit.setText(get_last("package_root", ""))
        pkg_row.addWidget(self.package_root_edit)
        pkg_row.addWidget(_btn("Browse", self._browse_package_root))
        id_form.addRow("Package Root:", pkg_row)

        layout.addWidget(id_grp)

        # ── File References ───────────────────────────────────────
        ref_grp = QGroupBox("File References")
        ref_form = QFormLayout(ref_grp)
        self.tp_lbl     = self._ref_row(ref_form, "Method File (.tp):")
        self.scr_lbl    = self._ref_row(ref_form, "Script File (.scr):")
        self.recipe_lbl = self._ref_row(ref_form, "Recipe File (.json):")
        layout.addWidget(ref_grp)

        # ── Package Status ────────────────────────────────────────
        status_grp = QGroupBox("Package Status")
        status_lay = QVBoxLayout(status_grp)

        self.status_lbl = QLabel("Paket oluşturulmadı")
        self.status_lbl.setStyleSheet(f"color: {_COLOR_NONE}; font-weight: bold;")
        status_lay.addWidget(self.status_lbl)

        self.session_dir_lbl = QLabel("")
        self.session_dir_lbl.setWordWrap(True)
        self.session_dir_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        status_lay.addWidget(self.session_dir_lbl)

        self.aggregate_btn = _btn("Aggregate CSVs → xlsx", self._run_aggregator)
        self.aggregate_btn.setEnabled(False)
        self.aggregate_btn.setToolTip(
            "CSV ölçümlerini birleştirip xlsx oluşturur.\n"
            "Recipe tamamlandığında otomatik çalışır.")
        status_lay.addWidget(self.aggregate_btn)

        layout.addWidget(status_grp)

        # ── Package Log ───────────────────────────────────────────
        log_grp = QGroupBox("Package Log")
        log_lay = QVBoxLayout(log_grp)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(160)
        self.log_edit.setFont(QFont("Consolas", 9))
        log_lay.addWidget(self.log_edit)
        layout.addWidget(log_grp)

        layout.addStretch()

    def _ref_row(self, form: QFormLayout, label: str) -> QLabel:
        lbl = QLabel("—")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #888;")
        form.addRow(label, lbl)
        return lbl

    # ── Part Number değişince ─────────────────────────────────────

    def _on_part_number_changed(self, text: str):
        part = text.strip()
        self.part_number_changed.emit(part)   # → MethodEditorPanel
        self._update_csv_path()
        self._refresh_status()

    def _update_csv_path(self):
        """CSV yolunu hesaplar ve ScriptParamsPanel'e bildirir."""
        part = self.part_number_edit.text().strip()
        root = self.package_root_edit.text().strip()
        if part and root:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = os.path.join(
                root, part, f"{ts}_{part}", "measurements", f"{part}.csv"
            )
            self.csv_path_changed.emit(csv_path)
        else:
            self.csv_path_changed.emit("")

    # ── Referans güncelleme (dışarıdan çağrılır) ──────────────────

    def set_tp_ref(self, path: str):
        self._set_ref(self.tp_lbl, path)

    def set_scr_ref(self, path: str):
        self._set_ref(self.scr_lbl, path)

    def set_recipe_ref(self, path: str):
        self._set_ref(self.recipe_lbl, path)

    def _set_ref(self, lbl: QLabel, path: str):
        if path and os.path.isfile(path):
            lbl.setText(os.path.basename(path))
            lbl.setToolTip(path)
            lbl.setStyleSheet(f"color: {_COLOR_READY};")
        elif path:
            lbl.setText(f"Bulunamadı: {os.path.basename(path)}")
            lbl.setToolTip(path)
            lbl.setStyleSheet(f"color: {_COLOR_ERROR};")
        else:
            lbl.setText("—")
            lbl.setToolTip("")
            lbl.setStyleSheet("color: #888;")

    # ── Browse ────────────────────────────────────────────────────

    def _browse_package_root(self):
        path = open_dir(self, "Paket Kök Dizini Seç", "package_root")
        if path:
            self.package_root_edit.setText(path)
            remember("package_root", path)
            self._update_csv_path()
            self._refresh_status()

    # ── Durum ─────────────────────────────────────────────────────

    def _refresh_status(self):
        if self._session:
            return
        part = self.part_number_edit.text().strip()
        root = self.package_root_edit.text().strip()
        if part and root:
            self.status_lbl.setText("Hazır — recipe başlatıldığında paket oluşturulacak")
            self.status_lbl.setStyleSheet(f"color: {_COLOR_READY};")
        else:
            self.status_lbl.setText("Paket oluşturulmadı")
            self.status_lbl.setStyleSheet(f"color: {_COLOR_NONE};")

    def _set_status(self, text: str, color: str):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ── Build Package — RecipeTab tarafından çağrılır ─────────────

    def build_package(self, tp_path: str, scr_params: dict, recipe_path: str) -> bool:
        """
        Recipe başlamadan önce paketi oluşturur.

        Parametreler
        ------------
        tp_path     : MethodEditorPanel'den gelen kaynak .tp yolu
        scr_params  : ScriptParamsPanel.get_scr_params() çıktısı
        recipe_path : RecipeTab._current_recipe_path
        """
        if self._session is not None:
            self._log("⚠ Aktif oturum zaten var — mevcut oturum kullanılıyor.")
            return True

        part_number  = self.part_number_edit.text().strip()
        package_root = self.package_root_edit.text().strip()

        if not part_number:
            QMessageBox.warning(self, "Uyarı",
                "Part Number boş olamaz.\nMeasurement Setup sekmesinden girin.")
            return False

        if not package_root or not os.path.isdir(package_root):
            QMessageBox.warning(self, "Uyarı",
                "Geçerli bir Package Root dizini seçilmemiş.")
            return False

        if not tp_path or not os.path.isfile(tp_path):
            QMessageBox.warning(self, "Uyarı",
                "Method dosyası (.tp) yüklenmemiş.\n"
                "Measurement Setup sekmesinden bir .tp dosyası yükleyin.")
            return False

        if not scr_params.get("method_file"):
            QMessageBox.warning(self, "Uyarı",
                "Script parametreleri eksik.")
            return False

        try:
            self._session = MeasurementSession.create(package_root, part_number)
            packager = Packager(self._session)

            # .tp kopyala
            packager.pack_tp(tp_path)
            self._log(f"✓ .tp kopyalandı: {os.path.basename(tp_path)}")

            # .scr üret ve kopyala
            from bench_test.dropview.script_generator import generate_dropview_script
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".scr", delete=False) as tmp:
                tmp_path = tmp.name
            generate_dropview_script(
                method_file=scr_params["method_file"],
                output_csv=scr_params["output_csv"],
                repeat_times=scr_params["repeat_times"],
                wait_ms=scr_params["wait_ms"],
                output_script_path=tmp_path,
            )
            packager.pack_script(tmp_path)
            os.unlink(tmp_path)
            self._log(f"✓ .scr üretildi ve kopyalandı.")

            # recipe.json
            if recipe_path and os.path.isfile(recipe_path):
                packager.pack_recipe(recipe_path)
                self._log(f"✓ recipe kopyalandı: {os.path.basename(recipe_path)}")
            else:
                self._log("⚠ recipe dosyası bulunamadı, atlandı.")

            packager.finalize()

            # Referansları güncelle
            self.set_scr_ref(packager.get_packed_scr_path())
            self.set_tp_ref(self._session.tp_path if hasattr(self._session, "tp_path") else tp_path)

            session_name = os.path.basename(self._session.session_dir)
            self._set_status(f"Aktif: {part_number} / {session_name}", _COLOR_RUNNING)
            self.session_dir_lbl.setText(self._session.session_dir)
            self.session_id_edit.setText(session_name)
            self.aggregate_btn.setEnabled(True)

            self._log_writer = LogWriter(self._session)
            self._log_writer.open()

            self._log(f"Paket oluşturuldu: {self._session.session_dir}")
            self.log_signal.emit(f"Paket oluşturuldu: {self._session.session_dir}")
            self.sm.build(self._session)
            return True

        except PackagerError as e:
            self._session = None
            QMessageBox.critical(self, "Paket Hatası", str(e))
            self._log(f"✗ Paket hatası: {e}")
            self._set_status("Hata — paket oluşturulamadı", _COLOR_ERROR)
            return False

        except Exception as e:
            self._session = None
            QMessageBox.critical(self, "Hata", f"Paket oluşturulamadı:\n{e}")
            self._log(f"✗ Beklenmeyen hata: {e}")
            self._set_status("Hata — paket oluşturulamadı", _COLOR_ERROR)
            return False

    # ── Recipe yaşam döngüsü ──────────────────────────────────────

    def write_to_log(self, msg: str):
        if self._log_writer and self._log_writer.is_open:
            self._log_writer.write(msg)

    def on_recipe_completed(self):
        if self._session:
            self._set_status(f"Tamamlandı: {self._session.part_number}", _COLOR_DONE)
            self._log("Oturum tamamlandı.")
        self.sm.finish()

    def on_recipe_aborted(self):
        if self._session:
            self._set_status(f"Durduruldu: {self._session.part_number}", _COLOR_ERROR)
            self._log("Oturum durduruldu.")
        self.sm.stop()

    # ── Aggregator ────────────────────────────────────────────────

    def _run_aggregator(self):
        if not self._session:
            QMessageBox.warning(self, "Aggregator", "Aktif oturum yok.")
            return
        if self.sm.state == "running":
            QMessageBox.warning(self, "Aggregator",
                                "Recipe çalışıyor. Bitmesini bekleyin.")
            return
        aggregator = Aggregator(self._session)
        xlsx_path = aggregator.run(parent=self)
        if xlsx_path:
            self._log(f"✓ Aggregate tamamlandı: {os.path.basename(xlsx_path)}")
            self.log_signal.emit(f"Aggregate tamamlandı: {xlsx_path}")
        else:
            self._log("⚠ Aggregate iptal edildi veya başarısız.")
        self._ask_keep_session()

    def _ask_keep_session(self):
        if not self._session:
            return
        session_dir = self._session.session_dir
        part_number = self._session.part_number
        status      = self._session.status.value

        reply = QMessageBox.question(
            self, "Oturumu Sakla",
            f"Oturum: {part_number}  [{status}]\n"
            f"{session_dir}\n\n"
            f"Bu oturum kaydedilsin mi?\n"
            f"'Hayır' seçerseniz tüm dosyalar silinir.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.No:
            self._close_log_writer()
            try:
                shutil.rmtree(session_dir)
                self._log(f"Oturum silindi: {session_dir}")
                self.log_signal.emit(f"Oturum silindi: {session_dir}")
            except Exception as e:
                self._log(f"✗ Dizin silinemedi: {e}")
                QMessageBox.warning(self, "Hata", f"Dizin silinemedi:\n{e}")
        else:
            self._log(f"Oturum saklandı: {session_dir}")
            self._close_log_writer()

        self._session    = None
        self._log_writer = None
        self.sm.reset()
        self.aggregate_btn.setEnabled(False)
        self.session_dir_lbl.setText("")
        self.session_id_edit.setText("")
        self._set_status("Hazır — yeni recipe başlatılabilir", _COLOR_READY)

    def get_session(self) -> MeasurementSession:
        return self._session

    # ── Log ───────────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_edit.append(msg)
        if self._log_writer and self._log_writer.is_open:
            self._log_writer.write(msg)

    def _on_state_changed(self, state: str):
        if state == AGGREGATING:
            self.aggregate_btn.setEnabled(False)
        elif state == IDLE:
            if self._session is not None:
                self._ask_keep_session()
