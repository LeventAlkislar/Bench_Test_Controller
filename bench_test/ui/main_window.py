# bench_test/ui/main_window.py
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QMessageBox

from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.ui.tabs.connection_tab import ConnectionTab
from bench_test.ui.tabs.manual_tab import ManualControlTab
from bench_test.ui.tabs.script_editor_tab import ScriptEditorTab
from bench_test.ui.tabs.recipe_tab import RecipeTab
from bench_test.ui.tabs.log_tab import LogTab


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

        self.conn_tab    = ConnectionTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.manual_tab  = ManualControlTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl)
        self.script_tab  = ScriptEditorTab()
        self.recipe_tab  = RecipeTab(self.ctrl_a, self.ctrl_b, self.dv_ctrl, self.script_tab)
        self.log_tab     = LogTab()

        tabs.addTab(self.conn_tab,   "Connection")
        tabs.addTab(self.manual_tab, "Manual Control")
        tabs.addTab(self.script_tab, "Script Editor")
        tabs.addTab(self.recipe_tab, "Recipe Control")
        tabs.addTab(self.log_tab,    "Full Log")

        self.script_tab._restore_last_scr()

        for tab in [self.conn_tab, self.manual_tab, self.script_tab, self.recipe_tab]:
            tab.log_signal.connect(self.log_tab.append)

        self.dv_ctrl.start_polling()

    def closeEvent(self, event):
        if self.recipe_tab.recipe_runner and self.recipe_tab.recipe_runner.is_alive():
            reply = QMessageBox.question(self, "Confirm", "Recipe is running. Stop and exit?")
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.recipe_tab.stop_event.set()
        self.dv_ctrl.stop_polling()
        self.ctrl_a.disconnect()
        self.ctrl_b.disconnect()
        event.accept()