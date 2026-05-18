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

from bench_test.measurement.session import SessionStatus
from bench_test.ui.tabs.package_panel       import PackagePanel
from bench_test.ui.tabs.method_editor_panel import MethodEditorPanel
from bench_test.ui.tabs.script_params_panel import ScriptParamsPanel
from bench_test.utils.paths import remember_value


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
        self.method_panel.set_mode_provider(self.script_panel.get_measurement_mode)

        self._connect_signals()
        self._build_ui()
        self._sync_restored_state()

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
        self.script_panel.experiment_params_changed.connect(
            self._on_script_experiment_params_changed
        )
        self.script_panel.measurement_mode_changed.connect(
            self.method_panel.set_measurement_mode
        )

        # recipe_tab referansı varsa recipe dosyasını izle
        if self.recipe_tab and hasattr(self.recipe_tab, "scr_changed"):
            pass  # gerekirse ileride eklenir

    def _sync_restored_state(self):
        """Acilista constructor'larda yuklenen alanlari tekrar senkronize eder."""
        part_number = self.package_panel.part_number_edit.text().strip()
        if part_number:
            self.method_panel.set_sensor_sample(sensor="", sample=part_number)

        self.package_panel._update_csv_path()
        self.package_panel._refresh_status()
        self.method_panel.set_measurement_mode(
            self.script_panel.get_measurement_mode()
        )

        tp_path = self.method_panel.get_current_path()
        if tp_path:
            self.script_panel.set_method_path(tp_path)
            self.package_panel.set_tp_ref(tp_path)

        recipe_path = ""
        if self.recipe_tab:
            recipe_path = getattr(self.recipe_tab, "_current_recipe_path", "") or ""
        if recipe_path:
            self.package_panel.set_recipe_ref(recipe_path)

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
        experiment_params = self.get_experiment_params()

        # CSV yolu henüz placeholder ise güncelle
        if not scr_params.get("output_csv"):
            self.package_panel._update_csv_path()
            scr_params = self.script_panel.get_scr_params()

        return self.package_panel.build_package(
            tp_path    = tp_path,
            scr_params = scr_params,
            recipe_path= recipe_path,
            experiment_params=experiment_params,
            measurement_mode=scr_params.get("measurement_mode"),
        )

    def write_to_log(self, msg: str):
        self.package_panel.write_to_log(msg)

    def on_recipe_completed(self):
        self.package_panel.on_recipe_completed()

    def on_recipe_aborted(self):
        self.package_panel.on_recipe_aborted()

    def get_session(self):
        return self.package_panel.get_session()

    def get_experiment_params(self) -> dict:
        """Script panel ve viewer delay alanlarini tek session snapshot'inda toplar."""
        params = self.script_panel.get_experiment_params()
        mw = self._get_main_window()
        if mw and hasattr(mw, "viewer_tab"):
            params.update(mw.viewer_tab.get_response_delay_params())
        return params

    def _get_main_window(self):
        w = self.parent()
        while w:
            if hasattr(w, "switch_display"):
                return w
            w = w.parent() if hasattr(w, "parent") else None
        return None

    def _on_script_experiment_params_changed(self, _params: dict):
        self._persist_experiment_params(self.get_experiment_params())

    def on_response_delay_changed(self, minutes: int, seconds: int):
        """Viewer delay degisince label'i gunceller ve session/global kaydi yapar."""
        self.script_panel.set_response_delay(minutes, seconds)
        self._persist_experiment_params(self.get_experiment_params())

    def _persist_experiment_params(self, params: dict):
        """Moda gore experiment_params'i session.json ve/veya last_paths'a yazar."""
        mw = self._get_main_window()
        ctx = getattr(mw, "_ctx", None) if mw is not None else None
        mode = getattr(ctx, "display_mode", "empty") if ctx is not None else "empty"

        if mode in ("empty", "active"):
            self._remember_experiment_defaults(params)

        session = None
        if ctx is not None and mode in ("active", "archived"):
            session = ctx.displayed_session
        if session is None and mode == "active":
            session = self.package_panel.get_session()

        if mode == "history":
            return
        if session is None:
            return

        session.experiment_params = dict(params or {})
        try:
            session.save()
        except Exception as e:
            self.log_signal.emit(f"Experiment params could not be saved: {e}")

        current_session = self.package_panel.get_session()
        if (
            current_session is not None
            and current_session.session_dir == session.session_dir
        ):
            current_session.experiment_params = dict(params or {})

        if mw and hasattr(mw, "viewer_tab"):
            viewer_session = getattr(mw.viewer_tab, "_session", None)
            if (
                viewer_session is not None
                and viewer_session.session_dir == session.session_dir
            ):
                viewer_session.experiment_params = dict(params or {})
                mw.viewer_tab._refresh()

        if mw and hasattr(mw, "recipe_tab"):
            mw.recipe_tab._display_experiment_params = dict(params or {})
            mw.recipe_tab._refresh_table()

    def _remember_experiment_defaults(self, params: dict):
        """Yeni/aktif akista son kullanici tercihlerini last_paths.json'a yazar."""
        for key in (
            "temperature_c",
            "flow_rate_ml_min",
            "response_delay_min",
            "response_delay_sec",
            "port_glucose",
        ):
            if key in params:
                remember_value(key, params[key])

    @property
    def sm(self):
        """ViewerTab ve MainWindow SessionStateMachine'e buradan erişir."""
        return self.package_panel.sm

    def restore_from_session(self, session_dir):
        """Geçmiş session'daki .tp ve .scr dosyalarını panellere yükler."""
        from pathlib import Path
        import json as _json
        import os
        
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
        measurement_mode = data.get("measurement_mode", "")
        if measurement_mode:
            self.script_panel.set_measurement_mode(measurement_mode)
            self.method_panel.set_measurement_mode(measurement_mode)

        # Part Number → PackagePanel
        part_number = data.get("part_number", "")
        if part_number:
            self.package_panel.part_number_edit.setText(part_number)

        session_id = data.get("session_id", "") or os.path.basename(session_dir)
        if session_id:
            self.package_panel.session_id_edit.setText(session_id)

        # .tp → MethodEditorPanel
        tp_rel = files.get("tp", "")
        if tp_rel:
            tp_abs = path / tp_rel
            if tp_abs.is_file():
                self.method_panel.load_from_path(str(tp_abs), persist=False)

        # .scr → ScriptParamsPanel
        scr_rel = files.get("script", "")
        if scr_rel:
            scr_abs = path / scr_rel
            if scr_abs.is_file():
                self.script_panel.restore_from_scr(
                    str(scr_abs), log_fn=self.log_signal.emit
                )

        experiment_params = data.get("experiment_params") or {}
        if experiment_params:
            self.script_panel.restore_experiment_params(experiment_params)
            self.script_panel.set_response_delay(
                int(experiment_params.get("response_delay_min", 0)),
                int(experiment_params.get("response_delay_sec", 0)),
            )
        else:
            self.script_panel.restore_default_experiment_params()

    def render_session(self, session) -> None:
        """
        MainWindow.switch_display() tarafindan cagrilir.
        Session snapshot'indan panelleri doldurur.
        """
        import os

        self.package_panel.part_number_edit.setText(session.part_number)
        self.package_panel.session_id_edit.setText(
            os.path.basename(session.session_dir)
        )
        self.package_panel.session_dir_lbl.setText(session.session_dir)
        measurement_mode = getattr(session, "measurement_mode", "") or ""
        if measurement_mode:
            self.script_panel.set_measurement_mode(measurement_mode)
            self.method_panel.set_measurement_mode(measurement_mode)

        tp_path = session.get_file_path("tp")
        if not tp_path:
            candidate = os.path.join(
                session.session_dir, "measurement_parameters", "method.tp"
            )
            tp_path = candidate if os.path.isfile(candidate) else ""
        if tp_path and os.path.isfile(tp_path):
            self.method_panel.load_from_path(tp_path, persist=False)

        scr_path = session.get_file_path("script")
        if not scr_path:
            candidate = os.path.join(
                session.session_dir, "measurement_parameters", "dropview_script.scr"
            )
            scr_path = candidate if os.path.isfile(candidate) else ""
        if scr_path and os.path.isfile(scr_path):
            self.script_panel.restore_from_scr(
                scr_path, log_fn=self.log_signal.emit
            )

        experiment_params = getattr(session, "experiment_params", {}) or {}
        if experiment_params:
            self.script_panel.restore_experiment_params(experiment_params)
            self.script_panel.set_response_delay(
                int(experiment_params.get("response_delay_min", 0)),
                int(experiment_params.get("response_delay_sec", 0)),
            )
        else:
            self.script_panel.restore_default_experiment_params()
            mw = self._get_main_window()
            if mw and hasattr(mw, "viewer_tab"):
                mw.viewer_tab.restore_default_response_delay()
                delay_params = mw.viewer_tab.get_response_delay_params()
                self.script_panel.set_response_delay(
                    delay_params["response_delay_min"],
                    delay_params["response_delay_sec"],
                )
            self.log_signal.emit(
                "Session has no experiment_params; using current defaults."
            )

        self.package_panel.set_tp_ref(tp_path or "")
        self.package_panel.set_scr_ref(scr_path or "")
        recipe_path = session.get_file_path("recipe") or ""
        self.package_panel.set_recipe_ref(recipe_path)

        if session.status == SessionStatus.IN_PROGRESS:
            self.package_panel._set_status(
                f"Aktif: {session.part_number} / {os.path.basename(session.session_dir)}",
                "#2196F3",
            )
            self.package_panel.aggregate_btn.setEnabled(True)
        else:
            self.package_panel._set_status(
                f"[{session.status.value}] {session.part_number}",
                "#888888",
            )
