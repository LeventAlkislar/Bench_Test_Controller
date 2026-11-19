# main.py
import sys
import os
import logging

def setup_logging():
    log_path = os.path.join(
        os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
        else os.path.dirname(os.path.abspath(__file__)),
        "bench_test_log.txt"
    )
    handler = logging.FileHandler(log_path, delay=True, encoding="utf-8")
    handler.setLevel(logging.ERROR)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logging.getLogger().setLevel(logging.ERROR)
    logging.getLogger().addHandler(handler)

setup_logging()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

def main():
    arguments = sys.argv[1:]
    if arguments == ["--watchdog"]:
        from bench_test.watchdog.supervisor import run_supervisor
        run_supervisor()
        return
    from bench_test.ui.main_window import MainWindow
    from bench_test.watchdog.state import ProcessLock, runtime_directory
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QMessageBox
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app_icon = QIcon(resource_path(os.path.join("bench_test", "ui", "assets", "app_icon.ico")))
    app.setWindowIcon(app_icon)
    try:
        application_lock = ProcessLock(runtime_directory() / "application.lock")
        application_lock.__enter__()
    except RuntimeError:
        QMessageBox.information(None, "Already Running", "Bench Test Controller is already running.")
        return
    try:
        win = MainWindow()
        win.setWindowIcon(app_icon)
        win.show()
        def restore_if_needed():
            try:
                token = arguments[1] if len(arguments) == 2 and arguments[0] == "--watchdog-recover" else None
                win.watchdog.accept_startup(token)
            except Exception as exc:
                win.statusBar().showMessage(f"Automatic recovery unavailable: {exc}")
                logging.exception("Automatic recovery startup failed")
        QTimer.singleShot(0, restore_if_needed)
        app.exec()
    finally:
        application_lock.__exit__(None, None, None)


if __name__ == "__main__":
    main()
