"""Tkinter UI for the Vail-CW Thetis app."""

from __future__ import annotations

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
PTT_METHODS = ["CAT", "RTS", "DTR", "THETIS_DSP"]
LINE_OPTIONS = ["None", "DTR", "RTS"]
CAT_BAUD_RATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]


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
        self._build()
        self._populate_device_lists()
        self._bind_ctrl_keys()
        self._ready = True

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame = ttk.Frame(self, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)

        # Keep compatibility state vars even though MIDI controls are hidden.
        self.midi_status = tk.StringVar(value="disabled")
        self.midi_last = tk.StringVar(value="-")

        row = 0
        ttk.Label(frame, text="Audio Output").grid(row=row, column=0, sticky="w")
        self.audio_out_var = tk.StringVar(value=self._config.audio_output_device)
        self.audio_out_combo = ttk.Combobox(frame, textvariable=self.audio_out_var, state="readonly")
        self.audio_out_combo.grid(row=row, column=1, sticky="ew")
        ttk.Button(frame, text="Refresh", command=self.refresh_audio).grid(row=row, column=2, padx=5)

        row += 1
        ttk.Label(frame, text="Vail MIDI Out").grid(row=row, column=0, sticky="w")
        self.vail_midi_var = tk.StringVar(value=self._config.midi_device or "")
        self.vail_midi_combo = ttk.Combobox(frame, textvariable=self.vail_midi_var, state="readonly")
        self.vail_midi_combo.grid(row=row, column=1, sticky="ew")
        midi_buttons = ttk.Frame(frame)
        midi_buttons.grid(row=row, column=2, sticky="e")
        ttk.Button(midi_buttons, text="Refresh", command=self.refresh_midi).grid(row=0, column=0, padx=2)
        ttk.Button(midi_buttons, text="Sync Vail", command=self._handle_sync_vail).grid(row=0, column=1, padx=2)

        row += 1
        self.decode_audio_enabled_var = tk.BooleanVar(value=bool(self._config.decode_audio_enabled))
        ttk.Checkbutton(frame, text="Decode Audio In", variable=self.decode_audio_enabled_var).grid(
            row=row, column=1, sticky="w"
        )

        row += 1
        ttk.Label(frame, text="Decode Audio Input").grid(row=row, column=0, sticky="w")
        self.decode_audio_in_var = tk.StringVar(value=self._config.decode_audio_input_device)
        self.decode_audio_in_combo = ttk.Combobox(frame, textvariable=self.decode_audio_in_var, state="readonly")
        self.decode_audio_in_combo.grid(row=row, column=1, sticky="ew")

        row += 1
        ttk.Label(frame, text="Decode Range (Hz)").grid(row=row, column=0, sticky="w")
        self.decode_tone_low_var = tk.DoubleVar(value=float(self._config.decode_audio_tone_low_freq))
        self.decode_tone_high_var = tk.DoubleVar(value=float(self._config.decode_audio_tone_high_freq))
        range_frame = ttk.Frame(frame)
        range_frame.grid(row=row, column=1, sticky="ew")
        range_frame.columnconfigure(1, weight=1)
        ttk.Label(range_frame, text="Min").grid(row=0, column=0, padx=(0, 4))
        ttk.Spinbox(range_frame, from_=300, to=1200, increment=10, textvariable=self.decode_tone_low_var, width=7).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Label(range_frame, text="Max").grid(row=0, column=2, padx=(0, 4))
        ttk.Spinbox(range_frame, from_=300, to=1200, increment=10, textvariable=self.decode_tone_high_var, width=7).grid(
            row=0, column=3
        )

        row += 1
        self.sidetone_enabled_var = tk.BooleanVar(value=bool(self._config.sidetone_enabled))
        ttk.Checkbutton(frame, text="Sidetone On", variable=self.sidetone_enabled_var).grid(
            row=row, column=1, sticky="w"
        )
        ttk.Button(frame, text="Test Sidetone", command=self._handle_test_sidetone).grid(
            row=row, column=2, padx=5
        )

        row += 1
        ttk.Label(frame, text="Sidetone Frequency (Hz)").grid(row=row, column=0, sticky="w")
        self.tone_freq_var = tk.DoubleVar(value=self._config.sidetone_freq)
        ttk.Spinbox(frame, from_=300, to=1200, textvariable=self.tone_freq_var, increment=10).grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        ttk.Label(frame, text="Sidetone Volume").grid(row=row, column=0, sticky="w")
        self.tone_vol_var = tk.DoubleVar(value=self._config.sidetone_volume)
        ttk.Scale(frame, from_=0.0, to=1.0, variable=self.tone_vol_var, orient="horizontal").grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        ttk.Label(frame, text="Keyer Type").grid(row=row, column=0, sticky="w")
        self.keyer_var = tk.StringVar(value=self._config.keyer_type)
        ttk.Combobox(frame, textvariable=self.keyer_var, values=KEYER_TYPES, state="readonly").grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        ttk.Label(frame, text="WPM").grid(row=row, column=0, sticky="w")
        self.wpm_var = tk.DoubleVar(value=self._config.wpm)
        ttk.Spinbox(frame, from_=5, to=60, textvariable=self.wpm_var, increment=1).grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        ttk.Label(frame, text="Dit/Dah Ratio").grid(row=row, column=0, sticky="w")
        self.ratio_var = tk.DoubleVar(value=self._config.dit_dah_ratio)
        ttk.Spinbox(frame, from_=2.0, to=6.0, textvariable=self.ratio_var, increment=0.1).grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        ttk.Label(frame, text="Weighting (%)").grid(row=row, column=0, sticky="w")
        self.weight_var = tk.DoubleVar(value=self._config.weighting)
        ttk.Spinbox(frame, from_=30.0, to=70.0, textvariable=self.weight_var, increment=1).grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        self.reverse_var = tk.BooleanVar(value=self._config.paddle_reverse)
        ttk.Checkbutton(frame, text="Paddle Reverse", variable=self.reverse_var).grid(
            row=row, column=1, sticky="w"
        )

        row += 1
        ttk.Label(frame, text="CAT/DSP Port").grid(row=row, column=0, sticky="w")
        self.cat_port_var = tk.StringVar(value=self._config.cat_port)
        self.cat_port_combo = ttk.Combobox(frame, textvariable=self.cat_port_var)
        self.cat_port_combo.grid(row=row, column=1, sticky="ew")
        ttk.Button(frame, text="Refresh", command=self.refresh_cat_ports).grid(row=row, column=2, padx=5)

        row += 1
        ttk.Label(frame, text="CAT Baud").grid(row=row, column=0, sticky="w")
        self.cat_baud_var = tk.StringVar(value=str(self._config.cat_baud))
        ttk.Combobox(
            frame,
            textvariable=self.cat_baud_var,
            values=CAT_BAUD_RATES,
            state="readonly",
        ).grid(row=row, column=1, sticky="ew")

        row += 1
        ttk.Label(frame, text="PTT Method").grid(row=row, column=0, sticky="w")
        ptt_method = self._config.ptt_method if self._config.ptt_method in PTT_METHODS else "CAT"
        self.ptt_var = tk.StringVar(value=ptt_method)
        ttk.Combobox(frame, textvariable=self.ptt_var, values=PTT_METHODS, state="readonly").grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        ttk.Label(frame, text="Thetis Key Line").grid(row=row, column=0, sticky="w")
        key_line = self._config.thetis_key_line if self._config.thetis_key_line in LINE_OPTIONS else "DTR"
        self.thetis_key_line_var = tk.StringVar(value=key_line)
        ttk.Combobox(
            frame,
            textvariable=self.thetis_key_line_var,
            values=LINE_OPTIONS,
            state="readonly",
        ).grid(row=row, column=1, sticky="ew")

        row += 1
        ttk.Label(frame, text="Thetis PTT Line").grid(row=row, column=0, sticky="w")
        ptt_line = self._config.thetis_ptt_line if self._config.thetis_ptt_line in LINE_OPTIONS else "None"
        self.thetis_ptt_line_var = tk.StringVar(value=ptt_line)
        ttk.Combobox(
            frame,
            textvariable=self.thetis_ptt_line_var,
            values=LINE_OPTIONS,
            state="readonly",
        ).grid(row=row, column=1, sticky="ew")

        row += 1
        self.thetis_key_invert_var = tk.BooleanVar(value=self._config.thetis_key_invert)
        ttk.Checkbutton(
            frame,
            text="Invert Thetis Key",
            variable=self.thetis_key_invert_var,
        ).grid(row=row, column=1, sticky="w")

        row += 1
        self.thetis_ptt_invert_var = tk.BooleanVar(value=self._config.thetis_ptt_invert)
        ttk.Checkbutton(
            frame,
            text="Invert Thetis PTT",
            variable=self.thetis_ptt_invert_var,
        ).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="TX Hang Time (s)").grid(row=row, column=0, sticky="w")
        self.hang_var = tk.DoubleVar(value=self._config.tx_hang_time)
        ttk.Spinbox(frame, from_=0.0, to=2.0, textvariable=self.hang_var, increment=0.05).grid(
            row=row, column=1, sticky="ew"
        )

        row += 1
        self.manual_ptt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Manual PTT",
            variable=self.manual_ptt_var,
            command=self._toggle_manual_ptt,
        ).grid(row=row, column=1, sticky="w")
        ttk.Button(frame, text="Release Key", command=self._handle_release).grid(row=row, column=2, padx=5)

        row += 1
        ttk.Label(frame, text="CTRL Status").grid(row=row, column=0, sticky="w")
        self.ctrl_status = tk.StringVar(value="disabled")
        ttk.Label(frame, textvariable=self.ctrl_status).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="Decode Audio").grid(row=row, column=0, sticky="w")
        self.decode_audio_status = tk.StringVar(value="disabled")
        ttk.Label(frame, textvariable=self.decode_audio_status).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="Decode WPM").grid(row=row, column=0, sticky="w")
        self.decode_wpm = tk.StringVar(value="-")
        ttk.Label(frame, textvariable=self.decode_wpm).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="PTT Status").grid(row=row, column=0, sticky="w")
        self.ptt_status = tk.StringVar(value="RX")
        ttk.Label(frame, textvariable=self.ptt_status).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="Keying").grid(row=row, column=0, sticky="w")
        self.keying_status = tk.StringVar(value="OFF")
        ttk.Label(frame, textvariable=self.keying_status).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="Port Status").grid(row=row, column=0, sticky="w")
        self.port_status = tk.StringVar(value="n/a")
        ttk.Label(frame, textvariable=self.port_status).grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="Audio Level").grid(row=row, column=0, sticky="w")
        self.level_var = tk.DoubleVar(value=0.0)
        self.level_bar = ttk.Progressbar(frame, maximum=1.0, variable=self.level_var)
        self.level_bar.grid(row=row, column=1, sticky="ew")

        row += 1
        ttk.Label(frame, text="Decoded").grid(row=row, column=0, sticky="w")
        self.decode_font_var = tk.IntVar(value=int(self._config.decode_font_size))
        font_frame = ttk.Frame(frame)
        font_frame.grid(row=row, column=1, sticky="w")
        ttk.Label(font_frame, text="Font").grid(row=0, column=0, padx=(0, 4))
        ttk.Spinbox(font_frame, from_=9, to=42, textvariable=self.decode_font_var, width=5).grid(
            row=0, column=1
        )
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=row, column=2, sticky="e")
        ttk.Button(button_frame, text="Clear", command=self._handle_clear_decode).grid(row=0, column=0, padx=3)
        ttk.Button(button_frame, text="Detach", command=self._open_decode_window).grid(row=0, column=1, padx=3)

        row += 1
        self.decoded_text = tk.Text(frame, height=8, wrap="word")
        self.decoded_text.grid(row=row, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(row, weight=1)
        self.decoded_text.tag_configure("local", foreground="#111111")
        self.decoded_text.tag_configure("audio", foreground="#B00020")
        self._apply_decode_font()
        self.decoded_text.configure(state="disabled")

        self._bind_changes()

    def _bind_changes(self) -> None:
        for var in [
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
        # UI simplificada: modo de entrada fijo a CTRL.
        self._config.input_mode = "CTRL"
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
        self._config.wpm = self._safe_float(self.wpm_var.get(), self._config.wpm)
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
        from midi import list_midi_output_devices

        devices = list_midi_output_devices()
        self.vail_midi_combo["values"] = devices
        current = self.vail_midi_var.get()
        if current not in devices:
            self.vail_midi_var.set(devices[0] if devices else "")

    def refresh_audio(self) -> None:
        outputs = _audio_output_device_list()
        inputs = _audio_input_device_list()
        self.audio_out_combo["values"] = outputs
        self.decode_audio_in_combo["values"] = inputs
        if self.audio_out_var.get() not in outputs:
            self.audio_out_var.set(outputs[0] if outputs else "")
        if self.decode_audio_in_var.get() not in inputs:
            self.decode_audio_in_var.set(inputs[0] if inputs else "")

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
        # Ensure current combobox value is persisted before syncing.
        self._apply_config()
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

    def set_midi_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self.midi_status.set(label if label else ("connected" if connected else "disabled"))

        self.after(0, update)

    def set_ctrl_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self.ctrl_status.set(label if label else ("enabled" if connected else "disabled"))

        self.after(0, update)

    def set_decode_audio_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self.decode_audio_status.set(label if label else ("listening" if connected else "disabled"))

        self.after(0, update)

    def set_decode_wpm(self, wpm: Optional[float]) -> None:
        def update() -> None:
            if wpm is None:
                self.decode_wpm.set("-")
            else:
                self.decode_wpm.set(f"{wpm:.1f}")

        self.after(0, update)

    def set_midi_last(self, text: str) -> None:
        def update() -> None:
            self.midi_last.set(text)

        self.after(0, update)

    def set_ptt_status(self, tx: bool) -> None:
        def update() -> None:
            self.ptt_status.set("TX" if tx else "RX")

        self.after(0, update)

    def set_port_status(self, ok: bool, message: str) -> None:
        def update() -> None:
            self.port_status.set(message)

        self.after(0, update)

    def set_audio_level(self, level: float) -> None:
        def update() -> None:
            self.level_var.set(max(0.0, min(level, 1.0)))

        self.after(0, update)

    def set_keying_status(self, active: bool) -> None:
        def update() -> None:
            self.keying_status.set("ON" if active else "OFF")

        self.after(0, update)

    def append_decoded_text(self, text: str, source: str = "local") -> None:
        def update() -> None:
            tag = "audio" if source == "audio" else "local"
            self._decode_segments.append((text, tag))
            if len(self._decode_segments) > 1200:
                self._decode_segments = self._decode_segments[-800:]
            self._append_decode_widget(self.decoded_text, text, tag)
            if self._decode_popup_text and self._decode_popup_text.winfo_exists():
                self._append_decode_widget(self._decode_popup_text, text, tag)

        self.after(0, update)

    def clear_decoded_text(self) -> None:
        def update() -> None:
            self._decode_segments.clear()
            self._clear_decode_widget(self.decoded_text)
            if self._decode_popup_text and self._decode_popup_text.winfo_exists():
                self._clear_decode_widget(self._decode_popup_text)

        self.after(0, update)

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
