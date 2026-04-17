# bench_test/ui/tabs/measurement_setup_tab.py
# -*- coding: utf-8 -*-
"""
MeasurementSetupTab
===================
Üç paneli yan yana barındıran ana sekme:

  Kolon 1 — PackagePanel      : Part Number, Root, File References
  Kolon 2 — MethodEditorPanel : .tp yükleme ve parametre görüntüleme
  Kolon 3 — ScriptParamsPanel : Repeat, Wait ve otomatik yollar

Sinyal akışı:
  PackagePanel.part_number_changed → MethodEditorPanel.set_sensor_sample
  PackagePanel.csv_path_changed    → ScriptParamsPanel.set_csv_path
  MethodEditorPanel.tp_loaded      → ScriptParamsPanel.set_method_path
  MethodEditorPanel.tp_loaded      → PackagePanel.set_tp_ref

Dışa açık arayüz (RecipeTab tarafından kullanılır):
  build_package(recipe_path) → bool
  write_to_log(msg)
  on_recipe_completed()
  on_recipe_aborted()
  get_session()
  sm  (SessionStateMachine — ViewerTab için)
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import pyqtSignal

from bench_test.ui.tabs.package_panel       import PackagePanel
from bench_test.ui.tabs.method_editor_panel import MethodEditorPanel
from bench_test.ui.tabs.script_params_panel import ScriptParamsPanel


def _divider() -> QFrame:
    """Kolonlar arası dikey ayırıcı çizgi."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class MeasurementSetupTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, recipe_tab=None, parent=None):
        super().__init__(parent)
        self.recipe_tab = recipe_tab

        # ── Panel örnekleri ───────────────────────────────────────
        self.package_panel = PackagePanel()
        self.method_panel  = MethodEditorPanel()
        self.script_panel  = ScriptParamsPanel()

        self._connect_signals()
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(4, 4, 4, 4)

        for panel, stretch in [
            (self.package_panel, 3),
            (_divider(),         0),
            (self.method_panel,  4),
            (_divider(),         0),
            (self.script_panel,  3),
        ]:
            if isinstance(panel, QFrame):
                root.addWidget(panel)
            else:
                panel.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Expanding,
                )
                root.addWidget(panel, stretch=stretch)

    # ── Sinyal bağlantıları ───────────────────────────────────────

    def _connect_signals(self):
        # Part Number → Sensor/Sample otomatik doldurma
        self.package_panel.part_number_changed.connect(
            lambda part: self.method_panel.set_sensor_sample(sensor="", sample=part)
        )

        # CSV yolu → ScriptParamsPanel
        self.package_panel.csv_path_changed.connect(
            self.script_panel.set_csv_path
        )

        # .tp yüklenince → ScriptParamsPanel method yolu
        self.method_panel.tp_loaded.connect(
            self.script_panel.set_method_path
        )

        # .tp yüklenince → PackagePanel file reference
        self.method_panel.tp_loaded.connect(
            self.package_panel.set_tp_ref
        )

        # PackagePanel log sinyalini yukarı ilet
        self.package_panel.log_signal.connect(self.log_signal)

        # recipe_tab referansı varsa recipe dosyasını izle
        if self.recipe_tab and hasattr(self.recipe_tab, "scr_changed"):
            pass  # gerekirse ileride eklenir

    # ── RecipeTab arayüzü ─────────────────────────────────────────

    def build_package(self, recipe_path: str = "") -> bool:
        """
        RecipeTab._start_recipe() tarafından çağrılır.
        Üç panelden veri toplayıp PackagePanel.build_package()'a iletir.
        """
        # recipe_path yoksa recipe_tab'dan dene
        if not recipe_path and self.recipe_tab:
            recipe_path = getattr(
                self.recipe_tab, "_current_recipe_path", ""
            ) or ""

        tp_path    = self.method_panel.get_current_path()
        scr_params = self.script_panel.get_scr_params()

        # CSV yolu henüz placeholder ise güncelle
        if not scr_params.get("output_csv"):
            self.package_panel._update_csv_path()
            scr_params = self.script_panel.get_scr_params()

        return self.package_panel.build_package(
            tp_path    = tp_path,
            scr_params = scr_params,
            recipe_path= recipe_path,
        )

    def write_to_log(self, msg: str):
        self.package_panel.write_to_log(msg)

    def on_recipe_completed(self):
        self.package_panel.on_recipe_completed()

    def on_recipe_aborted(self):
        self.package_panel.on_recipe_aborted()

    def get_session(self):
        return self.package_panel.get_session()

    @property
    def sm(self):
        """ViewerTab ve MainWindow SessionStateMachine'e buradan erişir."""
        return self.package_panel.sm

    def restore_from_session(self, session_dir):
        """Geçmiş session'daki .tp ve .scr dosyalarını panellere yükler."""
        from pathlib import Path
        import json as _json

        path = Path(session_dir)
        session_json = path / "session.json"
        if not session_json.is_file():
            return

        try:
            with open(session_json, encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            return

        files = data.get("files", {})

        # .tp → MethodEditorPanel
        tp_rel = files.get("tp", "")
        if tp_rel:
            tp_abs = path / tp_rel
            if tp_abs.is_file():
                self.method_panel.load_from_path(str(tp_abs))

        # .scr → ScriptParamsPanel
        scr_rel = files.get("script", "")
        if scr_rel:
            scr_abs = path / scr_rel
            if scr_abs.is_file():
                self.script_panel.restore_from_scr(str(scr_abs))