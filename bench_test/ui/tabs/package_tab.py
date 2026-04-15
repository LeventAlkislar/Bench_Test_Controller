# bench_test/ui/tabs/package_tab.py
# -*- coding: utf-8 -*-
"""
PackageTab
==========
Ölçüm paketini yöneten sekme.

Sorumluluklar:
- Part Number ve Package Root girişi
- Statik dosya referanslarını ScriptEditorTab ve RecipeTab'dan otomatik alma
- Oturum dizinini oluşturma ve paketleme (Packager)
- _start_recipe() öncesinde RecipeTab tarafından çağrılır
"""

import os
import shutil

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QMessageBox, QTextEdit
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont

from bench_test.measurement.session import MeasurementSession, SessionStatus
from bench_test.measurement.packager import Packager, PackagerError
from bench_test.measurement.aggregator import Aggregator, AggregatorError
from bench_test.measurement.log_writer import LogWriter
from bench_test.utils.paths import get_last, remember, open_dir
from bench_test.ui.widgets import _btn, _lbl


# Durum renkleri
_COLOR_NONE    = "#888888"
_COLOR_READY   = "#4CAF50"
_COLOR_RUNNING = "#2196F3"
_COLOR_DONE    = "#4CAF50"
_COLOR_ERROR   = "#F44336"


class PackageTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, script_tab=None, recipe_tab=None):
        super().__init__()
        self.script_tab  = script_tab
        self.recipe_tab  = recipe_tab
        self._session   : MeasurementSession = None
        self._log_writer: LogWriter           = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── 1. Oturum Kimliği ─────────────────────────────────────
        id_grp = QGroupBox("Session Identity")
        id_form = QFormLayout(id_grp)

        self.part_number_edit = QLineEdit()
        self.part_number_edit.setPlaceholderText("e.g. Sil, XXX-YYY-ZZZ")
        self.part_number_edit.setMaximumWidth(200)
        self.part_number_edit.textChanged.connect(self._refresh_status)
        id_form.addRow("Part Number:", self.part_number_edit)

        pkg_row = QHBoxLayout()
        self.package_root_edit = QLineEdit()
        self.package_root_edit.setReadOnly(True)
        self.package_root_edit.setPlaceholderText("Paket kök dizini seçilmedi...")
        self.package_root_edit.setText(get_last("package_root", ""))
        pkg_row.addWidget(self.package_root_edit)
        pkg_row.addWidget(_btn("Browse", self._browse_package_root))
        id_form.addRow("Package Root:", pkg_row)

        layout.addWidget(id_grp)

        # ── 2. Dosya Referansları ─────────────────────────────────
        ref_grp = QGroupBox("File References")
        ref_form = QFormLayout(ref_grp)

        self.tp_lbl   = self._ref_row(ref_form, "Method (.tp):")
        self.scr_lbl  = self._ref_row(ref_form, "Script (.scr):")
        self.recipe_lbl = self._ref_row(ref_form, "Recipe (.json):")

        refresh_row = QHBoxLayout()
        refresh_row.addStretch()
        refresh_row.addWidget(_btn("Refresh", self._refresh_refs))
        ref_form.addRow("", refresh_row)

        layout.addWidget(ref_grp)

        # ── 3. Paket Durumu ───────────────────────────────────────
        status_grp = QGroupBox("Package Status")
        status_lay = QVBoxLayout(status_grp)

        self.status_lbl = QLabel("Paket oluşturulmadı")
        self.status_lbl.setStyleSheet(f"color: {_COLOR_NONE}; font-weight: bold;")
        status_lay.addWidget(self.status_lbl)

        self.session_dir_lbl = QLabel("")
        self.session_dir_lbl.setWordWrap(True)
        self.session_dir_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        status_lay.addWidget(self.session_dir_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.create_pkg_btn = _btn("Create Package", self._create_package)
        self.create_pkg_btn.setEnabled(False)   # şimdilik pasif
        self.create_pkg_btn.setToolTip("Henüz aktif değil — recipe başlarken otomatik oluşturulur")
        btn_row.addWidget(self.create_pkg_btn)

        self.aggregate_btn = _btn("Aggregate CSVs → xlsx", self._run_aggregator)
        self.aggregate_btn.setEnabled(False)
        self.aggregate_btn.setToolTip(
            "CSV ölçümlerini birleştirip xlsx oluşturur.\n"
            "Recipe tamamlandığında otomatik çalışır,\n"
            "ya da buradan manuel tetikleyebilirsiniz.")
        btn_row.addWidget(self.aggregate_btn)
        status_lay.addLayout(btn_row)

        layout.addWidget(status_grp)

        # ── 4. Paket Logu ─────────────────────────────────────────
        log_grp = QGroupBox("Package Log")
        log_lay = QVBoxLayout(log_grp)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(160)
        self.log_edit.setFont(QFont("Consolas", 9))
        log_lay.addWidget(self.log_edit)
        layout.addWidget(log_grp)

        layout.addStretch()

        # İlk yüklemede referansları doldur
        self._refresh_refs()

    def _ref_row(self, form: QFormLayout, label: str) -> QLabel:
        """Dosya referans satırı: yol etiketi + durum göstergesi."""
        row = QHBoxLayout()
        path_lbl = QLabel("—")
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet("color: #888;")
        row.addWidget(path_lbl, stretch=1)
        form.addRow(label, row)
        return path_lbl

    # ── Referans yenileme ─────────────────────────────────────────

    def _refresh_refs(self):
        """ScriptEditorTab ve RecipeTab'dan güncel dosya yollarını çeker."""

        # .tp
        tp = ""
        if self.script_tab:
            tp = self.script_tab.method_edit.text().strip()
        self._set_ref(self.tp_lbl, tp)

        # .scr
        scr = ""
        if self.script_tab:
            scr = self.script_tab.get_current_scr_path()
        self._set_ref(self.scr_lbl, scr)

        # recipe
        recipe = ""
        if self.recipe_tab and hasattr(self.recipe_tab, "_current_recipe_path"):
            recipe = self.recipe_tab._current_recipe_path or ""
        self._set_ref(self.recipe_lbl, recipe)

        self._refresh_status()

    def _set_ref(self, lbl: QLabel, path: str):
        """Referans etiketini günceller, dosya varlığını renkle gösterir."""
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

    # ── Durum güncelleme ──────────────────────────────────────────

    def _refresh_status(self):
        """Mevcut duruma göre durum etiketini günceller."""
        if self._session:
            return  # Aktif oturum varsa dokunma
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

    # ── Browse ────────────────────────────────────────────────────

    def _browse_package_root(self):
        path = open_dir(self, "Paket Kök Dizini Seç", "package_root")
        if path:
            self.package_root_edit.setText(path)
            self._refresh_status()

    # ── Create Package (şimdilik pasif) ───────────────────────────

    def _create_package(self):
        """Şimdilik pasif — ileride manuel tetikleme için."""
        pass

    # ── Ana metot: RecipeTab tarafından çağrılır ──────────────────

    def build_package(self) -> bool:
        """
        Recipe başlamadan önce paketi oluşturur.
        RecipeTab._start_recipe() tarafından çağrılır.

        Aktif session zaten varsa (önceki recipe tamamlanmadan yeni
        başlatma girişimi) tekrar paket oluşturmaz, mevcut session ile
        devam eder.

        Returns
        -------
        bool
            Başarılıysa True, hata veya iptal varsa False.
        """
        # Aktif session varsa yeniden paket oluşturma
        if self._session is not None:
            self._log("⚠ Aktif oturum zaten var — mevcut oturum kullanılıyor.")
            return True

        self._refresh_refs()

        part_number  = self.part_number_edit.text().strip()
        package_root = self.package_root_edit.text().strip()

        # ── Validasyon ────────────────────────────────────────────
        if not part_number:
            QMessageBox.warning(self, "Uyarı",
                "Part Number boş olamaz.\nPackage sekmesinden girin.")
            return False

        if not package_root or not os.path.isdir(package_root):
            QMessageBox.warning(self, "Uyarı",
                "Geçerli bir Package Root dizini seçilmemiş.\n"
                "Package sekmesinden seçin.")
            return False

        scr_path = self.script_tab.get_current_scr_path() if self.script_tab else ""
        if not scr_path or not os.path.isfile(scr_path):
            QMessageBox.warning(self, "Uyarı",
                "Script dosyası (.scr) seçilmemiş.\n"
                "Script Editor sekmesinden bir .scr dosyası yükleyin.")
            return False

        # ── Paketleme ─────────────────────────────────────────────
        try:
            self._session = MeasurementSession.create(package_root, part_number)
            packager = Packager(self._session)

            # .tp
            tp_path = self.script_tab.method_edit.text().strip()
            if tp_path and os.path.isfile(tp_path):
                packager.pack_tp(tp_path)
                self._log(f"✓ .tp kopyalandı: {os.path.basename(tp_path)}")
            else:
                self._log("⚠ .tp dosyası bulunamadı, atlandı.")

            # .scr — CSV yolu measurements/ dizinine yönlendirilir
            packager.pack_script(scr_path)
            self._log(f"✓ .scr kopyalandı ve güncellendi: {os.path.basename(scr_path)}")

            # recipe.json
            recipe_path = ""
            if self.recipe_tab and hasattr(self.recipe_tab, "_current_recipe_path"):
                recipe_path = self.recipe_tab._current_recipe_path or ""
            if recipe_path and os.path.isfile(recipe_path):
                packager.pack_recipe(recipe_path)
                self._log(f"✓ recipe kopyalandı: {os.path.basename(recipe_path)}")
            else:
                self._log("⚠ recipe dosyası bulunamadı, atlandı.")

            packager.finalize()

            # ScriptEditorTab'ı güncelle → yeni .scr'yi yükle
            new_scr = packager.get_packed_scr_path()
            if new_scr and self.script_tab:
                self.script_tab._load_from_path(new_scr)
                self._log(f"✓ Script Editor güncellendi → {new_scr}")

            # Durum güncelle
            session_name = os.path.basename(self._session.session_dir)
            self._set_status(
                f"Aktif: {part_number} / {session_name}",
                _COLOR_RUNNING)
            self.session_dir_lbl.setText(self._session.session_dir)
            self.aggregate_btn.setEnabled(True)

            # LogWriter'ı aç — recipe boyunca her log satırı buraya düşecek
            self._log_writer = LogWriter(self._session)
            self._log_writer.open()

            self._log(f"Paket oluşturuldu: {self._session.session_dir}")
            self.log_signal.emit(f"Paket oluşturuldu: {self._session.session_dir}")
            return True

        except PackagerError as e:
            self._session = None
            QMessageBox.critical(self, "Paket Hatası", str(e))
            self._log(f"✗ Paket hatası: {e}")
            self.log_signal.emit(f"Paket hatası: {e}")
            self._set_status("Hata — paket oluşturulamadı", _COLOR_ERROR)
            return False

        except Exception as e:
            self._session = None
            QMessageBox.critical(self, "Hata", f"Paket oluşturulamadı:\n{e}")
            self._log(f"✗ Beklenmeyen hata: {e}")
            self.log_signal.emit(f"Paket hatası: {e}")
            self._set_status("Hata — paket oluşturulamadı", _COLOR_ERROR)
            return False

    def write_to_log(self, msg: str):
        """
        Dışarıdan (MainWindow log_signal bağlantısı) gelen log satırlarını
        session.log dosyasına yazar.
        """
        if self._log_writer and self._log_writer.is_open:
            self._log_writer.write(msg)

    def on_recipe_completed(self):
        """RecipeTab recipe tamamlandığında çağırır."""
        if self._session:
            self._session.set_status(SessionStatus.COMPLETED)
            self._set_status(
                f"Tamamlandı: {self._session.part_number}", _COLOR_DONE)
            self._log("Oturum tamamlandı.")
            self.log_signal.emit(
                f"Oturum tamamlandı: {self._session.session_dir}")
            if self._log_writer:
                self._log_writer.close()
            # Aggregate çalıştır, sonra sakla/sil sor
            self._run_aggregator()

    # ── Aggregator ────────────────────────────────────────────────

    def _run_aggregator(self):
        """
        Aggregator'ı GUI moduyla çalıştırır.
        - COMPLETED → direkt çalışır
        - ABORTED   → önce onay dialog'u gösterir
        - Manuel buton → aynı akış
        """
        if not self._session:
            QMessageBox.warning(self, "Aggregator",
                "Aktif oturum yok.\nÖnce bir recipe çalıştırın.")
            return

        # ABORTED ise kullanıcıdan onay al
        if self._session.status == SessionStatus.ABORTED:
            reply = QMessageBox.question(
                self, "Kısmi Veri",
                "Recipe durduruldu — CSV dosyaları eksik olabilir.\n\n"
                "Mevcut ölçümlerle xlsx oluşturulsun mu?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._log("Aggregate iptal edildi (kullanıcı onaylamadı).")
                return

        aggregator = Aggregator(self._session)
        xlsx_path = aggregator.run(parent=self)  # saat offset dialog'u burada açılır

        if xlsx_path:
            self._log(f"✓ Aggregate tamamlandı: {os.path.basename(xlsx_path)}")
            self.log_signal.emit(f"Aggregate tamamlandı: {xlsx_path}")
        else:
            self._log("⚠ Aggregate iptal edildi veya başarısız.")

        # Aggregate sonucu ne olursa olsun sakla/sil sor
        self._ask_keep_session()


    def on_recipe_aborted(self):
        """RecipeTab recipe durdurulduğunda çağırır."""
        if self._session:
            self._session.set_status(SessionStatus.ABORTED)
            self._set_status(
                f"Durduruldu: {self._session.part_number}", _COLOR_ERROR)
            self._log("Oturum durduruldu.")
            self.log_signal.emit(
                f"Oturum durduruldu: {self._session.session_dir}")
            if self._log_writer:
                self._log_writer.close()
            # Aggregate otomatik çalıştır (kısmi veri onayı içinde sorulur)
            self._run_aggregator()

    def _ask_keep_session(self):
        """
        Oturum dizinini sakla veya sil.
        Aggregate tamamlandıktan sonra çağrılır.
        Kullanıcı 'Hayır' derse session_dir tamamen silinir.
        Her iki durumda da _session = None yapılır.
        """
        if not self._session:
            return

        session_dir  = self._session.session_dir
        part_number  = self._session.part_number
        status       = self._session.status.value

        reply = QMessageBox.question(
            self,
            "Oturumu Sakla",
            f"Oturum: {part_number}  [{status}]\n"
            f"{session_dir}\n\n"
            f"Bu oturum kaydedilsin mi?\n"
            f"'Hayır' seçerseniz tüm dosyalar silinir.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.No:
            try:
                shutil.rmtree(session_dir)
                self._log(f"Oturum silindi: {session_dir}")
                self.log_signal.emit(f"Oturum silindi: {session_dir}")
            except Exception as e:
                self._log(f"✗ Dizin silinemedi: {e}")
                QMessageBox.warning(self, "Hata", f"Dizin silinemedi:\n{e}")
        else:
            self._log(f"Oturum saklandı: {session_dir}")

        # Her durumda session'ı kapat — yeni recipe yeni session açar
        self._session    = None
        self._log_writer = None
        self.aggregate_btn.setEnabled(False)
        self.session_dir_lbl.setText("")
        self._set_status("Hazır — yeni recipe başlatılabilir", _COLOR_READY)

    def get_session(self) -> MeasurementSession:
        """Aktif oturumu döndürür. Yoksa None."""
        return self._session

    # ── Log yardımcısı ────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_edit.append(msg)
        if self._log_writer and self._log_writer.is_open:
            self._log_writer.write(msg)
