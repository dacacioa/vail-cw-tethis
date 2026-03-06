"""Application entry point."""

from __future__ import annotations

import logging
import os
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

MIDI_SYNC_AUTO_COOLDOWN_SEC = float(os.getenv("MIDI_SYNC_AUTO_COOLDOWN_SEC", "1.0"))


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
        self._sync_lock = threading.Lock()
        self._sync_inflight = False
        self._ui: Optional[AppUI] = None
        self._last_wpm_for_autosync = float(config.wpm)
        self._last_midi_target_for_autosync = (config.midi_device or "").strip()
        self._wpm_sync_after_id: Optional[str] = None
        self._last_sync_request_ts = 0.0
        self._input_mode = self._normalize_input_mode(config.input_mode)
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
        self._midi.set_enabled(self._input_mode == "MIDI")
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
        self._ctrl.set_enabled(self._input_mode == "CTRL")
        self._schedule_level_poll()
        # Open MIDI after UI is idle to avoid multiple open/close cycles at startup.
        if self._ui and self._input_mode == "MIDI":
            self._ui.after(200, lambda: self._midi.open(""))

    def run(self) -> None:
        self._ui.mainloop()

    def shutdown(self) -> None:
        if self._ui and self._wpm_sync_after_id is not None:
            try:
                self._ui.after_cancel(self._wpm_sync_after_id)
            except Exception:
                pass
            self._wpm_sync_after_id = None
        self._ctrl.stop()
        self._midi.shutdown()
        self._audio_decode.stop()
        self._keyer.stop()
        self._audio.stop()
        self._cat.stop()
        self._ui.destroy()

    def apply_config(self, config: AppConfig) -> None:
        wpm_changed = False
        midi_target_changed = False
        with self._lock:
            prev_input_mode = self._input_mode
            config.input_mode = self._normalize_input_mode(config.input_mode)
            self._config = config
            save_config(config)
            self._keyer.update_settings(_keyer_settings(config))
            self._audio.update_settings(_audio_settings(config))
            self._audio_decode.update_settings(_audio_decode_settings(config))
            self._cat.update_settings(_cat_settings(config))
            self._midi.update_mapping(config.midi_channel, config.midi_note_dit, config.midi_note_dah)
            self._midi.set_sync_output_device(config.midi_device)
            self._input_mode = config.input_mode
            mode_changed = prev_input_mode != self._input_mode
            if mode_changed:
                logging.info("Input mode change: %s -> %s", prev_input_mode, self._input_mode)
            self._ctrl.set_enabled(self._input_mode == "CTRL")
            self._midi.set_enabled(self._input_mode == "MIDI")
            if self._input_mode == "CTRL":
                self._midi_state = (False, False)
                self._ctrl_state = (False, False)
                # Defensive: ensure the keyboard polling thread is alive
                # after switching back from MIDI mode.
                self._ctrl.start()
            self._apply_input_state()
            prev_midi_enabled = prev_input_mode == "MIDI"
            midi_enabled = self._input_mode == "MIDI"
            # `midi_device` in this UI is output/sync selection, while
            # `self._midi.device_name` is current input port. They are often
            # different names (e.g. "...0" input vs "...1" output), so do not
            # use this comparison to trigger reconnect loops.
            should_reopen_midi = midi_enabled and (
                not prev_midi_enabled or not self._midi.device_name
            )
            current_wpm = float(config.wpm)
            if abs(current_wpm - self._last_wpm_for_autosync) >= 0.01:
                self._last_wpm_for_autosync = current_wpm
                wpm_changed = True
            current_midi_target = (config.midi_device or "").strip()
            if current_midi_target != self._last_midi_target_for_autosync:
                self._last_midi_target_for_autosync = current_midi_target
                if current_midi_target:
                    midi_target_changed = True
            if should_reopen_midi:
                logging.info("MIDI mode active, ensuring input open")
                if self._ui:
                    self._ui.after(50, lambda: self._midi.open(""))
        if wpm_changed or (midi_target_changed and self._input_mode == "MIDI"):
            self._schedule_wpm_autosync()

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
        value = (mode or "CTRL").strip().upper()
        if value == "MIDI":
            return "MIDI"
        if value in {"CTRL", "KEYBOARD", "KEYBOARDCTRL", "AUTO"}:
            return "CTRL"
        return "CTRL"

    def _effective_state(self) -> Tuple[bool, bool]:
        if self._input_mode == "MIDI":
            return self._midi_state
        return self._ctrl_state

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
        if self._input_mode != "MIDI":
            if self._ui:
                self._ui.set_midi_status(False, "disabled")
            return
        self._midi.close()
        self._midi.open("")

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

    def _schedule_wpm_autosync(self) -> None:
        with self._lock:
            has_midi_target = bool((self._config.midi_device or "").strip())
        if not has_midi_target:
            return
        if not self._ui:
            self._sync_vail_hardware()
            return
        if self._wpm_sync_after_id is not None:
            try:
                self._ui.after_cancel(self._wpm_sync_after_id)
            except Exception:
                pass
        self._wpm_sync_after_id = self._ui.after(180, self._run_wpm_autosync)

    def _run_wpm_autosync(self) -> None:
        self._wpm_sync_after_id = None
        self._sync_vail_hardware(trigger="auto")

    def _sync_vail_hardware(self, trigger: str = "manual") -> None:
        now_mono = time.monotonic()
        with self._lock:
            if trigger != "auto" and self._ui and self._wpm_sync_after_id is not None:
                try:
                    self._ui.after_cancel(self._wpm_sync_after_id)
                except Exception:
                    pass
                self._wpm_sync_after_id = None
            if trigger == "auto":
                cooldown = max(0.0, MIDI_SYNC_AUTO_COOLDOWN_SEC)
                if cooldown > 0.0 and now_mono - self._last_sync_request_ts < cooldown:
                    logging.info("Sync Vail auto-skipped (cooldown %.2fs)", cooldown)
                    return
            # In MIDI mode prefer syncing against the currently opened MIDI
            # input pair (e.g. "... 0" -> "... 1"), not an unrelated output.
            device = self._midi.device_name if self._input_mode == "MIDI" and self._midi.device_name else self._config.midi_device
            wpm = float(self._config.wpm)
            keyer_type = self._config.keyer_type
            keep_midi = self._input_mode == "MIDI"
            force_passthrough = False
            if keep_midi:
                # Safe sync in MIDI mode: avoid Program Change during sync to
                # reduce chances of adapter lockups. Startup already forces
                # passthrough.
                keyer_type = None
            if self._sync_inflight:
                logging.info("Sync Vail skipped: another sync is still running")
                return
            self._sync_inflight = True
            self._last_sync_request_ts = now_mono
        self._midi.set_sync_output_device(device)
        self._midi.suppress_note_learning(0.4)

        def _run_sync() -> None:
            try:
                if not device:
                    logging.warning("Sync Vail skipped: no MIDI output device selected")
                    return
                logging.info("Sync Vail target: %s", device)
                with self._sync_lock:
                    self._midi.sync_vail_hardware(
                        wpm=wpm,
                        keyer_type=keyer_type,
                        keep_midi_mode=keep_midi,
                        force_passthrough=force_passthrough,
                    )
                logging.info("Sync Vail completed")
            except Exception as exc:
                logging.warning("Sync Vail failed: %s", exc)
            finally:
                with self._lock:
                    self._sync_inflight = False

        threading.Thread(target=_run_sync, name="midi-sync", daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    controller = AppController(config)
    controller.run()


if __name__ == "__main__":
    main()
