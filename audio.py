"""Sidetone audio output engine."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass

try:
    import numpy as np
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency
    np = None
    sd = None


@dataclass
class AudioSettings:
    output_device: str
    sample_rate: int
    channels: int
    sidetone_enabled: bool
    sidetone_freq: float
    sidetone_volume: float


class SidetoneOscillator:
    """Simple sine oscillator with gated envelope."""

    def __init__(self, frequency: float, sample_rate: int) -> None:
        self._frequency = frequency
        self._sample_rate = sample_rate
        self._phase = 0.0
        self._gain = 0.0

    def update(self, frequency: float, sample_rate: int) -> None:
        self._frequency = frequency
        self._sample_rate = sample_rate

    def generate(self, frames: int, active: bool, volume: float) -> "np.ndarray":
        if np is None:
            return None
        target = 1.0 if active else 0.0
        if frames <= 0:
            return np.zeros((0,), dtype=np.float32)
        ramp = self._gate_ramp(frames, target)
        increment = 2.0 * math.pi * self._frequency / self._sample_rate
        phases = self._phase + increment * np.arange(frames, dtype=np.float32)
        self._phase = float((phases[-1] + increment) % (2.0 * math.pi))
        return np.sin(phases).astype(np.float32) * ramp * volume

    def _gate_ramp(self, frames: int, target: float) -> "np.ndarray":
        if np is None:
            return None
        if self._gain == target:
            return np.full(frames, target, dtype=np.float32)
        step = 1.0 / max(int(self._sample_rate * 0.005), 1)
        if target > self._gain:
            ramp = self._gain + step * np.arange(frames, dtype=np.float32)
            ramp = np.minimum(ramp, target)
        else:
            ramp = self._gain - step * np.arange(frames, dtype=np.float32)
            ramp = np.maximum(ramp, target)
        self._gain = float(ramp[-1])
        return ramp


class AudioEngine:
    """Audio engine that outputs sidetone only."""

    def __init__(self, settings: AudioSettings) -> None:
        self._settings = settings
        self._keying = False
        self._lock = threading.Lock()
        self._stream = None
        self._osc = SidetoneOscillator(settings.sidetone_freq, settings.sample_rate)
        self._last_level = 0.0

    def start(self) -> None:
        if not sd or not np:
            return
        if self._stream:
            return
        try:
            self._open_streams()
        except Exception as exc:
            logging.getLogger(__name__).error("Audio start failed: %s", exc)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def update_settings(self, settings: AudioSettings) -> None:
        restart = (
            self._settings.sample_rate != settings.sample_rate
            or self._settings.channels != settings.channels
            or self._settings.output_device != settings.output_device
        )
        with self._lock:
            self._settings = settings
            self._osc.update(settings.sidetone_freq, settings.sample_rate)
        if restart:
            self.stop()
            self.start()

    def set_keying(self, active: bool) -> None:
        with self._lock:
            self._keying = active

    def set_ptt(self, active: bool) -> None:
        # Kept for compatibility with existing call sites.
        return

    def get_level(self) -> float:
        return self._last_level

    def _open_streams(self) -> None:
        settings = self._settings
        output_dev = self._resolve_device(settings.output_device)
        self._stream = sd.OutputStream(
            samplerate=settings.sample_rate,
            channels=settings.channels,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=0,
            latency="low",
            device=output_dev,
        )
        self._stream.start()

    def _audio_callback(self, outdata, frames, time_info, status) -> None:
        if np is None:
            return
        with self._lock:
            settings = self._settings
            keying = self._keying
        active = keying and settings.sidetone_enabled
        sidetone = self._osc.generate(frames, active, settings.sidetone_volume)
        if sidetone is None:
            sidetone = np.zeros(frames, dtype=np.float32)
        if settings.channels > 1:
            sidetone = np.repeat(sidetone.reshape(-1, 1), settings.channels, axis=1)
        else:
            sidetone = sidetone.reshape(-1, 1)
        outdata[:] = sidetone
        if frames > 0:
            self._last_level = float(np.sqrt(np.mean(sidetone[:, 0] ** 2)))

    def _resolve_device(self, value: str):
        if not value:
            return None
        if isinstance(value, int):
            return value
        text = str(value)
        if text.isdigit():
            return int(text)
        if ":" in text:
            prefix = text.split(":", 1)[0].strip()
            if prefix.isdigit():
                return int(prefix)
        return text
