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
from bench_test.ui.main_window import MainWindow


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app_icon = QIcon(resource_path(os.path.join("bench_test", "ui", "assets", "app_icon.ico")))
    app.setWindowIcon(app_icon)
    win = MainWindow()
    win.setWindowIcon(app_icon)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
