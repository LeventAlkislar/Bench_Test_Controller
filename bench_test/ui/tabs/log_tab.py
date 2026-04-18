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
        btns.addWidget(_btn("Clear Log",     self.log.clear,  "#F44336"))
        btns.addWidget(_btn("Save Log",      self._save, "#4CAF50"))
        btns.addWidget(_btn("Time Stamp", self._insert_timestamp,       "#2196F3"))
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
        self.log.append(f"[{ts}] User Note | ")
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log.setTextCursor(cursor)
        self.log.setFocus()

    def _save(self):
        path = save_file(self, "Logu Kaydet", "log_dir", "Text (*.txt)", ".txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log.toPlainText())

    def restore_from_session(self, session_dir):
        from pathlib import Path
        log_path = Path(session_dir) / "logs" / "session.log"
        if not log_path.is_file():
            return
        self.log.clear()
        self.log.append(f"# --- Restored from: {session_dir} ---")
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                self.log.append(line.rstrip())
        sb = self.log.verticalScrollBar()
        sb.setValue(0)

    def clear(self):
        """LogTab'ı açılış haline getirir."""
        self.log.clear()