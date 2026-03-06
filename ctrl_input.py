"""Keyboard Ctrl input handling (left/right Ctrl as paddles)."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from typing import Callable, Optional, Tuple


LOGGER = logging.getLogger(__name__)

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_RMENU = 0xA5
LEFT_CTRL_KEYCODES = {162}
RIGHT_CTRL_KEYCODES = {163, 165}
LEFT_CTRL_KEYSYM_NUMS = {65507}
RIGHT_CTRL_KEYSYM_NUMS = {65508, 65027}
EVENT_OVERRIDE_SEC = 0.25
LEFT_PRESS_CONFIRM_SEC = 0.004

PaddleCallback = Callable[[bool, bool], None]
StatusCallback = Callable[[bool, str], None]


class CtrlKeyboardInput:
    """Reads global left/right Ctrl state and maps to DIT/DAH."""

    def __init__(
        self,
        on_paddle: PaddleCallback,
        on_status: Optional[StatusCallback] = None,
        poll_interval_sec: float = 0.003,
    ) -> None:
        self._on_paddle = on_paddle
        self._on_status = on_status
        self._poll_interval_sec = max(0.001, poll_interval_sec)
        self._enabled = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state: Tuple[bool, bool] = (False, False)
        self._event_dit = False
        self._event_dah = False
        self._event_override_until = 0.0
        self._pending_left_press = False
        self._pending_left_deadline = 0.0
        self._lock = threading.Lock()
        self._user32 = None

        if os.name == "nt":
            try:
                self._user32 = ctypes.windll.user32
            except Exception as exc:
                LOGGER.warning("Ctrl input unavailable (user32): %s", exc)
                self._user32 = None

    def handle_key_event(self, key: str, keycode: int, keysym_num: int, pressed: bool) -> None:
        """Fast path from UI key events when the app window has focus."""
        with self._lock:
            if not self._enabled:
                return
        side, confident = self._resolve_event_side(key, keycode, keysym_num)
        if not side:
            return
        now = time.monotonic()
        with self._lock:
            if side == "right":
                self._pending_left_press = False
                self._pending_left_deadline = 0.0
                if pressed:
                    self._event_dit = False
                    self._event_dah = True
                else:
                    self._event_dah = False
            else:
                # Ignore synthetic left-control events while DAH is active.
                if self._event_dah and pressed:
                    return
                if pressed:
                    if confident or not self._is_right_alt_pressed():
                        # Trusted left-control event: apply immediately.
                        self._pending_left_press = False
                        self._pending_left_deadline = 0.0
                        self._event_dit = True
                    else:
                        # Ambiguous left event: delay a few ms so it can be
                        # canceled if part of right-control / AltGr composite.
                        self._pending_left_press = True
                        self._pending_left_deadline = now + LEFT_PRESS_CONFIRM_SEC
                else:
                    self._pending_left_press = False
                    self._pending_left_deadline = 0.0
                    self._event_dit = False

            next_state = (self._event_dit, self._event_dah)
            if next_state == self._state:
                self._event_override_until = now + EVENT_OVERRIDE_SEC
                return
            self._state = next_state
            self._event_override_until = now + EVENT_OVERRIDE_SEC
        self._on_paddle(next_state[0], next_state[1])

    def _resolve_event_side(self, key: str, keycode: int, keysym_num: int) -> Tuple[str, bool]:
        key_name = (key or "").strip()
        if keycode in RIGHT_CTRL_KEYCODES or keysym_num in RIGHT_CTRL_KEYSYM_NUMS:
            return "right", True
        if keycode in LEFT_CTRL_KEYCODES or keysym_num in LEFT_CTRL_KEYSYM_NUMS:
            return "left", True
        if key_name in {"Alt_R", "ISO_Level3_Shift", "Mode_switch", "Control_R"}:
            return "right", False
        if key_name == "Control_L":
            return "left", False
        return "", False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ctrl-keyboard-input", daemon=True)
        self._thread.start()
        self._emit_status(False, self._status_label())

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._emit_state(False, False)
        self._emit_status(False, "stopped")

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                self._event_dit = False
                self._event_dah = False
                self._event_override_until = 0.0
                self._pending_left_press = False
                self._pending_left_deadline = 0.0
        if not self._enabled:
            self._emit_state(False, False)
        self._emit_status(self._enabled and self._is_available(), self._status_label())

    def _is_available(self) -> bool:
        return self._user32 is not None

    def _is_right_alt_pressed(self) -> bool:
        if not self._is_available():
            return False
        try:
            return bool(self._user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
        except Exception:
            return False

    def _status_label(self) -> str:
        if not self._is_available():
            return "unsupported (Windows only)"
        return "listening" if self._enabled else "disabled"

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            emit_state: Optional[Tuple[bool, bool]] = None
            with self._lock:
                enabled = self._enabled
                if (
                    self._pending_left_press
                    and now >= self._pending_left_deadline
                    and not self._event_dah
                ):
                    self._pending_left_press = False
                    self._pending_left_deadline = 0.0
                    self._event_dit = True
                    next_state = (self._event_dit, self._event_dah)
                    if next_state != self._state:
                        self._state = next_state
                        self._event_override_until = now + EVENT_OVERRIDE_SEC
                        emit_state = next_state
                event_override = (
                    self._event_dit
                    or self._event_dah
                    or self._pending_left_press
                    or (now < self._event_override_until)
                )

            if not enabled or not self._is_available():
                time.sleep(0.05)
                continue
            if emit_state is not None:
                self._on_paddle(emit_state[0], emit_state[1])
            if event_override:
                time.sleep(self._poll_interval_sec)
                continue

            dit, dah = self._read_ctrl_state()
            self._emit_state(dit, dah)
            time.sleep(self._poll_interval_sec)

    def _read_ctrl_state(self) -> Tuple[bool, bool]:
        if not self._is_available():
            return False, False
        try:
            left = bool(self._user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000)
            right = bool(self._user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000)
            right_alt = bool(self._user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
            # Some devices/layouts expose right-ctrl as AltGr (RAlt + LCtrl).
            if right_alt and not right:
                right = True
                left = False
            return left, right
        except Exception as exc:
            LOGGER.warning("Ctrl key polling error: %s", exc)
            return False, False

    def _emit_state(self, dit: bool, dah: bool) -> None:
        next_state = (bool(dit), bool(dah))
        with self._lock:
            if next_state == self._state:
                return
            self._state = next_state
        self._on_paddle(next_state[0], next_state[1])

    def _emit_status(self, connected: bool, label: str) -> None:
        if self._on_status:
            self._on_status(connected, label)
