"""CustomTkinter UI for the Vail-CW Thetis app."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from typing import Callable, List, Optional

try:
    import customtkinter as ctk
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise RuntimeError(
        "customtkinter is required. Install dependencies with `pip install -r requirements.txt`."
    ) from exc

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

FONT_FAMILY = "Segoe UI"
COLORS = {
    "bg": "#0B0E12",
    "sidebar": "#0E1319",
    "surface": "#141A22",
    "surface_alt": "#10151D",
    "surface_hover": "#1A2230",
    "border": "#242D3A",
    "text": "#F3F6FB",
    "muted": "#8F99A8",
    "accent": "#4A90FF",
    "accent_hover": "#71A8FF",
    "success": "#47C784",
    "warning": "#F4B860",
    "danger": "#FF7A7A",
    "track": "#202938",
}
TONE_COLORS = {
    "neutral": COLORS["text"],
    "muted": COLORS["muted"],
    "accent": COLORS["accent"],
    "success": COLORS["success"],
    "warning": COLORS["warning"],
    "danger": COLORS["danger"],
}


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def _font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


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


class SurfaceCard(ctk.CTkFrame):
    """A reusable dark card with title and body container."""

    def __init__(self, master, title: str, description: str = "", expand: bool = False) -> None:
        super().__init__(
            master,
            fg_color=COLORS["surface"],
            corner_radius=20,
            border_width=1,
            border_color=COLORS["border"],
        )
        self.grid_columnconfigure(0, weight=1)
        if expand:
            self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=title, font=_font(18, "bold"), text_color=COLORS["text"]).grid(
            row=0, column=0, sticky="w"
        )
        if description:
            ctk.CTkLabel(
                header,
                text=description,
                font=_font(11),
                text_color=COLORS["muted"],
                justify="left",
                wraplength=820,
            ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.body.grid_columnconfigure(0, weight=1)


class StatusChip(ctk.CTkFrame):
    """Compact metric block for live status values."""

    def __init__(self, master, title: str, value: str = "-") -> None:
        super().__init__(
            master,
            fg_color=COLORS["surface_alt"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title, font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 2)
        )
        self._value_label = ctk.CTkLabel(self, text=value, font=_font(16, "bold"), text_color=COLORS["text"])
        self._value_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

    def set_value(self, text: str, tone: str = "neutral") -> None:
        self._value_label.configure(text=text, text_color=TONE_COLORS.get(tone, COLORS["text"]))


class ResponsivePage(ctk.CTkScrollableFrame):
    """Scrollable page that rearranges cards between one and two columns."""

    def __init__(self, master) -> None:
        super().__init__(
            master,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=COLORS["surface_hover"],
            scrollbar_button_hover_color=COLORS["accent"],
        )
        self._cards: List[tuple[ctk.CTkFrame, str]] = []
        self._columns = 0
        self._relayout_after_id: Optional[str] = None
        self.bind("<Configure>", self._schedule_relayout, add="+")
        self.grid_columnconfigure(0, weight=1)

    def add_card(self, card: ctk.CTkFrame, mode: str = "single") -> None:
        self._cards.append((card, mode))
        self._schedule_relayout()

    def _schedule_relayout(self, _event=None) -> None:
        if self._relayout_after_id is not None:
            return
        self._relayout_after_id = self.after(40, self._relayout)

    def _relayout(self) -> None:
        self._relayout_after_id = None
        width = max(self.winfo_width(), 1)
        columns = 1 if width < 980 else 2
        if columns != self._columns:
            for column in range(2):
                self.grid_columnconfigure(column, weight=1 if column < columns else 0, uniform="page")
            self._columns = columns

        for card, _mode in self._cards:
            card.grid_forget()

        row = 0
        col = 0
        for card, mode in self._cards:
            if mode == "full":
                if col != 0:
                    row += 1
                    col = 0
                card.grid(row=row, column=0, columnspan=columns, sticky="nsew", padx=0, pady=(0, 16))
                row += 1
                continue

            if columns == 1:
                padx = 0
            elif col == 0:
                padx = (0, 10)
            else:
                padx = (10, 0)

            card.grid(row=row, column=col, sticky="nsew", padx=padx, pady=(0, 16))
            col += 1
            if col >= columns:
                row += 1
                col = 0


class AppUI(ctk.CTk):
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
        self.geometry("1380x900")
        self.minsize(1040, 720)
        self.configure(fg_color=COLORS["bg"])
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
        self._decode_window: Optional[ctk.CTkToplevel] = None
        self._decode_popup_text: Optional[tk.Text] = None
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._midi_refresh_inflight = False
        self._audio_refresh_inflight = False
        self._midi_refresh_token = 0
        self._audio_refresh_token = 0
        self._active_page = ""
        self._resize_after_id: Optional[str] = None
        self._sidebar_compact = False
        self._midi_connected = False
        self._ctrl_connected = False
        self._decode_connected = False
        self._build()
        self._populate_device_lists()
        self._bind_ctrl_keys()
        self.after(16, self._drain_ui_queue)
        self._ready = True

    def _build(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._page_meta = {
            "studio": {
                "label": "Studio",
                "short": "Main",
                "title": "Studio",
                "subtitle": "Live control surface with navigation, health and decoded output.",
            },
            "keyer": {
                "label": "Keyer",
                "short": "Keyer",
                "title": "Keyer",
                "subtitle": "Speed, feel and paddle behaviour.",
            },
            "audio": {
                "label": "Audio",
                "short": "Audio",
                "title": "Audio",
                "subtitle": "Output routing and sidetone tuning.",
            },
            "decode": {
                "label": "Decode",
                "short": "Decode",
                "title": "Decode",
                "subtitle": "Audio decode input, tone window and live telemetry.",
            },
            "radio": {
                "label": "Radio",
                "short": "CAT",
                "title": "CAT / Thetis",
                "subtitle": "Radio transport, lines and transmit control.",
            },
        }
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._pages: dict[str, ResponsivePage] = {}
        self._status_chips: dict[str, StatusChip] = {}
        self._build_sidebar()
        self._build_main_shell()
        self._build_pages()
        self._bind_changes()
        self._update_wpm_display()
        self._update_mode_controls()
        self._sync_shell_snapshot()
        self._refresh_decode_views()
        self._status_chips["ctrl"].set_value("off", "muted")
        self._status_chips["midi"].set_value("off", "muted")
        self._status_chips["ptt"].set_value("RX", "neutral")
        self._status_chips["keying"].set_value("OFF", "muted")
        self._status_chips["port"].set_value("n/a", "muted")
        self._refresh_sidetone_volume_display()
        self._show_page("studio")
        self.bind("<Configure>", self._handle_window_resize, add="+")
        self.after(120, self._apply_shell_responsive_state)

    def _build_sidebar(self) -> None:
        self._sidebar = ctk.CTkFrame(self, width=248, corner_radius=0, fg_color=COLORS["sidebar"])
        self._sidebar.grid(row=0, column=0, sticky="nsw")
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_columnconfigure(0, weight=1)
        self._sidebar.grid_rowconfigure(3, weight=1)

        brand = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(20, 18))
        brand.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(brand, text="Vail-CW", font=_font(22, "bold"), text_color=COLORS["text"]).grid(
            row=0, column=0, sticky="w"
        )
        self._brand_subtitle = ctk.CTkLabel(
            brand,
            text="Desktop keying console",
            font=_font(11),
            text_color=COLORS["muted"],
        )
        self._brand_subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._sidebar_summary = ctk.CTkFrame(
            self._sidebar,
            fg_color=COLORS["surface"],
            corner_radius=18,
            border_width=1,
            border_color=COLORS["border"],
        )
        self._sidebar_summary.grid(row=1, column=0, sticky="ew", padx=18)
        self._sidebar_summary.grid_columnconfigure(0, weight=1)
        self._sidebar_summary.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self._sidebar_summary,
            text="Input",
            font=_font(10, "bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            self._sidebar_summary,
            text="Speed",
            font=_font(10, "bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=1, sticky="w", padx=14, pady=(12, 2))
        mode_value = str(self._config.input_mode or "CTRL").strip().upper()
        if mode_value not in INPUT_MODES:
            mode_value = "CTRL"
        self._sidebar_mode_value = ctk.CTkLabel(
            self._sidebar_summary,
            text=mode_value,
            font=_font(16, "bold"),
            text_color=COLORS["text"],
        )
        self._sidebar_mode_value.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 14))
        self._sidebar_speed_value = ctk.CTkLabel(
            self._sidebar_summary,
            text=f"{self._clamp_wpm(self._config.wpm)} WPM",
            font=_font(16, "bold"),
            text_color=COLORS["accent"],
        )
        self._sidebar_speed_value.grid(row=1, column=1, sticky="w", padx=14, pady=(0, 14))

        nav = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="nsew", padx=12, pady=(18, 0))
        nav.grid_columnconfigure(0, weight=1)
        for row, key in enumerate(self._page_meta):
            meta = self._page_meta[key]
            button = ctk.CTkButton(
                nav,
                text=meta["label"],
                command=lambda page_key=key: self._show_page(page_key),
                fg_color="transparent",
                hover_color=COLORS["surface_hover"],
                text_color=COLORS["text"],
                anchor="w",
                corner_radius=14,
                height=42,
                font=_font(12, "bold"),
            )
            button.grid(row=row, column=0, sticky="ew", padx=6, pady=4)
            self._nav_buttons[key] = button

        footer = ctk.CTkFrame(
            self._sidebar,
            fg_color=COLORS["surface_alt"],
            corner_radius=18,
            border_width=1,
            border_color=COLORS["border"],
        )
        footer.grid(row=4, column=0, sticky="ew", padx=18, pady=(18, 20))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, text="Live note", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 2)
        )
        self._sidebar_note_label = ctk.CTkLabel(
            footer,
            text="Studio keeps the decoded stream visible while the rest of the controls stay one click away.",
            font=_font(11),
            justify="left",
            wraplength=190,
            text_color=COLORS["text"],
        )
        self._sidebar_note_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))

    def _build_main_shell(self) -> None:
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.grid(row=0, column=1, sticky="nsew", padx=24, pady=24)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)
        self._header = ctk.CTkFrame(shell, fg_color="transparent")
        self._header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self._header.grid_columnconfigure(0, weight=1)
        self._header_title = ctk.CTkLabel(self._header, text="", font=_font(30, "bold"), text_color=COLORS["text"])
        self._header_title.grid(row=0, column=0, sticky="w")
        self._header_subtitle = ctk.CTkLabel(
            self._header,
            text="",
            font=_font(12),
            text_color=COLORS["muted"],
        )
        self._header_subtitle.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._header_meta = ctk.CTkLabel(
            self._header,
            text="",
            font=_font(11, "bold"),
            text_color=COLORS["accent"],
        )
        self._header_meta.grid(row=0, column=1, sticky="e")

        self._page_host = ctk.CTkFrame(shell, fg_color="transparent")
        self._page_host.grid(row=1, column=0, sticky="nsew")
        self._page_host.grid_columnconfigure(0, weight=1)
        self._page_host.grid_rowconfigure(0, weight=1)

    def _build_pages(self) -> None:
        studio = ResponsivePage(self._page_host)
        studio.grid(row=0, column=0, sticky="nsew")
        studio.grid_remove()
        studio.add_card(self._build_routing_card(studio))
        studio.add_card(self._build_actions_card(studio))
        studio.add_card(self._build_status_card(studio), mode="full")
        studio.add_card(self._build_decoded_card(studio), mode="full")
        self._pages["studio"] = studio

        keyer = ResponsivePage(self._page_host)
        keyer.grid(row=0, column=0, sticky="nsew")
        keyer.grid_remove()
        keyer.add_card(self._build_speed_card(keyer), mode="full")
        keyer.add_card(self._build_keyer_card(keyer))
        keyer.add_card(self._build_timing_card(keyer))
        self._pages["keyer"] = keyer

        audio = ResponsivePage(self._page_host)
        audio.grid(row=0, column=0, sticky="nsew")
        audio.grid_remove()
        audio.add_card(self._build_audio_output_card(audio))
        audio.add_card(self._build_sidetone_card(audio))
        self._pages["audio"] = audio

        decode = ResponsivePage(self._page_host)
        decode.grid(row=0, column=0, sticky="nsew")
        decode.grid_remove()
        decode.add_card(self._build_decode_input_card(decode))
        decode.add_card(self._build_decode_range_card(decode))
        decode.add_card(self._build_decode_telemetry_card(decode), mode="full")
        self._pages["decode"] = decode

        radio = ResponsivePage(self._page_host)
        radio.grid(row=0, column=0, sticky="nsew")
        radio.grid_remove()
        radio.add_card(self._build_cat_connectivity_card(radio))
        radio.add_card(self._build_cat_routing_card(radio))
        self._pages["radio"] = radio

    def _build_routing_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Routing", "Input mode and Vail MIDI output live here.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        mode_value = str(self._config.input_mode or "CTRL").strip().upper()
        if mode_value not in INPUT_MODES:
            mode_value = "CTRL"
        self.input_mode_var = tk.StringVar(value=mode_value)
        self.vail_midi_var = tk.StringVar(value=self._config.midi_device or "")
        self.midi_status = tk.StringVar(value="disabled")
        self.midi_last = tk.StringVar(value="-")

        ctk.CTkLabel(body, text="Input mode", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.input_mode_selector = ctk.CTkSegmentedButton(
            body,
            values=INPUT_MODES,
            variable=self.input_mode_var,
            selected_color=COLORS["accent"],
            selected_hover_color=COLORS["accent_hover"],
            unselected_color=COLORS["surface_alt"],
            unselected_hover_color=COLORS["surface_hover"],
            corner_radius=12,
            height=38,
            font=_font(12, "bold"),
        )
        self.input_mode_selector.grid(row=0, column=1, sticky="ew", pady=(0, 14))

        ctk.CTkLabel(body, text="Vail MIDI out", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        self.vail_midi_combo = self._make_combo(body, self.vail_midi_var)
        self.vail_midi_combo.grid(row=1, column=1, sticky="ew", pady=(0, 14))
        self.midi_refresh_btn = self._make_button(body, "Refresh", self.refresh_midi, width=96)
        self.midi_refresh_btn.grid(row=1, column=2, sticky="e", padx=(10, 0), pady=(0, 14))

        meta = ctk.CTkFrame(
            body,
            fg_color=COLORS["surface_alt"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        meta.grid(row=2, column=0, columnspan=3, sticky="ew")
        meta.grid_columnconfigure(0, weight=0)
        meta.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(meta, text="MIDI status", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 2)
        )
        self._midi_status_value_label = ctk.CTkLabel(
            meta,
            text=self.midi_status.get(),
            font=_font(13, "bold"),
            text_color=COLORS["text"],
        )
        self._midi_status_value_label.grid(row=0, column=1, sticky="w", padx=(0, 14), pady=(12, 2))
        ctk.CTkLabel(meta, text="Last message", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=1, column=0, sticky="nw", padx=14, pady=(4, 12)
        )
        self._midi_last_value_label = ctk.CTkLabel(
            meta,
            text=self.midi_last.get(),
            font=_font(11),
            justify="left",
            wraplength=360,
            text_color=COLORS["text"],
        )
        self._midi_last_value_label.grid(row=1, column=1, sticky="w", padx=(0, 14), pady=(4, 12))
        return card

    def _build_actions_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Quick Actions", "Frequent actions stay close to the live status view.")
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self.sync_vail_btn = self._make_button(body, "Sync Vail", self._handle_sync_vail, primary=True)
        self.sync_vail_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 10))
        self._midi_reconnect_btn = self._make_button(body, "Reconnect MIDI", self._handle_midi_reconnect)
        self._midi_reconnect_btn.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 10))

        self._release_button = self._make_button(body, "Release Key", self._handle_release)
        self._release_button.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 10))
        self._sidetone_test_button = self._make_button(body, "Test Sidetone", self._handle_test_sidetone)
        self._sidetone_test_button.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 10))

        self._action_snapshot_label = ctk.CTkLabel(
            body,
            text="",
            font=_font(11),
            justify="left",
            wraplength=420,
            text_color=COLORS["muted"],
        )
        self._action_snapshot_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        return card

    def _build_status_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Live Status", "Instant feedback for input, transport and decode.")
        body = card.body
        chip_grid = ctk.CTkFrame(body, fg_color="transparent")
        chip_grid.grid(row=0, column=0, sticky="ew")
        for column in range(3):
            chip_grid.grid_columnconfigure(column, weight=1)

        chip_defs = [
            ("ctrl", "CTRL"),
            ("midi", "MIDI"),
            ("ptt", "PTT"),
            ("keying", "Keying"),
            ("port", "CAT"),
            ("decode", "Decode"),
        ]
        for index, (key, label) in enumerate(chip_defs):
            chip = StatusChip(chip_grid, label)
            chip.grid(
                row=index // 3,
                column=index % 3,
                sticky="ew",
                padx=(0, 10) if index % 3 != 2 else 0,
                pady=(0, 10) if index < 3 else 0,
            )
            self._status_chips[key] = chip

        meter = ctk.CTkFrame(
            body,
            fg_color=COLORS["surface_alt"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        meter.grid(row=1, column=0, sticky="ew", pady=(18, 12))
        meter.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(meter, text="Audio level", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=14
        )
        self.level_var = tk.DoubleVar(value=0.0)
        self.level_bar = ctk.CTkProgressBar(meter, height=10, corner_radius=6, fg_color=COLORS["track"])
        self.level_bar.grid(row=0, column=1, sticky="ew", pady=14)
        self.level_bar.configure(progress_color=COLORS["accent"])
        self.level_bar.set(0.0)
        self._level_value_label = ctk.CTkLabel(meter, text="0%", font=_font(11, "bold"), text_color=COLORS["text"])
        self._level_value_label.grid(row=0, column=2, sticky="e", padx=14, pady=14)

        self._port_detail_label = ctk.CTkLabel(
            body,
            text="n/a",
            font=_font(11),
            justify="left",
            wraplength=900,
            text_color=COLORS["muted"],
        )
        self._port_detail_label.grid(row=2, column=0, sticky="w")
        return card

    def _build_decoded_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Decoded Stream", "Local decode and audio decode share the same live console.", expand=True)
        card.body.grid_rowconfigure(1, weight=1)
        card.body.grid_columnconfigure(0, weight=1)
        toolbar = ctk.CTkFrame(card.body, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        toolbar.grid_columnconfigure(1, weight=1)
        self.decode_font_var = tk.IntVar(value=int(self._config.decode_font_size))

        font_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        font_frame.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(font_frame, text="Font", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self._make_spinbox(font_frame, from_=9, to=42, increment=1, variable=self.decode_font_var, width=5).grid(
            row=0, column=1, sticky="w"
        )

        button_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        button_frame.grid(row=0, column=2, sticky="e")
        self._make_button(button_frame, "Clear", self._handle_clear_decode, width=84).grid(
            row=0, column=0, padx=(0, 8)
        )
        self._make_button(button_frame, "Detach", self._open_decode_window, width=84).grid(row=0, column=1)

        text_shell = ctk.CTkFrame(
            card.body,
            fg_color=COLORS["surface_alt"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        text_shell.grid(row=1, column=0, sticky="nsew")
        text_shell.grid_rowconfigure(0, weight=1)
        text_shell.grid_columnconfigure(0, weight=1)
        self.decoded_text = self._create_text_widget(text_shell)
        self.decoded_text.grid(row=0, column=0, sticky="nsew", padx=(14, 0), pady=14)
        scrollbar = ctk.CTkScrollbar(text_shell, command=self.decoded_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 14), pady=14)
        self.decoded_text.configure(yscrollcommand=scrollbar.set)
        self._apply_decode_font()
        return card

    def _build_speed_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Speed", "Large controls with enough spacing to tune without hunting.")
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        self.wpm_var = tk.DoubleVar(value=float(self._clamp_wpm(self._config.wpm)))
        self.wpm_value_label = ctk.CTkLabel(body, text="", font=_font(42, "bold"), text_color=COLORS["text"])
        self.wpm_value_label.grid(row=0, column=0, sticky="w")
        self._wpm_meta_label = ctk.CTkLabel(body, text="", font=_font(11), text_color=COLORS["muted"])
        self._wpm_meta_label.grid(row=1, column=0, sticky="w", pady=(4, 16))
        self.wpm_scale = ctk.CTkSlider(
            body,
            from_=5,
            to=40,
            number_of_steps=35,
            variable=self.wpm_var,
            command=self._on_wpm_scale,
            height=18,
            button_length=20,
            button_corner_radius=10,
            corner_radius=999,
            progress_color=COLORS["accent"],
            fg_color=COLORS["track"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self.wpm_scale.grid(row=2, column=0, sticky="ew")
        ticks = ctk.CTkFrame(body, fg_color="transparent")
        ticks.grid(row=3, column=0, sticky="ew", pady=(8, 16))
        ticks.grid_columnconfigure(0, weight=1)
        ticks.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ticks, text="5 WPM", font=_font(10), text_color=COLORS["muted"]).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(ticks, text="40 WPM", font=_font(10), text_color=COLORS["muted"]).grid(row=0, column=1, sticky="e")

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="w")
        for index, (label, delta) in enumerate([("-5", -5), ("-1", -1), ("+1", 1), ("+5", 5)]):
            self._make_button(buttons, label, lambda amount=delta: self._nudge_wpm(amount), width=72).grid(
                row=0, column=index, padx=(0, 8) if index < 3 else 0
            )
        return card

    def _build_keyer_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Keyer Type", "Switching feel should not require drilling into nested menus.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        self.keyer_var = tk.StringVar(value=self._config.keyer_type)
        self.reverse_var = tk.BooleanVar(value=self._config.paddle_reverse)
        ctk.CTkLabel(body, text="Algorithm", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self._make_combo(body, self.keyer_var, values=KEYER_TYPES).grid(row=0, column=1, sticky="ew", pady=(0, 14))
        self._make_switch(body, "Paddle reverse", self.reverse_var).grid(row=1, column=0, columnspan=2, sticky="w")
        return card

    def _build_timing_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Timing", "Ratio and weighting stay side by side so the hand feel is easy to compare.")
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self.ratio_var = tk.DoubleVar(value=self._config.dit_dah_ratio)
        self.weight_var = tk.DoubleVar(value=self._config.weighting)

        ratio_box = ctk.CTkFrame(body, fg_color=COLORS["surface_alt"], corner_radius=16)
        ratio_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(ratio_box, text="Dit / Dah ratio", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self._make_spinbox(ratio_box, from_=2.0, to=6.0, increment=0.1, variable=self.ratio_var, width=6).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 14)
        )

        weight_box = ctk.CTkFrame(body, fg_color=COLORS["surface_alt"], corner_radius=16)
        weight_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(weight_box, text="Weighting (%)", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self._make_spinbox(weight_box, from_=30.0, to=70.0, increment=1.0, variable=self.weight_var, width=6).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 14)
        )
        return card

    def _build_audio_output_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Audio Output", "Select the playback device used by sidetone.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        self.audio_out_var = tk.StringVar(value=self._config.audio_output_device)
        ctk.CTkLabel(body, text="Output device", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.audio_out_combo = self._make_combo(body, self.audio_out_var)
        self.audio_out_combo.grid(row=0, column=1, sticky="ew", pady=(0, 14))
        self._make_button(body, "Refresh", self.refresh_audio, width=96).grid(
            row=0, column=2, sticky="e", padx=(10, 0), pady=(0, 14)
        )
        self._audio_output_summary_label = ctk.CTkLabel(
            body,
            text="Device list is shared with the decode input page.",
            font=_font(11),
            justify="left",
            wraplength=420,
            text_color=COLORS["muted"],
        )
        self._audio_output_summary_label.grid(row=1, column=0, columnspan=3, sticky="w")
        return card

    def _build_sidetone_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Sidetone", "Round controls and wider spacing keep fast adjustments precise.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        self.sidetone_enabled_var = tk.BooleanVar(value=bool(self._config.sidetone_enabled))
        self.tone_freq_var = tk.DoubleVar(value=self._config.sidetone_freq)
        self.tone_vol_var = tk.DoubleVar(value=self._config.sidetone_volume)

        self._make_switch(body, "Enable sidetone", self.sidetone_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        self._make_button(body, "Test", self._handle_test_sidetone, width=84).grid(row=0, column=2, sticky="e", pady=(0, 14))
        ctk.CTkLabel(body, text="Frequency (Hz)", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        self._make_spinbox(body, from_=300, to=1200, increment=10, variable=self.tone_freq_var, width=7).grid(
            row=1, column=1, sticky="w", pady=(0, 14)
        )

        ctk.CTkLabel(body, text="Volume", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=2, column=0, sticky="w"
        )
        volume_row = ctk.CTkFrame(body, fg_color="transparent")
        volume_row.grid(row=2, column=1, columnspan=2, sticky="ew")
        volume_row.grid_columnconfigure(0, weight=1)
        self._tone_volume_slider = ctk.CTkSlider(
            volume_row,
            from_=0.0,
            to=1.0,
            number_of_steps=100,
            variable=self.tone_vol_var,
            height=16,
            corner_radius=999,
            progress_color=COLORS["accent"],
            fg_color=COLORS["track"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._tone_volume_slider.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._tone_volume_value_label = ctk.CTkLabel(volume_row, text="20%", font=_font(11, "bold"), text_color=COLORS["text"])
        self._tone_volume_value_label.grid(row=0, column=1, sticky="e", padx=(10, 0), pady=(0, 4))
        return card

    def _build_decode_input_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Decode Input", "Decode can be enabled independently from local keying.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        self.decode_audio_enabled_var = tk.BooleanVar(value=bool(self._config.decode_audio_enabled))
        self.decode_audio_in_var = tk.StringVar(value=self._config.decode_audio_input_device)
        self.decode_audio_status = tk.StringVar(value="disabled")
        self.decode_wpm = tk.StringVar(value="-")

        self._make_switch(body, "Decode audio input", self.decode_audio_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        ctk.CTkLabel(body, text="Input device", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=1, column=0, sticky="w", pady=(0, 10)
        )
        self.decode_audio_in_combo = self._make_combo(body, self.decode_audio_in_var)
        self.decode_audio_in_combo.grid(row=1, column=1, sticky="ew", pady=(0, 14))
        self._make_button(body, "Refresh", self.refresh_audio, width=96).grid(
            row=1, column=2, sticky="e", padx=(10, 0), pady=(0, 14)
        )
        self._decode_input_status_label = ctk.CTkLabel(
            body,
            text=self.decode_audio_status.get(),
            font=_font(11),
            justify="left",
            wraplength=420,
            text_color=COLORS["muted"],
        )
        self._decode_input_status_label.grid(row=2, column=0, columnspan=3, sticky="w")
        return card

    def _build_decode_range_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Tone Window", "Keep the decode band tight enough to reject noise without choking the signal.")
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self.decode_tone_low_var = tk.DoubleVar(value=float(self._config.decode_audio_tone_low_freq))
        self.decode_tone_high_var = tk.DoubleVar(value=float(self._config.decode_audio_tone_high_freq))

        low_box = ctk.CTkFrame(body, fg_color=COLORS["surface_alt"], corner_radius=16)
        low_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(low_box, text="Min (Hz)", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self._make_spinbox(low_box, from_=300, to=1200, increment=10, variable=self.decode_tone_low_var, width=7).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 14)
        )

        high_box = ctk.CTkFrame(body, fg_color=COLORS["surface_alt"], corner_radius=16)
        high_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(high_box, text="Max (Hz)", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4)
        )
        self._make_spinbox(high_box, from_=300, to=1200, increment=10, variable=self.decode_tone_high_var, width=7).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 14)
        )
        return card

    def _build_decode_telemetry_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Telemetry", "Status, estimated WPM and actions are grouped in one wider pane.")
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        status_box = ctk.CTkFrame(body, fg_color=COLORS["surface_alt"], corner_radius=16)
        status_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        status_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(status_box, text="Decode status", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 2)
        )
        self._decode_status_value_label = ctk.CTkLabel(
            status_box,
            text=self.decode_audio_status.get(),
            font=_font(24, "bold"),
            text_color=COLORS["text"],
        )
        self._decode_status_value_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 14))

        speed_box = ctk.CTkFrame(body, fg_color=COLORS["surface_alt"], corner_radius=16)
        speed_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        speed_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(speed_box, text="Estimated WPM", font=_font(10, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 2)
        )
        self._decode_speed_value_label = ctk.CTkLabel(
            speed_box,
            text="-",
            font=_font(24, "bold"),
            text_color=COLORS["accent"],
        )
        self._decode_speed_value_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 14))

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        actions.grid_columnconfigure(1, weight=1)
        self._decode_range_value_label = ctk.CTkLabel(
            actions,
            text="",
            font=_font(11),
            justify="left",
            wraplength=560,
            text_color=COLORS["muted"],
        )
        self._decode_range_value_label.grid(row=0, column=0, sticky="w")
        button_frame = ctk.CTkFrame(actions, fg_color="transparent")
        button_frame.grid(row=0, column=1, sticky="e")
        self._make_button(button_frame, "Clear", self._handle_clear_decode, width=84).grid(
            row=0, column=0, padx=(0, 8)
        )
        self._make_button(button_frame, "Detach", self._open_decode_window, width=84).grid(row=0, column=1)
        return card

    def _build_cat_connectivity_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Connectivity", "The CAT/DSP link stays on its own panel so transport errors stand out.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        self.cat_port_var = tk.StringVar(value=self._config.cat_port)
        self.cat_baud_var = tk.StringVar(value=str(self._config.cat_baud))
        self.port_status = tk.StringVar(value="n/a")

        ctk.CTkLabel(body, text="CAT / DSP port", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.cat_port_combo = self._make_combo(body, self.cat_port_var, state="normal")
        self.cat_port_combo.grid(row=0, column=1, sticky="ew", pady=(0, 14))
        self._make_button(body, "Refresh", self.refresh_cat_ports, width=96).grid(
            row=0, column=2, sticky="e", padx=(10, 0), pady=(0, 14)
        )

        ctk.CTkLabel(body, text="CAT baud", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=1, column=0, sticky="w"
        )
        self._make_combo(body, self.cat_baud_var, values=CAT_BAUD_RATES).grid(row=1, column=1, sticky="ew")
        self._cat_port_detail_label = ctk.CTkLabel(
            body,
            text=self.port_status.get(),
            font=_font(11),
            justify="left",
            wraplength=420,
            text_color=COLORS["muted"],
        )
        self._cat_port_detail_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))
        return card

    def _build_cat_routing_card(self, parent) -> SurfaceCard:
        card = SurfaceCard(parent, "Transmit Routing", "PTT line selection and manual control are grouped in one place.")
        body = card.body
        body.grid_columnconfigure(1, weight=1)
        self.ptt_var = tk.StringVar(value=self._config.ptt_method if self._config.ptt_method in PTT_METHODS else "CAT")
        key_line = self._config.thetis_key_line if self._config.thetis_key_line in LINE_OPTIONS else "DTR"
        ptt_line = self._config.thetis_ptt_line if self._config.thetis_ptt_line in LINE_OPTIONS else "None"
        self.thetis_key_line_var = tk.StringVar(value=key_line)
        self.thetis_ptt_line_var = tk.StringVar(value=ptt_line)
        self.thetis_key_invert_var = tk.BooleanVar(value=self._config.thetis_key_invert)
        self.thetis_ptt_invert_var = tk.BooleanVar(value=self._config.thetis_ptt_invert)
        self.hang_var = tk.DoubleVar(value=self._config.tx_hang_time)
        self.manual_ptt_var = tk.BooleanVar(value=False)
        self.ctrl_status = tk.StringVar(value="disabled")
        self.ptt_status = tk.StringVar(value="RX")
        self.keying_status = tk.StringVar(value="OFF")

        rows = [
            ("PTT method", self._make_combo(body, self.ptt_var, values=PTT_METHODS)),
            ("Thetis key line", self._make_combo(body, self.thetis_key_line_var, values=LINE_OPTIONS)),
            ("Thetis PTT line", self._make_combo(body, self.thetis_ptt_line_var, values=LINE_OPTIONS)),
        ]
        for row, (label, widget) in enumerate(rows):
            ctk.CTkLabel(body, text=label, font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
                row=row, column=0, sticky="w", pady=(0, 10)
            )
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=(0, 14 if row < 2 else 10))

        self._make_switch(body, "Invert Thetis key", self.thetis_key_invert_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 10)
        )
        self._make_switch(body, "Invert Thetis PTT", self.thetis_ptt_invert_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )

        ctk.CTkLabel(body, text="TX hang time (s)", font=_font(11, "bold"), text_color=COLORS["muted"]).grid(
            row=5, column=0, sticky="w", pady=(0, 10)
        )
        self._make_spinbox(body, from_=0.0, to=2.0, increment=0.05, variable=self.hang_var, width=6).grid(
            row=5, column=1, sticky="w", pady=(0, 14)
        )

        self._make_switch(body, "Manual PTT", self.manual_ptt_var, command=self._toggle_manual_ptt).grid(
            row=6, column=0, sticky="w"
        )
        self._make_button(body, "Release Key", self._handle_release, width=110).grid(row=6, column=2, sticky="e")
        return card

    def _make_button(self, master, text: str, command, width: int = 0, primary: bool = False) -> ctk.CTkButton:
        return ctk.CTkButton(
            master,
            text=text,
            command=command,
            width=width,
            height=38,
            corner_radius=14,
            border_width=0 if primary else 1,
            border_color=COLORS["border"],
            fg_color=COLORS["accent"] if primary else COLORS["surface_alt"],
            hover_color=COLORS["accent_hover"] if primary else COLORS["surface_hover"],
            text_color=COLORS["text"],
            font=_font(12, "bold"),
        )

    def _make_combo(
        self,
        master,
        variable: tk.StringVar,
        values: Optional[List[str]] = None,
        state: str = "readonly",
    ) -> ctk.CTkComboBox:
        return ctk.CTkComboBox(
            master,
            variable=variable,
            values=list(values or [""]),
            state=state,
            height=38,
            corner_radius=14,
            border_width=1,
            border_color=COLORS["border"],
            button_color=COLORS["surface_hover"],
            button_hover_color=COLORS["accent"],
            fg_color=COLORS["surface_alt"],
            text_color=COLORS["text"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["surface_hover"],
            dropdown_text_color=COLORS["text"],
            font=_font(12),
        )

    def _make_switch(
        self,
        master,
        text: str,
        variable: tk.BooleanVar,
        command=None,
    ) -> ctk.CTkSwitch:
        return ctk.CTkSwitch(
            master,
            text=text,
            variable=variable,
            command=command,
            switch_width=42,
            switch_height=22,
            corner_radius=999,
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            button_hover_color=COLORS["text"],
            text_color=COLORS["text"],
            font=_font(12, "bold"),
        )

    def _make_spinbox(self, master, from_, to, increment, variable, width: int = 7) -> tk.Spinbox:
        return tk.Spinbox(
            master,
            from_=from_,
            to=to,
            increment=increment,
            textvariable=variable,
            width=width,
            relief="flat",
            bd=0,
            justify="center",
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            buttonbackground=COLORS["surface_hover"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            disabledbackground=COLORS["surface_alt"],
            readonlybackground=COLORS["surface_alt"],
            font=(FONT_FAMILY, 11),
        )

    def _create_text_widget(self, master) -> tk.Text:
        widget = tk.Text(
            master,
            wrap="word",
            relief="flat",
            bd=0,
            bg=COLORS["surface_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent"],
            highlightthickness=0,
            padx=12,
            pady=12,
        )
        widget.tag_configure("local", foreground=COLORS["text"])
        widget.tag_configure("audio", foreground="#FFB38A")
        widget.configure(state="disabled")
        return widget

    def _show_page(self, page_key: str) -> None:
        if page_key == self._active_page:
            return
        if self._active_page in self._pages:
            self._pages[self._active_page].grid_remove()
        self._pages[page_key].grid()
        self._pages[page_key]._schedule_relayout()
        self._active_page = page_key
        meta = self._page_meta[page_key]
        self._header_title.configure(text=meta["title"])
        self._header_subtitle.configure(text=meta["subtitle"])
        for key, button in self._nav_buttons.items():
            active = key == page_key
            button.configure(
                fg_color=COLORS["accent"] if active else "transparent",
                hover_color=COLORS["accent_hover"] if active else COLORS["surface_hover"],
                anchor="center" if self._sidebar_compact else "w",
            )

    def _bind_changes(self) -> None:
        for var in [
            self.input_mode_var,
            self.vail_midi_var,
            self.audio_out_var,
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
            return
        self.wpm_value_label.configure(text=f"{wpm} WPM")
        ratio = self._safe_float(self.ratio_var.get(), self._config.dit_dah_ratio)
        weight = self._safe_float(self.weight_var.get(), self._config.weighting)
        self._wpm_meta_label.configure(text=f"Ratio {ratio:.1f} · Weighting {weight:.0f}%")

    def _on_wpm_scale(self, value) -> None:
        wpm = self._clamp_wpm(float(value))
        if abs(float(self.wpm_var.get()) - float(wpm)) > 0.001:
            self.wpm_var.set(float(wpm))
            return
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
        self._midi_reconnect_btn.configure(state="normal" if midi_mode else "disabled")

    def _sync_shell_snapshot(self) -> None:
        mode = str(self.input_mode_var.get() or "CTRL").strip().upper()
        if mode not in INPUT_MODES:
            mode = "CTRL"
        wpm = self._clamp_wpm(self.wpm_var.get())
        ratio = self._safe_float(self.ratio_var.get(), self._config.dit_dah_ratio)
        weight = self._safe_float(self.weight_var.get(), self._config.weighting)
        low = self._safe_float(self.decode_tone_low_var.get(), self._config.decode_audio_tone_low_freq)
        high = self._safe_float(self.decode_tone_high_var.get(), self._config.decode_audio_tone_high_freq)
        if low > high:
            low, high = high, low
        self._sidebar_mode_value.configure(text=mode)
        self._sidebar_speed_value.configure(text=f"{wpm} WPM")
        self._header_meta.configure(text=f"{mode} input · {wpm} WPM · {self.keyer_var.get()}")
        self._action_snapshot_label.configure(
            text=f"{self.keyer_var.get()} keyer · ratio {ratio:.1f} · weighting {weight:.0f}%"
        )
        self._decode_range_value_label.configure(text=f"Decode band {low:.0f}-{high:.0f} Hz")

    def _refresh_sidetone_volume_display(self) -> None:
        volume = max(0.0, min(float(self.tone_vol_var.get()), 1.0))
        self._tone_volume_value_label.configure(text=f"{int(round(volume * 100))}%")

    def _refresh_decode_views(self) -> None:
        status_text = self.decode_audio_status.get() or "disabled"
        speed_text = self.decode_wpm.get() if self.decode_wpm.get() not in {"", "-"} else "-"
        status_tone = "success" if self._decode_connected else "muted"
        speed_label = speed_text if speed_text == "-" else f"{speed_text} WPM"
        self._decode_input_status_label.configure(text=f"Status: {status_text}")
        self._decode_status_value_label.configure(text=status_text, text_color=TONE_COLORS.get(status_tone, COLORS["text"]))
        self._decode_speed_value_label.configure(text=speed_label)
        chip_text = speed_label if self._decode_connected and speed_text != "-" else status_text
        self._status_chips["decode"].set_value(chip_text, "accent" if self._decode_connected else "muted")

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
            return
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
        self._sync_shell_snapshot()
        self._refresh_sidetone_volume_display()
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
                values = devices or [""]
                self.vail_midi_combo.configure(values=values)
                current = self.vail_midi_var.get()
                preferred = _pick_midi_output_for_ui(devices)
                if current not in devices:
                    self.vail_midi_var.set(preferred or "")
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
                self.audio_out_combo.configure(values=outputs or [""])
                self.decode_audio_in_combo.configure(values=inputs or [""])
                if self.audio_out_var.get() not in outputs:
                    self.audio_out_var.set(outputs[0] if outputs else "")
                if self.decode_audio_in_var.get() not in inputs:
                    self.decode_audio_in_var.set(inputs[0] if inputs else "")
                self._audio_refresh_inflight = False

            self._enqueue_ui(apply_devices)

        threading.Thread(target=worker, name="ui-refresh-audio", daemon=True).start()

    def refresh_cat_ports(self) -> None:
        ports = _serial_ports()
        self.cat_port_combo.configure(values=ports or [""])
        if self.cat_port_var.get() not in ports and ports:
            self.cat_port_var.set(ports[0])

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
        self._config.midi_device = self.vail_midi_var.get()
        self._config.keyer_type = self.keyer_var.get()
        self._config.wpm = float(self._clamp_wpm(self._safe_float(self.wpm_var.get(), self._config.wpm)))
        if self._on_sync_vail:
            self._on_sync_vail()

    def _open_decode_window(self) -> None:
        if self._decode_window and self._decode_window.winfo_exists():
            self._decode_window.lift()
            return

        win = ctk.CTkToplevel(self)
        win.title("Decoded CW")
        win.geometry("960x520")
        win.minsize(560, 320)
        win.configure(fg_color=COLORS["bg"])
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            win,
            fg_color=COLORS["surface"],
            corner_radius=18,
            border_width=1,
            border_color=COLORS["border"],
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(0, weight=1)

        text_shell = ctk.CTkFrame(shell, fg_color=COLORS["surface_alt"], corner_radius=14)
        text_shell.grid(row=0, column=0, sticky="nsew", padx=16, pady=(16, 12))
        text_shell.grid_columnconfigure(0, weight=1)
        text_shell.grid_rowconfigure(0, weight=1)
        text = self._create_text_widget(text_shell)
        text.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
        scrollbar = ctk.CTkScrollbar(text_shell, command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 12), pady=12)
        text.configure(yscrollcommand=scrollbar.set)
        text.configure(font=self._decode_font_tuple())

        controls = ctk.CTkFrame(shell, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        controls.grid_columnconfigure(0, weight=1)
        button_frame = ctk.CTkFrame(controls, fg_color="transparent")
        button_frame.grid(row=0, column=1, sticky="e")
        self._make_button(button_frame, "Clear", self._handle_clear_decode, width=84).grid(
            row=0, column=0, padx=(0, 8)
        )
        self._make_button(button_frame, "Close", win.destroy, width=84).grid(row=0, column=1)

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

    def _handle_window_resize(self, event) -> None:
        if event.widget is not self:
            return
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(40, self._apply_shell_responsive_state)

    def _apply_shell_responsive_state(self) -> None:
        self._resize_after_id = None
        compact = self.winfo_width() < 1180
        if compact == self._sidebar_compact:
            return
        self._sidebar_compact = compact
        self._sidebar.configure(width=104 if compact else 248)
        self._brand_subtitle.configure(text="" if compact else "Desktop keying console")
        if compact:
            self._sidebar_summary.grid_remove()
            self._sidebar_note_label.configure(text="Decoded output and controls remain one click away.", wraplength=72)
        else:
            self._sidebar_summary.grid()
            self._sidebar_note_label.configure(
                text="Studio keeps the decoded stream visible while the rest of the controls stay one click away.",
                wraplength=190,
            )
        for key, button in self._nav_buttons.items():
            meta = self._page_meta[key]
            button.configure(text=meta["short"] if compact else meta["label"], anchor="center" if compact else "w")
        self._show_page(self._active_page or "studio")

    def set_midi_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self._midi_connected = connected
            value = label if label else ("connected" if connected else "disabled")
            self.midi_status.set(value)
            self._midi_status_value_label.configure(text=value)
            self._status_chips["midi"].set_value("ready" if connected else "off", "success" if connected else "muted")

        self._enqueue_ui(update)

    def set_ctrl_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self._ctrl_connected = connected
            value = label if label else ("enabled" if connected else "disabled")
            self.ctrl_status.set(value)
            self._status_chips["ctrl"].set_value("ready" if connected else "off", "success" if connected else "muted")

        self._enqueue_ui(update)

    def set_decode_audio_status(self, connected: bool, label: str) -> None:
        def update() -> None:
            self._decode_connected = connected
            self.decode_audio_status.set(label if label else ("listening" if connected else "disabled"))
            self._refresh_decode_views()

        self._enqueue_ui(update)

    def set_decode_wpm(self, wpm: Optional[float]) -> None:
        def update() -> None:
            if wpm is None:
                self.decode_wpm.set("-")
            else:
                self.decode_wpm.set(f"{wpm:.1f}")
            self._refresh_decode_views()

        self._enqueue_ui(update)

    def set_midi_last(self, text: str) -> None:
        def update() -> None:
            self.midi_last.set(text)
            self._midi_last_value_label.configure(text=text)

        self._enqueue_ui(update)

    def set_ptt_status(self, tx: bool) -> None:
        def update() -> None:
            self.ptt_status.set("TX" if tx else "RX")
            self._status_chips["ptt"].set_value("TX" if tx else "RX", "warning" if tx else "neutral")

        self._enqueue_ui(update)

    def set_port_status(self, ok: bool, message: str) -> None:
        def update() -> None:
            value = message or "n/a"
            self.port_status.set(value)
            self._port_detail_label.configure(text=value)
            self._cat_port_detail_label.configure(text=value)
            self._status_chips["port"].set_value("ready" if ok else "down", "success" if ok else "danger")

        self._enqueue_ui(update)

    def set_audio_level(self, level: float) -> None:
        def update() -> None:
            clamped = max(0.0, min(level, 1.0))
            self.level_var.set(clamped)
            self.level_bar.set(clamped)
            self._level_value_label.configure(text=f"{int(round(clamped * 100))}%")

        self._enqueue_ui(update)

    def set_keying_status(self, active: bool) -> None:
        def update() -> None:
            self.keying_status.set("ON" if active else "OFF")
            self._status_chips["keying"].set_value("ON" if active else "OFF", "accent" if active else "muted")

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
        return (FONT_FAMILY, size)

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
