"""Tkinter UI for the Vail-CW Thetis app."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency
    sd = None

try:
    import serial.tools.list_ports as list_ports
except ImportError:  # pragma: no cover - optional dependency
    list_ports = None

from config import AppConfig


ConfigCallback = Callable[[AppConfig], None]
PTTCallback = Callable[[bool], None]
ReleaseCallback = Callable[[], None]
MidiReconnectCallback = Callable[[], None]
TestSidetoneCallback = Callable[[], None]
ClearDecodeCallback = Callable[[], None]
SyncVailCallback = Callable[[], None]
CtrlKeyEventCallback = Callable[[str, int, int, bool], None]


KEYER_TYPES = [
    "Straight",
    "Bug",
    "IambicA",
    "IambicB",
    "Ultimatic",
    "SingleDot",
    "ElBug",
    "PlainIambic",
    "Keyahead",
]
INPUT_MODES = ["CTRL", "MIDI"]
PTT_METHODS = ["CAT", "RTS", "DTR", "THETIS_DSP"]
LINE_OPTIONS = ["None", "DTR", "RTS"]
CAT_BAUD_RATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
MIDI_HW_HINTS = ("vail", "summit", "seeed", "xiao")
MIDI_SOFT_HINTS = ("microsoft gs wavetable", "midi mapper", "software synth", "virtual")


def _audio_output_device_list() -> List[str]:
    if not sd:
        return []
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_output_channels", 0) <= 0:
            continue
        name = dev.get("name", f"Device {idx}")
        devices.append(f"{idx}: {name}")
    return devices


def _audio_input_device_list() -> List[str]:
    if not sd:
        return []
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = dev.get("name", f"Device {idx}")
        devices.append(f"{idx}: {name}")
    return devices


def _serial_ports() -> List[str]:
    if not list_ports:
        return []
    return [port.device for port in list_ports.comports()]


def _pick_midi_output_for_ui(devices: List[str]) -> str:
    if not devices:
        return ""

    def score(name: str) -> int:
        lower = (name or "").lower()
        value = 0
        if any(h in lower for h in MIDI_HW_HINTS):
            value += 100
        if any(h in lower for h in MIDI_SOFT_HINTS):
            value -= 100
        return value

    ranked = sorted(devices, key=score, reverse=True)
    return ranked[0]


def _is_soft_midi_output_for_ui(name: str) -> bool:
    lower = (name or "").lower()
    return any(h in lower for h in MIDI_SOFT_HINTS)


class AppUI(tk.Tk):
    """Main application window."""

    def __init__(
        self,
        config: AppConfig,
        on_config: ConfigCallback,
        on_manual_ptt: Optional[PTTCallback] = None,
        on_release: Optional[ReleaseCallback] = None,
        on_midi_reconnect: Optional[MidiReconnectCallback] = None,
        on_test_sidetone: Optional[TestSidetoneCallback] = None,
        on_clear_decode: Optional[ClearDecodeCallback] = None,
        on_sync_vail: Optional[SyncVailCallback] = None,
        on_ctrl_key_event: Optional[CtrlKeyEventCallback] = None,
    ) -> None:
        super().__init__()
        self.title("Vail-CW Thetis")
        self._ready = False
        self._config = config
        self._on_config = on_config
        self._on_manual_ptt = on_manual_ptt
        self._on_release = on_release
        self._on_midi_reconnect = on_midi_reconnect
        self._on_test_sidetone = on_test_sidetone
        self._on_clear_decode = on_clear_decode
        self._on_sync_vail = on_sync_vail
        self._on_ctrl_key_event = on_ctrl_key_event
        self._decode_segments: List[tuple[str, str]] = []
        self._decode_window: Optional[tk.Toplevel] = None
        self._decode_popup_text: Optional[tk.Text] = None
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._midi_refresh_inflight = False
        self._audio_refresh_inflight = False
        self._midi_refresh_token = 0
        self._audio_refresh_token = 0
        self._build()
        self._populate_device_lists()
        self._bind_ctrl_keys()
        self.after(16, self._drain_ui_queue)
        self._ready = True

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame = ttk.Frame(self, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)

        # Keep compatibility state vars even though MIDI controls are hidden.
        self.midi_status = tk.StringVar(value="disabled")
        self.midi_last = tk.StringVar(value="-")

        row = 0
        input_block = ttk.LabelFrame(frame, text="Input")
        input_block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        input_block.columnconfigure(1, weight=1)

        ttk.Label(input_block, text="Input Mode").grid(row=0, column=0, sticky="w")
        mode_value = str(self._config.input_mode or "CTRL").strip().upper()
        if mode_value not in INPUT_MODES:
            mode_value = "CTRL"
        self.input_mode_var = tk.StringVar(value=mode_value)
        ttk.Combobox(input_block, textvariable=self.input_mode_var, values=INPUT_MODES, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )

        ttk.Label(input_block, text="Audio Output").grid(row=1, column=0, sticky="w")
        self.audio_out_var = tk.StringVar(value=self._config.audio_output_device)
        self.audio_out_combo = ttk.Combobox(input_block, textvariable=self.audio_out_var, state="readonly")
        self.audio_out_combo.grid(row=1, column=1, sticky="ew")
        ttk.Button(input_block, text="Refresh", command=self.refresh_audio).grid(row=1, column=2, padx=5)

        ttk.Label(input_block, text="Vail MIDI Out").grid(row=2, column=0, sticky="w")
        self.vail_midi_var = tk.StringVar(value=self._config.midi_device or "")
        self.vail_midi_combo = ttk.Combobox(input_block, textvariable=self.vail_midi_var, state="readonly")
        self.vail_midi_combo.grid(row=2, column=1, sticky="ew")
        midi_buttons = ttk.Frame(input_block)
        midi_buttons.grid(row=2, column=2, sticky="e")
        self.midi_refresh_btn = ttk.Button(midi_buttons, text="Refresh", command=self.refresh_midi)
        self.midi_refresh_btn.grid(row=0, column=0, padx=2)
        self.sync_vail_btn = ttk.Button(midi_buttons, text="Sync Vail", command=self._handle_sync_vail)
        self.sync_vail_btn.grid(row=0, column=1, padx=2)

        row += 1
        wpm_frame = ttk.LabelFrame(frame, text="WPM")
        wpm_frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        wpm_frame.columnconfigure(0, weight=1)

        keyer_row = ttk.Frame(wpm_frame)
        keyer_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        keyer_row.columnconfigure(1, weight=1)
        ttk.Label(keyer_row, text="Keyer Type").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.keyer_var = tk.StringVar(value=self._config.keyer_type)
        ttk.Combobox(keyer_row, textvariable=self.keyer_var, values=KEYER_TYPES, state="readonly").grid(
            row=0, column=1, sticky="ew"
        )

        self.wpm_var = tk.DoubleVar(value=float(self._clamp_wpm(self._config.wpm)))
        self.wpm_value_var = tk.StringVar(value="")
        value_row = ttk.Frame(wpm_frame)
        value_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 0))
        value_row.columnconfigure(0, weight=1)
        ttk.Label(value_row, text="Speed").grid(row=0, column=0, sticky="w")
        self.wpm_value_label = tk.Label(value_row, textvariable=self.wpm_value_var, font=("Segoe UI", 24, "bold"))
        self.wpm_value_label.grid(row=0, column=1, sticky="e")
        self.wpm_scale = ttk.Scale(
            wpm_frame,
            from_=5,
            to=40,
            orient="horizontal",
            variable=self.wpm_var,
            command=self._on_wpm_scale,
        )
        self.wpm_scale.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 0))
        ticks = ttk.Frame(wpm_frame)
        ticks.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))
        ticks.columnconfigure(0, weight=1)
        ttk.Label(ticks, text="5").grid(row=0, column=0, sticky="w")
        ttk.Label(ticks, text="40").grid(row=0, column=1, sticky="e")
        wpm_buttons = ttk.Frame(wpm_frame)
        wpm_buttons.grid(row=4, column=0, sticky="e", padx=8, pady=(0, 4))
        ttk.Button(wpm_buttons, text="-5", width=4, command=lambda: self._nudge_wpm(-5)).grid(row=0, column=0, padx=2)
        ttk.Button(wpm_buttons, text="-1", width=4, command=lambda: self._nudge_wpm(-1)).grid(row=0, column=1, padx=2)
        ttk.Button(wpm_buttons, text="+1", width=4, command=lambda: self._nudge_wpm(1)).grid(row=0, column=2, padx=2)
        ttk.Button(wpm_buttons, text="+5", width=4, command=lambda: self._nudge_wpm(5)).grid(row=0, column=3, padx=2)
        self._update_wpm_display()

        tune_frame = ttk.Frame(wpm_frame)
        tune_frame.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 6))
        tune_frame.columnconfigure(1, weight=1)
        tune_frame.columnconfigure(3, weight=1)
        ttk.Label(tune_frame, text="Dit/Dah Ratio").grid(row=0, column=0, sticky="w")
        self.ratio_var = tk.DoubleVar(value=self._config.dit_dah_ratio)
        ttk.Spinbox(tune_frame, from_=2.0, to=6.0, textvariable=self.ratio_var, increment=0.1, width=7).grid(
            row=0, column=1, sticky="w", padx=(8, 18)
        )
        ttk.Label(tune_frame, text="Weighting (%)").grid(row=0, column=2, sticky="w")
        self.weight_var = tk.DoubleVar(value=self._config.weighting)
        ttk.Spinbox(tune_frame, from_=30.0, to=70.0, textvariable=self.weight_var, increment=1, width=7).grid(
            row=0, column=3, sticky="w", padx=(8, 0)
        )
        self.reverse_var = tk.BooleanVar(value=self._config.paddle_reverse)
        ttk.Checkbutton(wpm_frame, text="Paddle Reverse", variable=self.reverse_var).grid(
            row=6, column=0, sticky="w", padx=8, pady=(0, 6)
        )

        row += 1
        decode_block = ttk.LabelFrame(frame, text="Decode")
        decode_block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        decode_block.columnconfigure(1, weight=1)
        self.decode_audio_enabled_var = tk.BooleanVar(value=bool(self._config.decode_audio_enabled))
        ttk.Checkbutton(decode_block, text="Decode Audio In", variable=self.decode_audio_enabled_var).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(decode_block, text="Decode Audio Input").grid(row=1, column=0, sticky="w")
        self.decode_audio_in_var = tk.StringVar(value=self._config.decode_audio_input_device)
        self.decode_audio_in_combo = ttk.Combobox(decode_block, textvariable=self.decode_audio_in_var, state="readonly")
        self.decode_audio_in_combo.grid(row=1, column=1, sticky="ew")

        ttk.Label(decode_block, text="Decode Range (Hz)").grid(row=2, column=0, sticky="w")
        self.decode_tone_low_var = tk.DoubleVar(value=float(self._config.decode_audio_tone_low_freq))
        self.decode_tone_high_var = tk.DoubleVar(value=float(self._config.decode_audio_tone_high_freq))
        range_frame = ttk.Frame(decode_block)
        range_frame.grid(row=2, column=1, sticky="ew")
        ttk.Label(range_frame, text="Min").grid(row=0, column=0, padx=(0, 4))
        ttk.Spinbox(range_frame, from_=300, to=1200, increment=10, textvariable=self.decode_tone_low_var, width=7).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Label(range_frame, text="Max").grid(row=0, column=2, padx=(0, 4))
        ttk.Spinbox(range_frame, from_=300, to=1200, increment=10, textvariable=self.decode_tone_high_var, width=7).grid(
            row=0, column=3
        )
        ttk.Label(decode_block, text="Decode Audio").grid(row=3, column=0, sticky="w")
        self.decode_audio_status = tk.StringVar(value="disabled")
        ttk.Label(decode_block, textvariable=self.decode_audio_status).grid(row=3, column=1, sticky="w")
        ttk.Label(decode_block, text="Decode WPM").grid(row=4, column=0, sticky="w")
        self.decode_wpm = tk.StringVar(value="-")
        ttk.Label(decode_block, textvariable=self.decode_wpm).grid(row=4, column=1, sticky="w")

        row += 1
        sidetone_block = ttk.LabelFrame(frame, text="Sidetone")
        sidetone_block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        sidetone_block.columnconfigure(1, weight=1)
        self.sidetone_enabled_var = tk.BooleanVar(value=bool(self._config.sidetone_enabled))
        ttk.Checkbutton(sidetone_block, text="Sidetone On", variable=self.sidetone_enabled_var).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Button(sidetone_block, text="Test Sidetone", command=self._handle_test_sidetone).grid(
            row=0, column=2, padx=5
        )
        ttk.Label(sidetone_block, text="Sidetone Frequency (Hz)").grid(row=1, column=0, sticky="w")
        self.tone_freq_var = tk.DoubleVar(value=self._config.sidetone_freq)
        ttk.Spinbox(sidetone_block, from_=300, to=1200, textvariable=self.tone_freq_var, increment=10).grid(
            row=1, column=1, sticky="ew"
        )
        ttk.Label(sidetone_block, text="Sidetone Volume").grid(row=2, column=0, sticky="w")
        self.tone_vol_var = tk.DoubleVar(value=self._config.sidetone_volume)
        ttk.Scale(sidetone_block, from_=0.0, to=1.0, variable=self.tone_vol_var, orient="horizontal").grid(
            row=2, column=1, sticky="ew"
        )

        row += 1
        cat_block = ttk.LabelFrame(frame, text="CAT / Thetis")
        cat_block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        cat_block.columnconfigure(1, weight=1)
        ttk.Label(cat_block, text="CAT/DSP Port").grid(row=0, column=0, sticky="w")
        self.cat_port_var = tk.StringVar(value=self._config.cat_port)
        self.cat_port_combo = ttk.Combobox(cat_block, textvariable=self.cat_port_var)
        self.cat_port_combo.grid(row=0, column=1, sticky="ew")
        ttk.Button(cat_block, text="Refresh", command=self.refresh_cat_ports).grid(row=0, column=2, padx=5)
        ttk.Label(cat_block, text="CAT Baud").grid(row=1, column=0, sticky="w")
        self.cat_baud_var = tk.StringVar(value=str(self._config.cat_baud))
        ttk.Combobox(
            cat_block,
            textvariable=self.cat_baud_var,
            values=CAT_BAUD_RATES,
            state="readonly",
        ).grid(row=1, column=1, sticky="ew")
        ttk.Label(cat_block, text="PTT Method").grid(row=2, column=0, sticky="w")
        ptt_method = self._config.ptt_method if self._config.ptt_method in PTT_METHODS else "CAT"
        self.ptt_var = tk.StringVar(value=ptt_method)
        ttk.Combobox(cat_block, textvariable=self.ptt_var, values=PTT_METHODS, state="readonly").grid(
            row=2, column=1, sticky="ew"
        )
        ttk.Label(cat_block, text="Thetis Key Line").grid(row=3, column=0, sticky="w")
        key_line = self._config.thetis_key_line if self._config.thetis_key_line in LINE_OPTIONS else "DTR"
        self.thetis_key_line_var = tk.StringVar(value=key_line)
        ttk.Combobox(
            cat_block,
            textvariable=self.thetis_key_line_var,
            values=LINE_OPTIONS,
            state="readonly",
        ).grid(row=3, column=1, sticky="ew")
        ttk.Label(cat_block, text="Thetis PTT Line").grid(row=4, column=0, sticky="w")
        ptt_line = self._config.thetis_ptt_line if self._config.thetis_ptt_line in LINE_OPTIONS else "None"
        self.thetis_ptt_line_var = tk.StringVar(value=ptt_line)
        ttk.Combobox(
            cat_block,
            textvariable=self.thetis_ptt_line_var,
            values=LINE_OPTIONS,
            state="readonly",
        ).grid(row=4, column=1, sticky="ew")
        self.thetis_key_invert_var = tk.BooleanVar(value=self._config.thetis_key_invert)
        ttk.Checkbutton(
            cat_block,
            text="Invert Thetis Key",
            variable=self.thetis_key_invert_var,
        ).grid(row=5, column=1, sticky="w")
        self.thetis_ptt_invert_var = tk.BooleanVar(value=self._config.thetis_ptt_invert)
        ttk.Checkbutton(
            cat_block,
            text="Invert Thetis PTT",
            variable=self.thetis_ptt_invert_var,
        ).grid(row=6, column=1, sticky="w")
        ttk.Label(cat_block, text="TX Hang Time (s)").grid(row=7, column=0, sticky="w")
        self.hang_var = tk.DoubleVar(value=self._config.tx_hang_time)
        ttk.Spinbox(cat_block, from_=0.0, to=2.0, textvariable=self.hang_var, increment=0.05).grid(
            row=7, column=1, sticky="ew"
        )
        self.manual_ptt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            cat_block,
            text="Manual PTT",
            variable=self.manual_ptt_var,
            command=self._toggle_manual_ptt,
        ).grid(row=8, column=1, sticky="w")
        ttk.Button(cat_block, text="Release Key", command=self._handle_release).grid(row=8, column=2, padx=5)

        row += 1
        status_block = ttk.LabelFrame(frame, text="Status")
        status_block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        status_block.columnconfigure(1, weight=1)
        ttk.Label(status_block, text="CTRL Status").grid(row=0, column=0, sticky="w")
        self.ctrl_status = tk.StringVar(value="disabled")
        ttk.Label(status_block, textvariable=self.ctrl_status).grid(row=0, column=1, sticky="w")
        ttk.Label(status_block, text="PTT Status").grid(row=1, column=0, sticky="w")
        self.ptt_status = tk.StringVar(value="RX")
        ttk.Label(status_block, textvariable=self.ptt_status).grid(row=1, column=1, sticky="w")
        ttk.Label(status_block, text="Keying").grid(row=2, column=0, sticky="w")
        self.keying_status = tk.StringVar(value="OFF")
        ttk.Label(status_block, textvariable=self.keying_status).grid(row=2, column=1, sticky="w")
        ttk.Label(status_block, text="Port Status").grid(row=3, column=0, sticky="w")
        self.port_status = tk.StringVar(value="n/a")
        ttk.Label(status_block, textvariable=self.port_status).grid(row=3, column=1, sticky="w")
        ttk.Label(status_block, text="Audio Level").grid(row=4, column=0, sticky="w")
        self.level_var = tk.DoubleVar(value=0.0)
        self.level_bar = ttk.Progressbar(status_block, maximum=1.0, variable=self.level_var)
        self.level_bar.grid(row=4, column=1, sticky="ew")

        row += 1
        decoded_block = ttk.LabelFrame(frame, text="Decoded")
        decoded_block.grid(row=row, column=0, sticky="nsew")
        decoded_block.columnconfigure(0, weight=1)
        decoded_block.rowconfigure(1, weight=1)
        frame.rowconfigure(row, weight=1)
        self.decode_font_var = tk.IntVar(value=int(self._config.decode_font_size))
        toolbar = ttk.Frame(decoded_block)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        font_frame = ttk.Frame(toolbar)
        font_frame.grid(row=0, column=0, sticky="w")
        ttk.Label(font_frame, text="Font").grid(row=0, column=0, padx=(0, 4))
        ttk.Spinbox(font_frame, from_=9, to=42, textvariable=self.decode_font_var, width=5).grid(
            row=0, column=1
        )
        button_frame = ttk.Frame(toolbar)
        button_frame.grid(row=0, column=2, sticky="e")
        ttk.Button(button_frame, text="Clear", command=self._handle_clear_decode).grid(row=0, column=0, padx=3)
        ttk.Button(button_frame, text="Detach", command=self._open_decode_window).grid(row=0, column=1, padx=3)

        self.decoded_text = tk.Text(decoded_block, height=8, wrap="word")
        self.decoded_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.decoded_text.tag_configure("local", foreground="#111111")
        self.decoded_text.tag_configure("audio", foreground="#B00020")
        self._apply_decode_font()
        self.decoded_text.configure(state="disabled")

        self._bind_changes()
        self._update_mode_controls()

    def _bind_changes(self) -> None:
        for var in [
            self.input_mode_var,
            self.audio_out_var,
            self.vail_midi_var,
            self.decode_audio_enabled_var,
            self.decode_audio_in_var,
            self.decode_tone_low_var,
            self.decode_tone_high_var,
            self.decode_font_var,
            self.sidetone_enabled_var,
            self.keyer_var,
            self.wpm_var,
            self.ratio_var,
            self.weight_var,
            self.reverse_var,
            self.tone_freq_var,
            self.tone_vol_var,
            self.cat_port_var,
            self.cat_baud_var,
            self.ptt_var,
            self.thetis_key_line_var,
            self.thetis_ptt_line_var,
            self.thetis_key_invert_var,
            self.thetis_ptt_invert_var,
            self.hang_var,
        ]:
            var.trace_add("write", self._apply_config)

    @staticmethod
    def _clamp_wpm(value: float) -> int:
        return max(5, min(40, int(round(float(value)))))

    def _update_wpm_display(self) -> None:
        wpm = self._clamp_wpm(self.wpm_var.get())
        if abs(float(self.wpm_var.get()) - float(wpm)) > 0.001:
            self.wpm_var.set(float(wpm))
        self.wpm_value_var.set(f"{wpm} WPM")

    def _on_wpm_scale(self, value: str) -> None:
        wpm = self._clamp_wpm(float(value))
        if abs(float(self.wpm_var.get()) - float(wpm)) > 0.001:
            self.wpm_var.set(float(wpm))
        self._update_wpm_display()

    def _nudge_wpm(self, delta: int) -> None:
        next_wpm = self._clamp_wpm(self.wpm_var.get() + delta)
        self.wpm_var.set(float(next_wpm))
        self._update_wpm_display()

    def _update_mode_controls(self) -> None:
        mode = str(self.input_mode_var.get() or "CTRL").strip().upper()
        midi_mode = mode == "MIDI"
        self.vail_midi_combo.configure(state="readonly" if midi_mode else "disabled")
        self.midi_refresh_btn.configure(state="normal" if midi_mode else "disabled")
        self.sync_vail_btn.configure(state="normal" if midi_mode else "disabled")

    def _bind_ctrl_keys(self) -> None:
        self.bind_all("<KeyPress>", self._on_ctrl_key_press)
        self.bind_all("<KeyRelease>", self._on_ctrl_key_release)

    def _on_ctrl_key_press(self, event: tk.Event) -> None:
        if not self._on_ctrl_key_event:
            return
        key = str(getattr(event, "keysym", ""))
        if key in {"Control_L", "Control_R", "Alt_R", "ISO_Level3_Shift", "Mode_switch"}:
            keycode = int(getattr(event, "keycode", 0) or 0)
            keysym_num = int(getattr(event, "keysym_num", 0) or 0)
            self._on_ctrl_key_event(key, keycode, keysym_num, True)

    def _on_ctrl_key_release(self, event: tk.Event) -> None:
        if not self._on_ctrl_key_event:
            return
        key = str(getattr(event, "keysym", ""))
        if key in {"Control_L", "Control_R", "Alt_R", "ISO_Level3_Shift", "Mode_switch"}:
            keycode = int(getattr(event, "keycode", 0) or 0)
            keysym_num = int(getattr(event, "keysym_num", 0) or 0)
            self._on_ctrl_key_event(key, keycode, keysym_num, False)

    def _apply_config(self, *_) -> None:
        if not self._ready:
            return
        selected_mode = str(self.input_mode_var.get() or "CTRL").strip().upper()
        self._config.input_mode = selected_mode if selected_mode in INPUT_MODES else "CTRL"
        self._update_mode_controls()
        self._config.midi_device = self.vail_midi_var.get()
        self._config.audio_output_device = self.audio_out_var.get()
        self._config.decode_audio_enabled = bool(self.decode_audio_enabled_var.get())
        self._config.decode_audio_input_device = self.decode_audio_in_var.get()
        tone_low = self._safe_float(self.decode_tone_low_var.get(), self._config.decode_audio_tone_low_freq)
        tone_high = self._safe_float(self.decode_tone_high_var.get(), self._config.decode_audio_tone_high_freq)
        if tone_low > tone_high:
            tone_low, tone_high = tone_high, tone_low
        self._config.decode_audio_tone_low_freq = max(300.0, min(1200.0, tone_low))
        self._config.decode_audio_tone_high_freq = max(300.0, min(1200.0, tone_high))
        # Keep legacy single-tone field centered in the active range.
        self._config.decode_audio_tone_freq = (
            self._config.decode_audio_tone_low_freq + self._config.decode_audio_tone_high_freq
        ) * 0.5
        self._config.decode_font_size = int(self._safe_float(self.decode_font_var.get(), self._config.decode_font_size))
        self._apply_decode_font()
        self._config.keyer_type = self.keyer_var.get()
        wpm = self._clamp_wpm(self._safe_float(self.wpm_var.get(), self._config.wpm))
        self._config.wpm = float(wpm)
        if abs(float(self.wpm_var.get()) - float(wpm)) > 0.001:
            self.wpm_var.set(float(wpm))
        self._update_wpm_display()
        self._config.dit_dah_ratio = self._safe_float(self.ratio_var.get(), self._config.dit_dah_ratio)
        self._config.weighting = self._safe_float(self.weight_var.get(), self._config.weighting)
        self._config.paddle_reverse = bool(self.reverse_var.get())
        self._config.sidetone_enabled = bool(self.sidetone_enabled_var.get())
        self._config.sidetone_freq = self._safe_float(self.tone_freq_var.get(), self._config.sidetone_freq)
        self._config.sidetone_volume = self._safe_float(self.tone_vol_var.get(), self._config.sidetone_volume)
        self._config.cat_port = self.cat_port_var.get()
        self._config.cat_baud = int(self._safe_float(self.cat_baud_var.get(), self._config.cat_baud))
        ptt_method = self.ptt_var.get()
        self._config.ptt_method = ptt_method if ptt_method in PTT_METHODS else "CAT"
        key_line = self.thetis_key_line_var.get()
        ptt_line = self.thetis_ptt_line_var.get()
        self._config.thetis_key_line = key_line if key_line in LINE_OPTIONS else "DTR"
        self._config.thetis_ptt_line = ptt_line if ptt_line in LINE_OPTIONS else "None"
        self._config.thetis_key_invert = bool(self.thetis_key_invert_var.get())
        self._config.thetis_ptt_invert = bool(self.thetis_ptt_invert_var.get())
        self._config.tx_hang_time = self._safe_float(self.hang_var.get(), self._config.tx_hang_time)
        self._on_config(self._config)

    def _safe_float(self, value, fallback: float) -> float:
        try:
            return float(value)
        except Exception:
            return fallback

    def refresh_midi(self) -> None:
        if self._midi_refresh_inflight:
            return
        self._midi_refresh_inflight = True
        self._midi_refresh_token += 1
        token = self._midi_refresh_token

        def watchdog() -> None:
            if self._midi_refresh_inflight and token == self._midi_refresh_token:
                logging.getLogger(__name__).warning("MIDI refresh timeout")
                self._midi_refresh_inflight = False

        self.after(4000, watchdog)

        def worker() -> None:
            devices: List[str] = []
            try:
                from midi import list_midi_output_devices

                devices = list_midi_output_devices()
            except Exception as exc:
                logging.getLogger(__name__).warning("MIDI refresh failed: %s", exc)

            def apply_devices() -> None:
                if token != self._midi_refresh_token:
                    return
                self.vail_midi_combo["values"] = devices
                current = self.vail_midi_var.get()
                preferred = _pick_midi_output_for_ui(devices)
                if current not in devices:
                    self.vail_midi_var.set(preferred)
                elif _is_soft_midi_output_for_ui(current) and preferred and preferred != current:
                    self.vail_midi_var.set(preferred)
                self._midi_refresh_inflight = False

            self._enqueue_ui(apply_devices)

        threading.Thread(target=worker, name="ui-refresh-midi", daemon=True).start()

    def refresh_audio(self) -> None:
        if self._audio_refresh_inflight:
            return
        self._audio_refresh_inflight = True
        self._audio_refresh_token += 1
        token = self._audio_refresh_token

        def watchdog() -> None:
            if self._audio_refresh_inflight and token == self._audio_refresh_token:
                logging.getLogger(__name__).warning("Audio refresh timeout")
                self._audio_refresh_inflight = False

        self.after(4000, watchdog)

        def worker() -> None:
            outputs: List[str] = []
            inputs: List[str] = []
            try:
                outputs = _audio_output_device_list()
                inputs = _audio_input_device_list()
            except Exception as exc:
                logging.getLogger(__name__).warning("Audio refresh failed: %s", exc)

            def apply_devices() -> None:
                if token != self._audio_refresh_token:
                    return
                self.audio_out_combo["values"] = outputs
                self.decode_audio_in_combo["values"] = inputs
                if self.audio_out_var.get() not in outputs:
                    self.audio_out_var.set(outputs[0] if outputs else "")
                if self.decode_audio_in_var.get() not in inputs:
                    self.decode_audio_in_var.set(inputs[0] if inputs else "")
                self._audio_refresh_inflight = False

            self._enqueue_ui(apply_devices)

        threading.Thread(target=worker, name="ui-refresh-audio", daemon=True).start()

    def refresh_cat_ports(self) -> None:
        self.cat_port_combo["values"] = _serial_ports()

    def _populate_device_lists(self) -> None:
        self.refresh_midi()
        self.refresh_audio()
        self.refresh_cat_ports()

    def _toggle_manual_ptt(self) -> None:
        if self._on_manual_ptt:
            self._on_manual_ptt(bool(self.manual_ptt_var.get()))

    def _handle_release(self) -> None:
        if self._on_release:
            self._on_release()

    def _handle_midi_reconnect(self) -> None:
        if self._on_midi_reconnect:
            self._on_midi_reconnect()

    def _handle_test_sidetone(self) -> None:
        if self._on_test_sidetone:
            self._on_test_sidetone()

    def _handle_clear_decode(self) -> None:
        if self._on_clear_decode:
            self._on_clear_decode()
        else:
            self.clear_decoded_text()

    def _handle_sync_vail(self) -> None:
        # Avoid forcing a full config apply/reconnect cycle from the sync button.
        # Snapshot only fields required for sync into the in-memory config.
        self._config.midi_device = self.vail_midi_var.get()
        self._config.keyer_type = self.keyer_var.get()
        self._config.wpm = float(self._clamp_wpm(self._safe_float(self.wpm_var.get(), self._config.wpm)))
        if self._on_sync_vail:
            self._on_sync_vail()

    def _open_decode_window(self) -> None:
        if self._decode_window and self._decode_window.winfo_exists():
            self._decode_window.lift()
            return

        win = tk.Toplevel(self)
        win.title("Decoded CW")
        win.geometry("900x450")
        win.minsize(500, 250)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)

        text = tk.Text(win, wrap="word")
        text.grid(row=0, column=0, sticky="nsew")
        text.tag_configure("local", foreground="#111111")
        text.tag_configure("audio", foreground="#B00020")
        text.configure(font=self._decode_font_tuple())
        text.configure(state="disabled")

        controls = ttk.Frame(win, padding=6)
        controls.grid(row=1, column=0, sticky="ew")
        ttk.Button(controls, text="Clear", command=self._handle_clear_decode).grid(row=0, column=0, padx=4)
        ttk.Button(controls, text="Close", command=win.destroy).grid(row=0, column=1, padx=4)

        self._decode_window = win
        self._decode_popup_text = text
        self._copy_decode_to_popup()
        win.bind("<Destroy>", self._on_decode_window_destroy, add=True)

    def _copy_decode_to_popup(self) -> None:
        if not self._decode_popup_text:
            return
        self._decode_popup_text.configure(state="normal")
        self._decode_popup_text.delete("1.0", "end")
        for text, tag in self._decode_segments:
            self._decode_popup_text.insert("end", text, tag)
        self._decode_popup_text.configure(state="disabled")

    def _on_decode_window_destroy(self, _event=None) -> None:
        if self._decode_window and not self._decode_window.winfo_exists():
            self._decode_window = None
            self._decode_popup_text = None

    def _enqueue_ui(self, fn: Callable[[], None]) -> None:
        # Tkinter is not thread-safe: never call widget APIs from worker
        # threads. Background threads enqueue updates for the main loop.
        if threading.current_thread() is threading.main_thread():
            try:
                fn()
            except Exception as exc:
                logging.getLogger(__name__).debug("UI update error (main): %s", exc)
            return
        self._ui_queue.put(fn)

    def _drain_ui_queue(self) -> None:
        processed = 0
        try:
            while processed < 256:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logging.getLogger(__name__).debug("UI update error (queued): %s", exc)
                processed += 1
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(16, self._drain_ui_queue)

    def set_midi_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self.midi_status.set(label if label else ("connected" if connected else "disabled"))

        self._enqueue_ui(update)

    def set_ctrl_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self.ctrl_status.set(label if label else ("enabled" if connected else "disabled"))

        self._enqueue_ui(update)

    def set_decode_audio_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self.decode_audio_status.set(label if label else ("listening" if connected else "disabled"))

        self._enqueue_ui(update)

    def set_decode_wpm(self, wpm: Optional[float]) -> None:
        def update() -> None:
            if wpm is None:
                self.decode_wpm.set("-")
            else:
                self.decode_wpm.set(f"{wpm:.1f}")

        self._enqueue_ui(update)

    def set_midi_last(self, text: str) -> None:
        def update() -> None:
            self.midi_last.set(text)

        self._enqueue_ui(update)

    def set_ptt_status(self, tx: bool) -> None:
        def update() -> None:
            self.ptt_status.set("TX" if tx else "RX")

        self._enqueue_ui(update)

    def set_port_status(self, ok: bool, message: str) -> None:
        def update() -> None:
            self.port_status.set(message)

        self._enqueue_ui(update)

    def set_audio_level(self, level: float) -> None:
        def update() -> None:
            self.level_var.set(max(0.0, min(level, 1.0)))

        self._enqueue_ui(update)

    def set_keying_status(self, active: bool) -> None:
        def update() -> None:
            self.keying_status.set("ON" if active else "OFF")

        self._enqueue_ui(update)

    def append_decoded_text(self, text: str, source: str = "local") -> None:
        def update() -> None:
            tag = "audio" if source == "audio" else "local"
            self._decode_segments.append((text, tag))
            if len(self._decode_segments) > 1200:
                self._decode_segments = self._decode_segments[-800:]
            self._append_decode_widget(self.decoded_text, text, tag)
            if self._decode_popup_text and self._decode_popup_text.winfo_exists():
                self._append_decode_widget(self._decode_popup_text, text, tag)

        self._enqueue_ui(update)

    def clear_decoded_text(self) -> None:
        def update() -> None:
            self._decode_segments.clear()
            self._clear_decode_widget(self.decoded_text)
            if self._decode_popup_text and self._decode_popup_text.winfo_exists():
                self._clear_decode_widget(self._decode_popup_text)

        self._enqueue_ui(update)

    def _append_decode_widget(self, widget: tk.Text, text: str, tag: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text, tag)
        # Keep memory bounded in long sessions.
        try:
            chars = int(widget.count("1.0", "end-1c", "chars")[0])
            if chars > 4000:
                widget.delete("1.0", "1000.0")
        except Exception:
            pass
        widget.see("end")
        widget.configure(state="disabled")

    def _clear_decode_widget(self, widget: tk.Text) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    def _decode_font_tuple(self) -> tuple[str, int]:
        size = int(self._safe_float(self.decode_font_var.get(), self._config.decode_font_size))
        size = max(9, min(42, size))
        return ("Consolas", size)

    def _apply_decode_font(self) -> None:
        font = self._decode_font_tuple()
        try:
            self.decoded_text.configure(font=font)
        except Exception:
            pass
        if self._decode_popup_text and self._decode_popup_text.winfo_exists():
            try:
                self._decode_popup_text.configure(font=font)
            except Exception:
                pass
