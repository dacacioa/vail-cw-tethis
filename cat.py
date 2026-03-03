"""CAT / PTT control for TX keying."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

try:
    import serial
except ImportError:  # pragma: no cover - optional dependency
    serial = None


StatusCallback = Callable[[bool, str], None]
PortStatusCallback = Callable[[bool, str], None]


@dataclass
class CatSettings:
    port: str
    baudrate: int
    ptt_method: str
    thetis_key_line: str
    thetis_ptt_line: str
    thetis_key_invert: bool
    thetis_ptt_invert: bool
    hang_time: float


def _parse_rigctld(port: str) -> Optional[Tuple[str, int]]:
    if not port.startswith("rigctld://"):
        return None
    target = port[len("rigctld://") :]
    if ":" not in target:
        return None
    host, port_str = target.split(":", 1)
    try:
        return host, int(port_str)
    except ValueError:
        return None


class CatController:
    """Manages CAT/PTT switching with optional hang time."""

    def __init__(
        self,
        settings: CatSettings,
        on_status: Optional[StatusCallback] = None,
        on_port_status: Optional[PortStatusCallback] = None,
    ) -> None:
        self._settings = settings
        self._on_status = on_status
        self._on_port_status = on_port_status
        self._serial = None
        self._lock = threading.Lock()
        self._cw_active = False
        self._manual_active = False
        self._tx_state = False
        self._thetis_key_state = False
        self._thetis_ptt_state = False
        self._release_timer: Optional[threading.Timer] = None

    def start(self) -> None:
        self._open()

    def stop(self) -> None:
        self._cancel_hang()
        if self._settings.ptt_method == "THETIS_DSP":
            self._set_thetis_key(False, allow_open=False)
            self._set_thetis_ptt(False, allow_open=False)
        else:
            self._set_lines(False)
        self._thetis_key_state = False
        self._thetis_ptt_state = False
        self._close_serial()
        self._notify_port(False, "Disconnected")
        self._update_tx_status(False)

    def update_settings(self, settings: CatSettings) -> None:
        restart = (
            self._settings.port != settings.port
            or self._settings.baudrate != settings.baudrate
            or self._settings.ptt_method != settings.ptt_method
            or self._settings.thetis_key_line != settings.thetis_key_line
            or self._settings.thetis_ptt_line != settings.thetis_ptt_line
            or self._settings.thetis_key_invert != settings.thetis_key_invert
            or self._settings.thetis_ptt_invert != settings.thetis_ptt_invert
        )
        self._settings = settings
        if restart:
            self.stop()
            self.start()

    def request_cw(self, active: bool) -> None:
        with self._lock:
            self._cw_active = active
        self._apply_tx()

    def set_manual(self, active: bool) -> None:
        with self._lock:
            self._manual_active = active
        self._apply_tx()

    def _apply_tx(self) -> None:
        with self._lock:
            cw_active = self._cw_active
            manual_active = self._manual_active
            hang = max(self._settings.hang_time, 0.0)
            method = self._settings.ptt_method
        if method == "THETIS_DSP":
            self._apply_thetis_dsp(cw_active, manual_active, hang)
            return

        desired = cw_active or manual_active
        if desired:
            self._cancel_hang()
            self._set_tx(desired)
            return
        if hang <= 0:
            self._set_tx(False)
            return
        if self._release_timer:
            return
        self._release_timer = threading.Timer(hang, self._release_after_hang)
        self._release_timer.daemon = True
        self._release_timer.start()

    def _apply_thetis_dsp(self, cw_active: bool, manual_active: bool, hang: float) -> None:
        key_on = cw_active
        ptt_on = cw_active or manual_active
        self._set_thetis_key(key_on)

        if ptt_on:
            self._cancel_hang()
            self._set_thetis_ptt(True)
            self._publish_thetis_tx_state()
            return

        if hang <= 0:
            self._set_thetis_ptt(False)
            self._publish_thetis_tx_state()
            return

        if not self._release_timer:
            self._release_timer = threading.Timer(hang, self._release_after_hang)
            self._release_timer.daemon = True
            self._release_timer.start()
        self._publish_thetis_tx_state()

    def _release_after_hang(self) -> None:
        with self._lock:
            cw_active = self._cw_active
            manual_active = self._manual_active
            method = self._settings.ptt_method
            self._release_timer = None
        if cw_active or manual_active:
            return
        if method == "THETIS_DSP":
            self._set_thetis_ptt(False)
            self._publish_thetis_tx_state()
            return
        self._set_tx(False)

    def _set_tx(self, active: bool) -> None:
        if active == self._tx_state:
            return
        ok = self._send_ptt(active)
        if ok:
            self._update_tx_status(active)

    def _update_tx_status(self, active: bool) -> None:
        if active == self._tx_state:
            return
        self._tx_state = active
        if self._on_status:
            self._on_status(active, "TX" if active else "RX")

    def _publish_thetis_tx_state(self) -> None:
        line_active = self._thetis_key_state or self._thetis_ptt_state
        self._update_tx_status(line_active)

    def _open(self) -> None:
        if not self._settings.port or self._settings.ptt_method == "HAMLIB":
            return
        if self._settings.ptt_method == "CAT" and _parse_rigctld(self._settings.port):
            return
        if not serial:
            self._notify_port(False, "Serial unavailable (pyserial not installed)")
            return
        try:
            self._serial = serial.Serial(
                self._settings.port,
                self._settings.baudrate,
                timeout=0.5,
                write_timeout=0.5,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False,
            )
            self._initialize_line_states()
            self._notify_port(True, f"Connected {self._settings.port}")
        except Exception as exc:
            self._serial = None
            self._notify_port(False, f"Serial error opening {self._settings.port}: {exc}")

    def _ensure_serial(self, context: str) -> bool:
        if self._serial:
            return True
        self._open()
        if self._serial:
            return True
        self._notify_port(False, f"No serial for {context}")
        return False

    def _send_ptt(self, active: bool) -> bool:
        method = self._settings.ptt_method
        rig = _parse_rigctld(self._settings.port)
        if method == "CAT" and rig:
            return self._send_rigctld(active, rig)
        if method == "CAT":
            return self._send_cat(active)
        if method == "RTS":
            return self._drive_line("RTS", active, invert=False, context="RTS")
        if method == "DTR":
            return self._drive_line("DTR", active, invert=False, context="DTR")
        return False

    def _send_cat(self, active: bool) -> bool:
        if not self._ensure_serial("CAT"):
            return False
        command = b"TX;" if active else b"RX;"
        try:
            self._serial.write(command)
            self._serial.flush()
            return True
        except Exception:
            self._notify_port(False, "Serial error CAT")
            self._close_serial()
            return False

    def _set_thetis_key(self, active: bool, allow_open: bool = True) -> None:
        if active == self._thetis_key_state:
            return
        line = self._normalize_line(self._settings.thetis_key_line)
        if line == "NONE":
            self._thetis_key_state = False
            return
        ok = self._drive_line(
            line,
            active,
            invert=bool(self._settings.thetis_key_invert),
            context=f"Thetis key ({line})",
            allow_open=allow_open,
        )
        if ok:
            self._thetis_key_state = active

    def _set_thetis_ptt(self, active: bool, allow_open: bool = True) -> None:
        if active == self._thetis_ptt_state:
            return
        line = self._normalize_line(self._settings.thetis_ptt_line)
        if line == "NONE":
            self._thetis_ptt_state = False
            return
        ok = self._drive_line(
            line,
            active,
            invert=bool(self._settings.thetis_ptt_invert),
            context=f"Thetis PTT ({line})",
            allow_open=allow_open,
        )
        if ok:
            self._thetis_ptt_state = active

    def _normalize_line(self, line: str) -> str:
        value = (line or "None").strip().upper()
        if value in {"RTS", "DTR"}:
            return value
        return "NONE"

    def _drive_line(self, line: str, active: bool, invert: bool, context: str, allow_open: bool = True) -> bool:
        if line == "NONE":
            return True
        if allow_open:
            if not self._ensure_serial(context):
                return False
        elif not self._serial:
            return False
        try:
            level = (not active) if invert else active
            if line == "RTS":
                self._serial.rts = level
            else:
                self._serial.dtr = level
            return True
        except Exception:
            self._notify_port(False, f"Serial error {context}")
            self._close_serial()
            return False

    def _set_lines(self, active: bool) -> None:
        if not self._serial:
            return
        try:
            self._serial.rts = active
            self._serial.dtr = active
        except Exception:
            pass

    def _initialize_line_states(self) -> None:
        if not self._serial:
            return
        self._set_lines(False)
        if self._settings.ptt_method != "THETIS_DSP":
            return
        key_line = self._normalize_line(self._settings.thetis_key_line)
        ptt_line = self._normalize_line(self._settings.thetis_ptt_line)
        if key_line != "NONE":
            self._drive_line(
                key_line,
                active=False,
                invert=bool(self._settings.thetis_key_invert),
                context=f"Thetis key ({key_line})",
                allow_open=False,
            )
        if ptt_line != "NONE":
            self._drive_line(
                ptt_line,
                active=False,
                invert=bool(self._settings.thetis_ptt_invert),
                context=f"Thetis PTT ({ptt_line})",
                allow_open=False,
            )
        self._thetis_key_state = False
        self._thetis_ptt_state = False

    def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def release(self) -> None:
        """Force lines low and clear tx state."""
        with self._lock:
            self._cw_active = False
            self._manual_active = False
        self._cancel_hang()
        if self._settings.ptt_method == "THETIS_DSP":
            self._set_thetis_key(False, allow_open=False)
            self._set_thetis_ptt(False, allow_open=False)
        else:
            self._set_lines(False)
        self._thetis_key_state = False
        self._thetis_ptt_state = False
        self._update_tx_status(False)
        self._notify_port(True, f"Connected {self._settings.port}" if self._serial else "Disconnected")

    def _cancel_hang(self) -> None:
        if self._release_timer:
            self._release_timer.cancel()
            self._release_timer = None

    def _notify_port(self, connected: bool, message: str) -> None:
        if self._on_port_status:
            self._on_port_status(connected, message)

    def _send_rigctld(self, active: bool, target: Tuple[str, int]) -> bool:
        host, port = target
        try:
            with socket.create_connection((host, port), timeout=0.5) as sock:
                cmd = f"T {1 if active else 0}\n".encode("ascii")
                sock.sendall(cmd)
            return True
        except Exception:
            return False
