# bench_test/ui/tabs/recipe_tab.py
import json
import os
import queue
import threading

from dataclasses import asdict
from typing import List, Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QDoubleSpinBox, QTextEdit, QGroupBox, QFileDialog,
    QMessageBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDialog, QDialogButtonBox,
    QCheckBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from bench_test.valve.multiport import ValveController
from bench_test.valve.injector import InjectorValveController
from bench_test.dropview.controller import DropViewController
from bench_test.recipe.models import Recipe, RecipeStep, StepLoop
from bench_test.recipe.runner import RecipeRunner, DROPVIEW_ACTIONS, DROPVIEW_LABELS, DROPVIEW_ZERO_DURATION_OK
from bench_test.utils.paths import open_file, save_file, get_last, remember
from bench_test.ui.widgets import _btn, _lbl

_DV_COMBO_LABELS = [
    "None",
    "Start DropView",
    "Start Measure",
    "Stop Measure",
    "Exit DropView",
]
_DV_IDX_TO_KEY = {
    0: "none",
    1: "start_dropview",
    2: "start_measure",
    3: "stop_measure",
    4: "exit_dropview",
}
VALVE_B_LABELS   = {0: "-", 1: "Load", 2: "Inject"}
COL_STEP, COL_VA, COL_VB, COL_DUR, COL_DESC, COL_DV, COL_SCR, COL_LOOP = range(8)

class StepLoopDialog(QDialog):
    def __init__(self, parent, total_steps, existing_loops, selected_steps=None):
        super().__init__(parent)
        self.setWindowTitle("Set Step Loop Range")
        self.setFixedSize(350, 210)
        self.result_loop = None
        self.existing_loops = existing_loops
        self.total_steps = total_steps

        layout = QVBoxLayout(self)

        layout.addWidget(_lbl("Create a loop range for specific steps:", bold=True))
        layout.addWidget(_lbl("Steps within this range will repeat the specified \nnumber of times.", color="#888"))

        form = QFormLayout()
        self.start_spin = QSpinBox(); self.start_spin.setRange(1, total_steps); self.start_spin.setFixedHeight(22)
        self.end_spin   = QSpinBox(); self.end_spin.setRange(1, total_steps); self.end_spin.setFixedHeight(22)
        self.loop_spin  = QSpinBox(); self.loop_spin.setRange(1, 999); self.loop_spin.setValue(2); self.loop_spin.setFixedHeight(22)

        if selected_steps:
            self.start_spin.setValue(selected_steps[0])
            self.end_spin.setValue(selected_steps[-1])

        form.addRow("From Step:", self.start_spin)
        form.addRow("To Step:",   self.end_spin)
        form.addRow("Loop Count:", self.loop_spin)
        layout.addLayout(form)

        self.preview = QLabel("")
        self.preview.setStyleSheet("color:#4CAF50;")
        layout.addWidget(self.preview)

        self.start_spin.valueChanged.connect(self._update_preview)
        self.end_spin.valueChanged.connect(self._update_preview)
        self.loop_spin.valueChanged.connect(self._update_preview)
        self._update_preview()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _update_preview(self):
        s = self.start_spin.value(); e = self.end_spin.value(); n = self.loop_spin.value()
        if s > e:
            self.preview.setText("Warning: Start must be <= End"); self.preview.setStyleSheet("color:#F44336;")
        else:
            self.preview.setText(f"Steps {s} through {e} will repeat {n} times")
            self.preview.setStyleSheet("color:#4CAF50;")

    def _on_ok(self):
        s = self.start_spin.value(); e = self.end_spin.value(); n = self.loop_spin.value()
        if s > e:
            QMessageBox.critical(self, "Error", "Start step must be <= End step"); return
        for ex in self.existing_loops:
            if not (e < ex.start_step or s > ex.end_step):
                QMessageBox.critical(self, "Error",
                    f"Overlaps with existing loop (Steps {ex.start_step}-{ex.end_step})"); return
        self.result_loop = StepLoop(s, e, n)
        self.accept()

class RecipeTab(QWidget):
    log_signal = pyqtSignal(str)

    def __init__(self, ctrl_a: ValveController, ctrl_b: InjectorValveController,
                 dv_ctrl: DropViewController,
                 script_tab: "ScriptEditorTab" = None,
                 package_tab: "PackageTab" = None):          # ← YENİ
        super().__init__()
        self.ctrl_a = ctrl_a; self.ctrl_b = ctrl_b
        self.dv_ctrl = dv_ctrl
        self.script_tab  = script_tab
        self.package_tab = package_tab                        # ← YENİ
        self.recipe_steps: List[RecipeStep] = []
        self.step_loops:   List[StepLoop]   = []
        self.recipe_runner: Optional[RecipeRunner] = None
        self.stop_event    = threading.Event()
        self.status_queue  = queue.Queue()
        self._ignoring_changes = False
        self._current_recipe_path = ""                        # ← YENİ

        self._build()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_queue)
        self._poll_timer.start()

    def _build(self):
        layout = QVBoxLayout(self)

        # ── Recipe settings ───────────────────────────────────
        gs = QGroupBox("Recipe Settings")
        fls = QHBoxLayout(gs)
        fls.addWidget(QLabel("Recipe Name:"))
        self.name_edit = QLineEdit("New Recipe"); self.name_edit.setMaximumWidth(200)
        fls.addWidget(self.name_edit)
        fls.addWidget(QLabel("  Recipe Loops:"))
        self.loop_spin = QSpinBox(); self.loop_spin.setRange(1, 999); self.loop_spin.setValue(1)
        self.loop_spin.valueChanged.connect(self._update_total_time)
        fls.addWidget(self.loop_spin); fls.addStretch()
        layout.addWidget(gs)

        # ── Add step ──────────────────────────────────────────
        ga = QGroupBox("Add Recipe Step")
        fla = QHBoxLayout(ga)

        fla.addWidget(QLabel("Valve A:"))
        self.step_port = QSpinBox(); self.step_port.setRange(0, 8); self.step_port.setMaximumWidth(55)
        self.step_port.setToolTip("0 = No change, 1-8 = port")
        fla.addWidget(self.step_port)

        fla.addWidget(QLabel("Valve B:"))
        self.step_vb = QComboBox()
        self.step_vb.addItems(["0 - No change", "1 - Load (1-6,2-3,4-5)", "2 - Inject (1-2,3-4,5-6)"])
        fla.addWidget(self.step_vb)

        fla.addWidget(QLabel("Duration (min):"))
        self.step_dur = QDoubleSpinBox()
        self.step_dur.setRange(0, 9999)   # 0'a izin var
        self.step_dur.setValue(60)
        self.step_dur.setMaximumWidth(80)
        fla.addWidget(self.step_dur)

        fla.addWidget(QLabel("Description:"))
        self.step_desc = QLineEdit(); self.step_desc.setMaximumWidth(120)
        fla.addWidget(self.step_desc)

        fla.addWidget(QLabel("DropView:"))
        self.step_dv = QComboBox()
        self.step_dv.addItems(_DV_COMBO_LABELS)
        fla.addWidget(self.step_dv)

        fla.addWidget(_btn("Add Step", self._add_step, "#4CAF50"))
        layout.addWidget(ga)

        # ── Steps table ───────────────────────────────────────
        gt = QGroupBox("Recipe Steps (Double-click to edit | Select rows for step loops)")
        gtl = QVBoxLayout(gt)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Step #", "Valve A", "Valve B", "Duration (min)", "Description", "DropView Action", "Script (.scr)", "Loop Info"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        gtl.addWidget(self.table)
        layout.addWidget(gt, 1)

        # ── Step buttons ──────────────────────────────────────
        sb = QHBoxLayout()
        sb.addWidget(_btn("Duplicate",       self._duplicate))
        sb.addWidget(_btn("Move Up",         self._move_up))
        sb.addWidget(_btn("Move Down",       self._move_down))
        sb.addWidget(_btn("Remove Selected", self._remove_step))
        sb.addWidget(_btn("Clear All",       self._clear_all))
        sb.addWidget(QFrame())
        sb.addWidget(_btn("Set Step Loop",   self._set_loop,   "#9C27B0"))
        sb.addWidget(_btn("Clear Loops",     self._clear_loops,"#607D8B"))
        sb.addStretch()
        layout.addLayout(sb)

        # ── Loops display ─────────────────────────────────────
        gl = QGroupBox("Active Step Loops")
        gll = QVBoxLayout(gl)
        self.loops_lbl = QLabel("No step loops defined")
        self.loops_lbl.setStyleSheet("color:#9C27B0;")
        gll.addWidget(self.loops_lbl)
        layout.addWidget(gl)

        # ── File ops + total time ─────────────────────────────
        fb = QHBoxLayout()
        fb.addWidget(_btn("Save Recipe", self._save_recipe))
        fb.addWidget(_btn("Load Recipe", self._load_recipe))
        fb.addStretch()
        self.total_time_lbl = QLabel("Total Time: 0 min (0h 0m)")
        self.total_time_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        fb.addWidget(self.total_time_lbl)
        layout.addLayout(fb)

        # ── Execution ─────────────────────────────────────────
        ge = QGroupBox("Recipe Execution")
        gel = QVBoxLayout(ge)

        eb = QHBoxLayout()
        self.start_btn = _btn("Start Recipe", self._start_recipe, "#4CAF50", 130)
        self.pause_btn = _btn("Pause",        self._pause_recipe, "#FF9800", 80)
        self.stop_btn  = _btn("Stop Recipe",  self._stop_recipe,  "#F44336", 130)
        self.pause_btn.setEnabled(False); self.stop_btn.setEnabled(False)
        eb.addWidget(self.start_btn); eb.addWidget(self.pause_btn); eb.addWidget(self.stop_btn); eb.addStretch()
        gel.addLayout(eb)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setFont(QFont("Segoe UI", 11))
        gel.addWidget(self.status_lbl)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("Progress:"))
        self.progress = QProgressBar(); self.progress.setMaximum(100); self.progress.setMinimumWidth(300)
        pr.addWidget(self.progress)
        self.progress_lbl = QLabel("0%")
        pr.addWidget(self.progress_lbl); pr.addStretch()
        gel.addLayout(pr)
        layout.addWidget(ge)

    # ── Step management ───────────────────────────────────────
    def _add_step(self):
        port     = self.step_port.value()
        duration = self.step_dur.value()
        desc     = self.step_desc.text()
        vb_str   = self.step_vb.currentText()
        vb_state = int(vb_str[0]) if vb_str and vb_str[0].isdigit() else 0
        dv_idx   = self.step_dv.currentIndex()
        dv_action = _DV_IDX_TO_KEY.get(dv_idx, "none")

        # Süre 0 sadece DropView aksiyonu olduğunda geçerli
        if duration <= 0 and dv_action == "none":
            QMessageBox.warning(self, "Uyarı",
                "Süre 0 yalnızca bir DropView aksiyonu seçildiğinde kullanılabilir.\n"
                "None aksiyonunda süre 0 girilemeez.")
            return

        # En az bir aksiyon olmalı
        if port == 0 and vb_state == 0 and dv_action == "none":
            QMessageBox.warning(self, "Warning", "At least one action must be specified"); return

        step = RecipeStep(port=port, duration_minutes=duration, description=desc,
                          valve_b_state=vb_state, dropview_action=dv_action)
        self.recipe_steps.append(step)
        self._refresh_table(); self._update_total_time()
        log_msg = (f"Adım eklendi: A=Port{port}, B={VALVE_B_LABELS[vb_state]}, "
                   f"DV={DROPVIEW_LABELS.get(dv_action, dv_action)}, {duration}min")
        if desc:
            log_msg += f"  |  {desc}"
        self.log_signal.emit(log_msg)

    def _get_session_scr_name(self) -> str:
        """Session'dan patch'li .scr dosyasının adını döndürür."""
        if self.package_tab:
            session = self.package_tab.get_session()
            if session:
                path = session.get_file_path("script") or ""
                if path:
                    return os.path.basename(path)
        # Session yoksa Script Editor'daki aktif dosyayı göster
        if self.script_tab:
            path = self.script_tab.get_current_scr_path()
            if path:
                return f"{os.path.basename(path)} *"  # * = henüz patch'lenmemiş
        return "--"

    def _refresh_table(self):
        self._ignoring_changes = True
        self.table.setRowCount(len(self.recipe_steps))
        for i, step in enumerate(self.recipe_steps):
            loop_info = self._get_loop_info(i + 1)
            vals = [
                str(i + 1),
                f"Port {step.port}" if step.port > 0 else "-",
                VALVE_B_LABELS.get(step.valve_b_state, "-"),
                str(step.duration_minutes),
                step.description,
                DROPVIEW_LABELS.get(step.dropview_action, "-"),
                self._get_session_scr_name() if step.dropview_action == "start_measure" else "--",
                loop_info
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if j in (0, 7):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, item)
        self._ignoring_changes = False

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._ignoring_changes: return
        row = item.row(); col = item.column()
        if row >= len(self.recipe_steps): return
        step = self.recipe_steps[row]
        val  = item.text().strip()
        try:
            if col == COL_VA:
                p = int(val.replace("Port ", "")) if val != "-" else 0
                if 0 <= p <= 8: step.port = p
                else: raise ValueError()
            elif col == COL_VB:
                if val.strip().isdigit():
                    s = int(val.strip())
                    if 0 <= s <= 2: step.valve_b_state = s
                    else: raise ValueError()
                else:
                    rev = {v: k for k, v in VALVE_B_LABELS.items()}
                    step.valve_b_state = rev.get(val, 0)
            elif col == COL_DUR:
                d = float(val)
                if d >= 0:
                    if d <= 0 and step.dropview_action == "none":
                        pass
                    else:
                        step.duration_minutes = d
                else:
                    raise ValueError()
            elif col == COL_DESC:
                step.description = val
            elif col == COL_DV:
                _dv_map = {
                    "none": "none", "0": "none", "-": "none",
                    "start dropview": "start_dropview", "start_dropview": "start_dropview", "1": "start_dropview",
                    "start measure": "start_measure",   "start_measure": "start_measure",   "2": "start_measure",
                    "stop measure": "stop_measure",     "stop_measure": "stop_measure",     "3": "stop_measure",
                    "exit dropview": "exit_dropview",   "exit_dropview": "exit_dropview",   "4": "exit_dropview",
                }
                step.dropview_action = _dv_map.get(val.lower(), "none")
            elif col == COL_SCR:
                pass
        except (ValueError, TypeError):
            pass
        self._refresh_table(); self._update_total_time()

    def _on_cell_double_clicked(self, row: int, col: int):
        pass  # COL_SCR artık readonly — çift tık ile düzenleme yok

    def _remove_step(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for r in rows:
            if r < len(self.recipe_steps): del self.recipe_steps[r]
        self.step_loops = [l for l in self.step_loops if l.end_step <= len(self.recipe_steps)]
        self._refresh_table(); self._update_loops_display(); self._update_total_time()

    def _move_up(self):
        rows = [i.row() for i in self.table.selectedItems()]
        if not rows: return
        r = rows[0]
        if r > 0:
            self.recipe_steps[r], self.recipe_steps[r-1] = self.recipe_steps[r-1], self.recipe_steps[r]
            self._refresh_table(); self.table.selectRow(r-1)

    def _move_down(self):
        rows = [i.row() for i in self.table.selectedItems()]
        if not rows: return
        r = rows[0]
        if r < len(self.recipe_steps) - 1:
            self.recipe_steps[r], self.recipe_steps[r+1] = self.recipe_steps[r+1], self.recipe_steps[r]
            self._refresh_table(); self.table.selectRow(r+1)

    def _duplicate(self):
        rows = [i.row() for i in self.table.selectedItems()]
        if not rows: return
        r = rows[0]
        if r < len(self.recipe_steps):
            s = self.recipe_steps[r]
            ns = RecipeStep(s.port, s.duration_minutes, s.description + " (copy)",
                            s.valve_b_state, s.dropview_action)
            self.recipe_steps.insert(r+1, ns)
            self._refresh_table(); self._update_total_time()

    def _clear_all(self):
        if self.recipe_steps and QMessageBox.question(self, "Confirm", "Clear all steps and loops?") \
                == QMessageBox.StandardButton.Yes:
            self.recipe_steps.clear(); self.step_loops.clear()
            self._refresh_table(); self._update_loops_display(); self._update_total_time()

    def _set_loop(self):
        if not self.recipe_steps:
            QMessageBox.warning(self, "Warning", "Add steps first"); return
        rows = sorted({i.row() for i in self.table.selectedItems()})
        sel  = [r+1 for r in rows] if rows else None
        dlg  = StepLoopDialog(self, len(self.recipe_steps), self.step_loops, sel)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_loop:
            self.step_loops.append(dlg.result_loop)
            self._update_loops_display(); self._refresh_table(); self._update_total_time()

    def _clear_loops(self):
        if self.step_loops and QMessageBox.question(self, "Confirm", "Clear all step loops?") \
                == QMessageBox.StandardButton.Yes:
            self.step_loops.clear()
            self._update_loops_display(); self._refresh_table(); self._update_total_time()

    def _get_loop_info(self, step_num):
        for loop in self.step_loops:
            if loop.start_step <= step_num <= loop.end_step:
                return f"Loop x{loop.loop_count}"
        return ""

    def _update_loops_display(self):
        if not self.step_loops:
            self.loops_lbl.setText("No step loops defined"); return
        parts = [f"Steps {l.start_step}-{l.end_step} x{l.loop_count}"
                 for l in sorted(self.step_loops, key=lambda x: x.start_step)]
        self.loops_lbl.setText("  |  ".join(parts))

    def _update_total_time(self):
        if not self.recipe_steps:
            self.total_time_lbl.setText("Total Time: 0 min (0h 0m)"); return
        times = [s.duration_minutes for s in self.recipe_steps]
        processed = set()
        total = 0
        for loop in self.step_loops:
            t = sum(times[loop.start_step-1:loop.end_step]) * loop.loop_count
            total += t
            for i in range(loop.start_step-1, loop.end_step): processed.add(i)
        for i, t in enumerate(times):
            if i not in processed: total += t
        total *= self.loop_spin.value()
        h = int(total // 60); m = int(total % 60)
        self.total_time_lbl.setText(f"Total Time: {total:.1f} min ({h}h {m}m)")

    def _save_recipe(self):
        if not self.recipe_steps:
            QMessageBox.warning(self, "Uyarı", "Kaydedilecek adım yok"); return
        path = save_file(self, "Recipe Kaydet", "recipe_dir",
                          "JSON (*.json);;Tüm dosyalar (*.*)", ".json",
                          self.name_edit.text().replace(" ", "_") + ".json")
        if path:
            data = {"name": self.name_edit.text(), "loop_count": self.loop_spin.value(),
                    "steps": [asdict(s) for s in self.recipe_steps],
                    "step_loops": [asdict(l) for l in self.step_loops]}
            with open(path, "w") as f: json.dump(data, f, indent=2)
            self._current_recipe_path = path                  # ← YENİ
            if self.package_tab:                              # ← YENİ
                self.package_tab._refresh_refs()              # ← YENİ
            self.log_signal.emit(f"Recipe saved: {path}")

    def _load_recipe(self):
        path = open_file(self, "Recipe Yükle", "recipe_dir",
                          "JSON (*.json);;Tüm dosyalar (*.*)")
        if path:
            try:
                with open(path) as f: data = json.load(f)
                self.name_edit.setText(data.get("name", ""))
                self.loop_spin.setValue(data.get("loop_count", 1))
                self.recipe_steps = [
                    RecipeStep(**{k: v for k, v in s.items() if k != "dropview_scr"})
                    for s in data.get("steps", [])]
                self.step_loops   = [StepLoop(**l) for l in data.get("step_loops", [])]
                self._refresh_table(); self._update_loops_display(); self._update_total_time()
                self._current_recipe_path = path              # ← YENİ
                if self.package_tab:                          # ← YENİ
                    self.package_tab._refresh_refs()          # ← YENİ
                self.log_signal.emit(f"Recipe loaded: {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load: {e}")

    def _start_recipe(self):
        # ── Paketi oluştur ────────────────────────────────────
        if self.package_tab and not self.package_tab.build_package():
            return
        # Session .scr yolu artık hazır — COL_SCR sütununu güncelle
        if self.package_tab and self.package_tab.get_session():
            self._refresh_table()

        if not self.ctrl_a.is_connected() and not self.ctrl_b.is_connected():
            QMessageBox.warning(self, "Warning", "No valves connected"); return
        if not self.recipe_steps:
            QMessageBox.warning(self, "Warning", "No recipe steps"); return

        self.stop_event.clear()
        recipe = Recipe(self.name_edit.text(), self.recipe_steps.copy(),
                        self.loop_spin.value(), self.step_loops.copy())
        # Packager'ın ürettiği patch'li .scr yolunu al
        session_scr = ""
        if self.package_tab:
            session = self.package_tab.get_session()
            if session:
                session_scr = session.get_file_path("script") or ""

        self.recipe_runner = RecipeRunner(
            self.ctrl_a, self.ctrl_b, recipe, self.status_queue, self.stop_event,
            self.dv_ctrl, session_scr_path=session_scr)
        self.recipe_runner.start()

        self.start_btn.setEnabled(False); self.pause_btn.setEnabled(True); self.stop_btn.setEnabled(True)
        self.log_signal.emit(f"Recipe started: {recipe.name}")

    def _pause_recipe(self):
        if self.recipe_runner:
            if self.pause_btn.text() == "Pause":
                self.recipe_runner.pause(); self.pause_btn.setText("Resume")
                self.status_lbl.setText("PAUSED"); self.log_signal.emit("Recipe paused")
            else:
                self.recipe_runner.resume(); self.pause_btn.setText("Pause")
                self.log_signal.emit("Recipe resumed")

    def _stop_recipe(self):
        self.stop_event.set()
        self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False); self.pause_btn.setText("Pause")
        self.stop_btn.setEnabled(False); self.status_lbl.setText("Stopped")
        self.progress.setValue(0); self.progress_lbl.setText("0%")
        if self.package_tab:                                  # ← YENİ
            self.package_tab.on_recipe_aborted()              # ← YENİ
        self.log_signal.emit("Recipe stopped")

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self.status_queue.get_nowait()
                if msg_type == "progress":
                    rem   = data.get("remaining_minutes", 0)
                    total = data.get("total_minutes", 1)
                    pct   = max(0, min(100, ((total - rem) / total) * 100)) if total > 0 else 100
                    parts = []
                    if data.get("port", 0) > 0:        parts.append(f"A:Port {data['port']}")
                    if data.get("valve_b_state", 0) > 0:
                        parts.append(f"B:{'Load' if data['valve_b_state']==1 else 'Inject'}")
                    li = data.get("loop_info", "")
                    st = f"{li}Step {data['step']}/{data['total_steps']}: {', '.join(parts) or 'No change'} - {rem:.1f} min remaining"
                    if data.get("description"): st += f" ({data['description']})"
                    self.status_lbl.setText(st)
                    self.progress.setValue(int(pct)); self.progress_lbl.setText(f"{pct:.0f}%")
                elif msg_type in ("switching", "running", "log"):
                    self.status_lbl.setText(str(data)); self.log_signal.emit(str(data))
                elif msg_type == "completed":
                    self.status_lbl.setText(f"COMPLETED: {data}")
                    self.progress.setValue(100); self.progress_lbl.setText("100%")
                    self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False)
                    self.stop_btn.setEnabled(False)
                    if self.package_tab:                      # ← YENİ
                        self.package_tab.on_recipe_completed()  # ← YENİ
                    QMessageBox.information(self, "Recipe Complete", str(data))
                    self.log_signal.emit(str(data))
                elif msg_type == "finished":
                    self.status_lbl.setText(f"TAMAMLANDI: {data}")
                    self.progress.setValue(100)
                    self.progress_lbl.setText("100%")
                    self.start_btn.setEnabled(True)
                    self.pause_btn.setEnabled(False)
                    self.stop_btn.setEnabled(False)
                    if self.package_tab:                      # ← YENİ
                        self.package_tab.on_recipe_completed()  # ← YENİ
                    QMessageBox.information(self, "Recipe Complete", str(data))
                    self.log_signal.emit(str(data))
                elif msg_type == "stopped":
                    self.status_lbl.setText(f"Stopped: {data}"); self.log_signal.emit(str(data))
                elif msg_type == "error":
                    self.status_lbl.setText(f"ERROR: {data}")
                    self.start_btn.setEnabled(True); self.pause_btn.setEnabled(False)
                    self.stop_btn.setEnabled(False)
                    if self.package_tab:                      # ← YENİ
                        self.package_tab.on_recipe_aborted()  # ← YENİ
                    QMessageBox.critical(self, "Recipe Error", str(data))
                    self.log_signal.emit(f"Recipe error: {data}")
                elif msg_type == "warning":
                    self.log_signal.emit(f"Warning: {data}")
        except queue.Empty:
            pass
