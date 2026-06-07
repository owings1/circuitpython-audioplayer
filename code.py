from __future__ import annotations

import audiobusio
import audiocore
import board
import busio
import digitalio
import displayio
import os
import random
import time
from adafruit_debouncer import Button
from adafruit_ticks import ticks_ms, ticks_diff
from micropython import const

try:
  from typing import Iterable
except ImportError:
  pass

from classes import *
from utils import as_pin, settings

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
  sd: SDHelper|None = None
  oled: OledDisplay|None = None
  wav_files: list[str]|None = None
  ctlmode: bool = False
  ctldirty: bool = False
  params: list[ConfigParam]|None = None
  paramsmap: dict[int, ConfigParam]|None = None
  param_selected: int|None = None
  last_ctl_active_at: int|None = None
  _fp = None
  _wave = None
  _button_pin: digitalio.DigitalInOut|None = None
  _ctlbtn_pin: digitalio.DigitalInOut|None = None

  def main(self) -> None:
    try:
      self.init()
      print(f'Running loop')
      while True:
        self.loop()
        time.sleep(settings.loop_delay_secs)
    except KeyboardInterrupt:
      print(f'Stopping from Ctrl-C')
    finally:
      self.deinit()
  
  def init(self) -> None:
    self.deinit()
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
    if self.sd.ensure_ready():
      self.load_saved_state()

  def deinit(self) -> None:
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
    self._wave = None
    self._fp = None
    self._button_pin = None
    self._ctlbtn_pin = None

  def loop(self) -> None:
    self.check_close()
    self.check_ctlmode_idle()
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
      self.play_random_wav_file()

  def check_close(self):
    if self._fp and not self.audio.playing:
      print('Playback complete')
      self._fp.close()
      self._fp = None
      self._wave = None

  def check_ctlmode_idle(self) -> None:
    if self.ctlmode and self.last_ctl_active_at is not None:
      elapsed_ms = ticks_diff(ticks_ms(), self.last_ctl_active_at)
      if elapsed_ms / 1000 > settings.idle_secs:
        self.ctlmode_exit()

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

  def play_random_wav_file(self):
    if not self.sd.ensure_ready():
      print('Cannot play audio: No SD card detected')
      return
    if not self.wav_files:
      print('No wav files available')
      return
    self.audio.stop()
    self.check_close()
    # Pick a random filename from our clean list
    filename = random.choice(self.wav_files)
    fullpath = f'{settings.sd_path}/{filename}'
    print(f'Opening: {filename}')
    try:
      self._fp = open(fullpath, 'rb')
      self._wave = audiocore.WaveFile(self._fp)
      print(f'Playing track...')
      self.audio.play(self._wave)
    except Exception as e:
      print(f'Error playing {filename}: {e!r}')

  def after_mount(self) -> None:
    self.reload_wav_files()

  def before_umount(self) -> None:
    self.check_close()

  def after_umount(self) -> None:
    self.wav_files = None

app = App()

if __name__ == '__main__':
  app.main()
