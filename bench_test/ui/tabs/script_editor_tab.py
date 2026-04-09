# bench_test/ui/tabs/script_editor_tab.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox,
    QDoubleSpinBox, QTextEdit, QGroupBox, QMessageBox
)
from PyQt6.QtCore import pyqtSignal

from bench_test.utils.paths import open_file, save_file, get_last, remember
from bench_test.dropview.script_generator import generate_dropview_script


def _btn(label, slot):
    b = QPushButton(label)
    b.clicked.connect(slot)
    return b


class ScriptEditorTab(QWidget):
    log_signal  = pyqtSignal(str)
    scr_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._current_path = ""
        self._modified     = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Yol ───────────────────────────────────────────────
        path_grp = QGroupBox("Script Dosyası")
        path_lay = QHBoxLayout(path_grp)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("Henüz kaydedilmedi...")
        path_lay.addWidget(self.path_edit)
        path_lay.addWidget(_btn("New",     self._new))
        path_lay.addWidget(_btn("Load",    self._load))
        path_lay.addWidget(_btn("Save",    self._save))
        path_lay.addWidget(_btn("Save As", self._save_as))
        layout.addWidget(path_grp)

        # ── Parametreler ───────────────────────────────────────
        param_grp = QGroupBox("Parametreler")
        form      = QFormLayout(param_grp)

        method_row = QHBoxLayout()
        self.method_edit = QLineEdit()
        self.method_edit.setPlaceholderText("C:\\...\\method.tp")
        self.method_edit.textChanged.connect(self._mark_modified)
        method_row.addWidget(self.method_edit)
        method_row.addWidget(_btn("Browse", self._browse_method))
        form.addRow("Method (.tp):", method_row)

        csv_row = QHBoxLayout()
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("C:\\...\\output.csv")
        self.csv_edit.textChanged.connect(self._mark_modified)
        csv_row.addWidget(self.csv_edit)
        csv_row.addWidget(_btn("Browse", self._browse_csv))
        form.addRow("CSV Çıktı:", csv_row)

        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 99999)
        self.repeat_spin.setValue(1440)
        self.repeat_spin.valueChanged.connect(self._mark_modified)
        form.addRow("Tekrar Sayısı:", self.repeat_spin)

        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(1.0, 3600.0)
        self.wait_spin.setValue(47.0)
        self.wait_spin.setSuffix(" sn")
        self.wait_spin.valueChanged.connect(self._mark_modified)
        form.addRow("Bekleme Süresi:", self.wait_spin)

        layout.addWidget(param_grp)

        # ── XML Önizleme ───────────────────────────────────────
        prev_grp = QGroupBox("XML Önizleme")
        prev_lay = QVBoxLayout(prev_grp)
        self.xml_preview = QTextEdit()
        self.xml_preview.setReadOnly(True)
        self.xml_preview.setMaximumHeight(200)
        prev_lay.addWidget(self.xml_preview)
        prev_lay.addWidget(_btn("Önizlemeyi Güncelle", self._update_preview))
        layout.addWidget(prev_grp)
        layout.addStretch()

    def _mark_modified(self):
        self._modified = True

    def _update_preview(self):
        if not self.method_edit.text() or not self.csv_edit.text():
            return
        import tempfile, os
        try:
            with tempfile.NamedTemporaryFile(suffix=".scr", delete=False) as tmp:
                tmp_path = tmp.name
            generate_dropview_script(
                method_file=self.method_edit.text(),
                output_csv=self.csv_edit.text(),
                repeat_times=self.repeat_spin.value(),
                wait_ms=int(self.wait_spin.value() * 1000),
                output_script_path=tmp_path
            )
            with open(tmp_path, "r", encoding="utf-8") as f:
                self.xml_preview.setPlainText(f.read())
            os.unlink(tmp_path)
        except Exception as e:
            self.xml_preview.setPlainText(f"Önizleme hatası: {e}")

    def _restore_last_scr(self):
        last = get_last("scr_last_used", "")
        if last and __import__("os").path.isfile(last):
            try:
                self._load_from_path(last)
                self.log_signal.emit(f"Son script yüklendi: {last}")
            except Exception:
                pass

    def _load_from_path(self, path: str):
        import xml.etree.ElementTree as ET
        tree    = ET.parse(path)
        root    = tree.getroot()
        actions = root.find("actions")

        load = actions.find(".//action[@type='LOADMETHOD']/file")
        if load is not None:
            self.method_edit.setText(load.text or "")

        exp = actions.find(".//action[@type='EXPORTCURVES']/file")
        if exp is not None:
            self.csv_edit.setText(exp.text or "")

        times = actions.find(".//action[@type='REPEAT']/times")
        if times is not None:
            self.repeat_spin.setValue(int(times.text))

        wait = actions.find(".//action[@type='WAIT']")
        if wait is not None:
            ms = int(wait.get("timeMS", 47000))
            self.wait_spin.setValue(ms / 1000)

        self._current_path = path
        self._modified     = False
        self.path_edit.setText(path)
        remember("scr_last_used", path)
        self.scr_changed.emit(path)
        self._update_preview()

    def _new(self):
        self._current_path = ""
        self._modified     = False
        self.path_edit.setText("")
        self.method_edit.setText("")
        self.csv_edit.setText("")
        self.repeat_spin.setValue(1440)
        self.wait_spin.setValue(47.0)
        self.xml_preview.clear()
        self.log_signal.emit("Yeni script formu açıldı.")

    def _load(self):
        path = open_file(self, "Script Dosyası Aç", "scr_open_dir",
                         "Script dosyası (*.scr);;Tüm dosyalar (*.*)")
        if not path:
            return
        try:
            self._load_from_path(path)
            if self.method_edit.text():
                remember("method_dir", self.method_edit.text())
            if self.csv_edit.text():
                remember("csv_output_dir", self.csv_edit.text())
            self.log_signal.emit(f"Script yüklendi: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Dosya yüklenemedi: {e}")

    def _save(self):
        if not self._current_path:
            self._save_as()
            return
        self._write_scr(self._current_path)

    def _save_as(self):
        path = save_file(self, "Script Dosyasını Kaydet", "scr_save_dir",
                         "Script dosyası (*.scr);;Tüm dosyalar (*.*)", ".scr")
        if not path:
            return
        self._current_path = path
        self.path_edit.setText(path)
        self._write_scr(path)

    def _write_scr(self, path: str):
        if not self.method_edit.text():
            QMessageBox.warning(self, "Uyarı", "Method dosyası seçilmedi.")
            return
        if not self.csv_edit.text():
            QMessageBox.warning(self, "Uyarı", "CSV çıktı yolu girilmedi.")
            return
        try:
            generate_dropview_script(
                method_file=self.method_edit.text(),
                output_csv=self.csv_edit.text(),
                repeat_times=self.repeat_spin.value(),
                wait_ms=int(self.wait_spin.value() * 1000),
                output_script_path=path
            )
            self._modified = False
            self.path_edit.setText(path)
            remember("scr_last_used", path)
            self.scr_changed.emit(path)
            self.log_signal.emit(f"Script kaydedildi: {path}")
            self._update_preview()
            QMessageBox.information(self, "Başarıldı", f"Script kaydedildi:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Kaydetme hatası: {e}")

    def _browse_method(self):
        p = open_file(self, "Method Dosyası Seç", "method_dir",
                      "DropView Method (*.tp);;Tüm dosyalar (*.*)")
        if p:
            self.method_edit.setText(p)

    def _browse_csv(self):
        p = save_file(self, "CSV Çıktı Dosyası", "csv_output_dir",
                      "CSV dosyası (*.csv)", ".csv")
        if p:
            self.csv_edit.setText(p)

    def get_current_scr_path(self) -> str:
        return self._current_path