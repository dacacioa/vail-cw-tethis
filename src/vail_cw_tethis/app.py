from __future__ import annotations

import sys
from PySide6 import QtWidgets

from vail_cw_tethis.audio import AudioEngine, AudioSettings
from vail_cw_tethis.cat import CatController, CatSettings
from vail_cw_tethis.config import AppConfig, CatConfig, load_config, save_config
from vail_cw_tethis.keyer import KeyerEngine, KeyerSettings, KeyerType
from vail_cw_tethis.midi import MidiKeyer


class MainWindow(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Vail-CW Tethis")
        self._config = load_config()
        self._audio: AudioEngine | None = None
        self._midi: MidiKeyer | None = None
        self._keyer: KeyerEngine | None = None
        self._cat: CatController | None = None
        self._voice_ptt = False

        self._build_ui()
        self._load_config(self._config)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.midi_combo = QtWidgets.QComboBox()
        self.mic_combo = QtWidgets.QComboBox()
        self.output_combo = QtWidgets.QComboBox()
        self.keyer_combo = QtWidgets.QComboBox()
        self.keyer_combo.addItems([k.value for k in KeyerType])
        self.wpm_spin = QtWidgets.QSpinBox()
        self.wpm_spin.setRange(5, 60)
        self.ratio_spin = QtWidgets.QDoubleSpinBox()
        self.ratio_spin.setRange(2.0, 5.0)
        self.ratio_spin.setSingleStep(0.1)
        self.weighting_spin = QtWidgets.QDoubleSpinBox()
        self.weighting_spin.setRange(10.0, 90.0)
        self.weighting_spin.setSingleStep(1.0)
        self.reverse_check = QtWidgets.QCheckBox("Invertir paletas")

        self.sidetone_freq_spin = QtWidgets.QDoubleSpinBox()
        self.sidetone_freq_spin.setRange(200.0, 1200.0)
        self.sidetone_freq_spin.setSingleStep(10.0)
        self.sidetone_vol_spin = QtWidgets.QDoubleSpinBox()
        self.sidetone_vol_spin.setRange(0.0, 1.0)
        self.sidetone_vol_spin.setSingleStep(0.05)
        self.local_vol_spin = QtWidgets.QDoubleSpinBox()
        self.local_vol_spin.setRange(0.0, 1.0)
        self.local_vol_spin.setSingleStep(0.05)

        self.route_combo = QtWidgets.QComboBox()
        self.route_combo.addItems(["OutputOnly", "LocalOnly", "Both"])
        self.mix_combo = QtWidgets.QComboBox()
        self.mix_combo.addItems(["AlwaysMix", "CwMutesMic", "PushToTalkVoice"])

        self.cat_port_edit = QtWidgets.QLineEdit()
        self.cat_baud_spin = QtWidgets.QSpinBox()
        self.cat_baud_spin.setRange(1200, 115200)
        self.cat_baud_spin.setSingleStep(300)
        self.ptt_mode_combo = QtWidgets.QComboBox()
        self.ptt_mode_combo.addItems(["CAT", "RTS", "DTR"])
        self.tx_hang_spin = QtWidgets.QSpinBox()
        self.tx_hang_spin.setRange(0, 2000)
        self.tx_hang_spin.setSingleStep(50)

        form.addRow("MIDI device", self.midi_combo)
        form.addRow("Mic input", self.mic_combo)
        form.addRow("Audio output", self.output_combo)
        form.addRow("Keyer type", self.keyer_combo)
        form.addRow("WPM", self.wpm_spin)
        form.addRow("Dit/Dah ratio", self.ratio_spin)
        form.addRow("Weighting", self.weighting_spin)
        form.addRow("Paddle reverse", self.reverse_check)
        form.addRow("Sidetone frequency", self.sidetone_freq_spin)
        form.addRow("Sidetone volume", self.sidetone_vol_spin)
        form.addRow("Local monitor volume", self.local_vol_spin)
        form.addRow("Sidetone route", self.route_combo)
        form.addRow("Mix mode", self.mix_combo)
        form.addRow("CAT COM port", self.cat_port_edit)
        form.addRow("CAT baudrate", self.cat_baud_spin)
        form.addRow("PTT mode", self.ptt_mode_combo)
        form.addRow("TX hang time (ms)", self.tx_hang_spin)

        layout.addLayout(form)

        button_row = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh devices")
        self.start_button = QtWidgets.QPushButton("Start")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.voice_ptt_button = QtWidgets.QPushButton("Voice PTT")
        self.voice_ptt_button.setCheckable(True)
        self.save_button = QtWidgets.QPushButton("Save config")
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.voice_ptt_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        self.refresh_button.clicked.connect(self._refresh_devices)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)
        self.save_button.clicked.connect(self._save)
        self.voice_ptt_button.toggled.connect(self._toggle_voice_ptt)

        self._refresh_devices()

    def _refresh_devices(self) -> None:
        from vail_cw_tethis.audio import AudioEngine
        from vail_cw_tethis.midi import MidiKeyer

        midi_names = MidiKeyer.list_inputs()
        self._populate_combo(self.midi_combo, midi_names)
        mic_devices = AudioEngine.list_devices("input")
        out_devices = AudioEngine.list_devices("output")
        self._populate_combo(self.mic_combo, mic_devices)
        self._populate_combo(self.output_combo, out_devices)

    @staticmethod
    def _populate_combo(combo: QtWidgets.QComboBox, values: list[str]) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        combo.blockSignals(False)

    def _load_config(self, config: AppConfig) -> None:
        self.midi_combo.setCurrentText(config.midi_device)
        self.mic_combo.setCurrentText(config.audio_input_device)
        self.output_combo.setCurrentText(config.audio_output_device)
        self.keyer_combo.setCurrentText(config.keyer_type)
        self.wpm_spin.setValue(config.wpm)
        self.ratio_spin.setValue(config.dit_dah_ratio)
        self.weighting_spin.setValue(config.weighting)
        self.reverse_check.setChecked(config.paddle_reverse)
        self.sidetone_freq_spin.setValue(config.sidetone_frequency)
        self.sidetone_vol_spin.setValue(config.sidetone_volume)
        self.local_vol_spin.setValue(config.local_monitor_volume)
        self.route_combo.setCurrentText(config.sidetone_route)
        self.mix_combo.setCurrentText(config.mix_mode)
        self.cat_port_edit.setText(config.cat.com_port)
        self.cat_baud_spin.setValue(config.cat.baudrate)
        self.ptt_mode_combo.setCurrentText(config.cat.ptt_mode)
        self.tx_hang_spin.setValue(config.cat.tx_hang_time_ms)

    def _collect_config(self) -> AppConfig:
        return AppConfig(
            midi_device=self.midi_combo.currentText(),
            audio_input_device=self.mic_combo.currentText(),
            audio_output_device=self.output_combo.currentText(),
            keyer_type=self.keyer_combo.currentText(),
            wpm=self.wpm_spin.value(),
            dit_dah_ratio=self.ratio_spin.value(),
            weighting=self.weighting_spin.value(),
            paddle_reverse=self.reverse_check.isChecked(),
            sidetone_frequency=self.sidetone_freq_spin.value(),
            sidetone_volume=self.sidetone_vol_spin.value(),
            local_monitor_volume=self.local_vol_spin.value(),
            sidetone_route=self.route_combo.currentText(),
            mix_mode=self.mix_combo.currentText(),
            cat=CatConfig(
                com_port=self.cat_port_edit.text(),
                baudrate=self.cat_baud_spin.value(),
                ptt_mode=self.ptt_mode_combo.currentText(),
                tx_hang_time_ms=self.tx_hang_spin.value(),
            ),
        )

    def _start(self) -> None:
        config = self._collect_config()
        self._config = config
        save_config(config)
        self._cat = CatController(
            CatSettings(
                com_port=config.cat.com_port,
                baudrate=config.cat.baudrate,
                ptt_mode=config.cat.ptt_mode,
                tx_hang_time_ms=config.cat.tx_hang_time_ms,
            )
        )
        if config.cat.com_port:
            self._cat.connect()
        self._audio = AudioEngine(
            AudioSettings(
                input_device=config.audio_input_device,
                output_device=config.audio_output_device,
                sidetone_frequency=config.sidetone_frequency,
                sidetone_volume=config.sidetone_volume,
                local_monitor_volume=config.local_monitor_volume,
                sidetone_route=config.sidetone_route,
                mix_mode=config.mix_mode,
            ),
            on_ptt_change=self._set_ptt,
        )
        self._audio.start()
        self._keyer = KeyerEngine(
            KeyerSettings(
                keyer_type=KeyerType(config.keyer_type),
                wpm=config.wpm,
                dit_dah_ratio=config.dit_dah_ratio,
                weighting=config.weighting,
                paddle_reverse=config.paddle_reverse,
            ),
            on_key=self._handle_key,
        )
        self._keyer.start()
        self._midi = MidiKeyer(config.midi_device, self._handle_paddles)
        self._midi.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def _stop(self) -> None:
        if self._midi:
            self._midi.stop()
            self._midi = None
        if self._keyer:
            self._keyer.stop()
            self._keyer = None
        if self._audio:
            self._audio.stop()
            self._audio = None
        if self._cat:
            self._cat.close()
            self._cat = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _handle_paddles(self, dit: bool, dah: bool) -> None:
        if self._keyer:
            self._keyer.set_paddles(dit, dah)

    def _handle_key(self, state: bool) -> None:
        if self._audio:
            self._audio.set_key_down(state)
        if state:
            self._set_ptt(True)
        elif not self._voice_ptt:
            self._set_ptt(False)

    def _set_ptt(self, state: bool) -> None:
        if self._cat:
            self._cat.set_ptt(state)

    def _toggle_voice_ptt(self, checked: bool) -> None:
        self._voice_ptt = checked
        if self._audio:
            self._audio.set_ptt_active(checked)
        if not checked and self._cat:
            self._cat.set_ptt(False)

    def _save(self) -> None:
        config = self._collect_config()
        save_config(config)
        self._config = config


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.resize(520, 640)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
