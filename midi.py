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
SEND_PASSTHROUGH_PC = os.getenv("VAIL_SEND_PASSTHROUGH_PC", "0") == "1"
FORCE_RETRY_SEC = float(os.getenv("VAIL_FORCE_RETRY_SEC", "2.0"))


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
        self._mapping = MidiMapping(channel=1, note_dit=48, note_dah=50)

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
        self._monitor_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None

        self._state = {"dit": False, "dah": False}
        self._matched_events = 0
        self._learn_note_dit: Optional[int] = None
        self._learn_note_dah: Optional[int] = None
        self._last_unmatched_log = 0.0
        self._last_force_attempt = 0.0
        self._lock = threading.Lock()

    @property
    def device_name(self) -> str:
        return self._device_name

    def update_mapping(self, channel: int, note_dit: int, note_dah: int) -> None:
        with self._lock:
            self._mapping = MidiMapping(channel=channel, note_dit=note_dit, note_dah=note_dah)
            self._matched_events = 0
            self._learn_note_dit = None
            self._learn_note_dah = None

    def set_sync_output_device(self, device_name: str) -> None:
        self._sync_output_device = (device_name or "").strip()

    def sync_vail_hardware(
        self,
        wpm: float,
        keyer_type: Optional[str] = None,
        keep_midi_mode: bool = False,
    ) -> None:
        """Sync keyer settings to Vail adapter output (mode/keyer/wpm)."""
        if not mido or not _set_backend():
            return

        target = self._output
        temporary_output = None
        if target is None:
            preferred = self._sync_output_device or self._device_name or self._desired_device
            temporary_output = self._open_temp_output(preferred)
            target = temporary_output
        if not target:
            return

        try:
            target.send(
                mido.Message(
                    "control_change",
                    channel=0,
                    control=VAIL_CC_MODE,
                    value=VAIL_MODE_MIDI,
                )
            )
            program = _keyer_program(keyer_type)
            if program is not None:
                target.send(mido.Message("program_change", channel=0, program=program))

            cc_value = _wpm_to_cc_dit_value(wpm)
            target.send(
                mido.Message(
                    "control_change",
                    channel=0,
                    control=VAIL_CC_DIT_DURATION,
                    value=cc_value,
                )
            )
            # This app currently keys via CTRL input; return adapter to keyboard
            # mode after sync so keying keeps working.
            if not keep_midi_mode:
                target.send(
                    mido.Message(
                        "control_change",
                        channel=0,
                        control=VAIL_CC_MODE,
                        value=VAIL_MODE_KEYBOARD,
                    )
                )
            LOGGER.info(
                "Vail sync sent: keyer=%s wpm=%.1f cc1=%d keep_midi=%s",
                keyer_type or "-",
                float(wpm),
                cc_value,
                bool(keep_midi_mode),
            )
        except Exception as exc:
            LOGGER.warning("Vail sync failed: %s", exc)
        finally:
            if temporary_output is not None:
                try:
                    temporary_output.close()
                except Exception:
                    pass

    def open(self, preferred: str = "") -> None:
        self._desired_device = preferred
        with self._lock:
            self._matched_events = 0
            self._learn_note_dit = None
            self._learn_note_dah = None
        self._ensure_threads()
        self._reconnect.set()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._close_port()
            self._disabled_reported = False
            self._set_status(False, "disabled")
        else:
            self._disabled_reported = False
            self._reconnect.set()

    def close(self) -> None:
        LOGGER.info("MIDI close requested")
        self._close_port()

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
            if not self._enabled:
                if self._input:
                    self._close_port()
                if not self._disabled_reported:
                    self._set_status(False, "disabled")
                    self._disabled_reported = True
                self._reconnect.wait(0.5)
                self._reconnect.clear()
                continue
            self._disabled_reported = False
            if not self._input:
                self._attempt_open()
            else:
                devices = list_midi_devices()
                if self._device_name and self._device_name not in devices:
                    LOGGER.warning("MIDI device disconnected: %s", self._device_name)
                    self._close_port()
                    continue
                desired = self._desired_device
                if desired and desired in devices and desired != self._device_name:
                    LOGGER.info("MIDI switching to device: %s", desired)
                    self._close_port()
            self._reconnect.wait(2.0)
            self._reconnect.clear()

    def _attempt_open(self) -> None:
        if not self._enabled:
            return
        devices = list_midi_devices()
        name = self._desired_device if self._desired_device in devices else auto_detect_device(devices)
        if not name:
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
            self._open_output(name)
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
        candidates: List[str] = []
        if preferred:
            for output_name in outputs:
                lower = output_name.lower()
                if preferred in lower or lower in preferred:
                    candidates.append(output_name)
        if not candidates:
            candidates = [
                output_name
                for output_name in outputs
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
            LOGGER.warning("No MIDI output ports found (cannot force MIDI mode)")
            return

        candidates: List[str] = []
        if name in outputs:
            candidates.append(name)
        else:
            scored = sorted(
                ((_port_name_score(name, out_name), out_name) for out_name in outputs),
                reverse=True,
            )
            candidates.extend(out_name for score, out_name in scored if score >= 0.45)

        if not candidates and len(outputs) == 1:
            candidates.append(outputs[0])

        if not candidates:
            LOGGER.warning(
                "No matching MIDI output for input '%s'. Available outputs: %s",
                name,
                outputs,
            )
            return

        for out_name in candidates:
            try:
                self._output = mido.open_output(out_name)
                LOGGER.info("MIDI output paired: input='%s' output='%s'", name, out_name)
                self._send_startup_commands()
                return
            except Exception as exc:
                LOGGER.warning("MIDI output open error (%s): %s", out_name, exc)
                self._output = None

        LOGGER.warning(
            "Failed to open all MIDI output candidates for '%s'. Available outputs: %s",
            name,
            outputs,
        )

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
                LOGGER.warning("Temporary MIDI output open error (%s): %s", out_name, exc)
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
            if not self._input:
                time.sleep(0.05)
                continue
            try:
                for message in self._input.iter_pending():
                    self._handle_message(message, time.time())
            except Exception as exc:
                LOGGER.error("MIDI poll error: %s", exc)
                time.sleep(0.1)
                continue
            time.sleep(0.005)

    def _handle_message(self, message: "mido.Message", ts: float) -> None:
        if self._on_message:
            self._on_message(_format_message(message))

        msg_type = getattr(message, "type", "")
        if msg_type not in ("note_on", "note_off"):
            return

        channel = int(getattr(message, "channel", 0)) + 1
        note = int(message.note)
        velocity = int(message.velocity)
        is_on = msg_type == "note_on" and velocity > 0
        self._maybe_auto_map(channel, note, is_on)

        updated = False
        with self._lock:
            mapping = self._mapping
            if channel != mapping.channel:
                now = time.time()
                if now - self._last_unmatched_log > 2.0:
                    LOGGER.info(
                        "Ignoring MIDI channel %d (mapped channel=%d)",
                        channel,
                        mapping.channel,
                    )
                    self._last_unmatched_log = now
                return
            if note == mapping.note_dit:
                if self._state["dit"] != is_on:
                    self._state["dit"] = is_on
                    updated = True
            elif note == mapping.note_dah:
                if self._state["dah"] != is_on:
                    self._state["dah"] = is_on
                    updated = True
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

    def _maybe_auto_map(self, channel: int, note: int, is_on: bool) -> None:
        if not is_on:
            return
        with self._lock:
            mapping = self._mapping
            mapped_notes = {mapping.note_dit, mapping.note_dah}
            if note in mapped_notes:
                if channel != mapping.channel:
                    self._mapping = MidiMapping(
                        channel=channel,
                        note_dit=mapping.note_dit,
                        note_dah=mapping.note_dah,
                    )
                    LOGGER.info(
                        "MIDI auto-map channel: %d -> %d",
                        mapping.channel,
                        channel,
                    )
                self._matched_events += 1
                return

            new_channel = mapping.channel
            new_dit = mapping.note_dit
            new_dah = mapping.note_dah
            changed = False

            if channel != mapping.channel:
                new_channel = channel
                changed = True
                LOGGER.info(
                    "MIDI auto-map channel: %d -> %d (from note %d)",
                    mapping.channel,
                    channel,
                    note,
                )

            if self._learn_note_dit is None and self._matched_events == 0:
                self._learn_note_dit = note
                if note != mapping.note_dit:
                    LOGGER.info(
                        "MIDI auto-map DIT note: %d -> %d",
                        mapping.note_dit,
                        note,
                    )
                    new_dit = note
                    changed = True
            elif (
                note != mapping.note_dit
                and self._learn_note_dah is None
                and (mapping.note_dah == 50 or mapping.note_dah == mapping.note_dit)
            ):
                self._learn_note_dah = note
                if note != mapping.note_dah:
                    LOGGER.info(
                        "MIDI auto-map DAH note: %d -> %d",
                        mapping.note_dah,
                        note,
                    )
                    new_dah = note
                    changed = True

            if changed:
                self._mapping = MidiMapping(
                    channel=new_channel,
                    note_dit=new_dit,
                    note_dah=new_dah,
                )

