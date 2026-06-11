from __future__ import annotations

import array
import board
import busio
import digitalio
import os
import random
import time
from adafruit_debouncer import Button
from adafruit_ticks import ticks_ms, ticks_diff
from micropython import const

try:
  from typing import Generator, Iterable, Literal
  import adafruit_vl53l0x
  import audiobusio
  import espnow
  import synthio
except ImportError:
  pass

from classes import *
from utils import as_pin, btomacstr, macstrtob, couples, notetofreq, settings

STATE_FILENAME = const('_state')
PARAMID_DEFAULT_WAVEFORM = const(0x08)
PARAMID_SYNTH_VOLUME = const(0x09)
DURATION_SCALE = 0.1

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
  note_queue: Generator[tuple[int, int]]|None = None
  waveforms: tuple[array.array, ...]|None = None
  active_waveform: int|None = None
  tof: adafruit_vl53l0x.VL53L0X|None = None
  last_tof_trigger_at: int|None = None
  tof_armed: bool = True
  tof_trigger_start: int|None = None
  _fp = None
  _wave = None
  _sample = None
  _button_pin: digitalio.DigitalInOut|None = None
  _ctlbtn_pin: digitalio.DigitalInOut|None = None
  _peers: list[espnow.Peer]|None = None

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
      self._peers = []
      for i, macstr in enumerate(settings.esp_peers):
        print(f'Initializing peer {i}: {macstr}')
        peer = espnow.Peer(mac=macstrtob(macstr))
        self.enow.peers.append(peer)
        self._peers.append(peer)
    # Initialize the button
    if settings.button_enabled:
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
    if settings.audio_enabled:
      print(f'Initializing audio')
      try:
        import audiobusio
        self.audio = audiobusio.I2SOut(
          bit_clock=as_pin(settings.audio_pin_bclk),
          word_select=as_pin(settings.audio_pin_lrc),
          data=as_pin(settings.audio_pin_din))
      except Exception as e:
        print(f'Failed to initialize audio: {e!r}')
    if settings.oled_enabled:
      print(f'Initializing display')
      try:
        from i2cdisplaybus import I2CDisplayBus
        self.i2c = self.i2c or board.I2C()
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
    self.paramsmap = {}
    self.params = []
    if settings.audio_enabled and settings.synth_enabled:
      print(f'Initializing synth')
      try:
        import samples
        import synthio
        self.synth = synthio.Synthesizer(sample_rate=22050)
        print(f'Creating waveform table')
        self.waveforms = samples.create_wave_tables()
        default_waveform = settings.synth_default_waveform
        if default_waveform not in range(len(self.waveforms)):
          default_waveform = samples.WTI_SINE
        self.paramsmap |= {
          PARAMID_DEFAULT_WAVEFORM: ConfigParam(
            id=PARAMID_DEFAULT_WAVEFORM,
            name='synth_default_waveform',
            title='Default Synth Wave',
            choices=('sine', 'triangle', 'saw', 'square'),
            selected=default_waveform),
          PARAMID_SYNTH_VOLUME: ConfigParam(
            id=PARAMID_SYNTH_VOLUME,
            name='synth_volume',
            title='Synth Volume',
            choices=range(1, 11),
            selected=settings.synth_volume % 10)}
        self.params += [
          self.paramsmap[PARAMID_DEFAULT_WAVEFORM],
          self.paramsmap[PARAMID_SYNTH_VOLUME]]
      except Exception as e:
        print(f'Failed to initialize synth: {e!r}')
    if settings.tof_enabled:
      print(f'Initializing TOF sensor')
      try:
        import adafruit_vl53l0x
        self.i2c = self.i2c or board.I2C()
        self.tof = adafruit_vl53l0x.VL53L0X(self.i2c)
        # Optimizing sensor performance parameters
        self.tof.measurement_timing_budget = 33000  # High-speed low-latency sampling (33ms)
      except Exception as e:
        print(f'Failed to initialize TOF sensor: {e!r}')
      else:
        print(f'Initialized TOF sensor')
    if settings.sd_enabled:
      print(f'Initializing SD')
      self.spi = self.spi or board.SPI()
      self.sd = SDHelper(
        spi=self.spi,
        pin_cs=settings.sd_pin_cs,
        path=settings.sd_path,
        after_mount=self.after_mount,
        before_umount=self.before_umount,
        after_umount=self.after_umount)
      if self.sd.ensure_ready():
        self.load_saved_state()
    if self.synth:
      self.init_envelope()

  def deinit(self) -> None:
    if self.enow is not None:
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
    if self.note_queue:
      try:
        self.note_queue.close()
      except:
        pass
    if settings.oled_enabled:
      import displayio
      displayio.release_displays()
    self.i2c = None
    self.spi = None
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
    self.note_queue = None
    self.waveforms = None
    self.active_waveform = None
    self.tof = None
    self.last_tof_trigger_at = None
    self.tof_armed = True
    self.tof_trigger_start = None
    self._wave = None
    self._fp = None
    self._sample = None
    self._button_pin = None
    self._ctlbtn_pin = None
    self._peers = None

  @property
  def default_waveform(self) -> int|None:
    try:
      return self.paramsmap[PARAMID_DEFAULT_WAVEFORM].selected
    except KeyError:
      pass

  def loop(self) -> None:
    self.check_close()
    if self.enow is not None:
      self.check_espnow_commands()
    if self.ctlbtn:
      self.run_ctlbtn()
    if self.button:
      self.run_button()
    if self.tof:
      self.run_tof()

  def execute(self, cmdbuf: bytes) -> None:
    if not cmdbuf:
      return
    try:
      command = cmdbuf[0]
      # Command 0x05: Play random track
      if command == 0x05:
        self.play_random_wav_file()            
      # Command 0x06: Play track by array index [command, index_byte]
      elif command == 0x06:
        if len(cmdbuf) < 2:
          raise ValueError('Malformed command: missing index byte')
        self.play_wav_by_index(cmdbuf[1])
      # Command 0x0A: Trigger Pre-Programmed Sequence [command, tune_id]
      elif command == 0x0A:
        if len(cmdbuf) < 2:
          raise ValueError('Malformed command: missing index byte')
        self.play_prefab_tune(cmdbuf[1], self.default_waveform)
      # Command 0x0B: Trigger Pre-Programmed Sequence with Waveform [command, tune_id, waveform_id]
      elif command == 0x0B:
        if len(cmdbuf) < 3:
          raise ValueError('Malformed command: missing index or waveform byte')
        self.play_prefab_tune(cmdbuf[1], cmdbuf[2])
    except Exception as e:
      print(f'Error executing command: {e!r}')

  def send(self, payload: bytes, peerid: int|Literal['*']) -> None:
    if self.enow is None:
      print(f'Cannot send payload: ESP-NOW not initialized')
    if peerid == '*':
      peer = None
    else:
      try:
        peer = self._peers[peerid]
      except IndexError:
        print(f'Cannot send payload: No peer at index {peerid}')
        return
      except Exception as e:
        print(f'Send failed: {e!r}')
        return
    print(f'Sending payload {payload.hex()} to peer {peerid}')
    try:
      self.enow.send(payload, peer)
    except Exception as e:
      print(f'Transmission failed: {e!r}')

  def send_or_execute(self, payload: bytes, peerid: int|Literal['*']|None):
    if peerid is None:
      self.execute(payload)
    else:
      self.send(payload, peerid)

  def run_button(self) -> None:
    self.button.update()
    if self.button.long_press:
      self.handle_button_long_press()
    elif self.button.short_count:
      self.handle_button_short_press(self.button.short_count)

  def run_ctlbtn(self) -> None:
    self.check_ctlmode_idle()
    self.ctlbtn.update()
    if self.ctlbtn.long_press:
      self.handle_ctlbtn_long_press()
    elif self.ctlbtn.short_count:
      self.handle_ctlbtn_short_press(self.ctlbtn.short_count)

  def run_tof(self) -> None:
    distance = self.tof.range
    if distance >= settings.tof_threshold_mm:
      self.tof_armed = True
      self.tof_trigger_start = None
      return
    if not self.tof_armed:
      return
    current_time = ticks_ms()
    # Debounce
    if self.tof_trigger_start is None:
      self.tof_trigger_start = current_time
    if ticks_diff(current_time, self.tof_trigger_start) < settings.tof_debounce_secs * 1000:
      return
    # Cooldown
    if (
      self.last_tof_trigger_at is not None and
      ticks_diff(current_time, self.last_tof_trigger_at) < settings.tof_cooldown_secs * 1000):
      return
    print(f'Motion Detected! Target Range: {distance}mm')
    self.last_tof_trigger_at = current_time
    self.tof_armed = False
    self.handle_tof_trigger()

  def handle_tof_trigger(self) -> None:
    self.send_or_execute(settings.tof_payload, settings.tof_peer)
    
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
      self.send_or_execute(settings.button_payload, settings.button_peer)

  def check_close(self):
    if not self.audio:
      return
    if self.synth:
      if self.audio_stop_at is not None:
        if ticks_ms() >= self.audio_stop_at:
          from synthio import Note
          if isinstance(self._sample, Note):
            print('Releasing synth note')
            self.synth.release(self._sample)
            self._sample = None
          else:
            self.audio.stop()
          self.audio_stop_at = None
          if self.note_queue:
            self.play_next_queued_note()
    try:
      playing = self.audio.playing
    except ValueError:
      # Corner case: ValueError: Object has been deinitialized and can no longer be used. Create a new object.
      return
    if not playing:
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
      if self.synth:
        self.init_envelope()
      if self.sd:
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
    self.oled.header = param.title
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

  def init_envelope(self) -> None:
    try:
      volume = self.paramsmap[PARAMID_SYNTH_VOLUME].value
    except KeyError:
      print(f'Warning: volume parameter missing, using 10')
      volume = 10
    from synthio import Envelope
    self.synth_envelope = Envelope(
      attack_time=settings.synth_attack_time,
      decay_time=settings.synth_decay_time,
      attack_level=settings.synth_attack_level * (volume / 10),
      sustain_level=settings.synth_sustain_level * (volume / 10),
      release_time=settings.synth_release_time)
    
  def reload_wav_files(self) -> None:
    if not settings.audio_enabled or not settings.sd_enabled:
      return
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
      print('Cannot play wav file: No SD card detected')
      return
    if not self.wav_files:
      print('No wav files available')
      return
    # Pick a random filename from our clean list
    filename = random.choice(self.wav_files)
    self.play_wav_by_filename(filename)

  def play_wav_by_index(self, index: int) -> None:
    if not self.sd or not self.sd.ensure_ready():
      print('Cannot play wav file: No SD card detected')
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
    if not self.audio:
      print(f'Cannot play wav file: No audio initialized')
      return
    if self.synth:
      self.synth.release_all()
    self.audio.stop()
    self.check_close()
    self.audio_stop_at = None
    fullpath = f'{settings.sd_path}/{filename}'
    print(f'Opening: {filename}')
    try:
      from audiocore import WaveFile
      self._fp = open(fullpath, 'rb')
      self._wave = WaveFile(self._fp)
      print(f'Playing track...')
      self.audio.play(self._wave)
    except Exception as e:
      print(f'Error playing {filename}: {e!r}')

  def play_prefab_tune(self, index: int, waveform: int) -> None:
    if not self.audio:
      print(f'Cannot play tune: No audio initialized')
    if not self.synth:
      print(f'Cannot play tune: No synth initialized')
    if index >= len(settings.prefab_tunes):
      print(f'No tune at index {index}')
      return
    tune = settings.prefab_tunes[index]
    if not tune or len(tune) < 2:
      print(f'Empty tune at index {index}')
      return
    # Safely clear any active generator state queues first
    if self.note_queue:
      try:
        self.note_queue.close()
      except:
        pass
      self.note_queue = None
    if self._fp or self.audio_stop_at is not None:
      self.audio_stop_at = ticks_ms() # Force immediate expiration threshold
      self.check_close()
    self.synth.release_all()
    self.audio.stop()
    self.check_close()
    if waveform not in range(len(self.waveforms)):
      print(f'Invalid {waveform=}, using default')
      waveform = self.default_waveform
    self.active_waveform = waveform
    self.audio.play(self.synth)
    self.note_queue = couples(tune)
    self.play_next_queued_note()

  def play_next_queued_note(self) -> None:
    if not self.note_queue:
      return
    try:
      midi_note, duration_scalar = next(self.note_queue)
    except StopIteration:
      print(f'Tune playback complete')
      self.note_queue = None
      return
    try:
      frequency = notetofreq(midi_note)
      from synthio import Note
      self._sample = Note(
        frequency=frequency,
        envelope=self.synth_envelope,
        waveform=self.waveforms[self.active_waveform])
      self.synth.press(self._sample)
      duration_secs = duration_scalar * DURATION_SCALE
      self.audio_stop_at = ticks_ms() + int(duration_secs * 1000)
    except Exception as e:
      print(f'Queue execution error: {e!r}')
      try:
        self.note_queue.close()
      except:
        pass
      self.note_queue = None
      return

  def after_mount(self) -> None:
    self.reload_wav_files()

  def before_umount(self) -> None:
    self.check_close()

  def after_umount(self) -> None:
    self.wav_files = None

app = App()

if __name__ == '__main__':
  app.main()
