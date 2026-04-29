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

import os

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
            self._log(f"⚠ build() called in invalid state: {self._state}")
            return
        print(
            f"[StateMachine] build | prev_state={self._state} "
            f"| part={session.part_number} | dir={session.session_dir}"
        )
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
        print(
            f"[StateMachine] start | state={self._state} "
            f"| has_session={self._session is not None}"
        )
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
        self._log("Session state: IDLE (reset)")

    # ── Aggregate ─────────────────────────────────────────────────

    def _on_periodic_aggregate(self):
        """
        QTimer callback — RUNNING sırasında periyodik çalışır.
        Sessizce aggregate eder, aggregate_done emit eder.
        """
        if self._state != RUNNING:
            return
        print("[StateMachine] periodic aggregate tick")
        self._run_aggregate(periodic=True)

    def _debug_measurements_snapshot(self) -> str:
        """Measurements klasorundeki dosya durumunu kisa ozetler."""
        if not self._session:
            return "no_session"

        mdir = self._session.measurements_dir
        try:
            entries = sorted(os.scandir(mdir), key=lambda entry: entry.name.lower())
        except FileNotFoundError:
            return f"missing_dir={mdir}"
        except OSError as exc:
            return f"dir_error={type(exc).__name__}: {exc}"

        files = [entry for entry in entries if entry.is_file()]
        if not files:
            return "files=0"

        csv_count = 0
        xlsx_count = 0
        samples = []
        for entry in files:
            lower_name = entry.name.lower()
            if lower_name.endswith(".csv"):
                csv_count += 1
            elif lower_name.endswith(".xlsx"):
                xlsx_count += 1

            if len(samples) < 5:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = -1
                samples.append(f"{entry.name}:{size}B")

        extra = ""
        if len(files) > len(samples):
            extra = f" +{len(files) - len(samples)} more"

        return (
            f"files={len(files)} csv={csv_count} xlsx={xlsx_count} "
            f"sample=[{'; '.join(samples)}]{extra}"
        )

    def _run_aggregate(self, periodic: bool = False):
        """
        run_silent() çalıştırır.
        - Periyodik: CSV yoksa sessizce atlar.
        - Son aggregate (finish/stop): hata varsa loglar.
        Aggregate tamamlanınca aggregate_done emit eder.
        AGGREGATING state'inde ise _on_agg_done() çağrılır.
        """
        if not self._session:
            print(f"[StateMachine] aggregate skipped | periodic={periodic} | reason=no_session")
            return
        print(
            f"[StateMachine] aggregate start | periodic={periodic} "
            f"| part={self._session.part_number} | dir={self._session.session_dir} "
            f"| snapshot={self._debug_measurements_snapshot()}"
        )

        try:
            agg = Aggregator(self._session)
            xlsx_path = agg.run_silent(offset_hours=0.0)
            self._session.register_file("xlsx", xlsx_path)
            self._session.save()
            print(
                f"[StateMachine] aggregate success | periodic={periodic} "
                f"| xlsx={xlsx_path} | snapshot={self._debug_measurements_snapshot()}"
            )
            self.aggregate_done.emit(xlsx_path)
        except AggregatorError:
            print(
                f"[StateMachine] aggregate aggregator_error | periodic={periodic} "
                f"| measurements_dir={self._session.measurements_dir} "
                f"| snapshot={self._debug_measurements_snapshot()}"
            )
            if not periodic:
                self._log("⚠ Aggregate: CSV not found or could not be read.")
        except Exception as e:
            print(
                f"[StateMachine] aggregate exception | periodic={periodic} "
                f"| error={type(e).__name__}: {e}"
            )
            if not periodic:
                self._log(f"✗ Aggregate error: {e}")
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
        print(
            f"[StateMachine] transition | old={self._state} | new={new_state} "
            f"| has_session={self._session is not None}"
        )
        self._state = new_state
        self._log(f"Session state: {new_state.upper()}")
        self.state_changed.emit(new_state)

    def _log(self, msg: str):
        self.log_signal.emit(msg)
