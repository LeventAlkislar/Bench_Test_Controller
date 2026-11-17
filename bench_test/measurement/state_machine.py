# bench_test/measurement/state_machine.py
# -*- coding: utf-8 -*-
"""
SessionStateMachine
===================
Session ve recipe yaşam döngüsünü tek yerden yönetir.

State geçişleri:
    IDLE ──[build()]──► READY
    READY ──[start()]──► RUNNING
    RUNNING ──[finish() | stop() | error()]──► AGGREGATING
    AGGREGATING ──[_on_agg_done()]──► IDLE

Periyodik aggregate:
    RUNNING state'inde QTimer her AGGREGATE_INTERVAL_MS'de bir
    run_silent() çalıştırır ve aggregate_done sinyali emit eder.

Sinyaller:
    state_changed(state: str)    — her geçişte emit, ViewerTab dinler
    aggregate_done(xlsx: str)    — aggregate tamamlandığında emit
    log_signal(msg: str)         — MainWindow log_tab'e bağlanır

Kullanım (PackagePanel içinde):
    self.sm = SessionStateMachine()
    self.sm.aggregate_done.connect(self.viewer_tab._refresh)
    self.sm.log_signal.connect(self.log_tab.append)

    self.sm.build(session)       # paket oluşturulunca
    self.sm.start()              # recipe başlayınca
    self.sm.finish()             # recipe tamamlanınca
    self.sm.stop()               # kullanıcı durdurdu veya hata
"""

import traceback
import threading
from types import SimpleNamespace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from bench_test.config import AGGREGATE_INTERVAL_MS
from bench_test.measurement.aggregator import Aggregator, AggregatorError
from bench_test.measurement.session import (
    MEASUREMENT_MODE_CONTINUOUS_PAD,
    MeasurementSession,
    SessionStatus,
)
from bench_test.utils.debug_log import debug_log


# ── State sabitleri ───────────────────────────────────────────────
IDLE        = "idle"
READY       = "ready"
RUNNING     = "running"
AGGREGATING = "aggregating"


class SessionStateMachine(QObject):
    """
    Session ve recipe yaşam döngüsünü yöneten state machine.

    PackagePanel tarafından instantiate edilir ve sahiplenilir.
    Tüm geçişler bu sınıf üzerinden yapılır.
    """

    state_changed  = pyqtSignal(str)   # yeni state adı
    aggregate_done = pyqtSignal(str)   # xlsx tam yolu
    log_signal     = pyqtSignal(str)   # log mesajı
    aggregate_activity = pyqtSignal(bool)
    _aggregate_result = pyqtSignal(object, str, object, bool)

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._state  : str                          = IDLE
        self._session: MeasurementSession | None    = None
        self._aggregate_running = False
        self._aggregate_final_pending = False
        self._aggregate_result.connect(self._finish_aggregate)

        # Periyodik aggregate timer — sadece RUNNING'de aktif
        self._timer = QTimer(self)
        self._timer.setInterval(AGGREGATE_INTERVAL_MS)
        self._timer.timeout.connect(self._on_periodic_aggregate)

    # ── Okuyucular ────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def session(self) -> MeasurementSession | None:
        return self._session

    @property
    def aggregation_running(self) -> bool:
        return self._aggregate_running

    # ── Geçiş metotları ───────────────────────────────────────────

    def build(self, session: MeasurementSession):
        """
        Paket oluşturuldu → IDLE veya READY'den READY'e geç.
        PackagePanel.build_package() başarılı olunca çağrılır.
        """
        if self._state not in (IDLE, READY):
            self._log(f"⚠ build() called in invalid state: {self._state}")
            return
        self._session = session
        self._transition(READY)

    def start(self):
        """
        Recipe başladı → READY'den RUNNING'e geç.
        RecipeTab._start_recipe() tarafından çağrılır.
        """
        if self._state != READY:
            self._log(f"⚠ start() called in invalid state: {self._state}")
            return
        if self._session:
            self._session.set_status(SessionStatus.IN_PROGRESS)
        self._timer.start()
        self._transition(RUNNING)
        self._run_aggregate(periodic=True)

    def finish(self):
        """
        Recipe normal tamamlandı → RUNNING'den AGGREGATING'e geç.
        RecipeTab._poll_queue() 'completed'/'finished' mesajında çağrılır.
        """
        if self._state != RUNNING:
            self._log(f"⚠ finish() called in invalid state: {self._state}")
            return
        self._timer.stop()
        if self._session:
            self._session.set_status(SessionStatus.COMPLETED)
        self._transition(AGGREGATING)
        self._run_aggregate()

    def stop(self):
        """
        Recipe durduruldu veya hata oluştu → RUNNING'den AGGREGATING'e geç.
        RecipeTab._poll_queue() 'stopped'/'error' mesajında çağrılır.
        Kullanıcı perspektifinden finish() ile aynı akış: aggregate çalışır.
        """
        if self._state != RUNNING:
            self._log(f"⚠ stop() called in invalid state: {self._state}")
            return
        self._timer.stop()
        if self._session:
            self._session.set_status(SessionStatus.ABORTED)
        self._transition(AGGREGATING)
        self._run_aggregate()

    def reset(self):
        """Session kapatıldı — sessizce IDLE'a dön, sinyal emit etme."""
        self._timer.stop()
        self._session = None
        self._state = IDLE  # _transition() yerine direkt set — sinyal yok
        self._aggregate_final_pending = False
        self._log("Session state: IDLE (reset)")

    # ── Aggregate ─────────────────────────────────────────────────

    def _on_periodic_aggregate(self):
        """
        QTimer callback — RUNNING sırasında periyodik çalışır.
        Sessizce aggregate eder, aggregate_done emit eder.
        """
        if self._state != RUNNING:
            return
        self._run_aggregate(periodic=True)

    def _run_aggregate(self, periodic: bool = False):
        """
        run_silent() arka planda çalışır; sonuç ana Qt iş parçacığında işlenir.
        - Periyodik: CSV yoksa sessizce atlar.
        - Son aggregate (finish/stop): hata varsa loglar.
        Aggregate tamamlanınca aggregate_done emit eder.
        AGGREGATING state'inde ise _on_agg_done() çağrılır.
        """
        if not self._session:
            return

        if self._aggregate_running:
            if not periodic:
                self._aggregate_final_pending = True
                self._log("⚠ Aggregate already running; final pass queued.")
            return

        if getattr(self._session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD:
            try:
                if not periodic:
                    debug_log("Continuous PAD: xlsx aggregate skipped; .mtp segments remain available in Viewer.")
            finally:
                if self._state == AGGREGATING:
                    self._on_agg_done()
            return

        session = self._session
        # The worker reads immutable paths only; session metadata and Qt
        # notifications remain owned by the UI thread.
        source = SimpleNamespace(measurements_dir=session.measurements_dir, part_number=session.part_number)
        self._aggregate_running = True
        try:
            self.aggregate_activity.emit(True)
            threading.Thread(target=self._aggregate_worker, args=(session, source, periodic), daemon=True).start()
        except Exception as exc:
            self._finish_aggregate(session, "", (exc, traceback.format_exc()), periodic)

    def _aggregate_worker(self, session, source, periodic):
        path, error = "", None
        try:
            path = Aggregator(source).run_silent(offset_hours=0.0)
        except Exception as exc:
            error = (exc, traceback.format_exc())
        finally:
            self._aggregate_result.emit(session, path, error, periodic)

    @pyqtSlot(object, str, object, bool)
    def _finish_aggregate(self, session, xlsx_path, error, periodic):
        try:
            if session is not self._session:
                return
            if error:
                exc, diagnostic = error
                debug_log("Aggregate exception:\n" + diagnostic)
                if not periodic:
                    self._log("⚠ Aggregate: CSV not found or could not be read." if isinstance(exc, AggregatorError)
                              else f"✗ Aggregate error: {exc}")
            else:
                session.register_file("xlsx", xlsx_path)
                session.save()
                self.aggregate_done.emit(xlsx_path)
        except Exception as exc:
            debug_log("Aggregate completion exception:\n" + traceback.format_exc(), level="error")
            self._log(f"✗ Aggregate completion error: {exc}")
        finally:
            self._aggregate_running = False
            self.aggregate_activity.emit(False)
            if self._aggregate_final_pending and self._session is not None:
                self._aggregate_final_pending = False
                self._run_aggregate()
            elif self._state == AGGREGATING and session is self._session:
                self._on_agg_done()

    def _on_agg_done(self):
        """
        Son aggregate tamamlandı → IDLE'a geç.
        PackagePanel'in _ask_keep_session() akışını tetikler.
        """
        self._transition(IDLE)

    # ── Yardımcılar ───────────────────────────────────────────────

    def _transition(self, new_state: str):
        self._state = new_state
        self._log(f"Session state: {new_state.upper()}")
        self.state_changed.emit(new_state)

    def _log(self, msg: str):
        self.log_signal.emit(msg)
