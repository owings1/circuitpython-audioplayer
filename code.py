from __future__ import annotations

import audiobusio
import audiocore
import board
import busio
import digitalio
import displayio
import os
import sdcardio
import storage
import random
import time
from adafruit_debouncer import Button
from adafruit_ticks import ticks_ms, ticks_diff

from classes import *
from utils import as_pin, settings

class App:
  audio: audiobusio.I2SOut|None = None
  button: Button|None = None
  ctlbtn: Button|None = None
  spi: busio.SPI|None = None
  i2c: busio.I2C|None = None
  sdcard: sdcardio.SDCard|None = None
  oled: OledDisplay|None = None
  wav_files: list[str]|None = None
  ctlmode: bool = False
  params: list[ConfigParam]|None = []
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
    # Initialize the SD card
    print('Initializing SD card...')
    self.spi = board.SPI()
    self.sdcard = sdcardio.SDCard(self.spi, board.D3)
    vfs = storage.VfsFat(self.sdcard)
    storage.mount(vfs, settings.sd_path)
    print('SD Card mounted successfully')
    # List and filter only .wav files from the SD card directory
    self.wav_files = [
      f for f in os.listdir(settings.sd_path)
      if f.lower().endswith('.wav') and not f.startswith('.')]
    print('--- Found WAV files on SD card ---')
    for f in self.wav_files:
      print(f' - {f}')
    print(f'{len(self.wav_files)} tracks loaded')
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
    self.params = [
      ConfigParam(
        name='fruit',
        choices=('apple', 'banana', 'cherry')),
      ConfigParam(
        name='p2',
        choices=range(1, 33)),
      ConfigParam(
        name='p3',
        choices=range(8))]
  
  def deinit(self) -> None:
    if self._button_pin:
      self._button_pin.deinit()
    if self._ctlbtn_pin:
      self._ctlbtn_pin.deinit()
    if self.oled:
      self.oled.deinit()
    if self.audio:
      self.audio.deinit()
    if self.sdcard:
      self.sdcard.deinit()
    if self._fp:
      try:
        self._fp.close()
      except:
        pass
    displayio.release_displays()
    self.button = None
    self.ctlbtn = None
    self.oled = None
    self.audio = None
    self.sdcard = None
    self.wav_files = None
    self.ctlmode = False
    self.params = None
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
    else:
      if not self.audio.playing and self.wav_files:
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
    self.draw_display()
    self.last_ctl_active_at = ticks_ms()

  def ctlmode_exit(self) -> None:
    print('Exiting Control Mode')
    self.param_selected = None
    self.ctlmode = False
    self.last_ctl_active_at = None
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

app = App()

if __name__ == '__main__':
  app.main()
