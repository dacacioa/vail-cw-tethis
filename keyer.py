"""Keyer engine and CW element generation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


KeyingCallback = Callable[[bool], None]


@dataclass
class KeyerSettings:
    keyer_type: str
    wpm: float
    dit_dah_ratio: float
    weighting: float
    paddle_reverse: bool


class KeyerEngine:
    """Generates CW keying state from paddle input."""

    def __init__(self, settings: KeyerSettings, on_keying: Optional[KeyingCallback] = None) -> None:
        self._settings = settings
        self._on_keying = on_keying
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wakeup = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._paddle = {"dit": False, "dah": False}
        self._memory = {"dit": False, "dah": False}
        self._single_ready = {"dit": True, "dah": True}
        self._keying = False
        self._last_element: Optional[str] = None
        self._last_pressed: Optional[str] = None
        self._element_in_progress = False
        self._squeeze_during_element = False
        # Finer scheduler quantum to avoid extra gap growth in rapid dit sequences.
        self._tick_sec = 0.001

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="keyer-thread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wakeup.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def update_settings(self, settings: KeyerSettings) -> None:
        with self._lock:
            self._settings = settings
            self._memory = {"dit": False, "dah": False}
            self._last_element = None
        self._wakeup.set()

    def set_paddle_state(self, dit: bool, dah: bool) -> None:
        with self._lock:
            if self._settings.paddle_reverse:
                dit, dah = dah, dit
            changed = (dit != self._paddle["dit"]) or (dah != self._paddle["dah"])
            if not changed:
                return
            if dit and not self._paddle["dit"]:
                self._memory["dit"] = True
                self._last_pressed = "dit"
            if dah and not self._paddle["dah"]:
                self._memory["dah"] = True
                self._last_pressed = "dah"
            # Keep edge memory latched until consumed by the keyer loop.
            # This avoids losing very short note_on/note_off pairs that can
            # arrive faster than the scheduling interval.
            if not dit:
                self._single_ready["dit"] = True
            if not dah:
                self._single_ready["dah"] = True
            self._paddle["dit"] = dit
            self._paddle["dah"] = dah
        self._wakeup.set()

    def _set_keying(self, state: bool) -> None:
        if state == self._keying:
            return
        self._keying = state
        if self._on_keying:
            self._on_keying(state)

    def _dit_length(self, wpm: float, weighting: float) -> float:
        base = 1.2 / max(wpm, 1.0)
        scale = max(weighting, 10.0) / 50.0
        return base * scale

    def _element_gap(self, wpm: float) -> float:
        return 1.2 / max(wpm, 1.0)

    def _sleep_with_checks(self, duration: float) -> None:
        end_ts = time.perf_counter() + max(0.0, duration)
        while not self._stop_event.is_set():
            now = time.perf_counter()
            remaining = end_ts - now
            if remaining <= 0.0:
                break
            if remaining > self._tick_sec:
                time.sleep(self._tick_sec)
            elif remaining > 0.0004:
                # Yield without forcing a full scheduler tick.
                time.sleep(0)
            else:
                # Final sub-millisecond wait keeps edge timing tight.
                while time.perf_counter() < end_ts:
                    pass
                break
            with self._lock:
                if self._element_in_progress and self._paddle["dit"] and self._paddle["dah"]:
                    self._squeeze_during_element = True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                settings = self._settings
                paddle = dict(self._paddle)
                memory = dict(self._memory)
                single_ready = dict(self._single_ready)
                last_element = self._last_element
                last_pressed = self._last_pressed

            keyer_type = settings.keyer_type
            if keyer_type == "Straight":
                self._set_keying(paddle["dit"] or paddle["dah"])
                self._wakeup.wait(0.02)
                self._wakeup.clear()
                continue

            dit_len = self._dit_length(settings.wpm, settings.weighting)
            dah_len = dit_len * max(settings.dit_dah_ratio, 1.0)
            gap = self._element_gap(settings.wpm)

            if keyer_type in {"Bug", "ElBug"}:
                auto_element = "dit" if keyer_type == "Bug" else "dah"
                manual_element = "dah" if keyer_type == "Bug" else "dit"
                if paddle[manual_element] and not paddle[auto_element]:
                    self._set_keying(True)
                    self._wakeup.wait(0.01)
                    self._wakeup.clear()
                    with self._lock:
                        if not self._paddle[manual_element]:
                            self._set_keying(False)
                    continue
                if paddle[auto_element]:
                    self._send_element(auto_element, dit_len, dah_len, gap, settings)
                    continue
                self._set_keying(False)
                self._wakeup.wait(0.05)
                self._wakeup.clear()
                continue

            if keyer_type == "SingleDot":
                next_element = None
                if paddle["dit"] and single_ready["dit"]:
                    next_element = "dit"
                    with self._lock:
                        self._single_ready["dit"] = False
                elif paddle["dah"] and single_ready["dah"]:
                    next_element = "dah"
                    with self._lock:
                        self._single_ready["dah"] = False
                if next_element:
                    self._send_element(next_element, dit_len, dah_len, gap, settings)
                else:
                    self._set_keying(False)
                    self._wakeup.wait(0.02)
                    self._wakeup.clear()
                continue

            want_dit = paddle["dit"] or memory["dit"]
            want_dah = paddle["dah"] or memory["dah"]
            next_element: Optional[str] = None

            if keyer_type == "Ultimatic":
                if want_dit and want_dah:
                    next_element = last_pressed or "dit"
                elif want_dit:
                    next_element = "dit"
                elif want_dah:
                    next_element = "dah"
            else:
                if want_dit and want_dah:
                    if last_element is None:
                        next_element = "dit"
                    else:
                        next_element = "dah" if last_element == "dit" else "dit"
                elif want_dit:
                    next_element = "dit"
                elif want_dah:
                    next_element = "dah"

            consume = None
            if next_element and memory.get(next_element):
                consume = next_element
            if next_element:
                self._send_element(next_element, dit_len, dah_len, gap, settings, consume)
            else:
                self._set_keying(False)
                self._wakeup.wait(0.02)
                self._wakeup.clear()

    def _send_element(
        self,
        element: str,
        dit_len: float,
        dah_len: float,
        gap: float,
        settings: KeyerSettings,
        consume_memory: Optional[str],
    ) -> None:
        duration = dit_len if element == "dit" else dah_len
        self._element_in_progress = True
        self._squeeze_during_element = False
        on_ts = time.perf_counter()
        self._set_keying(True)
        # Keep element timing anchored even if the callback adds overhead.
        self._sleep_with_checks(max(0.0, duration - (time.perf_counter() - on_ts)))
        off_ts = time.perf_counter()
        self._set_keying(False)
        self._element_in_progress = False
        with self._lock:
            self._last_element = element
            if consume_memory:
                self._memory[consume_memory] = False
            iambic_b = settings.keyer_type in {"IambicB", "Keyahead"}
            if iambic_b and self._squeeze_during_element and not (
                self._paddle["dit"] or self._paddle["dah"]
            ):
                opposite = "dah" if element == "dit" else "dit"
                self._memory[opposite] = True
        # Same compensation for inter-element gap.
        self._sleep_with_checks(max(0.0, gap - (time.perf_counter() - off_ts)))
