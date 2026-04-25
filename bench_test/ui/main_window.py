from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from bench_test.dropview.controller import DropViewController
from bench_test.measurement.session import MeasurementSession
from bench_test.measurement.session_context import SessionContext
from bench_test.ui.tabs.log_tab import LogTab
from bench_test.ui.tabs.manual_tab import ManualControlTab
from bench_test.ui.tabs.measurement_setup_tab import MeasurementSetupTab
from bench_test.ui.tabs.recipe_tab import RecipeTab
from bench_test.ui.tabs.viewer_tab import ViewerTab
from bench_test.valve.injector import InjectorValveController
from bench_test.valve.multiport import ValveController


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bench Test Controller v1.0")
        self.setMinimumSize(1200, 900)

        self.ctrl_a = ValveController()
        self.ctrl_b = InjectorValveController()
        self.dv_ctrl = DropViewController(self)
        self._ctx = SessionContext()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)

        self.sim_banner = QLabel(
            "Simulation Mode - Valve connection missing, valve steps will be skipped"
        )
        self.sim_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sim_banner.setStyleSheet(
            "background-color: #E65100; color: white; "
            "font-weight: bold; font-size: 13px; padding: 6px;"
        )
        self.sim_banner.setVisible(False)
        layout.addWidget(self.sim_banner)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        self.manual_tab = ManualControlTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.recipe_tab = RecipeTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.recipe_tab._main_window = self
        self.setup_tab = MeasurementSetupTab(recipe_tab=self.recipe_tab)
        self.viewer_tab = ViewerTab(self.setup_tab.package_panel)
        self.log_tab = LogTab()

        tabs.addTab(self.manual_tab, "Manual Control")
        tabs.addTab(self.setup_tab, "Measurement Setup")
        tabs.addTab(self.recipe_tab, "Recipe Control")
        tabs.addTab(self.log_tab, "Full Log")
        tabs.addTab(self.viewer_tab, "Graphics Viewer")

        self.recipe_tab.package_tab = self.setup_tab
        if self.recipe_tab._current_recipe_path:
            self.setup_tab.package_panel.set_recipe_ref(
                self.recipe_tab._current_recipe_path
            )

        for tab in [
            self.manual_tab,
            self.setup_tab,
            self.recipe_tab,
            self.viewer_tab,
        ]:
            tab.log_signal.connect(self.log_tab.append)

        self.recipe_tab.log_signal.connect(self.setup_tab.write_to_log)
        self.setup_tab.sm.aggregate_done.connect(self._on_aggregate_done)
        self.setup_tab.sm.log_signal.connect(self.log_tab.append)
        self.setup_tab.sm.state_changed.connect(self._on_sm_state_changed)
        self.viewer_tab.session_loaded.connect(self._on_browse_session_loaded)
        self.setup_tab.package_panel.clear_session_requested.connect(
            self._on_clear_session
        )
        self.viewer_tab.delay_changed.connect(
            self.setup_tab.script_panel.set_response_delay
        )

        self.dv_ctrl.start_polling()

    def switch_display(self, session, mode: str, history_sessions=None):
        """
        Tum sekmeleri verilen session'a gore yeniden render eder.
        mode: "empty" | "active" | "archived" | "history"
        """
        self._ctx.switch(session, mode)
        self._clear_all_tabs()

        if mode == "history":
            self.viewer_tab.render_history(history_sessions or [])
            self.log_tab.render_history(history_sessions or [])
            return

        if session is not None:
            self.viewer_tab.render_session(session, live=(mode == "active"))
            self.log_tab.render_session(session)
            self.recipe_tab.render_session(session)
            self.setup_tab.render_session(session)

    def _clear_all_tabs(self):
        self.viewer_tab.clear()
        self.log_tab.clear()
        self.recipe_tab.clear(stop_runner=False)
        self.setup_tab.package_panel.clear_display()
        self.setup_tab.method_panel.clear()
        self.setup_tab.script_panel.clear()

    def _on_sm_state_changed(self, state: str):
        """StateMachine gecislerinde context'i guncelle."""
        if state == "ready":
            session = self.setup_tab.package_panel.get_session()
            self._ctx.set_active(session)
            self.switch_display(session, "active")
        elif state == "idle":
            self._ctx.clear_active()

    def _on_aggregate_done(self, xlsx: str):
        """Aggregate tamamlandiysa sadece live moddaki Viewer'i yenile."""
        if self._ctx.is_live:
            self.viewer_tab._refresh()

    def _on_browse_session_loaded(self, session_dir: str):
        """Viewer'dan gecmis session secildiginde tum sekmeleri guncelle."""
        try:
            session = MeasurementSession.load(session_dir)
        except Exception:
            return
        self.switch_display(session, "archived")

    def _on_clear_session(self):
        """Oturumu Temizle -> display'i bosalt, aktif pipeline etkilenmez."""
        if self._ctx.display_mode == "active":
            return
        self.switch_display(None, "empty")
        self.log_tab.append("Oturum temizlendi.")

    def set_simulation_mode(self, active: bool, missing: str = ""):
        self.sim_banner.setVisible(active)
        if active:
            self.setWindowTitle(
                f"Bench Test Controller v1.0  [Simulation - {missing} missing]"
            )
        else:
            self.setWindowTitle("Bench Test Controller v1.0")

    def closeEvent(self, event):
        if self.recipe_tab.recipe_runner and self.recipe_tab.recipe_runner.is_alive():
            reply = QMessageBox.question(
                self, "Confirm", "Recipe is running. Stop and exit?"
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.recipe_tab.stop_event.set()
        self.dv_ctrl.stop_polling()
        self.ctrl_a.disconnect()
        self.ctrl_b.disconnect()
        event.accept()
