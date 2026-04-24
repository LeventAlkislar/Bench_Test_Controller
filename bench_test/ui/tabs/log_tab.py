from datetime import datetime

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

from bench_test.ui.widgets import _btn, _lbl
from bench_test.utils.paths import save_file


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
        btns.addWidget(_btn("Clear Log", self.log.clear, "#F44336"))
        btns.addWidget(_btn("Save Log", self._save, "#4CAF50"))
        btns.addWidget(_btn("Time Stamp", self._insert_timestamp, "#2196F3"))
        btns.addStretch()
        layout.addLayout(btns)

    def append(self, msg: str, desc: str = ""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

    def render_session(self, session) -> None:
        """Session nesnesinden log dosyasini yukler."""
        import os

        log_path = os.path.join(session.logs_dir, "session.log")
        self.log.clear()
        if not os.path.isfile(log_path):
            self.log.append(f"# Log dosyasi bulunamadi: {log_path}")
            return
        mode = "archived" if session.status.value != "in_progress" else "active"
        self.log.append(
            f"# --- {mode.upper()} | {session.part_number} | {session.session_dir} ---"
        )
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                self.log.append(line.rstrip())
        sb = self.log.verticalScrollBar()
        sb.setValue(0)

    def render_history(self, sessions) -> None:
        """Bir part altindaki tum session loglarini toplu gosterir."""
        import os

        self.log.clear()
        if not sessions:
            self.log.append("# History log bulunamadi.")
            return

        part_number = sessions[0].part_number
        self.log.append(
            f"# --- HISTORY | {part_number} | {len(sessions)} session ---"
        )

        for session in sessions:
            log_path = os.path.join(session.logs_dir, "session.log")
            self.log.append(
                f"# --- {session.status.value.upper()} | {session.session_dir} ---"
            )
            if not os.path.isfile(log_path):
                self.log.append(f"# Log dosyasi bulunamadi: {log_path}")
                continue

            with open(log_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    self.log.append(line.rstrip())

        sb = self.log.verticalScrollBar()
        sb.setValue(0)

    def restore_from_session(self, session_dir):
        """Geriye donuk uyumluluk - render_session'a yonlendir."""
        from bench_test.measurement.session import MeasurementSession

        try:
            session = MeasurementSession.load(session_dir)
            self.render_session(session)
        except Exception:
            pass

    def clear(self):
        self.log.clear()
