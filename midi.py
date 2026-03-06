"""MIDI input handling for paddle/keyer events."""

from __future__ import annotations

import logging
import os
import threading
import time
from difflib import SequenceMatcher
from dataclasses import dataclass
import re
from typing import Callable, List, Optional

try:
    import mido
except ImportError:  # pragma: no cover - optional dependency
    mido = None


LOGGER = logging.getLogger(__name__)
DEBUG_MIDI = os.getenv("MIDI_DEBUG", "0") == "1"

AUTO_MIDI_HINTS = (
    "vail",
    "vail adapter",
    "vail lite",
    "summit",
    "seeed",
    "xiao",
)
SOFT_SYNTH_HINTS = (
    "microsoft gs wavetable",
    "microsoft wavetable",
    "midi mapper",
    "software synth",
    "virtual",
    "loopmidi",
    "loopbe",
)

# Vail adapter mode control (CC0).
VAIL_CC_MODE = 0
VAIL_MODE_MIDI = 0
# Keyboard/HID mode (values 64-127 per adapter notes).
VAIL_MODE_KEYBOARD = 127
# CC1 = Dit duration (value * 2 ms).
VAIL_CC_DIT_DURATION = 1
# Program Change 0 = passthrough (raw paddles, no keyer timing in adapter).
VAIL_KEYER_PASSTHROUGH = 0
# Default to passthrough so app keyer timing matches CTRL mode.
SEND_PASSTHROUGH_PC = os.getenv("VAIL_SEND_PASSTHROUGH_PC", "1") == "1"
FORCE_RETRY_SEC = float(os.getenv("VAIL_FORCE_RETRY_SEC", "2.0"))
TEMP_OUTPUT_ERROR_LOG_SEC = float(os.getenv("VAIL_TEMP_OUTPUT_ERROR_LOG_SEC", "15.0"))
MIDI_EDGE_DEBOUNCE_SEC = float(os.getenv("MIDI_EDGE_DEBOUNCE_SEC", "0.004"))
# Default to input-only connection. Some USB MIDI devices freeze when IN/OUT
# stay open simultaneously from Python/rtmidi.
MIDI_HOLD_OUTPUT_OPEN = os.getenv("MIDI_HOLD_OUTPUT_OPEN", "0") == "1"
MIDI_AUTO_STARTUP_SYNC = os.getenv("MIDI_AUTO_STARTUP_SYNC", "0") == "1"
MIDI_UI_MESSAGE_MIN_INTERVAL_SEC = float(os.getenv("MIDI_UI_MESSAGE_MIN_INTERVAL_SEC", "0.08"))
MIDI_NOTE_ON_FILTER_SEC = float(os.getenv("MIDI_NOTE_ON_FILTER_SEC", "0.018"))
MIDI_NOTE_LEARN_SUPPRESS_SEC = float(os.getenv("MIDI_NOTE_LEARN_SUPPRESS_SEC", "1.0"))
MIDI_AUTOMAP_STARTUP_WINDOW_SEC = float(os.getenv("MIDI_AUTOMAP_STARTUP_WINDOW_SEC", "1.2"))
MIDI_POLL_INTERVAL_SEC = float(os.getenv("MIDI_POLL_INTERVAL_SEC", "0.003"))
MIDI_FORCE_MODE_WHEN_MISSING_INPUT = os.getenv("MIDI_FORCE_MODE_WHEN_MISSING_INPUT", "0") == "1"
MIDI_SEND_STARTUP_COMMANDS = os.getenv("MIDI_SEND_STARTUP_COMMANDS", "0") == "1"


PaddleCallback = Callable[[bool, bool], None]
StatusCallback = Callable[[bool, str], None]
MessageCallback = Callable[[str], None]


@dataclass
class MidiMapping:
    channel: int  # 1-16
    note_dit: int
    note_dah: int


VAIL_KEYER_PROGRAMS = {
    "STRAIGHT": 1,
    "BUG": 2,
    "ELBUG": 3,
    "SINGLEDOT": 4,
    "ULTIMATIC": 5,
    "PLAINIAMBIC": 6,
    "IAMBICA": 7,
    "IAMBICB": 8,
    "KEYAHEAD": 9,
}


def _keyer_program(keyer_type: Optional[str]) -> Optional[int]:
    value = (keyer_type or "").strip().upper()
    if not value:
        return None
    return VAIL_KEYER_PROGRAMS.get(value)


def _wpm_to_cc_dit_value(wpm: float) -> int:
    # Same conversion used by Vail Zoomer:
    # dit_ms = 1200 / wpm; CC1 value = dit_ms / 2
    wpm_u8 = max(5, min(127, int(wpm)))
    dit_ms = max(1, 1200 // max(wpm_u8, 1))
    return max(1, min(127, dit_ms // 2))


def _set_backend() -> bool:
    if not mido:
        return False
    try:
        mido.set_backend("mido.backends.rtmidi")
        return True
    except Exception as exc:
        LOGGER.error("MIDI backend error: %s", exc)
        return False


def list_midi_devices() -> List[str]:
    if not mido or not _set_backend():
        return []
    try:
        return mido.get_input_names()
    except Exception as exc:
        LOGGER.error("MIDI list error: %s", exc)
        return []


def list_midi_output_devices() -> List[str]:
    if not mido or not _set_backend():
        return []
    try:
        return mido.get_output_names()
    except Exception as exc:
        LOGGER.error("MIDI output list error: %s", exc)
        return []


def _normalize_port_name(name: str) -> str:
    lower = name.lower().strip()
    lower = re.sub(r"\s+\d+$", "", lower)
    lower = re.sub(r"[^a-z0-9]+", " ", lower)
    return re.sub(r"\s+", " ", lower).strip()


def _port_name_score(input_name: str, output_name: str) -> float:
    a = _normalize_port_name(input_name)
    b = _normalize_port_name(output_name)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(a=a, b=b).ratio()
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    overlap = len(a_tokens & b_tokens) / max(1, len(a_tokens))
    return (0.7 * ratio) + (0.3 * overlap)


def auto_detect_device(devices: List[str]) -> str:
    for name in devices:
        lower = name.lower()
        if any(hint in lower for hint in AUTO_MIDI_HINTS):
            return name
    return devices[0] if devices else ""


def _is_soft_synth_name(name: str) -> bool:
    lower = (name or "").lower()
    return any(h in lower for h in SOFT_SYNTH_HINTS)


def _pick_input_device(preferred: str, devices: List[str]) -> str:
    preferred_name = (preferred or "").strip()
    if not devices:
        return ""
    if preferred_name in devices:
        return preferred_name
    if preferred_name:
        scored = sorted(
            ((_port_name_score(preferred_name, in_name), in_name) for in_name in devices),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.45:
            return scored[0][1]
    return auto_detect_device(devices)


def _pick_output_device(preferred: str, devices: List[str]) -> str:
    preferred_name = (preferred or "").strip()
    if not devices:
        return ""
    # Common Vail/XIAO naming on Windows: input "... 0" paired with output "... 1".
    if preferred_name:
        m = re.match(r"^(.*?)(\s+)(\d+)$", preferred_name)
        if m:
            base, sep, num_text = m.group(1), m.group(2), m.group(3)
            try:
                pair_name = f"{base}{sep}{int(num_text) + 1}"
                if pair_name in devices:
                    return pair_name
            except Exception:
                pass
    if preferred_name and preferred_name in devices:
        return preferred_name
    if preferred_name:
        scored = sorted(
            ((_port_name_score(preferred_name, out_name), out_name) for out_name in devices),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.45:
            return scored[0][1]
    hinted = [
        out_name
        for out_name in devices
        if any(hint in out_name.lower() for hint in AUTO_MIDI_HINTS)
    ]
    if hinted:
        return hinted[0]
    return devices[0]


def sync_vail_hardware_once(
    device_name: str,
    wpm: float,
    keyer_type: Optional[str] = None,
    keep_midi_mode: bool = False,
    force_passthrough: bool = False,
) -> bool:
    """One-shot sync using a temporary output port only."""
    if not mido or not _set_backend():
        return False
    outputs = list_midi_output_devices()
    out_name = _pick_output_device(device_name, outputs)
    if not out_name:
        return False
    try:
        output = mido.open_output(out_name)
    except Exception as exc:
        LOGGER.warning("Vail sync open output failed (%s): %s", out_name, exc)
        return False
    try:
        # Match Vail Zoomer strategy: ensure MIDI mode, then push settings.
        output.send(
            mido.Message(
                "control_change",
                channel=0,
                control=VAIL_CC_MODE,
                value=VAIL_MODE_MIDI,
            )
        )
        if force_passthrough:
            output.send(
                mido.Message(
                    "program_change",
                    channel=0,
                    program=VAIL_KEYER_PASSTHROUGH,
                )
            )
        else:
            program = _keyer_program(keyer_type)
            if program is not None:
                output.send(mido.Message("program_change", channel=0, program=program))

        cc_value = _wpm_to_cc_dit_value(wpm)
        output.send(
            mido.Message(
                "control_change",
                channel=0,
                control=VAIL_CC_DIT_DURATION,
                value=cc_value,
            )
        )
        LOGGER.info(
            "Vail sync sent: keyer=%s wpm=%.1f cc1=%d keep_midi=%s passthrough=%s",
            keyer_type or "-",
            float(wpm),
            cc_value,
            bool(keep_midi_mode),
            bool(force_passthrough),
        )
        return True
    except Exception as exc:
        LOGGER.warning("Vail sync failed: %s", exc)
        return False
    finally:
        try:
            output.close()
        except Exception:
            pass


def _format_message(message: "mido.Message") -> str:
    msg_type = getattr(message, "type", "")
    channel = getattr(message, "channel", None)
    if channel is not None:
        channel = int(channel) + 1
    if msg_type in ("note_on", "note_off"):
        return (
            f"{msg_type} ch={channel} note={int(message.note)} vel={int(message.velocity)}"
        )
    if msg_type == "control_change":
        return (
            f"cc ch={channel} cc={int(message.control)} val={int(message.value)}"
        )
    return str(message)


class MidiKeyerInput:
    """Translate MIDI note events into paddle states with hotplug support."""

    def __init__(
        self,
        on_paddle: PaddleCallback,
        on_status: Optional[StatusCallback] = None,
        on_message: Optional[MessageCallback] = None,
    ) -> None:
        self._on_paddle = on_paddle
        self._on_status = on_status
        self._on_message = on_message
        self._mapping = MidiMapping(channel=1, note_dit=0, note_dah=1)

        self._desired_device = ""
        self._sync_output_device = ""
        self._device_name = ""
        self._input: Optional["mido.ports.BaseInput"] = None
        self._output: Optional["mido.ports.BaseOutput"] = None
        self._connected = False
        self._enabled = True
        self._disabled_reported = False

        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._close_requested = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None

        self._state = {"dit": False, "dah": False}
        self._matched_events = 0
        self._learn_note_dit: Optional[int] = None
        self._learn_note_dah: Optional[int] = None
        self._last_unmatched_log = 0.0
        self._last_force_attempt = 0.0
        self._last_temp_output_error = 0.0
        self._last_temp_output_error_name = ""
        self._last_note_edge_ts: dict[int, float] = {}
        self._last_note_edge_state: dict[int, bool] = {}
        self._last_note_on_ts: dict[int, float] = {}
        self._last_message_emit_ts = 0.0
        self._note_learn_ignore_until = 0.0
        self._auto_map_until = 0.0
        self._mapping_locked = False
        self._io_lock = threading.Lock()
        self._lock = threading.Lock()

    @property
    def device_name(self) -> str:
        return self._device_name

    def update_mapping(self, channel: int, note_dit: int, note_dah: int) -> None:
        with self._lock:
            next_mapping = MidiMapping(channel=channel, note_dit=note_dit, note_dah=note_dah)
            if next_mapping == self._mapping:
                return
            self._mapping = next_mapping
            self._matched_events = 0
            self._learn_note_dit = None
            self._learn_note_dah = None
            self._mapping_locked = False
            self._auto_map_until = time.time() + max(0.0, MIDI_AUTOMAP_STARTUP_WINDOW_SEC)

    def set_sync_output_device(self, device_name: str) -> None:
        self._sync_output_device = (device_name or "").strip()

    def suppress_note_learning(self, seconds: float = MIDI_NOTE_LEARN_SUPPRESS_SEC) -> None:
        until = time.time() + max(0.0, float(seconds))
        with self._lock:
            if until > self._note_learn_ignore_until:
                self._note_learn_ignore_until = until

    def sync_vail_hardware(
        self,
        wpm: float,
        keyer_type: Optional[str] = None,
        keep_midi_mode: bool = False,
        force_passthrough: bool = False,
    ) -> None:
        """Sync keyer settings to Vail adapter output (mode/keyer/wpm)."""
        with self._io_lock:
            preferred = self._sync_output_device or self._device_name or self._desired_device
            output = self._output
            if output is not None:
                try:
                    self._send_sync_commands(
                        output=output,
                        wpm=wpm,
                        keyer_type=keyer_type,
                        keep_midi_mode=keep_midi_mode,
                        force_passthrough=force_passthrough,
                    )
                    return
                except Exception as exc:
                    LOGGER.warning("Connected MIDI sync failed, falling back to temp output: %s", exc)

            temp = self._open_temp_output(preferred)
            if temp is None:
                raise RuntimeError("No MIDI output available for sync")
            try:
                self._send_sync_commands(
                    output=temp,
                    wpm=wpm,
                    keyer_type=keyer_type,
                    keep_midi_mode=keep_midi_mode,
                    force_passthrough=force_passthrough,
                )
            finally:
                try:
                    temp.close()
                except Exception:
                    pass

    def open(self, preferred: str = "") -> None:
        # Do not enumerate/open devices from the UI thread. The monitor thread
        # performs all backend MIDI I/O to avoid UI stalls.
        self._desired_device = (preferred or "").strip()
        with self._lock:
            self._matched_events = 0
            self._learn_note_dit = None
            self._learn_note_dah = None
            self._state = {"dit": False, "dah": False}
            self._last_note_edge_ts.clear()
            self._last_note_edge_state.clear()
            self._last_note_on_ts.clear()
            self._mapping_locked = False
            self._auto_map_until = time.time() + max(0.0, MIDI_AUTOMAP_STARTUP_WINDOW_SEC)
        self._ensure_threads()
        self._reconnect.set()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._disabled_reported = False
            self._set_status(False, "disabled")
            self._close_requested.set()
        else:
            self._disabled_reported = False
        self._reconnect.set()

    def close(self) -> None:
        LOGGER.info("MIDI close requested")
        self._close_requested.set()
        self._reconnect.set()

    def shutdown(self) -> None:
        LOGGER.info("MIDI shutdown requested")
        self._stop.set()
        self._reconnect.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        if self._worker_thread:
            self._worker_thread.join(timeout=1.0)
        self._monitor_thread = None
        self._worker_thread = None
        self._close_port()

    def _ensure_threads(self) -> None:
        if not self._monitor_thread or not self._monitor_thread.is_alive():
            self._stop.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, name="midi-monitor", daemon=True
            )
            self._monitor_thread.start()
        if not self._worker_thread or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._worker_loop, name="midi-worker", daemon=True
            )
            self._worker_thread.start()

    def _set_status(self, connected: bool, label: str) -> None:
        self._connected = connected
        if self._on_status:
            self._on_status(connected, label)

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            if self._close_requested.is_set():
                self._close_requested.clear()
                self._close_port()
            if not self._enabled:
                if self._input is not None:
                    self._close_port()
                if not self._disabled_reported:
                    self._set_status(False, "disabled")
                    self._disabled_reported = True
                self._reconnect.wait(0.5)
                self._reconnect.clear()
                continue
            self._disabled_reported = False
            if self._input is None:
                self._attempt_open()
            self._reconnect.wait(2.0)
            self._reconnect.clear()

    def _attempt_open(self) -> None:
        if not self._enabled:
            return
        devices = list_midi_devices()
        name = _pick_input_device(self._desired_device, devices)
        if not name:
            forced = False
            if MIDI_FORCE_MODE_WHEN_MISSING_INPUT:
                forced = self._try_force_midi_mode()
            if forced:
                self._set_status(False, "forcing MIDI mode...")
            else:
                self._set_status(False, "disconnected")
            return
        try:
            LOGGER.info("MIDI open: %s", name)
            self._input = mido.open_input(name)
            self._device_name = name
            if MIDI_SEND_STARTUP_COMMANDS:
                if MIDI_HOLD_OUTPUT_OPEN:
                    # Startup mode commands must target the output paired to this
                    # input device, otherwise the adapter can remain in keyer mode.
                    self._open_output(name)
                else:
                    # One-shot mode switch without keeping output locked open.
                    self._send_startup_commands_temp(name)
                    self.suppress_note_learning(0.4)
            self._set_status(True, name)
        except Exception as exc:
            LOGGER.error("MIDI open error: %s", exc)
            self._set_status(False, f"error: {exc}")
            self._close_port()

    def _try_force_midi_mode(self) -> bool:
        if not mido or not _set_backend():
            return False

        now = time.time()
        if now - self._last_force_attempt < max(0.1, FORCE_RETRY_SEC):
            return False
        self._last_force_attempt = now

        outputs = list_midi_output_devices()
        if not outputs:
            return False

        preferred = self._desired_device.lower().strip()
        hardware_outputs = [name for name in outputs if not _is_soft_synth_name(name)]
        if not hardware_outputs:
            return False
        candidates: List[str] = []
        if preferred and not _is_soft_synth_name(preferred):
            for output_name in hardware_outputs:
                lower = output_name.lower()
                if preferred in lower or lower in preferred:
                    candidates.append(output_name)
        if not candidates:
            candidates = [
                output_name
                for output_name in hardware_outputs
                if any(hint in output_name.lower() for hint in AUTO_MIDI_HINTS)
            ]
        if not candidates:
            return False

        for output_name in candidates:
            output = None
            try:
                output = mido.open_output(output_name)
                self._send_startup_commands(output)
                LOGGER.info("MIDI mode force command sent to output: %s", output_name)
                return True
            except Exception as exc:
                LOGGER.warning("MIDI force mode failed on %s: %s", output_name, exc)
            finally:
                if output is not None:
                    try:
                        output.close()
                    except Exception:
                        pass
        return False

    def _open_output(self, name: str) -> None:
        self._close_output()
        outputs = list_midi_output_devices()
        if not outputs:
            LOGGER.warning("No MIDI output ports found")
            return

        preferred = (name or "").strip()
        chosen = _pick_output_device(preferred, outputs)
        candidates: List[str] = [chosen] if chosen else []
        if not candidates:
            candidates = outputs[:]

        seen = set()
        unique_candidates = []
        for out_name in candidates:
            if out_name in seen:
                continue
            seen.add(out_name)
            unique_candidates.append(out_name)
        if not unique_candidates:
            unique_candidates = outputs[:]

        for out_name in unique_candidates:
            try:
                self._output = mido.open_output(out_name)
                LOGGER.info("MIDI output paired: input='%s' output='%s'", self._device_name or "-", out_name)
                self._send_startup_commands()
                return
            except Exception as exc:
                LOGGER.warning("MIDI output open error (%s): %s", out_name, exc)
                self._output = None

        LOGGER.warning("Failed to open MIDI output. Available outputs: %s", outputs)

    def _send_startup_commands(self, output: Optional["mido.ports.BaseOutput"] = None) -> None:
        target = output or self._output
        if not target:
            return
        try:
            message = mido.Message(
                "control_change",
                channel=0,
                control=VAIL_CC_MODE,
                value=VAIL_MODE_MIDI,
            )
            target.send(message)
            LOGGER.info("MIDI mode switch sent (CC%d=%d)", VAIL_CC_MODE, VAIL_MODE_MIDI)
            if SEND_PASSTHROUGH_PC:
                passthrough = mido.Message(
                    "program_change",
                    channel=0,
                    program=VAIL_KEYER_PASSTHROUGH,
                )
                target.send(passthrough)
                LOGGER.info("MIDI keyer set to passthrough (PC=%d)", VAIL_KEYER_PASSTHROUGH)
        except Exception as exc:
            LOGGER.warning("MIDI startup command failed: %s", exc)

    def _send_sync_commands(
        self,
        output: "mido.ports.BaseOutput",
        wpm: float,
        keyer_type: Optional[str],
        keep_midi_mode: bool,
        force_passthrough: bool,
    ) -> None:
        # Ensure adapter is in MIDI mode before pushing settings.
        output.send(
            mido.Message(
                "control_change",
                channel=0,
                control=VAIL_CC_MODE,
                value=VAIL_MODE_MIDI,
            )
        )
        if force_passthrough:
            output.send(
                mido.Message(
                    "program_change",
                    channel=0,
                    program=VAIL_KEYER_PASSTHROUGH,
                )
            )
        else:
            program = _keyer_program(keyer_type)
            if program is not None:
                output.send(mido.Message("program_change", channel=0, program=program))
        cc_value = _wpm_to_cc_dit_value(wpm)
        output.send(
            mido.Message(
                "control_change",
                channel=0,
                control=VAIL_CC_DIT_DURATION,
                value=cc_value,
            )
        )
        LOGGER.info(
            "Vail sync sent: keyer=%s wpm=%.1f cc1=%d keep_midi=%s passthrough=%s",
            keyer_type or "-",
            float(wpm),
            cc_value,
            bool(keep_midi_mode),
            bool(force_passthrough),
        )

    def _send_startup_commands_temp(self, preferred: str = "") -> None:
        with self._io_lock:
            output = self._open_temp_output(preferred)
            if not output:
                return
            try:
                self._send_startup_commands(output=output)
            finally:
                try:
                    output.close()
                except Exception:
                    pass

    def _open_temp_output(self, preferred: str = "") -> Optional["mido.ports.BaseOutput"]:
        outputs = list_midi_output_devices()
        if not outputs:
            return None

        candidates: List[str] = []
        preferred_name = (preferred or "").strip()
        if preferred_name:
            if preferred_name in outputs:
                candidates.append(preferred_name)
            else:
                scored = sorted(
                    ((_port_name_score(preferred_name, out_name), out_name) for out_name in outputs),
                    reverse=True,
                )
                candidates.extend(out_name for score, out_name in scored if score >= 0.45)

        if not candidates:
            candidates.extend(
                out_name
                for out_name in outputs
                if any(hint in out_name.lower() for hint in AUTO_MIDI_HINTS)
            )

        if not candidates and len(outputs) == 1:
            candidates.append(outputs[0])

        seen = set()
        for out_name in candidates:
            if out_name in seen:
                continue
            seen.add(out_name)
            try:
                return mido.open_output(out_name)
            except Exception as exc:
                now = time.time()
                should_log = (
                    out_name != self._last_temp_output_error_name
                    or now - self._last_temp_output_error >= max(1.0, TEMP_OUTPUT_ERROR_LOG_SEC)
                )
                if should_log:
                    LOGGER.warning("Temporary MIDI output open error (%s): %s", out_name, exc)
                    self._last_temp_output_error = now
                    self._last_temp_output_error_name = out_name
                else:
                    LOGGER.debug("Temporary MIDI output open error (%s): %s", out_name, exc)
        return None

    def _close_port(self) -> None:
        self._close_output()
        if self._input is not None:
            try:
                self._input.close()
            except Exception:
                pass
            self._input = None
        if self._device_name:
            self._device_name = ""
        if self._connected:
            self._set_status(False, "disconnected")

    def _close_output(self) -> None:
        if self._output is not None:
            try:
                self._output.close()
            except Exception:
                pass
            self._output = None

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if self._input is None:
                time.sleep(0.05)
                continue
            try:
                for message in self._input.iter_pending():
                    self._handle_message(message, time.time())
            except Exception as exc:
                LOGGER.error("MIDI poll error: %s", exc)
                time.sleep(0.1)
                continue
            time.sleep(max(0.0005, MIDI_POLL_INTERVAL_SEC))

    def _handle_message(self, message: "mido.Message", ts: float) -> None:
        msg_type = getattr(message, "type", "")
        if msg_type not in ("note_on", "note_off"):
            return
        # Keep UI updates throttled even in debug mode to avoid Tk event queue
        # saturation when a device chatters.
        if self._on_message and (ts - self._last_message_emit_ts) >= max(0.01, MIDI_UI_MESSAGE_MIN_INTERVAL_SEC):
            self._last_message_emit_ts = ts
            self._on_message(_format_message(message))

        channel = int(getattr(message, "channel", 0)) + 1
        note = int(message.note)
        velocity = int(message.velocity)
        is_on = msg_type == "note_on" and velocity > 0
        self._maybe_auto_map(channel, note, is_on)

        updated = False
        with self._lock:
            mapping = self._mapping
            if note == mapping.note_dit:
                updated = self._apply_note_state("dit", note, is_on, ts)
            elif note == mapping.note_dah:
                updated = self._apply_note_state("dah", note, is_on, ts)
            else:
                now = time.time()
                if now - self._last_unmatched_log > 2.0:
                    LOGGER.info(
                        "Ignoring MIDI note ch=%d note=%d (mapped ch=%d dit=%d dah=%d)",
                        channel,
                        note,
                        mapping.channel,
                        mapping.note_dit,
                        mapping.note_dah,
                    )
                    self._last_unmatched_log = now

        if updated:
            if DEBUG_MIDI:
                LOGGER.info(
                    "MIDI state dit=%s dah=%s note=%s vel=%s ts=%.3f",
                    self._state["dit"],
                    self._state["dah"],
                    note,
                    velocity,
                    ts,
                )
            self._on_paddle(self._state["dit"], self._state["dah"])

    def _apply_note_state(self, key: str, note: int, is_on: bool, ts: float) -> bool:
        if self._state[key] == is_on:
            return False
        if is_on:
            last_on = self._last_note_on_ts.get(note, 0.0)
            if ts - last_on < max(0.0, MIDI_NOTE_ON_FILTER_SEC):
                return False
            self._last_note_on_ts[note] = ts
        last_edge = self._last_note_edge_ts.get(note, 0.0)
        last_state = self._last_note_edge_state.get(note)
        if (
            last_state is not None
            and last_state == is_on
            and ts - last_edge < max(0.0, MIDI_EDGE_DEBOUNCE_SEC)
        ):
            return False
        self._state[key] = is_on
        self._last_note_edge_ts[note] = ts
        self._last_note_edge_state[note] = is_on
        return True

    def _maybe_auto_map(self, channel: int, note: int, is_on: bool) -> None:
        with self._lock:
            now = time.time()
            mapping = self._mapping
            in_startup_window = now <= self._auto_map_until

            if (
                in_startup_window
                and not self._mapping_locked
                and channel != mapping.channel
                and self._matched_events == 0
                and is_on
            ):
                self._mapping = MidiMapping(
                    channel=channel,
                    note_dit=mapping.note_dit,
                    note_dah=mapping.note_dah,
                )
                LOGGER.info(
                    "MIDI auto-map channel: %d -> %d (startup)",
                    mapping.channel,
                    channel,
                )
                mapping = self._mapping

            if note in {mapping.note_dit, mapping.note_dah}:
                if is_on:
                    self._matched_events += 1
                    if self._matched_events >= 2:
                        self._mapping_locked = True
                return

            if self._mapping_locked:
                return
            allow_pair_autodetect = in_startup_window
            if not allow_pair_autodetect and (mapping.note_dit, mapping.note_dah) in {(0, 1), (1, 2)}:
                # Keep learning known 0/1<->1/2 Vail raw pairs even after
                # startup, because first real paddle press may happen later.
                allow_pair_autodetect = True
            if not allow_pair_autodetect:
                return

            if self._state["dit"] or self._state["dah"]:
                return

            if now < self._note_learn_ignore_until:
                return

            if self._matched_events > 0:
                # When defaults are 0/1 and adapter emits 1/2, a first press on
                # note 1 should not permanently block the 1/2 auto-map.
                if (mapping.note_dit, mapping.note_dah) not in {(0, 1), (1, 2)}:
                    return

            auto_pair: Optional[tuple[int, int]] = None
            if note == 2:
                auto_pair = (1, 2)
            elif note == 0:
                auto_pair = (0, 1)
            elif note in {61, 62}:
                auto_pair = (61, 62)
            elif note in {48, 50}:
                auto_pair = (50, 48)

            if auto_pair is None:
                return

            if (mapping.note_dit, mapping.note_dah) != auto_pair:
                LOGGER.info(
                    "MIDI auto-map notes: dit=%d -> %d dah=%d -> %d",
                    mapping.note_dit,
                    auto_pair[0],
                    mapping.note_dah,
                    auto_pair[1],
                )
                self._mapping = MidiMapping(
                    channel=channel,
                    note_dit=auto_pair[0],
                    note_dah=auto_pair[1],
                )


