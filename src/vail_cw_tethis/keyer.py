from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


class KeyerType(str, enum.Enum):
    STRAIGHT = "Straight"
    BUG = "Bug"
    IAMBIC_A = "IambicA"
    IAMBIC_B = "IambicB"
    ULTIMATIC = "Ultimatic"
    SINGLE_DOT = "SingleDot"
    ELBUG = "ElBug"
    PLAIN_IAMBIC = "PlainIambic"
    KEYAHEAD = "Keyahead"


@dataclass
class KeyerSettings:
    keyer_type: KeyerType
    wpm: int
    dit_dah_ratio: float
    weighting: float
    paddle_reverse: bool


class KeyerEngine:
    def __init__(self, settings: KeyerSettings, on_key: Callable[[bool], None]) -> None:
        self._settings = settings
        self._on_key = on_key
        self._lock = threading.Lock()
        self._dit_pressed = False
        self._dah_pressed = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._key_down = False
        self._last_element = None
        self._iambic_b_queue = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        self._set_key(False)

    def update_settings(self, settings: KeyerSettings) -> None:
        with self._lock:
            self._settings = settings

    def set_paddles(self, dit: bool, dah: bool) -> None:
        with self._lock:
            if self._settings.paddle_reverse:
                self._dit_pressed = dah
                self._dah_pressed = dit
            else:
                self._dit_pressed = dit
                self._dah_pressed = dah

    def _timings(self) -> tuple[float, float, float]:
        with self._lock:
            dit_len = 1.2 / max(self._settings.wpm, 1)
            weight_factor = max(self._settings.weighting, 10.0) / 50.0
            dit_len *= weight_factor
            dah_len = dit_len * self._settings.dit_dah_ratio
            elem_gap = dit_len
        return dit_len, dah_len, elem_gap

    def _run(self) -> None:
        while self._running:
            keyer_type = self._settings.keyer_type
            if keyer_type == KeyerType.STRAIGHT:
                self._run_straight()
            elif keyer_type == KeyerType.BUG:
                self._run_bug()
            elif keyer_type in (KeyerType.IAMBIC_A, KeyerType.IAMBIC_B, KeyerType.PLAIN_IAMBIC):
                self._run_iambic(mode_b=keyer_type == KeyerType.IAMBIC_B)
            elif keyer_type == KeyerType.ULTIMATIC:
                self._run_ultimatic()
            elif keyer_type == KeyerType.SINGLE_DOT:
                self._run_single_dot()
            elif keyer_type == KeyerType.ELBUG:
                self._run_elbug()
            elif keyer_type == KeyerType.KEYAHEAD:
                self._run_keyahead()
            else:
                time.sleep(0.01)

    def _set_key(self, state: bool) -> None:
        if self._key_down != state:
            self._key_down = state
            self._on_key(state)

    def _current_paddles(self) -> tuple[bool, bool]:
        with self._lock:
            return self._dit_pressed, self._dah_pressed

    def _run_straight(self) -> None:
        dit_pressed, dah_pressed = self._current_paddles()
        self._set_key(dit_pressed or dah_pressed)
        time.sleep(0.005)

    def _run_bug(self) -> None:
        dit_len, dah_len, gap = self._timings()
        dit_pressed, dah_pressed = self._current_paddles()
        if dit_pressed and not dah_pressed:
            self._send_element(dit_len, gap)
        elif dah_pressed:
            self._set_key(True)
            time.sleep(0.01)
        else:
            self._set_key(False)
            time.sleep(0.005)

    def _run_iambic(self, mode_b: bool) -> None:
        dit_len, dah_len, gap = self._timings()
        dit_pressed, dah_pressed = self._current_paddles()
        if not (dit_pressed or dah_pressed or self._iambic_b_queue):
            self._set_key(False)
            time.sleep(0.005)
            return
        if self._iambic_b_queue:
            self._iambic_b_queue = False
            element = self._alternate_element(dit_pressed, dah_pressed)
        else:
            element = self._select_element(dit_pressed, dah_pressed)
        if element is None:
            self._set_key(False)
            time.sleep(0.005)
            return
        length = dit_len if element == "dit" else dah_len
        self._send_element(length, gap)
        if mode_b:
            dit_pressed, dah_pressed = self._current_paddles()
            if dit_pressed and dah_pressed:
                self._iambic_b_queue = True

    def _run_ultimatic(self) -> None:
        dit_len, dah_len, gap = self._timings()
        dit_pressed, dah_pressed = self._current_paddles()
        if not (dit_pressed or dah_pressed):
            self._set_key(False)
            time.sleep(0.005)
            return
        if dit_pressed and dah_pressed:
            element = "dit" if self._last_element == "dit" else "dah"
        else:
            element = "dit" if dit_pressed else "dah"
        length = dit_len if element == "dit" else dah_len
        self._last_element = element
        self._send_element(length, gap)

    def _run_single_dot(self) -> None:
        dit_len, _, gap = self._timings()
        dit_pressed, dah_pressed = self._current_paddles()
        if dit_pressed or dah_pressed:
            self._send_element(dit_len, gap)
        else:
            self._set_key(False)
            time.sleep(0.01)

    def _run_elbug(self) -> None:
        dit_len, dah_len, gap = self._timings()
        dit_pressed, dah_pressed = self._current_paddles()
        if dit_pressed and not dah_pressed:
            self._send_element(dit_len, gap)
        elif dah_pressed and not dit_pressed:
            self._send_element(dah_len, gap)
        elif dit_pressed and dah_pressed:
            element = self._alternate_element(True, True)
            length = dit_len if element == "dit" else dah_len
            self._send_element(length, gap)
        else:
            self._set_key(False)
            time.sleep(0.005)

    def _run_keyahead(self) -> None:
        dit_len, dah_len, gap = self._timings()
        dit_pressed, dah_pressed = self._current_paddles()
        element = self._select_element(dit_pressed, dah_pressed)
        if element is None:
            self._set_key(False)
            time.sleep(0.005)
            return
        length = dit_len if element == "dit" else dah_len
        self._send_element(length, gap)

    def _select_element(self, dit_pressed: bool, dah_pressed: bool) -> Optional[str]:
        if dit_pressed and dah_pressed:
            return self._alternate_element(dit_pressed, dah_pressed)
        if dit_pressed:
            self._last_element = "dit"
            return "dit"
        if dah_pressed:
            self._last_element = "dah"
            return "dah"
        return None

    def _alternate_element(self, dit_pressed: bool, dah_pressed: bool) -> Optional[str]:
        if not (dit_pressed and dah_pressed):
            return self._select_element(dit_pressed, dah_pressed)
        if self._last_element == "dit":
            self._last_element = "dah"
        else:
            self._last_element = "dit"
        return self._last_element

    def _send_element(self, length: float, gap: float) -> None:
        self._set_key(True)
        time.sleep(length)
        self._set_key(False)
        time.sleep(gap)
