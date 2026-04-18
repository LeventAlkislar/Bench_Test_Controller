# bench_test/ui/main_window.py
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QMessageBox

from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.ui.tabs.connection_tab import ConnectionTab
from bench_test.ui.tabs.manual_tab import ManualControlTab
from bench_test.ui.tabs.measurement_setup_tab import MeasurementSetupTab
from bench_test.ui.tabs.recipe_tab import RecipeTab
from bench_test.ui.tabs.log_tab import LogTab
from bench_test.ui.tabs.viewer_tab import ViewerTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bench Test Controller v1.0")
        self.setMinimumSize(1200, 900)

        self.ctrl_a  = ValveController()
        self.ctrl_b  = InjectorValveController()
        self.dv_ctrl = DropViewController(self)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self.conn_tab   = ConnectionTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.manual_tab = ManualControlTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.recipe_tab = RecipeTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.setup_tab  = MeasurementSetupTab(recipe_tab=self.recipe_tab)
        self.viewer_tab = ViewerTab(self.setup_tab.package_panel)
        self.log_tab    = LogTab()

        tabs.addTab(self.conn_tab,    "Connection")
        tabs.addTab(self.manual_tab,  "Manual Control")
        tabs.addTab(self.setup_tab,   "Measurement Setup")
        tabs.addTab(self.recipe_tab,  "Recipe Control")
        tabs.addTab(self.log_tab,     "Full Log")
        tabs.addTab(self.viewer_tab,  "Graphics Viewer")

        self.recipe_tab.package_tab = self.setup_tab

        for tab in [self.conn_tab, self.manual_tab,
                    self.setup_tab, self.recipe_tab, self.viewer_tab]:
            tab.log_signal.connect(self.log_tab.append)

        self.recipe_tab.log_signal.connect(self.setup_tab.write_to_log)
        self.setup_tab.sm.aggregate_done.connect(self.viewer_tab._refresh)
        self.setup_tab.sm.log_signal.connect(self.log_tab.append)
        self.viewer_tab.session_loaded.connect(self._on_session_loaded)
        self.setup_tab.sm.state_changed.connect(
            self.viewer_tab._on_session_state_changed)
        self.setup_tab.package_panel.clear_session_requested.connect(   # ← YENİ
            self._on_clear_session)
        self.viewer_tab.delay_changed.connect(
            self.setup_tab.script_panel.set_response_delay)

        self.dv_ctrl.start_polling()

    def _on_clear_session(self):
        """Tüm tabları açılış haline getirir."""
        self.setup_tab.package_panel.clear()
        self.recipe_tab.clear()
        self.setup_tab.method_panel.clear()
        self.setup_tab.script_panel.clear()
        self.log_tab.clear()
        self.viewer_tab.clear()
        self.log_tab.append("Oturum temizlendi.")

    def _on_session_loaded(self, session_dir: str):
        from pathlib import Path
        path = Path(session_dir)
        self.log_tab.restore_from_session(path)
        self.recipe_tab.restore_from_session(path)
        self.setup_tab.restore_from_session(path)

    def closeEvent(self, event):
        if self.recipe_tab.recipe_runner and self.recipe_tab.recipe_runner.is_alive():
            reply = QMessageBox.question(
                self, "Confirm", "Recipe is running. Stop and exit?")
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.recipe_tab.stop_event.set()
        self.dv_ctrl.stop_polling()
        self.ctrl_a.disconnect()
        self.ctrl_b.disconnect()
        event.accept()