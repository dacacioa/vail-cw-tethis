"""Audio input tone detector for noisy Morse decoding."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

try:
    import numpy as np
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency
    np = None
    sd = None


ToneCallback = Callable[[bool], None]
StatusCallback = Callable[[bool, str], None]


@dataclass
class AudioDecodeSettings:
    enabled: bool
    input_device: str
    sample_rate: int
    tone_low_freq: float
    tone_high_freq: float


class AudioDecodeInput:
    """Detect Morse tone state from noisy audio input."""

    def __init__(
        self,
        settings: AudioDecodeSettings,
        on_tone: ToneCallback,
        on_status: Optional[StatusCallback] = None,
    ) -> None:
        self._settings = settings
        self._on_tone = on_tone
        self._on_status = on_status
        self._stream = None
        self._lock = threading.Lock()
        self._active = False
        self._noise_ratio = 0.02
        self._last_switch_ts = 0.0
        self._window_cache = {}

    def start(self) -> None:
        if not self._settings.enabled:
            self._notify(False, "disabled")
            return
        if not sd or not np:
            self._notify(False, "audio decode unavailable")
            return
        if self._stream:
            return
        try:
            self._open_stream()
            self._notify(True, "listening")
        except Exception as exc:
            self._notify(False, f"decode input error: {exc}")

    def stop(self) -> None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._set_active(False)

    def update_settings(self, settings: AudioDecodeSettings) -> None:
        restart = (
            self._settings.enabled != settings.enabled
            or self._settings.input_device != settings.input_device
            or self._settings.sample_rate != settings.sample_rate
            or abs(self._settings.tone_low_freq - settings.tone_low_freq) > 0.1
            or abs(self._settings.tone_high_freq - settings.tone_high_freq) > 0.1
        )
        self._settings = settings
        if restart:
            self.stop()
            self.start()

    def _open_stream(self) -> None:
        if not self._settings.enabled:
            return
        device = self._resolve_device(self._settings.input_device)
        self._stream = sd.InputStream(
            samplerate=self._settings.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
            blocksize=0,
            latency="low",
            device=device,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        if np is None:
            return
        if frames <= 0:
            return
        data = indata[:, 0].astype(np.float32, copy=False)
        ratio = self._tone_ratio(
            data,
            self._settings.sample_rate,
            self._settings.tone_low_freq,
            self._settings.tone_high_freq,
        )
        now = time.monotonic()

        with self._lock:
            if not self._active:
                self._noise_ratio = (0.995 * self._noise_ratio) + (0.005 * ratio)
            else:
                self._noise_ratio = (0.999 * self._noise_ratio) + (0.001 * ratio)

            on_ratio = max(0.08, self._noise_ratio * 4.0)
            off_ratio = max(0.05, self._noise_ratio * 2.6)

            active = self._active
            if not active:
                if ratio >= on_ratio and (now - self._last_switch_ts) >= 0.01:
                    active = True
            else:
                if ratio < off_ratio and (now - self._last_switch_ts) >= 0.012:
                    active = False

        self._set_active(active)

    def _set_active(self, active: bool) -> None:
        with self._lock:
            if active == self._active:
                return
            self._active = active
            self._last_switch_ts = time.monotonic()
        self._on_tone(active)

    def _tone_ratio(
        self,
        samples: "np.ndarray",
        sample_rate: int,
        tone_low_freq: float,
        tone_high_freq: float,
    ) -> float:
        """Return tonal concentration ratio in CW band (robust to noisy RX audio)."""
        if np is None:
            return 0.0
        n = len(samples)
        if n < 64:
            return 0.0

        window = self._window_cache.get(n)
        if window is None:
            window = np.hanning(n).astype(np.float32)
            self._window_cache[n] = window

        x = samples * window
        spectrum = np.fft.rfft(x)
        power = np.abs(spectrum) ** 2
        total = float(np.sum(power)) + 1e-12
        if total <= 0:
            return 0.0

        freqs = np.fft.rfftfreq(n, d=(1.0 / sample_rate))
        lo = max(250.0, min(float(tone_low_freq), float(tone_high_freq)))
        hi = min(1400.0, max(float(tone_low_freq), float(tone_high_freq)))
        if hi - lo < 20.0:
            center = (lo + hi) * 0.5
            lo = max(250.0, center - 20.0)
            hi = min(1400.0, center + 20.0)
        mask = (freqs >= lo) & (freqs <= hi)
        if not np.any(mask):
            return 0.0

        idx = np.where(mask)[0]
        peak_i = idx[int(np.argmax(power[idx]))]
        peak_f = freqs[peak_i]
        band = (freqs >= (peak_f - 35.0)) & (freqs <= (peak_f + 35.0))
        band_power = float(np.sum(power[band]))
        return band_power / total

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

    def _notify(self, connected: bool, label: str) -> None:
        if self._on_status:
            self._on_status(connected, label)
