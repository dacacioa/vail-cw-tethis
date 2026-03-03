"""Application entry point."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

from audio_decode_input import AudioDecodeInput, AudioDecodeSettings
from audio import AudioEngine, AudioSettings
from cat import CatController, CatSettings
from config import AppConfig, load_config, save_config
from ctrl_input import CtrlKeyboardInput
from keyer import KeyerEngine, KeyerSettings
from midi import MidiKeyerInput
from morse_decoder import MorseDecoder
from ui import AppUI


def _audio_settings(config: AppConfig) -> AudioSettings:
    return AudioSettings(
        output_device=config.audio_output_device,
        sample_rate=config.sample_rate,
        channels=config.channels,
        sidetone_enabled=config.sidetone_enabled,
        sidetone_freq=config.sidetone_freq,
        sidetone_volume=config.sidetone_volume,
    )


def _audio_decode_settings(config: AppConfig) -> AudioDecodeSettings:
    return AudioDecodeSettings(
        enabled=bool(config.decode_audio_enabled),
        input_device=config.decode_audio_input_device,
        sample_rate=config.sample_rate,
        tone_low_freq=config.decode_audio_tone_low_freq,
        tone_high_freq=config.decode_audio_tone_high_freq,
    )


def _keyer_settings(config: AppConfig) -> KeyerSettings:
    return KeyerSettings(
        keyer_type=config.keyer_type,
        wpm=config.wpm,
        dit_dah_ratio=config.dit_dah_ratio,
        weighting=config.weighting,
        paddle_reverse=config.paddle_reverse,
    )


def _cat_settings(config: AppConfig) -> CatSettings:
    return CatSettings(
        port=config.cat_port,
        baudrate=config.cat_baud,
        ptt_method=config.ptt_method,
        thetis_key_line=config.thetis_key_line,
        thetis_ptt_line=config.thetis_ptt_line,
        thetis_key_invert=config.thetis_key_invert,
        thetis_ptt_invert=config.thetis_ptt_invert,
        hang_time=config.tx_hang_time,
    )


class AppController:
    """Connects UI and backend modules."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._ui: Optional[AppUI] = None
        self._input_mode = "CTRL"
        self._audio = AudioEngine(_audio_settings(config))
        self._keyer = KeyerEngine(_keyer_settings(config), on_keying=self._on_keying)
        self._cat = CatController(
            _cat_settings(config),
            on_status=self._on_cat_status,
            on_port_status=self._on_cat_port_status,
        )
        self._midi_state: Tuple[bool, bool] = (False, False)
        self._ctrl_state: Tuple[bool, bool] = (False, False)
        self._active_state: Tuple[bool, bool] = (False, False)
        self._decoder = MorseDecoder(self._decoder_unit_seconds, auto_speed=False)
        self._rx_decoder = MorseDecoder(self._decoder_unit_seconds, auto_speed=True)
        self._midi_last_raw = ""
        self._midi_last_state = "-"
        self._midi = MidiKeyerInput(
            self._on_midi_paddle,
            on_status=self._on_midi_status,
            on_message=self._on_midi_message,
        )
        self._ctrl = CtrlKeyboardInput(
            self._on_ctrl_paddle,
            on_status=self._on_ctrl_status,
        )
        self._audio_decode = AudioDecodeInput(
            _audio_decode_settings(config),
            on_tone=self._on_audio_decode_tone,
            on_status=self._on_audio_decode_status,
        )
        self._midi.update_mapping(config.midi_channel, config.midi_note_dit, config.midi_note_dah)
        self._midi.set_sync_output_device(config.midi_device)
        # Mirror Vail Zoomer behavior: write keyer speed/type to hardware on settings load.
        self._midi.sync_vail_hardware(config.wpm, config.keyer_type)
        self._midi.set_enabled(self._input_mode in {"AUTO", "MIDI"})
        self._ui = AppUI(
            config,
            on_config=self.apply_config,
            on_manual_ptt=self._cat.set_manual,
            on_release=self._release_key,
            on_midi_reconnect=self._midi_reconnect,
            on_test_sidetone=self._test_sidetone,
            on_clear_decode=self._clear_decode_text,
            on_sync_vail=self._sync_vail_hardware,
            on_ctrl_key_event=self._ctrl.handle_key_event,
        )
        self._ui.protocol("WM_DELETE_WINDOW", self.shutdown)
        self._audio.start()
        self._audio_decode.start()
        self._cat.start()
        self._keyer.start()
        self._ctrl.start()
        self._ctrl.set_enabled(self._input_mode in {"AUTO", "CTRL"})
        self._schedule_level_poll()
        # Open MIDI after UI is idle to avoid multiple open/close cycles at startup.
        if self._ui and self._input_mode in {"AUTO", "MIDI"}:
            self._ui.after(200, lambda: self._midi.open(config.midi_device))

    def run(self) -> None:
        self._ui.mainloop()

    def shutdown(self) -> None:
        self._ctrl.stop()
        self._midi.shutdown()
        self._audio_decode.stop()
        self._keyer.stop()
        self._audio.stop()
        self._cat.stop()
        self._ui.destroy()

    def apply_config(self, config: AppConfig) -> None:
        with self._lock:
            prev_input_mode = self._input_mode
            config.input_mode = "CTRL"
            self._config = config
            save_config(config)
            self._keyer.update_settings(_keyer_settings(config))
            self._audio.update_settings(_audio_settings(config))
            self._audio_decode.update_settings(_audio_decode_settings(config))
            self._cat.update_settings(_cat_settings(config))
            self._midi.update_mapping(config.midi_channel, config.midi_note_dit, config.midi_note_dah)
            self._midi.set_sync_output_device(config.midi_device)
            self._midi.sync_vail_hardware(config.wpm, config.keyer_type)
            self._input_mode = "CTRL"
            self._ctrl.set_enabled(self._input_mode in {"AUTO", "CTRL"})
            self._midi.set_enabled(self._input_mode in {"AUTO", "MIDI"})
            if self._input_mode == "CTRL":
                self._midi_state = (False, False)
            self._apply_input_state()
            prev_midi_enabled = prev_input_mode in {"AUTO", "MIDI"}
            midi_enabled = self._input_mode in {"AUTO", "MIDI"}
            should_reopen_midi = (not prev_midi_enabled and midi_enabled) or (
                midi_enabled and config.midi_device != self._midi.device_name
            )
            if should_reopen_midi:
                if self._ui:
                    self._ui.after(50, lambda: self._midi.open(config.midi_device))

    def _on_keying(self, active: bool) -> None:
        self._audio.set_keying(active)
        self._cat.request_cw(active)
        self._decoder.on_keying(active, time.monotonic())
        self._flush_decoded_texts()
        if self._ui:
            self._ui.set_keying_status(active)

    def _on_audio_decode_tone(self, active: bool) -> None:
        self._rx_decoder.on_keying(active, time.monotonic())
        self._flush_decoded_texts()

    @staticmethod
    def _normalize_input_mode(mode: str) -> str:
        value = (mode or "Auto").strip().upper()
        if value == "MIDI":
            return "MIDI"
        if value in {"CTRL", "KEYBOARD", "KEYBOARDCTRL"}:
            return "CTRL"
        return "AUTO"

    def _effective_state(self) -> Tuple[bool, bool]:
        if self._input_mode == "MIDI":
            return self._midi_state
        if self._input_mode == "CTRL":
            return self._ctrl_state
        return (
            self._midi_state[0] or self._ctrl_state[0],
            self._midi_state[1] or self._ctrl_state[1],
        )

    def _apply_input_state(self) -> None:
        state = self._effective_state()
        if state == self._active_state:
            return
        self._active_state = state
        self._keyer.set_paddle_state(state[0], state[1])

    def _on_midi_paddle(self, dit: bool, dah: bool) -> None:
        self._midi_state = (dit, dah)
        self._apply_input_state()
        self._midi_last_state = f"dit={int(dit)} dah={int(dah)}"
        self._update_midi_last()

    def _on_ctrl_paddle(self, dit: bool, dah: bool) -> None:
        self._ctrl_state = (dit, dah)
        self._apply_input_state()

    def _on_midi_message(self, text: str) -> None:
        self._midi_last_raw = text
        self._update_midi_last()

    def _update_midi_last(self) -> None:
        if not self._ui:
            return
        if self._midi_last_raw:
            self._ui.set_midi_last(f"{self._midi_last_raw} | {self._midi_last_state}")
        else:
            self._ui.set_midi_last(self._midi_last_state)

    def _on_midi_status(self, connected: bool, label: str) -> None:
        if not connected:
            self._midi_state = (False, False)
            self._apply_input_state()
        if not self._ui:
            return
        self._ui.set_midi_status(connected, label)

    def _on_ctrl_status(self, connected: bool, label: str) -> None:
        if not self._ui:
            return
        self._ui.set_ctrl_status(connected, label)

    def _on_cat_status(self, tx: bool, label: str) -> None:
        if self._ui:
            self._ui.set_ptt_status(tx)
        self._audio.set_ptt(tx)

    def _on_cat_port_status(self, connected: bool, label: str) -> None:
        if self._ui:
            self._ui.set_port_status(connected, label)

    def _on_audio_decode_status(self, connected: bool, label: str) -> None:
        if self._ui:
            self._ui.set_decode_audio_status(connected, label)

    def _release_key(self) -> None:
        self._midi_state = (False, False)
        self._ctrl_state = (False, False)
        self._active_state = (False, False)
        self._keyer.set_paddle_state(False, False)
        self._keyer.update_settings(_keyer_settings(self._config))
        self._audio.set_keying(False)
        self._cat.release()
        self._decoder.poll(time.monotonic())
        self._rx_decoder.poll(time.monotonic())
        self._flush_decoded_texts()

    def _midi_reconnect(self) -> None:
        if self._input_mode not in {"AUTO", "MIDI"}:
            if self._ui:
                self._ui.set_midi_status(False, "disabled")
            return
        self._midi.close()
        self._midi.open(self._config.midi_device)

    def _test_sidetone(self) -> None:
        self._audio.set_keying(True)
        if self._ui:
            self._ui.set_keying_status(True)
        threading.Timer(0.2, self._stop_test_sidetone).start()

    def _stop_test_sidetone(self) -> None:
        self._audio.set_keying(False)
        if self._ui:
            self._ui.set_keying_status(False)

    def _schedule_level_poll(self) -> None:
        level = self._audio.get_level()
        self._decoder.poll(time.monotonic())
        self._rx_decoder.poll(time.monotonic())
        self._flush_decoded_texts()
        if self._ui:
            self._ui.set_audio_level(level)
            self._ui.set_decode_wpm(self._rx_decoder.estimated_wpm())
            self._ui.after(100, self._schedule_level_poll)

    def _decoder_unit_seconds(self) -> float:
        with self._lock:
            wpm = max(float(self._config.wpm), 1.0)
            weighting = max(float(self._config.weighting), 10.0) / 50.0
        return (1.2 / wpm) * weighting

    def _flush_decoded_texts(self) -> None:
        if not self._ui:
            return
        local_text = self._decoder.read_text()
        if local_text:
            self._ui.append_decoded_text(local_text, source="local")
        audio_text = self._rx_decoder.read_text()
        if audio_text:
            self._ui.append_decoded_text(audio_text, source="audio")

    def _clear_decode_text(self) -> None:
        self._decoder.reset()
        self._rx_decoder.reset()
        if self._ui:
            self._ui.clear_decoded_text()

    def _sync_vail_hardware(self) -> None:
        self._midi.set_sync_output_device(self._config.midi_device)
        self._midi.sync_vail_hardware(self._config.wpm, self._config.keyer_type)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    controller = AppController(config)
    controller.run()


if __name__ == "__main__":
    main()
