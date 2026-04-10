# main.py
import sys
import os
import logging

# Log dosyasını exe'nin yanına yaz
log_path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)), "bench_test_log.txt")

logging.basicConfig(
    filename=log_path,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8"
)

import traceback

def handle_exception(exc_type, exc_value, exc_tb):
    logging.error("Yakalanmamış hata:", exc_info=(exc_type, exc_value, exc_tb))

sys.excepthook = handle_exception

from PyQt6.QtWidgets import QApplication
from bench_test.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()