from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial


@dataclass
class CatSettings:
    com_port: str
    baudrate: int
    ptt_mode: str
    tx_hang_time_ms: int


class CatController:
    def __init__(self, settings: CatSettings) -> None:
        self._settings = settings
        self._serial: Optional[serial.Serial] = None
        self._ptt_state = False

    def connect(self) -> None:
        if not self._settings.com_port:
            raise RuntimeError("CAT COM port is not configured.")
        self._serial = serial.Serial(
            self._settings.com_port,
            baudrate=self._settings.baudrate,
            timeout=0.5,
        )
        self._serial.setDTR(False)
        self._serial.setRTS(False)

    def close(self) -> None:
        if self._serial:
            self._serial.close()
            self._serial = None

    def update_settings(self, settings: CatSettings) -> None:
        self._settings = settings

    def set_ptt(self, state: bool) -> None:
        if state == self._ptt_state:
            return
        self._ptt_state = state
        mode = self._settings.ptt_mode.upper()
        if mode == "CAT":
            self._send_cat("TX;" if state else "RX;")
        elif mode == "RTS":
            if self._serial:
                self._serial.setRTS(state)
        elif mode == "DTR":
            if self._serial:
                self._serial.setDTR(state)
        if not state and self._settings.tx_hang_time_ms > 0:
            time.sleep(self._settings.tx_hang_time_ms / 1000.0)

    def set_frequency(self, frequency_hz: int) -> None:
        self._send_cat(f"FA{frequency_hz:011d};")

    def _send_cat(self, command: str) -> None:
        if not self._serial:
            return
        self._serial.write(command.encode("ascii"))
        self._serial.flush()
