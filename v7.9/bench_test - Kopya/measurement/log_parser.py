# bench_test/measurement/log_parser.py
# -*- coding: utf-8 -*-
"""
LogParser
=========
session.log dosyasını okuyup grafik için anlamlı olayları çıkarır.

Çıktı türleri:
- StepEvent   : Step satırları → dikey marker çizgisi
- SystemEvent : Recipe started/stopped/paused/resumed → sistem marker
- ManualNote  : Tanımlanamayan serbest metin satırları → annotation panel

Log format örneği:
    [2026-03-31 13:50:48] Step 1/12: A:Port 1, B:Load for 30.0 min
    [2026-03-31 14:50:55] Step Loop 1/50 (Steps 3-12): Step 3/12: A:Port 1, B:Load for 75.0 min
    [2026-04-01 07:33:00] Sabah gelince hava aldim
    [2026-04-02 07:49:32] Recipe stopped by user
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# ─────────────────────────────────────────────────────────────────
#  Veri sınıfları
# ─────────────────────────────────────────────────────────────────

@dataclass
class StepEvent:
    """
    Bir step geçişini temsil eder.
    Grafikte dikey turuncu çizgi + etiket olarak gösterilir.
    """
    timestamp : datetime
    step_no   : int          # Mevcut step numarası (ör. 3)
    total_steps: int         # Toplam step sayısı (ör. 12)
    port_a    : Optional[int]  # Valve A port numarası (ör. 1), yoksa None
    valve_b   : Optional[str]  # "Load" | "Inject" | None
    duration_min: Optional[float]  # Süre dakika cinsinden
    loop_info : str          # "Loop 1/50" veya ""
    raw_line  : str          # Ham log satırı


@dataclass
class SystemEvent:
    """
    Recipe başlatma/durdurma/duraklatma/devam ettirme olayı.
    Grafikte farklı renkli dikey çizgi olarak gösterilir.
    """
    timestamp : datetime
    event_type: str   # "started" | "stopped" | "paused" | "resumed" | "error"
    detail    : str   # Ham metin (ör. "Recipe stopped by user")
    raw_line  : str


@dataclass
class ManualNote:
    """
    Tanınmayan serbest metin satırı.
    Sadece annotation panelinde gösterilir, grafikte çizgi yok.
    """
    timestamp : datetime
    text      : str
    raw_line  : str


@dataclass
class ParseResult:
    """LogParser.parse() dönüş değeri."""
    step_events  : List[StepEvent]   = field(default_factory=list)
    system_events: List[SystemEvent] = field(default_factory=list)
    manual_notes : List[ManualNote]  = field(default_factory=list)

    @property
    def all_events_sorted(self):
        """Tüm olayları zamana göre sıralı döndürür."""
        all_ev = (
            self.step_events +
            self.system_events +
            [ManualNote(n.timestamp, n.text, n.raw_line) for n in self.manual_notes]
        )
        return sorted(all_ev, key=lambda e: e.timestamp)


# ─────────────────────────────────────────────────────────────────
#  Regex desenleri
# ─────────────────────────────────────────────────────────────────

# Timestamp: [2026-03-31 13:50:48]
_RE_TIMESTAMP = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)", re.DOTALL)

# Step satırı: "Step 3/12: A:Port 1, B:Load for 75.0 min"
# Loop prefix opsiyonel: "Step Loop 1/50 (Steps 3-12): "
_RE_STEP = re.compile(
    r"(?:Step Loop (\d+)/(\d+) \(Steps \d+-\d+\):\s*)?"
    r"Step (\d+)/(\d+):\s*"
    r"(?:A:Port (\d+))?"
    r"(?:,?\s*B:(Load|Inject))?"
    r"(?:\s+for ([\d.]+) min)?",
    re.IGNORECASE
)

# Sistem olayları
_SYSTEM_PATTERNS = {
    "started" : re.compile(r"started recipe", re.IGNORECASE),
    "stopped" : re.compile(r"recipe stopped|stopped by user", re.IGNORECASE),
    "paused"  : re.compile(r"recipe paused", re.IGNORECASE),
    "resumed" : re.compile(r"recipe resumed", re.IGNORECASE),
    "error"   : re.compile(r"recipe error|error:", re.IGNORECASE),
}

# Yok sayılacak satırlar (grafik veya annotation'a eklenmez)
_RE_IGNORE = re.compile(
    r"^(switching:|valve [ab] (connected|disconnected)|"
    r"step \d+: duration changed|added step loop|"
    r"session (opened|closed)|[-─]{5,}|"
    r"part:|session opened:|"
    r"step loop \d+/\d+ \(steps \d+-\d+\): switching:)",
    re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────
#  Parser
# ─────────────────────────────────────────────────────────────────

class LogParser:
    """
    session.log dosyasını parse eder.

    Kullanım:
        parser = LogParser()
        result = parser.parse(log_path)
    """

    def parse(self, log_path: str) -> ParseResult:
        """
        Log dosyasını okur ve olayları sınıflandırır.

        Parameters
        ----------
        log_path : str
            session.log dosyasının tam yolu.

        Returns
        -------
        ParseResult
        """
        result = ParseResult()

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return result

        for raw_line in lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            # Timestamp parse
            m_ts = _RE_TIMESTAMP.match(raw_line)
            if not m_ts:
                continue

            ts_str  = m_ts.group(1)
            content = m_ts.group(2).strip()

            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

            if not content:
                continue

            # Yoksay
            if _RE_IGNORE.match(content):
                continue

            # Step Loop prefix'ini soy, içeriği tekrar kontrol et
            # "Step Loop 1/50 (Steps 3-12): Step 3/12: ..."
            content_for_step = content
            loop_info = ""
            m_loop_prefix = re.match(
                r"Step Loop (\d+)/(\d+) \(Steps \d+-\d+\):\s*(.*)", content, re.DOTALL)
            if m_loop_prefix:
                loop_info        = f"Loop {m_loop_prefix.group(1)}/{m_loop_prefix.group(2)}"
                content_for_step = m_loop_prefix.group(3).strip()

            # Step olayı mı?
            if content_for_step.lower().startswith("step "):
                step_ev = self._parse_step(ts, content_for_step, loop_info, raw_line)
                if step_ev:
                    result.step_events.append(step_ev)
                    continue

            # Sistem olayı mı?
            sys_ev = self._parse_system(ts, content, raw_line)
            if sys_ev:
                result.system_events.append(sys_ev)
                continue

            # Manuel not
            result.manual_notes.append(ManualNote(
                timestamp=ts, text=content, raw_line=raw_line))

        return result

    # ── Yardımcılar ───────────────────────────────────────────────

    def _parse_step(
        self,
        ts: datetime,
        content: str,
        loop_info: str,
        raw_line: str,
    ) -> Optional[StepEvent]:
        """'Step N/M: A:Port X, B:Load for Y min' satırını parse eder."""
        m = _RE_STEP.match(content)
        if not m:
            return None

        # Loop grup 1-2 burada kullanılmaz (prefix'te halledildi)
        step_no     = int(m.group(3)) if m.group(3) else None
        total_steps = int(m.group(4)) if m.group(4) else None
        port_a      = int(m.group(5)) if m.group(5) else None
        valve_b     = m.group(6).capitalize() if m.group(6) else None
        duration    = float(m.group(7)) if m.group(7) else None

        if step_no is None or total_steps is None:
            return None

        return StepEvent(
            timestamp   = ts,
            step_no     = step_no,
            total_steps = total_steps,
            port_a      = port_a,
            valve_b     = valve_b,
            duration_min= duration,
            loop_info   = loop_info,
            raw_line    = raw_line,
        )

    def _parse_system(
        self,
        ts: datetime,
        content: str,
        raw_line: str,
    ) -> Optional[SystemEvent]:
        """Recipe started/stopped/paused/resumed satırını parse eder."""
        for event_type, pattern in _SYSTEM_PATTERNS.items():
            if pattern.search(content):
                return SystemEvent(
                    timestamp  = ts,
                    event_type = event_type,
                    detail     = content,
                    raw_line   = raw_line,
                )
        return None


# ─────────────────────────────────────────────────────────────────
#  Kolaylık fonksiyonu
# ─────────────────────────────────────────────────────────────────

def parse_log(log_path: str) -> ParseResult:
    """LogParser().parse(log_path) kısayolu."""
    return LogParser().parse(log_path)
