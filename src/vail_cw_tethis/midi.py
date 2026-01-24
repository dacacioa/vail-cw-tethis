from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import threading
import time
from typing import Callable, Optional

import mido

from vail_cw_tethis.keyer import KeyerType


@dataclass
class MidiMapping:
    dit_note: int = 60
    dah_note: int = 61
    straight_note: int = 62
    dit_cc: Optional[int] = None
    dah_cc: Optional[int] = None


class MidiKeyer:
    def __init__(
        self,
        device_name: str,
        on_paddle: Callable[[bool, bool], None],
        mapping: MidiMapping | None = None,
        auto_map: bool = True,
    ) -> None:
        self._device_name = device_name
        self._on_paddle = on_paddle
        self._mapping = mapping or MidiMapping()
        self._auto_map = auto_map
        self._input: Optional[mido.ports.BaseInput] = None
        self._output: Optional[mido.ports.BaseOutput] = None
        self._dit_down = False
        self._dah_down = False
        self._auto_notes: list[int] = []
        self._auto_ccs: list[int] = []
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(__name__)
        self._last_keyer_type: Optional[KeyerType] = None
        self._last_wpm: Optional[int] = None
        self._last_sidetone_freq: Optional[float] = None

    @staticmethod
    def auto_detect(preferred_name: str = "") -> str:
        names = mido.get_input_names()
        if preferred_name:
            for name in names:
                if preferred_name.lower() in name.lower():
                    return name
        for name in names:
            if "vail" in name.lower() or "cw" in name.lower():
                return name
        return names[0] if names else ""

    @staticmethod
    def list_inputs() -> list[str]:
        return mido.get_input_names()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._connect_ports()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1)
            self._monitor_thread = None
        self._close_ports()

    def _monitor_loop(self) -> None:
        while self._running:
            if self._input is not None and getattr(self._input, "closed", False):
                self._close_ports()
            if self._input is None:
                self._connect_ports()
            time.sleep(2.0)

    def _connect_ports(self) -> None:
        device = self.auto_detect(self._device_name)
        if not device:
            self._logger.warning("No MIDI input devices found.")
            return
        try:
            self._input = mido.open_input(device, callback=self._handle_message)
        except OSError as exc:
            self._logger.warning("Failed to open MIDI input '%s': %s", device, exc)
            self._input = None
            return
        output_device = self._auto_detect_output(device)
        if output_device:
            try:
                self._output = mido.open_output(output_device)
            except OSError as exc:
                self._logger.warning("Failed to open MIDI output '%s': %s", output_device, exc)
                self._output = None
        else:
            self._logger.warning("No MIDI output device found for '%s'. Using input-only mode.", device)
        self._send_midi_mode()
        if self._last_keyer_type:
            self.send_keyer_type(self._last_keyer_type)
        if self._last_wpm:
            self.send_wpm(self._last_wpm)
        if self._last_sidetone_freq:
            self.send_sidetone_frequency(self._last_sidetone_freq)

    def _close_ports(self) -> None:
        if self._input:
            self._input.close()
            self._input = None
        if self._output:
            self._output.close()
            self._output = None

    @staticmethod
    def _auto_detect_output(device_name: str) -> str:
        outputs = mido.get_output_names()
        for name in outputs:
            if device_name.lower() in name.lower():
                return name
        base_name = device_name.rsplit(" ", 1)[0].lower()
        for name in outputs:
            if base_name and base_name in name.lower():
                return name
        return ""

    def _send_midi_mode(self) -> None:
        if not self._output:
            return
        try:
            self._output.send(mido.Message("control_change", channel=0, control=0, value=0))
        except OSError as exc:
            self._logger.warning("Failed to send MIDI mode message: %s", exc)

    def send_keyer_type(self, keyer_type: KeyerType) -> None:
        self._last_keyer_type = keyer_type
        program_map = {
            KeyerType.STRAIGHT: 1,
            KeyerType.BUG: 2,
            KeyerType.ELBUG: 3,
            KeyerType.SINGLE_DOT: 4,
            KeyerType.ULTIMATIC: 5,
            KeyerType.PLAIN_IAMBIC: 6,
            KeyerType.IAMBIC_A: 7,
            KeyerType.IAMBIC_B: 8,
            KeyerType.KEYAHEAD: 9,
        }
        program = program_map.get(keyer_type)
        if program is None:
            return
        if not self._output:
            return
        try:
            self._output.send(mido.Message("program_change", channel=0, program=program))
        except OSError as exc:
            self._logger.warning("Failed to send keyer program change: %s", exc)

    def send_wpm(self, wpm: int) -> None:
        self._last_wpm = wpm
        dit_ms = 1200 / max(wpm, 5)
        cc_value = min(int(dit_ms / 2), 127)
        if not self._output:
            return
        try:
            self._output.send(mido.Message("control_change", channel=0, control=1, value=cc_value))
        except OSError as exc:
            self._logger.warning("Failed to send WPM: %s", exc)

    def send_sidetone_frequency(self, freq_hz: float) -> None:
        self._last_sidetone_freq = freq_hz
        if freq_hz <= 0:
            return
        note = int(round(12 * math.log2(freq_hz / 440.0) + 69))
        note = max(0, min(127, note))
        if not self._output:
            return
        try:
            self._output.send(mido.Message("control_change", channel=0, control=2, value=note))
        except OSError as exc:
            self._logger.warning("Failed to send sidetone frequency: %s", exc)

    def _handle_message(self, message: mido.Message) -> None:
        if message.type not in ("note_on", "note_off", "control_change"):
            return
        if message.type == "control_change":
            is_down = message.value > 0
            if self._auto_map and message.control not in (self._mapping.dit_cc, self._mapping.dah_cc):
                if message.control not in self._auto_ccs and len(self._auto_ccs) < 2:
                    self._auto_ccs.append(message.control)
                    if len(self._auto_ccs) == 1:
                        self._mapping.dit_cc = message.control
                    elif len(self._auto_ccs) == 2:
                        self._mapping.dah_cc = message.control
            if self._mapping.dit_cc is not None and message.control == self._mapping.dit_cc:
                self._dit_down = is_down
            elif self._mapping.dah_cc is not None and message.control == self._mapping.dah_cc:
                self._dah_down = is_down
            self._on_paddle(self._dit_down, self._dah_down)
            return
        note = message.note
        is_down = message.type == "note_on" and message.velocity > 0
        if self._auto_map and note not in (
            self._mapping.dit_note,
            self._mapping.dah_note,
            self._mapping.straight_note,
        ):
            if note not in self._auto_notes and len(self._auto_notes) < 2:
                self._auto_notes.append(note)
                if len(self._auto_notes) == 1:
                    self._mapping.dit_note = note
                elif len(self._auto_notes) == 2:
                    self._mapping.dah_note = note
        if note == self._mapping.dit_note:
            self._dit_down = is_down
        elif note == self._mapping.dah_note:
            self._dah_down = is_down
        elif note == self._mapping.straight_note:
            self._dit_down = is_down
            self._dah_down = is_down
        self._on_paddle(self._dit_down, self._dah_down)
