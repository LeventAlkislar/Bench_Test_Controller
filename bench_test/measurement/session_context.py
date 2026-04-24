# bench_test/measurement/session_context.py
# -*- coding: utf-8 -*-
"""
SessionContext
==============
Uygulamada aynı anda iki bağımsız session kavramını taşır:

  active_session   : Pipeline çalışan, SM'e bağlı session.
                     Yalnızca SM.build() / SM.reset() ile değişir.

  displayed_session: Ekranda gösterilen session.
                     Yalnızca switch_display() ile değişir.

  display_mode     : "empty" | "active" | "archived"

Sahibi: MainWindow — hiçbir sekme doğrudan değiştirmez.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional

from bench_test.measurement.session import MeasurementSession

DisplayMode = Literal["empty", "active", "archived", "history"]


@dataclass
class SessionContext:
    active_session   : Optional[MeasurementSession] = field(default=None)
    displayed_session: Optional[MeasurementSession] = field(default=None)
    display_mode     : DisplayMode                  = field(default="empty")

    # ── Yardımcılar ───────────────────────────────────────────────

    def set_active(self, session: MeasurementSession) -> None:
        """SM.build() çağrısından hemen sonra MainWindow tarafından çağrılır."""
        self.active_session = session

    def clear_active(self) -> None:
        """SM.reset() çağrısından hemen sonra MainWindow tarafından çağrılır."""
        self.active_session = None
        if self.display_mode == "active":
            self.display_mode = "empty"
            self.displayed_session = None

    def switch(
        self,
        session: Optional[MeasurementSession],
        mode: DisplayMode,
    ) -> None:
        """
        Ekranda gösterilecek session'ı değiştirir.
        MainWindow.switch_display() bu metodu çağırır.
        """
        self.displayed_session = session
        self.display_mode = mode

    # ── Sorgu yardımcıları ────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        """Ekranda gösterilen session canlı (active) mı?"""
        return self.display_mode == "active"

    @property
    def is_archived(self) -> bool:
        return self.display_mode == "archived"

    @property
    def is_history(self) -> bool:
        return self.display_mode == "history"

    @property
    def is_empty(self) -> bool:
        return self.display_mode == "empty"
