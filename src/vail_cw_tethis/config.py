from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from platformdirs import user_config_dir


@dataclass
class CatConfig:
    com_port: str = ""
    baudrate: int = 9600
    ptt_mode: str = "CAT"  # CAT, RTS, DTR
    tx_hang_time_ms: int = 0


@dataclass
class AppConfig:
    midi_device: str = ""
    audio_input_device: str = ""
    audio_output_device: str = ""
    keyer_type: str = "IambicA"
    wpm: int = 20
    dit_dah_ratio: float = 3.0
    weighting: float = 50.0
    paddle_reverse: bool = False
    sidetone_frequency: float = 600.0
    sidetone_volume: float = 0.5
    local_monitor_volume: float = 0.4
    sidetone_route: str = "Both"  # OutputOnly, LocalOnly, Both
    mix_mode: str = "AlwaysMix"  # AlwaysMix, CwMutesMic, PushToTalkVoice
    cat: CatConfig = field(default_factory=CatConfig)


def _config_path() -> Path:
    base_dir = Path(user_config_dir("vail-cw-tethis", "vail-cw"))
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "config.json"


def load_config() -> AppConfig:
    path = _config_path()
    if not path.exists():
        return AppConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    cat_data = data.get("cat", {})
    return AppConfig(
        midi_device=data.get("midi_device", ""),
        audio_input_device=data.get("audio_input_device", ""),
        audio_output_device=data.get("audio_output_device", ""),
        keyer_type=data.get("keyer_type", "IambicA"),
        wpm=int(data.get("wpm", 20)),
        dit_dah_ratio=float(data.get("dit_dah_ratio", 3.0)),
        weighting=float(data.get("weighting", 50.0)),
        paddle_reverse=bool(data.get("paddle_reverse", False)),
        sidetone_frequency=float(data.get("sidetone_frequency", 600.0)),
        sidetone_volume=float(data.get("sidetone_volume", 0.5)),
        local_monitor_volume=float(data.get("local_monitor_volume", 0.4)),
        sidetone_route=data.get("sidetone_route", "Both"),
        mix_mode=data.get("mix_mode", "AlwaysMix"),
        cat=CatConfig(
            com_port=cat_data.get("com_port", ""),
            baudrate=int(cat_data.get("baudrate", 9600)),
            ptt_mode=cat_data.get("ptt_mode", "CAT"),
            tx_hang_time_ms=int(cat_data.get("tx_hang_time_ms", 0)),
        ),
    )


def save_config(config: AppConfig) -> None:
    path = _config_path()
    data = asdict(config)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
