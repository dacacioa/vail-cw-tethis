"""Configuration handling for the Vail-CW Thetis app."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict


CONFIG_PATH = Path("config.json")


@dataclass
class AppConfig:
    """Persistent app configuration."""

    input_mode: str = "CTRL"
    midi_device: str = ""
    midi_channel: int = 1
    midi_note_dit: int = 48
    midi_note_dah: int = 50
    audio_output_device: str = ""
    keyer_type: str = "IambicB"
    wpm: float = 20.0
    dit_dah_ratio: float = 3.0
    weighting: float = 50.0
    paddle_reverse: bool = False
    sidetone_enabled: bool = True
    sidetone_freq: float = 600.0
    sidetone_volume: float = 0.2
    decode_font_size: int = 16
    decode_audio_enabled: bool = False
    decode_audio_input_device: str = ""
    decode_audio_tone_freq: float = 700.0
    decode_audio_tone_low_freq: float = 580.0
    decode_audio_tone_high_freq: float = 820.0
    cat_port: str = ""
    cat_baud: int = 9600
    ptt_method: str = "CAT"
    thetis_key_line: str = "DTR"
    thetis_ptt_line: str = "None"
    thetis_key_invert: bool = False
    thetis_ptt_invert: bool = False
    tx_hang_time: float = 0.2
    sample_rate: int = 48000
    channels: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        fields = {field.name for field in cls.__dataclass_fields__.values()}
        filtered = {key: value for key, value in data.items() if key in fields}
        # Backward compatibility: if old single-tone config exists, derive range.
        if "decode_audio_tone_low_freq" not in filtered or "decode_audio_tone_high_freq" not in filtered:
            center = float(filtered.get("decode_audio_tone_freq", 700.0))
            filtered.setdefault("decode_audio_tone_low_freq", max(300.0, center - 120.0))
            filtered.setdefault("decode_audio_tone_high_freq", min(1200.0, center + 120.0))
        low = float(filtered.get("decode_audio_tone_low_freq", 580.0))
        high = float(filtered.get("decode_audio_tone_high_freq", 820.0))
        if low > high:
            low, high = high, low
        filtered["decode_audio_tone_low_freq"] = low
        filtered["decode_audio_tone_high_freq"] = high
        return cls(**filtered)


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        return AppConfig()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config.to_dict(), handle, indent=2, sort_keys=True)
