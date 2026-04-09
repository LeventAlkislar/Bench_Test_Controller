# bench_test/ui/widgets.py
from PyQt6.QtWidgets import QPushButton, QLabel


def _btn(label, slot, color=None, min_width=None):
    b = QPushButton(label)
    b.clicked.connect(slot)
    if color:
        b.setStyleSheet(f"background-color: {color}; color: white; font-weight: bold;")
    if min_width:
        b.setMinimumWidth(min_width)
    return b

def _lbl(text, bold=False, color=None):
    l = QLabel(text)
    if bold:
        l.setStyleSheet("font-weight: bold;")
    if color:
        l.setStyleSheet(l.styleSheet() + f" color: {color};")
    return l

def _status_lbl(text="● Disconnected"):
    from PyQt6.QtWidgets import QLabel
    from PyQt6.QtGui import QFont
    l = QLabel(text)
    l.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
    l.setStyleSheet("color:#F44336;")
    return l