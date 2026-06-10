from __future__ import annotations

import audiobusio
import audiocore
import board
import busio
import digitalio
import displayio
import os
import random
import synthio
import time
from adafruit_debouncer import Button
from adafruit_ticks import ticks_ms, ticks_diff
from micropython import const

try:
  import espnow
  from typing import Iterable
except ImportError:
  pass

import samples
from classes import *
from utils import as_pin, btomacstr, settings

STATE_FILENAME = const('_state')
PARAMID_FRUIT = const(0x30)
PARAMID_P2 = const(0x31)
PARAMID_P3 = const(0x32)

class App:
  audio: audiobusio.I2SOut|None = None
  button: Button|None = None
  ctlbtn: Button|None = None
  spi: busio.SPI|None = None
  i2c: busio.I2C|None = None
  enow: espnow.ESPNow|None = None
  sd: SDHelper|None = None
  oled: OledDisplay|None = None
  wav_files: list[str]|None = None
  ctlmode: bool = False
  ctldirty: bool = False
  params: list[ConfigParam]|None = None
  paramsmap: dict[int, ConfigParam]|None = None
  param_selected: int|None = None
  last_ctl_active_at: int|None = None
  audio_stop_at: int|None = None
  synth: synthio.Synthesizer|None = None
  synth_envelope: synthio.Envelope|None = None
  _fp = None
  _wave = None
  _sample = None
  _button_pin: digitalio.DigitalInOut|None = None
  _ctlbtn_pin: digitalio.DigitalInOut|None = None

  def main(self) -> None:
    try:
      self.init()
      print(f'Running loop')
      while True:
        self.loop()
        # time.sleep(settings.loop_delay_secs)
    except KeyboardInterrupt:
      print(f'Stopping from Ctrl-C')
    finally:
      self.deinit()
  
  def init(self) -> None:
    self.deinit()
    if settings.esp_enabled:
      print('Initializing ESP-NOW wireless interface...')
      try:
        import espnow
        import wifi
        wifi.radio.enabled = True
        wifi.radio.start_ap(' ', '', channel=settings.esp_channel, max_connections=0)
        wifi.radio.stop_ap()
        self.enow = espnow.ESPNow()
        print(f'ESP-NOW active. MAC Address: {btomacstr(wifi.radio.mac_address)}')
      except Exception as e:
        print(f'Failed to initialize ESP-NOW: {e!r}')
    # Initialize the button
    self._button_pin = digitalio.DigitalInOut(as_pin(settings.button_pin))
    self._button_pin.direction = digitalio.Direction.INPUT
    self._button_pin.pull = digitalio.Pull.UP
    self.button = Button(self._button_pin, long_duration_ms=settings.button_long_press_secs * 1000)
    if settings.ctlbtn_enabled:
      self._ctlbtn_pin = digitalio.DigitalInOut(as_pin(settings.ctlbtn_pin))
      self._ctlbtn_pin.direction = digitalio.Direction.INPUT
      self._ctlbtn_pin.pull = digitalio.Pull.UP
      self.ctlbtn = Button(self._ctlbtn_pin, long_duration_ms=settings.button_long_press_secs * 1000)
    # Initialize I2S audio out
    self.audio = audiobusio.I2SOut(
      bit_clock=as_pin(settings.audio_pin_bclk),
      word_select=as_pin(settings.audio_pin_lrc),
      data=as_pin(settings.audio_pin_din))
    self.spi = board.SPI()
    if settings.oled_enabled:
      print(f'Initializing display...')
      from i2cdisplaybus import I2CDisplayBus
      try:
        self.i2c = board.I2C()
        display_bus = I2CDisplayBus(
          i2c_bus=self.i2c,
          device_address=settings.oled_address)
        self.oled = OledDisplay(
          bus=display_bus,
          driver=settings.oled_driver,
          width=settings.oled_width,
          height=settings.oled_height,
          line_spacing=settings.oled_line_spacing,
          x_offset=settings.oled_x_offset)
      except Exception as e:
        print(f'Failed to initialize display: {e!r}')
    self.paramsmap = {
      PARAMID_FRUIT: ConfigParam(
        id=PARAMID_FRUIT,
        name='fruit',
        title='Fruit',
        choices=('apple', 'banana', 'cherry')),
      PARAMID_P2: ConfigParam(
        id=PARAMID_P2,
        name='p2',
        choices=range(1, 33)),
      PARAMID_P3: ConfigParam(
        id=PARAMID_P3,
        name='p3',
        choices=range(8))}
    self.params = [
      self.paramsmap[PARAMID_FRUIT],
      self.paramsmap[PARAMID_P2],
      self.paramsmap[PARAMID_P3]]
    self.sd = SDHelper(
      spi=self.spi,
      pin_cs=settings.sd_pin_cs,
      path=settings.sd_path,
      after_mount=self.after_mount,
      before_umount=self.before_umount,
      after_umount=self.after_umount)
    self.synth = synthio.Synthesizer(sample_rate=22050)
    self.audio.play(self.synth)
    self.synth_envelope = synthio.Envelope(
      attack_time=settings.synth_attack_time,
      decay_time=settings.synth_decay_time,
      attack_level=settings.synth_attack_level,
      sustain_level=settings.synth_sustain_level,
      release_time=settings.synth_release_time)
    if self.sd.ensure_ready():
      self.load_saved_state()

  def deinit(self) -> None:
    if self.enow:
      self.enow.deinit()
    if self._button_pin:
      self._button_pin.deinit()
    if self._ctlbtn_pin:
      self._ctlbtn_pin.deinit()
    if self.oled:
      self.oled.deinit()
    if self.audio:
      self.audio.deinit()
    if self._fp:
      try:
        self._fp.close()
      except:
        pass
    if self.sd:
      self.sd.close()
    displayio.release_displays()
    self.enow = None
    self.button = None
    self.ctlbtn = None
    self.oled = None
    self.audio = None
    self.sd = None
    self.wav_files = None
    self.ctlmode = False
    self.ctldirty = False
    self.params = None
    self.paramsmap = None
    self.param_selected = None
    self.last_ctl_active_at = None
    self.audio_stop_at = None
    self.synth = None
    self.synth_envelope = None
    self._wave = None
    self._fp = None
    self._sample = None
    self._button_pin = None
    self._ctlbtn_pin = None

  def loop(self) -> None:
    self.check_close()
    self.check_ctlmode_idle()
    self.check_espnow_commands()
    if self.ctlbtn:
      self.ctlbtn.update()
      if self.ctlbtn.long_press:
        self.handle_ctlbtn_long_press()
      elif self.ctlbtn.short_count:
        self.handle_ctlbtn_short_press(self.ctlbtn.short_count)
    if self.button:
      self.button.update()
      if self.button.long_press:
        self.handle_button_long_press()
      elif self.button.short_count:
        self.handle_button_short_press(self.button.short_count)

  def execute(self, cmdbuf: bytes) -> None:
    try:
      if cmdbuf:
        command = cmdbuf[0]
        # Command 0x05: Play random track
        if command == 0x05:
          self.play_random_wav_file()            
        # Command 0x06: Play track by array index [command, index_byte]
        elif command == 0x06:
          if len(cmdbuf) < 2:
            raise ValueError('Malformed command: missing index byte')
          self.play_wav_by_index(cmdbuf[1])
        # Command 0x07: Play 440hz sine wave for 10s
        elif command == 0x07:
          self.play_pure_tone(frequency=440, duration_secs=10.0)
        # Command 0x08: Play 440hz synth for 2s
        elif command == 0x08:
          self.play_synth_note(frequency=440, duration_secs=2.0)
        # Command 0x09: Play variable pitch/duration synth [command, midi_note, duration_scalar]
        elif command == 0x09:
          if len(cmdbuf) < 3:
            raise ValueError('Malformed 0x09 command: missing midi_note or duration byte')
          midi_note = cmdbuf[1]
          duration_scalar = cmdbuf[2]
          # Translate MIDI note to exact frequency (Tuning standard A4 = 440Hz)
          frequency = 440.0 * (2.0 ** ((midi_note - 69) / 12.0))
          # Map duration step to 100ms units (e.g. value of 20 = 2.0 seconds)
          duration_secs = duration_scalar * 0.1
          print(f'Received 0x09: MIDI Note {midi_note} -> {frequency:.2f}Hz for {duration_secs:.1f}s')
          self.play_synth_note(frequency=frequency, duration_secs=duration_secs)
    except Exception as e:
      print(f'Error executing command: {e!r}')
    
  def handle_button_long_press(self) -> None:
    self.handle_button_short_press(1)

  def handle_button_short_press(self, count: int) -> None:
    if self.ctlmode:
      if self.param_selected is None:
        print(f'Warning: no param selected')
        return
      param = self.params[self.param_selected]
      param.adjust(count)
      self.draw_display()
      self.last_ctl_active_at = ticks_ms()
      self.ctldirty = True
    else:
      self.execute(settings.button_payload)

  def check_close(self):
    if self.audio_stop_at is not None:
      if ticks_ms() >= self.audio_stop_at:
        if isinstance(self._sample, synthio.Note):
          print('Releasing synth note')
          self.synth.release(self._sample)
          self._sample = None
        else:
          self.audio.stop()
        self.audio_stop_at = None
    if not self.audio.playing:
      if self._sample:
        print(f'Sample playing complete')
        self._sample = None
      elif self._fp:
        print('Playback complete')
        self._fp.close()
        self._fp = None
        self._wave = None

  def check_ctlmode_idle(self) -> None:
    if self.ctlmode and self.last_ctl_active_at is not None:
      elapsed_ms = ticks_diff(ticks_ms(), self.last_ctl_active_at)
      if elapsed_ms / 1000 > settings.idle_secs:
        self.ctlmode_exit()

  def check_espnow_commands(self) -> None:
    if not self.enow:
      return
    packet = self.enow.read()
    if packet and packet.msg:
      print(f'Received packet from {btomacstr(packet.mac)}')
      self.execute(packet.msg)

  def handle_ctlbtn_long_press(self) -> None:
    if self.ctlmode:
      self.ctlmode_exit()
    else:
      self.ctlmode_enter()

  def handle_ctlbtn_short_press(self, count: int) -> None:
    if not self.ctlmode:
      return
    self.param_selected = (count + self.param_selected) % len(self.params)
    self.draw_display()
    self.last_ctl_active_at = ticks_ms()

  def ctlmode_enter(self) -> None:
    print('Entering Control Mode')
    self.param_selected = 0
    self.ctlmode = True
    self.ctldirty = False
    self.draw_display()
    self.last_ctl_active_at = ticks_ms()

  def ctlmode_exit(self) -> None:
    print(f'Exiting Control Mode')
    self.param_selected = None
    self.ctlmode = False
    self.last_ctl_active_at = None
    if self.ctldirty:
      self.save_state()
    if self.oled:
      self.oled.sleep()

  def draw_display(self) -> None:
    if not self.oled:
      return
    if self.param_selected is None:
      print(f'Warning: no param selected')
      return
    param = self.params[self.param_selected]
    self.oled.header = param.name
    self.oled.body = str(param.value)
    self.oled.wake()

  def load_saved_state(self) -> None:
    filepath = f'{settings.sd_path}/{STATE_FILENAME}'
    def bytegen():
      try:
        with open(filepath, 'rb') as fp:
          while True:
            byte = fp.read(1)
            if not byte:
              break
            yield byte[0]
      except OSError:
        print(f'No saved state file found at {filepath}')
        return
    self.load_state(bytegen())

  def save_state(self) -> None:
    filepath = f'{settings.sd_path}/{STATE_FILENAME}'
    tmppath = f'{filepath}.tmp'
    print('Saving configuration state...')
    try:
      with open(tmppath, 'wb') as fp:
        for param in self.params:
          fp.write(bytes([param.id, param.selected]))
      os.rename(tmppath, filepath)
      print('State saved successfully')
    except Exception as e:
      print(f'Error saving state: {e!r}')
      try:
        os.remove(tmppath)
      except OSError:
        pass

  def load_state(self, buf: Iterable[int]) -> None:
    it = iter(buf)
    for paramid in it:
      try:
        index = next(it)
      except StopIteration:
        break
      try:
        param = self.paramsmap[paramid]
      except KeyError:
        print(f'Ignored unknown param ID {paramid}')
        continue
      if index < len(param.choices):
        param.selected = index
        print(f'Loaded {param.name}={param.value}')
      else:
        print(f'Warning: ignored invalid {param.name} index {index}')

  def reload_wav_files(self) -> None:
    self.wav_files = [
      f for f in os.listdir(settings.sd_path)
      if f.lower().endswith('.wav') and not f.startswith('.') and not f.startswith('_')]
    self.wav_files.sort()
    print('--- Reloaded WAV files from SD ---')
    for f in self.wav_files:
      print(f' - {f}')
    print(f'{len(self.wav_files)} tracks indexed')

  def play_random_wav_file(self) -> None:
    if not self.sd or not self.sd.ensure_ready():
      print('Cannot play audio: No SD card detected')
      return
    if not self.wav_files:
      print('No wav files available')
      return
    # Pick a random filename from our clean list
    filename = random.choice(self.wav_files)
    self.play_wav_by_filename(filename)

  def play_wav_by_index(self, index: int) -> None:
    if not self.sd or not self.sd.ensure_ready():
      print('Cannot play audio: No SD card detected')
      return
    if not self.wav_files:
      print('No wav files available')
      return
    if index >= len(self.wav_files):
      print(f'No wav file at index {index}')
      return
    filename = self.wav_files[index]
    self.play_wav_by_filename(filename)

  def play_wav_by_filename(self, filename: str) -> None:
    self.audio.stop()
    self.check_close()
    fullpath = f'{settings.sd_path}/{filename}'
    print(f'Opening: {filename}')
    try:
      self._fp = open(fullpath, 'rb')
      self._wave = audiocore.WaveFile(self._fp)
      print(f'Playing track...')
      self.audio.play(self._wave)
    except Exception as e:
      print(f'Error playing {filename}: {e!r}')

  def play_pure_tone(self, frequency: int, duration_secs: float) -> None:
    self.audio.stop()
    self.check_close()
    self._sample = samples.generate_sine_wave(frequency=frequency)
    self.audio_stop_at = ticks_ms() + duration_secs * 1000
    self.audio.play(self._sample, loop=True)

  def play_synth_note(self, frequency: float, duration_secs: float) -> None:
    self.audio.stop()
    self.check_close()
    self.audio.play(self.synth)
    self._sample = synthio.Note(frequency=frequency, envelope=self.synth_envelope)
    print(f'Pressing synth note: {frequency}Hz')
    self.synth.press(self._sample)
    self.audio_stop_at = ticks_ms() + duration_secs * 1000

  def after_mount(self) -> None:
    self.reload_wav_files()

  def before_umount(self) -> None:
    self.check_close()

  def after_umount(self) -> None:
    self.wav_files = None

app = App()

if __name__ == '__main__':
  app.main()
