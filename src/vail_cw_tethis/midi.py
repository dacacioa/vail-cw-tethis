from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import mido


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
        self._dit_down = False
        self._dah_down = False
        self._auto_notes: list[int] = []

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
        device = self.auto_detect(self._device_name)
        if not device:
            raise RuntimeError("No MIDI input devices found.")
        self._input = mido.open_input(device, callback=self._handle_message)

    def stop(self) -> None:
        if self._input:
            self._input.close()
            self._input = None

    def _handle_message(self, message: mido.Message) -> None:
        if message.type not in ("note_on", "note_off", "control_change"):
            return
        if message.type == "control_change":
            is_down = message.value > 0
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
