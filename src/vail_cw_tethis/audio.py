from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


@dataclass
class AudioSettings:
    input_device: str
    output_device: str
    sidetone_frequency: float
    sidetone_volume: float
    local_monitor_volume: float
    sidetone_route: str
    mix_mode: str


class AudioEngine:
    def __init__(
        self,
        settings: AudioSettings,
        on_ptt_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self._settings = settings
        self._on_ptt_change = on_ptt_change
        self._stream: Optional[sd.Stream] = None
        self._phase = 0.0
        self._key_down = False
        self._ptt_active = False
        self._lock = threading.Lock()

    @staticmethod
    def list_devices(kind: str) -> list[str]:
        devices = sd.query_devices()
        if kind == "input":
            return [d["name"] for d in devices if d["max_input_channels"] > 0]
        return [d["name"] for d in devices if d["max_output_channels"] > 0]

    def update_settings(self, settings: AudioSettings) -> None:
        with self._lock:
            self._settings = settings

    def set_key_down(self, state: bool) -> None:
        self._key_down = state

    def set_ptt_active(self, state: bool) -> None:
        self._ptt_active = state
        if self._on_ptt_change:
            self._on_ptt_change(state)

    def start(self) -> None:
        input_device = self._settings.input_device or None
        output_device = self._settings.output_device or None
        self._stream = sd.Stream(
            samplerate=48000,
            blocksize=256,
            dtype="float32",
            channels=1,
            device=(input_device, output_device),
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata, outdata, frames, time_info, status) -> None:
        if status:
            pass
        with self._lock:
            settings = self._settings
        sidetone = self._generate_sidetone(frames, settings.sidetone_frequency)
        mic = indata.copy() if indata is not None else np.zeros((frames, 1), dtype=np.float32)
        keying = self._key_down
        mix_mode = settings.mix_mode

        mic_gain = 1.0
        if mix_mode == "CwMutesMic" and keying:
            mic_gain = 0.0
        elif mix_mode == "PushToTalkVoice" and not self._ptt_active:
            mic_gain = 0.0

        if settings.sidetone_route == "OutputOnly":
            sidetone_gain = settings.sidetone_volume
        elif settings.sidetone_route == "LocalOnly":
            sidetone_gain = settings.local_monitor_volume
        else:
            sidetone_gain = settings.sidetone_volume + settings.local_monitor_volume

        audio_mix = mic * mic_gain + sidetone * sidetone_gain
        outdata[:] = np.clip(audio_mix, -1.0, 1.0)

    def _generate_sidetone(self, frames: int, frequency: float) -> np.ndarray:
        if not self._key_down:
            return np.zeros((frames, 1), dtype=np.float32)
        samplerate = 48000
        phase_inc = 2.0 * math.pi * frequency / samplerate
        phases = self._phase + phase_inc * np.arange(frames)
        self._phase = (phases[-1] + phase_inc) % (2.0 * math.pi)
        tone = np.sin(phases).astype(np.float32)
        return tone.reshape(-1, 1)
