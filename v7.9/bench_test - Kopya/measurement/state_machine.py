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

Kullanım (PackageTab içinde):
    self.sm = SessionStateMachine()
    self.sm.aggregate_done.connect(self.viewer_tab._refresh)
    self.sm.log_signal.connect(self.log_tab.append)

    self.sm.build(session)       # paket oluşturulunca
    self.sm.start()              # recipe başlayınca
    self.sm.finish()             # recipe tamamlanınca
    self.sm.stop()               # kullanıcı durdurdu veya hata
"""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bench_test.config import AGGREGATE_INTERVAL_MS
from bench_test.measurement.aggregator import Aggregator, AggregatorError
from bench_test.measurement.session import MeasurementSession, SessionStatus


# ── State sabitleri ───────────────────────────────────────────────
IDLE        = "idle"
READY       = "ready"
RUNNING     = "running"
AGGREGATING = "aggregating"


class SessionStateMachine(QObject):
    """
    Session ve recipe yaşam döngüsünü yöneten state machine.

    PackageTab tarafından instantiate edilir ve sahiplenilir.
    Tüm geçişler bu sınıf üzerinden yapılır.
    """

    state_changed  = pyqtSignal(str)   # yeni state adı
    aggregate_done = pyqtSignal(str)   # xlsx tam yolu
    log_signal     = pyqtSignal(str)   # log mesajı

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._state  : str                          = IDLE
        self._session: MeasurementSession | None    = None

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

    # ── Geçiş metotları ───────────────────────────────────────────

    def build(self, session: MeasurementSession):
        """
        Paket oluşturuldu → IDLE veya READY'den READY'e geç.
        PackageTab.build_package() başarılı olunca çağrılır.
        """
        if self._state not in (IDLE, READY):
            self._log(f"⚠ build() geçersiz state'de çağrıldı: {self._state}")
            return
        self._session = session
        self._transition(READY)

    def start(self):
        """
        Recipe başladı → READY'den RUNNING'e geç.
        RecipeTab._start_recipe() tarafından çağrılır.
        """
        if self._state != READY:
            self._log(f"⚠ start() geçersiz state'de çağrıldı: {self._state}")
            return
        self._timer.start()
        self._transition(RUNNING)

    def finish(self):
        """
        Recipe normal tamamlandı → RUNNING'den AGGREGATING'e geç.
        RecipeTab._poll_queue() 'completed'/'finished' mesajında çağrılır.
        """
        if self._state != RUNNING:
            self._log(f"⚠ finish() geçersiz state'de çağrıldı: {self._state}")
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
            self._log(f"⚠ stop() geçersiz state'de çağrıldı: {self._state}")
            return
        self._timer.stop()
        if self._session:
            self._session.set_status(SessionStatus.ABORTED)
        self._transition(AGGREGATING)
        self._run_aggregate()

    def reset(self):
        """
        Session kapatıldı → IDLE'a dön.
        PackageTab._ask_keep_session() tamamlanınca çağrılır.
        """
        self._timer.stop()
        self._session = None
        self._transition(IDLE)

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
        run_silent() çalıştırır.
        - Periyodik: CSV yoksa sessizce atlar.
        - Son aggregate (finish/stop): hata varsa loglar.
        Aggregate tamamlanınca aggregate_done emit eder.
        AGGREGATING state'inde ise _on_agg_done() çağrılır.
        """
        if not self._session:
            return

        try:
            agg = Aggregator(self._session)
            xlsx_path = agg.run_silent(offset_hours=0.0)
            self._session.register_file("xlsx", xlsx_path)
            self._session.save()
            self._log(f"✓ Aggregate tamamlandı: {xlsx_path}")
            self.aggregate_done.emit(xlsx_path)
        except AggregatorError:
            if not periodic:
                self._log("⚠ Aggregate: CSV bulunamadı veya okunamadı.")
        except Exception as e:
            if not periodic:
                self._log(f"✗ Aggregate hatası: {e}")
        finally:
            if self._state == AGGREGATING:
                self._on_agg_done()

    def _on_agg_done(self):
        """
        Son aggregate tamamlandı → IDLE'a geç.
        PackageTab'ın _ask_keep_session() akışını tetikler.
        """
        self._transition(IDLE)

    # ── Yardımcılar ───────────────────────────────────────────────

    def _transition(self, new_state: str):
        self._state = new_state
        self._log(f"Session state: {new_state.upper()}")
        self.state_changed.emit(new_state)

    def _log(self, msg: str):
        self.log_signal.emit(msg)
