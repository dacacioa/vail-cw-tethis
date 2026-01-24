from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import mido


@dataclass
class MidiMapping:
    dit_note: int = 60
    dah_note: int = 61
    straight_note: int = 62


class MidiKeyer:
    def __init__(
        self,
        device_name: str,
        on_paddle: Callable[[bool, bool], None],
        mapping: MidiMapping | None = None,
    ) -> None:
        self._device_name = device_name
        self._on_paddle = on_paddle
        self._mapping = mapping or MidiMapping()
        self._input: Optional[mido.ports.BaseInput] = None
        self._dit_down = False
        self._dah_down = False

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
        if message.type not in ("note_on", "note_off"):
            return
        note = message.note
        is_down = message.type == "note_on" and message.velocity > 0
        if note == self._mapping.dit_note:
            self._dit_down = is_down
        elif note == self._mapping.dah_note:
            self._dah_down = is_down
        elif note == self._mapping.straight_note:
            self._dit_down = is_down
            self._dah_down = is_down
        self._on_paddle(self._dit_down, self._dah_down)
