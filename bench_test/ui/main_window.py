from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QStyle,
    QStyleOptionTab,
    QStylePainter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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
from bench_test.version import window_title


class RightAlignedTabBar(QTabBar):
    def __init__(self):
        super().__init__()
        self.setExpanding(False)
        self._spacer_index = -1
        self._spacer_width = 0

    def set_spacer_index(self, index: int):
        self._spacer_index = index
        self.updateGeometry()
        self.update()

    def set_spacer_width(self, width: int):
        width = max(0, width)
        if self._spacer_width == width:
            return
        self._spacer_width = width
        self.updateGeometry()
        self.update()

    def tabSizeHint(self, index: int):
        size = super().tabSizeHint(index)
        if index == self._spacer_index:
            return QSize(self._spacer_width, size.height())
        return size

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionTab()
        for index in range(self.count()):
            if index == self._spacer_index:
                continue
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)


class RightAlignedLogTabWidget(QTabWidget):
    def __init__(self):
        super().__init__()
        self._tab_bar = RightAlignedTabBar()
        self.setTabBar(self._tab_bar)
        self._spacer_index = -1

    def add_right_aligned_tab(self, widget: QWidget, label: str) -> int:
        if self._spacer_index < 0:
            self._spacer_index = self.addTab(QWidget(), "")
            self.setTabEnabled(self._spacer_index, False)
            self._tab_bar.set_spacer_index(self._spacer_index)
        index = self.addTab(widget, label)
        self._update_spacer_width()
        return index

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_spacer_width()

    def _update_spacer_width(self):
        if self._spacer_index < 0:
            return
        tab_bar = self.tabBar()
        fixed_width = 0
        for index in range(tab_bar.count()):
            if index == self._spacer_index:
                continue
            fixed_width += tab_bar.tabSizeHint(index).width()
        self._tab_bar.set_spacer_width(self.width() - fixed_width)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(window_title())
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

        self.part_banner = QLabel("")
        self.part_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.part_banner.setStyleSheet(
            "background-color: #1565C0; color: white; "
            "font-weight: bold; font-size: 14px; padding: 4px;"
        )
        self.part_banner.setVisible(False)
        layout.addWidget(self.part_banner)

        tabs = RightAlignedLogTabWidget()
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
        tabs.addTab(self.viewer_tab, "Graphics Viewer")
        tabs.add_right_aligned_tab(self.log_tab, "Full Log")

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
        self.setup_tab.sm.state_changed.connect(self.viewer_tab._on_session_state_changed)
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
            sessions = history_sessions or []
            part = sessions[0].part_number if sessions else ""
            self.set_part_banner(part)
            self.viewer_tab.render_history(sessions)
            self.log_tab.render_history(sessions)
            return

        if mode == "legacy" and session is not None:
            self.set_part_banner(session.part_number)
            self.viewer_tab.render_session(session, live=False)
            return

        if session is not None:
            self.set_part_banner(session.part_number)
            self.viewer_tab.render_session(session, live=(mode == "active"))
            self.log_tab.render_session(session)
            self.recipe_tab.render_session(session)
            self.setup_tab.render_session(session)
            return

        self.set_part_banner("")

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
        self.set_part_banner("")
        self.switch_display(None, "empty")
        self.log_tab.append("Session cleared.")

    def set_part_banner(self, part_number: str = ""):
        """Part numarasını üst banner'da gösterir; boşsa gizler."""
        if part_number:
            self.part_banner.setText(f"{part_number}")
#            self.part_banner.setText(f"Part: {part_number}")
            self.part_banner.setVisible(True)
        else:
            self.part_banner.setText("")
            self.part_banner.setVisible(False)

    def set_simulation_mode(self, active: bool, missing: str = ""):
        self.sim_banner.setVisible(active)
        if active:
            self.setWindowTitle(window_title(f"[Simulation - {missing} missing]"))
        else:
            self.setWindowTitle(window_title())

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
