# vail-cw-tethis

Cross-platform desktop app (Windows/macOS/Linux) inspired by **vail-zoomer** for use with the Vail-CW adapter.

## Features

- MIDI keyer input with automatic Vail-CW device discovery.
- Software sidetone generation with configurable frequency and volume.
- Mic + sidetone mixing modes (`AlwaysMix`, `CwMutesMic`, `PushToTalkVoice`).
- Audio output to a selectable device (VAC / Voicemeeter / VB-Cable).
- CAT control for PTT and frequency via virtual serial ports (Kenwood TS-2000 compatible), with optional RTS/DTR.
- Configuration persisted in JSON.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

vail-cw-tethis
```

## Configuration

Configuration is stored in your user config directory (`vail-cw-tethis/config.json`).
The following settings are persisted:

- MIDI device
- Audio input device (mic, or `None` to disable)
- Audio output device (VAC)
- Keyer type
- WPM
- Dit/Dah ratio
- Weighting
- Paddle reverse
- Sidetone frequency
- Sidetone volume
- Local monitor volume
- Sidetone route
- Mix mode
- CAT: COM port, baudrate, PTT mode (CAT/RTS/DTR), TX hang time

## Thetis setup

1. Enable VAC1.
2. Set TX audio input to VAC1.
3. Enable CAT on a virtual COM port.
