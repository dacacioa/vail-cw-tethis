# Vail-CW Thetis

Desktop CW app for Thetis/Vail workflows with:
- live keying from `CTRL` or `MIDI`,
- sidetone generation,
- CAT/PTT control,
- local and audio-input Morse decoding.

## Quick Start (2 minutes)
1. Open PowerShell in the project root:
- `cd C:\git\vail-cw-tethis`
2. Create and activate a virtual environment:
- `py -m venv .venv`
- `.\.venv\Scripts\Activate.ps1`
3. Install dependencies:
- `pip install -r requirements.txt`
4. Run the app:
- `python main.py`
5. In the UI, set:
- `Input Mode` (`CTRL` or `MIDI`)
- `Audio Output`
6. If using Vail MIDI hardware:
- Select `Vail MIDI Out`
- Press `Sync Vail`
- Move `WPM` (it auto-syncs after changes)

## Features
- Input modes: `CTRL` and `MIDI` (no `AUTO` mode).
- `CTRL` mode uses keyboard paddles:
- Left Ctrl = DIT
- Right Ctrl (or AltGr-compatible mapping) = DAH
- `MIDI` mode reads paddle note events from MIDI input.
- MIDI startup mode switch (`CC0=0`) to force Vail hardware into MIDI mode.
- Vail hardware sync (`Program Change` keyer + `CC1` WPM conversion).
- Automatic WPM sync to Vail when WPM changes in UI (debounced).
- Sidetone output with frequency and volume controls.
- Audio-input CW detection with configurable decode frequency range.
- Decoded text panel with local/audio color separation and detachable window.
- CAT/PTT methods: `CAT`, `RTS`, `DTR`, `THETIS_DSP`.
- Persistent config in `config.json`.

## Requirements
- Windows is recommended (global Ctrl paddle polling is Windows-specific).
- Python 3.10+.
- Dependencies from `requirements.txt`:
- `mido`
- `python-rtmidi`
- `sounddevice`
- `numpy`
- `pyserial`
- `pygame`

## Setup (Windows / PowerShell)
1. Open a terminal in the project root.
2. Run `cd C:\git\vail-cw-tethis`.
3. Create venv: `py -m venv .venv`.
4. Activate venv: `.\.venv\Scripts\Activate.ps1`.
5. Upgrade pip: `python -m pip install --upgrade pip`.
6. Install deps: `pip install -r requirements.txt`.
7. Start app: `python main.py`.

## UI Blocks

### Input
- `Input Mode`: `CTRL` or `MIDI`.
- `Audio Output`: sidetone output device.
- `Vail MIDI Out`: MIDI output device used for Vail sync commands.
- `Refresh` updates MIDI/audio device lists.
- `Sync Vail` sends current keyer + WPM to hardware.
- In `CTRL` mode, MIDI-specific controls are disabled.

### WPM
- `Keyer Type` selector (Straight, Bug, Iambic A/B, Ultimatic, etc.).
- Large WPM value display.
- Large WPM slider (`5..40 WPM`).
- Quick buttons: `-5`, `-1`, `+1`, `+5`.
- Additional timing controls:
- `Dit/Dah Ratio`
- `Weighting`
- `Paddle Reverse`
- Every WPM change is auto-synced to Vail (when a MIDI output is selected).
- Changing `Vail MIDI Out` in `MIDI` mode also triggers auto-sync.

### Decode
- `Decode Audio In` enables CW detection from audio input.
- `Decode Audio Input` selects source device.
- `Decode Range (Hz)` sets detector band (`Min`/`Max`).
- Status lines:
- `Decode Audio`
- `Decode WPM` (estimated RX speed).

### Sidetone
- `Sidetone On`
- `Test Sidetone`
- `Sidetone Frequency (Hz)`
- `Sidetone Volume`

### CAT / Thetis
- `CAT/DSP Port`
- `CAT Baud`
- `PTT Method`
- `Thetis Key Line`
- `Thetis PTT Line`
- `Invert Thetis Key`
- `Invert Thetis PTT`
- `TX Hang Time (s)`
- `Manual PTT`
- `Release Key`

### Status
- `CTRL Status`
- `PTT Status`
- `Keying`
- `Port Status`
- `Audio Level`

### Decoded
- Real-time decoded text.
- `Font` size control.
- `Clear` to reset text.
- `Detach` to open a resizable detached decode window.
- Audio-decoded text is shown in red for quick visual separation.

## Input Behavior

### CTRL mode
- Uses keyboard events and global key polling.
- Best supported on Windows.

### MIDI mode
- Opens MIDI input and listens for note on/off paddle events.
- Attempts output pairing for Vail sync commands.
- Startup includes mode switch command (`CC0=0`).
- Includes note/channel auto-mapping heuristics for common Vail note pairs.

## Vail Sync Details
- Manual sync: press `Sync Vail`.
- Auto sync: changing WPM in the UI triggers sync automatically.
- WPM conversion follows Vail Zoomer behavior:
- `dit_ms = 1200 / WPM`
- `CC1 = dit_ms / 2`

## Thetis DSP Notes
1. Set `PTT Method` to `THETIS_DSP`.
2. Select the correct virtual COM pair in `CAT/DSP Port`.
3. Match `Thetis Key Line` / `Thetis PTT Line` with Thetis DSP-CW settings.
4. Use invert options if polarity is reversed.

## Configuration
- File: `config.json`.
- Saved automatically when UI values change.
- Includes mode, MIDI mapping, WPM/keyer, sidetone, decode, and CAT settings.

## Troubleshooting
- If MIDI is not detected:
- Connect device before app start, then use `Refresh`.
- Close other apps that may lock MIDI ports.
- If MIDI keying works but sync does not:
- Verify `Vail MIDI Out` is set to the hardware output port.
- Check log lines after pressing `Sync Vail`.
- If CTRL mode does not key:
- Confirm running on Windows.
- Ensure app window receives keyboard events.
- If no sidetone:
- Check `Sidetone On`, volume, output device, and system mixer output routing.
