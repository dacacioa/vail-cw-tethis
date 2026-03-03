"""Single-writer serial keyer controller for RTS-based CW."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

try:
    import serial
except ImportError:  # pragma: no cover - optional dependency
    serial = None


LOG = logging.getLogger(__name__)


@dataclass
class SerialKeyerSettings:
    port: str
    baudrate: int = 9600
    rts_active_high: bool = True
    hang_ms: float = 150.0
    settle_ms: float = 50.0


class SerialKeyerController:
    """Owns the serial port and drives RTS for CW keying."""

    def __init__(self, settings: SerialKeyerSettings, status_cb=None) -> None:
        self._settings = settings
        self._status_cb = status_cb
        self._cmd_q: queue.Queue[Tuple[str, float]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="serial-keyer", daemon=True)
        self._stop = threading.Event()
        self._serial = None
        self._connected = False
        self._tx_state = False
        self._last_on = 0.0
        self._off_at: Optional[float] = None
        self._settle_until = 0.0
        self._thread.start()

    def update_settings(self, settings: SerialKeyerSettings) -> None:
        self._settings = settings
        self._cmd_q.put(("reconnect", time.monotonic()))

    def request_key_down(self, at_time: float) -> None:
        self._cmd_q.put(("down", at_time))

    def request_key_up(self, at_time: float) -> None:
        self._cmd_q.put(("up", at_time))

    def request_idle(self) -> None:
        self._cmd_q.put(("idle", time.monotonic()))

    def request_reconnect(self) -> None:
        self._cmd_q.put(("reconnect", time.monotonic()))

    def test_pulse(self, duration_ms: float = 200.0) -> None:
        now = time.monotonic()
        self.request_key_down(now)
        self.request_key_up(now + duration_ms / 1000.0)

    def stop(self) -> None:
        self._stop.set()
        self._cmd_q.put(("stop", time.monotonic()))
        self._thread.join(timeout=1.0)
        self._close_port()

    def _run(self) -> None:
        self._open_port()
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                cmd, ts = self._cmd_q.get(timeout=0.01)
            except queue.Empty:
                cmd, ts = None, None
            if cmd == "stop":
                break
            if cmd:
                self._handle_cmd(cmd, ts)
            self._check_off(now)
        self._close_port()

    def _handle_cmd(self, cmd: str, ts: float) -> None:
        if cmd == "reconnect":
            self._close_port()
            self._open_port()
            return
        if cmd == "down":
            self._key_down(ts)
            return
        if cmd == "up":
            self._key_up(ts)
            return
        if cmd == "idle":
            self._force_off()
            return

    def _open_port(self) -> None:
        if not serial:
            self._set_status(False, "Serial unavailable (pyserial not installed)")
            return
        if not self._settings.port:
            self._set_status(False, "Serial unavailable (no port set)")
            return
        try:
            self._serial = serial.Serial(
                self._settings.port,
                self._settings.baudrate,
                timeout=0.1,
                write_timeout=0.1,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False,
            )
            self._drive(False)
            self._connected = True
            self._settle_until = time.monotonic() + (self._settings.settle_ms / 1000.0)
            LOG.debug("OPEN serial %s", self._settings.port)
            self._set_status(True, f"Serial connected: {self._settings.port}")
        except Exception as exc:
            self._serial = None
            self._connected = False
            self._set_status(False, f"Serial error: {exc}")

    def _close_port(self) -> None:
        self._drive(False)
        if self._serial:
            try:
                self._serial.close()
                LOG.debug("CLOSE serial")
            except Exception:
                pass
        self._serial = None
        self._connected = False

    def _drive(self, active: bool) -> None:
        if not self._serial:
            return
        try:
            level = active if self._settings.rts_active_high else not active
            self._serial.rts = level
            self._serial.dtr = False
            LOG.debug("SET RTS %s", "ON" if active else "OFF")
        except Exception as exc:
            self._set_status(False, f"Serial error: {exc}")
            self._connected = False

    def _key_down(self, ts: float) -> None:
        now = time.monotonic()
        if not self._connected:
            self._open_port()
        if not self._connected:
            return
        if now < self._settle_until:
            LOG.debug("Skip KEY_DOWN during settle")
            return
        if self._tx_state:
            return
        when = max(ts, now)
        if when > now:
            time.sleep(max(0.0, when - now))
        self._drive(True)
        self._tx_state = True
        self._last_on = time.monotonic()
        self._off_at = None
        LOG.debug("KEY_DOWN at t=%.6f", self._last_on)

    def _key_up(self, ts: float) -> None:
        if not self._tx_state:
            return
        now = time.monotonic()
        target = max(ts, self._last_on)
        hang_target = ts + (self._settings.hang_ms / 1000.0)
        target = max(target, hang_target)
        self._off_at = target

    def _check_off(self, now: float) -> None:
        if self._tx_state and self._off_at and now >= self._off_at:
            self._drive(False)
            self._tx_state = False
            LOG.debug("KEY_UP at t=%.6f (on duration %.3f ms)", now, (now - self._last_on) * 1000.0)
            self._off_at = None

    def _force_off(self) -> None:
        if self._tx_state:
            self._drive(False)
            LOG.debug("FORCE OFF")
        self._tx_state = False
        self._off_at = None

    def _set_status(self, connected: bool, msg: str) -> None:
        LOG.debug("%s", msg)
        if self._status_cb:
            self._status_cb(connected, msg)
