# bench_test/ui/tabs/log_tab.py
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import pyqtSignal

from bench_test.utils.paths import save_file

from bench_test.ui.widgets import _btn, _lbl


class LogTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log, 1)

        btns = QHBoxLayout()
        btns.addWidget(_btn("Clear Log",     self.log.clear))
        btns.addWidget(_btn("Save Log",      self._save))
        btns.addWidget(_btn("Zaman Damgasi", self._insert_timestamp))
        btns.addStretch()
        layout.addLayout(btns)

    def append(self, msg: str, desc: str = ""):
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        if desc:
            line += f"  |  {desc}"
        self.log.append(line)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _insert_timestamp(self):
        self.log.setReadOnly(False)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log.append(f"[{ts}] ")
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log.setTextCursor(cursor)
        self.log.setFocus()

    def _save(self):
        path = save_file(self, "Logu Kaydet", "log_dir", "Text (*.txt)", ".txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log.toPlainText())