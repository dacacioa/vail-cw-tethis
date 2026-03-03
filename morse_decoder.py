"""Realtime Morse decoder based on key up/down timing."""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional


MORSE_TABLE: Dict[str, str] = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",
    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9",
    ".-.-.-": ".",
    "--..--": ",",
    "..--..": "?",
    "-..-.": "/",
    "-...-": "=",
}


class MorseDecoder:
    """Decode Morse from key transitions using configured or adaptive timing."""

    def __init__(self, unit_seconds_provider: Callable[[], float], auto_speed: bool = False) -> None:
        self._unit_seconds_provider = unit_seconds_provider
        self._auto_speed = bool(auto_speed)
        self._lock = threading.Lock()
        self._is_down = False
        self._down_since: Optional[float] = None
        self._last_up: Optional[float] = None
        self._symbols: List[str] = []
        self._text_buffer: List[str] = []
        self._char_committed = False
        self._word_space_emitted = False
        self._auto_unit: Optional[float] = None
        self._mark_history: List[float] = []
        self._gap_history: List[float] = []

    def reset(self) -> None:
        with self._lock:
            self._is_down = False
            self._down_since = None
            self._last_up = None
            self._symbols.clear()
            self._text_buffer.clear()
            self._char_committed = False
            self._word_space_emitted = False
            self._auto_unit = None
            self._mark_history.clear()
            self._gap_history.clear()

    def on_keying(self, active: bool, ts: Optional[float] = None) -> None:
        now = ts if ts is not None else time.monotonic()
        with self._lock:
            if active == self._is_down:
                return

            if active:
                if self._last_up is not None:
                    self._observe_gap(now - self._last_up)
                self._commit_gap_locked(now)
                self._is_down = True
                self._down_since = now
                return

            if self._down_since is None:
                self._is_down = False
                return

            duration = max(0.0, now - self._down_since)
            unit_before = self._unit()
            self._observe_mark(duration)
            self._symbols.append("." if duration <= (unit_before * 2.0) else "-")
            self._is_down = False
            self._down_since = None
            self._last_up = now
            self._char_committed = False
            self._word_space_emitted = False

    def poll(self, ts: Optional[float] = None) -> None:
        now = ts if ts is not None else time.monotonic()
        with self._lock:
            self._commit_gap_locked(now)

    def read_text(self) -> str:
        with self._lock:
            if not self._text_buffer:
                return ""
            text = "".join(self._text_buffer)
            self._text_buffer.clear()
            return text

    def estimated_wpm(self) -> Optional[float]:
        with self._lock:
            if not self._auto_speed:
                return None
            unit = self._auto_unit
        if unit is None:
            return None
        return max(4.0, min(80.0, 1.2 / max(unit, 0.015)))

    def _commit_gap_locked(self, now: float) -> None:
        if self._is_down or self._last_up is None:
            return
        gap = now - self._last_up
        unit = self._unit()

        if self._symbols and not self._char_committed and gap >= (unit * 2.5):
            code = "".join(self._symbols)
            decoded = MORSE_TABLE.get(code)
            if decoded:
                self._text_buffer.append(decoded)
            self._symbols.clear()
            self._char_committed = True

        if self._char_committed and not self._word_space_emitted and gap >= (unit * 6.0):
            self._text_buffer.append(" ")
            self._word_space_emitted = True

    def _observe_mark(self, duration: float) -> None:
        if not self._auto_speed:
            return
        if duration <= 0.0 or duration > 2.0:
            return

        if 0.01 <= duration <= 1.2:
            self._mark_history.append(duration)
            if len(self._mark_history) > 48:
                self._mark_history = self._mark_history[-48:]

        observed = self._unit_from_history(self._mark_history)
        if observed is not None:
            self._observe_unit(observed, alpha=0.50)

    def _observe_gap(self, gap: float) -> None:
        if not self._auto_speed:
            return
        if gap <= 0.0 or gap > 2.0:
            return

        if 0.01 <= gap <= 0.5:
            self._gap_history.append(gap)
            if len(self._gap_history) > 48:
                self._gap_history = self._gap_history[-48:]

        observed = self._unit_from_history(self._gap_history)
        if observed is not None:
            self._observe_unit(observed, alpha=0.35)

    def _unit_from_history(self, values: List[float]) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) < 4:
            return ordered[0]
        idx = max(0, int((len(ordered) - 1) * 0.25))
        return ordered[idx]

    def _observe_unit(self, observed: float, alpha: float) -> None:
        observed = max(0.015, min(0.30, float(observed)))
        if self._auto_unit is None:
            self._auto_unit = observed
            return
        self._auto_unit = ((1.0 - alpha) * self._auto_unit) + (alpha * observed)

    def _unit(self) -> float:
        if self._auto_speed and self._auto_unit is not None:
            return max(0.015, min(0.30, self._auto_unit))
        try:
            value = float(self._unit_seconds_provider())
        except Exception:
            value = 0.06
        return max(0.015, min(0.30, value))
