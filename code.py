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
from microcontroller import Pin
from micropython import const

try:
  from typing import Generator, Iterable, Literal, BinaryIO
  import adafruit_vl53l0x
  import audiobusio
  import audiocore
  import espnow
  import synthio
except ImportError:
  pass

import utils
from classes import *
from utils import as_pin, btomacstr, macstrtob, couples, notetofreq, settings

STATE_FILENAME = const('_state')
PARAMID_DEFAULT_WAVEFORM = const(0x08)
PARAMID_SYNTH_VOLUME = const(0x09)
PARAMID_TOF_THRESHOLD = const(0x10)
DURATION_SCALE = 0.1

class App:
  spi: busio.SPI|None = None
  i2c: busio.I2C|None = None
  params: list[ConfigParam]|None = None
  paramsmap: dict[int, ConfigParam]|None = None

  sd: SDHelper|None = None

  oled: OledDisplay|None = None

  enow: espnow.ESPNow|None = None
  enow_peers: list[espnow.Peer]|None = None

  audio: audiobusio.I2SOut|None = None
  audio_wav_files: list[str]|None = None
  audio_busy: bool = False
  audio_stop_at: int|None = None
  audio_wave: audiocore.WaveFile|None = None
  audio_fp: BinaryIO|None = None
  synth: synthio.Synthesizer|None = None
  synth_envelope: synthio.Envelope|None = None
  synth_note_queue: Generator[tuple[int, int]]|None = None
  synth_waveforms: tuple[array.array, ...]|None = None
  synth_active_waveform: int|None = None
  synth_sample: synthio.Note|None = None

  btn_button: Button|None = None
  btn_button_pin: digitalio.DigitalInOut|None = None

  ctl_ctlbtn: Button|None = None
  ctl_ctlbtn_pin: digitalio.DigitalInOut|None = None
  ctl_ctlmode: bool = False
  ctl_params_dirty: bool = False
  ctl_last_active_at: int|None = None
  ctl_param_selected: int|None = None

  tof: adafruit_vl53l0x.VL53L0X|None = None
  tof_last_trigger_at: int|None = None
  tof_armed: bool = True
  tof_trigger_start: int|None = None

  tstat: BaseThermostat|None = None

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
    if settings.esp_enabled:
      print('Initializing ESP-NOW wireless interface...')
      try:
        import espnow
        from wifi import radio
        radio.enabled = True
        radio.start_ap(' ', '', channel=settings.esp_channel, max_connections=0)
        radio.stop_ap()
        self.enow = espnow.ESPNow()
        print(f'ESP-NOW active. MAC Address: {utils.btomacstr(radio.mac_address)}')
      except Exception as e:
        print(f'Failed to initialize ESP-NOW: {e!r}')
      else:
        self.enow_peers = []
        for i, macstr in enumerate(settings.esp_peers):
          print(f'Initializing peer {i}: {macstr}')
          peer = espnow.Peer(mac=utils.macstrtob(macstr))
          self.enow.peers.append(peer)
          self.enow_peers.append(peer)
    # Initialize the buttons
    if settings.button_enabled:
      self.btn_button_pin = self.make_buttonio(settings.button_pin)
      self.btn_button = Button(
        self.btn_button_pin,
        long_duration_ms=settings.button_long_press_secs * 1000)
    if settings.ctlbtn_enabled:
      self.ctl_ctlbtn_pin = self.make_buttonio(settings.ctlbtn_pin)
      self.ctl_ctlbtn = Button(
        self.ctl_ctlbtn_pin,
        long_duration_ms=settings.button_long_press_secs * 1000)
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
        self.synth_waveforms = samples.create_wave_tables()
        default_waveform = settings.synth_default_waveform
        if default_waveform not in range(len(self.synth_waveforms)):
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
            selected=min(10, max(1, int(settings.synth_volume))))}
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
        self.paramsmap[PARAMID_TOF_THRESHOLD] = ConfigParam(
          id=PARAMID_TOF_THRESHOLD,
          name='tof_threshold',
          title='TOF Threshold',
          choices=range(16),
          selected=min(15, max(0, int(settings.tof_threshold))))
        self.params.append(self.paramsmap[PARAMID_TOF_THRESHOLD])
      except Exception as e:
        print(f'Failed to initialize TOF sensor: {e!r}')
      else:
        print(f'Initialized TOF sensor')
    if settings.tstat_enabled:
      print(f'Initializing tstat')
      try:
        if settings.tstat_i2c:
          self.i2c = self.i2c or board.I2C()
          self.tstat = ThermostatI2C(
            self.i2c,
            address=settings.tstat_address,
            heater_delay_secs=settings.tstat_heater_delay_secs,
            heater_cooldown_secs=settings.tstat_heater_cooldown_secs)
        else:
          self.tstat = ThermostatLocal(
            pin_desired=settings.tstat_local_pin_desired,
            pin_heater_relay=settings.tstat_local_pin_heater_relay,
            pin_fan_relay=settings.tstat_local_pin_fan_relay,
            pin_heat_switch=settings.tstat_local_pin_heat_switch,
            pin_fan_switch=settings.tstat_local_pin_fan_switch,
            pin_onewire_bus=settings.tstat_local_pin_onewire_bus,
            heater_delay_secs=settings.tstat_heater_delay_secs,
            heater_cooldown_secs=settings.tstat_heater_cooldown_secs)
      except Exception as e:
        print(f'Failed to initialize tstat: {e!r}')
    if settings.sd_enabled:
      print(f'Initializing SD')
      self.spi = self.spi or board.SPI()
      self.sd = SDHelper(
        spi=self.spi,
        pin_cs=settings.sd_pin_cs,
        path=settings.sd_path,
        after_mount=self.sd_after_mount,
        before_umount=self.sd_before_umount,
        after_umount=self.sd_after_umount)
      if self.sd.ensure_ready():
        self.sd_load_saved_state()
    if self.synth:
      self.synth_init_envelope()

  def deinit(self) -> None:
    if self.enow is not None:
      self.enow.deinit()
    if self.btn_button_pin:
      self.btn_button_pin.deinit()
    if self.ctl_ctlbtn_pin:
      self.ctl_ctlbtn_pin.deinit()
    if self.oled:
      self.oled.deinit()
    if self.audio:
      self.audio.deinit()
    if self.audio_fp:
      try:
        self.audio_fp.close()
      except:
        pass
    if self.sd:
      self.sd.deinit()
    if self.synth_note_queue:
      try:
        self.synth_note_queue.close()
      except:
        pass
    if self.oled or settings.oled_enabled:
      import displayio
      displayio.release_displays()
    if self.tstat:
      self.tstat.deinit()
    self.i2c = None
    self.spi = None
    self.params = None
    self.paramsmap = None
    self.oled = None
    self.sd = None
    self.enow = None
    self.enow_peers = None
    self.audio = None
    self.audio_wav_files = None
    self.audio_busy = False
    self.audio_stop_at = None
    self.audio_wave = None
    self.audio_fp = None
    self.synth = None
    self.synth_envelope = None
    self.synth_note_queue = None
    self.synth_waveforms = None
    self.synth_active_waveform = None
    self.synth_sample = None
    self.btn_button = None
    self.btn_button_pin = None
    self.ctl_ctlbtn = None
    self.ctl_ctlbtn_pin = None
    self.ctl_ctlmode = False
    self.ctl_params_dirty = False
    self.ctl_param_selected = None
    self.ctl_last_active_at = None
    self.tof = None
    self.tof_last_trigger_at = None
    self.tof_armed = True
    self.tof_trigger_start = None
    self.tstat = None

  @property
  def synth_default_waveform(self) -> int|None:
    try:
      return self.paramsmap[PARAMID_DEFAULT_WAVEFORM].selected
    except KeyError:
      pass

  @property
  def tof_threshold_mm(self) -> int:
    try:
      return self.paramsmap[PARAMID_TOF_THRESHOLD].value * settings.tof_threshold_scale
    except KeyError:
      return settings.tof_threshold * settings.tof_threshold_scale

  def loop(self) -> None:
    if self.audio:
      self.run_audio()
    if self.enow is not None:
      self.run_enow()
    if self.ctl_ctlbtn:
      self.run_ctl()
    if self.btn_button:
      self.run_btn()
    if self.tof:
      self.run_tof()
    if self.tstat:
      self.run_tstat()

  def app_execute(self, cmdbuf: bytes) -> None:
    if not cmdbuf:
      return
    try:
      command = cmdbuf[0]
      # Command 0x05: Play random track
      if command == 0x05:
        self.audio_play_random_wav_file()            
      # Command 0x06: Play track by array index [command, index_byte]
      elif command == 0x06:
        if len(cmdbuf) < 2:
          raise ValueError('Malformed command: missing index byte')
        self.audio_play_wav_by_index(cmdbuf[1])
      # Command 0x0A: Trigger Pre-Programmed Sequence [command, tune_id]
      elif command == 0x0A:
        if len(cmdbuf) < 2:
          raise ValueError('Malformed command: missing index byte')
        self.synth_play_prefab_tune(cmdbuf[1], self.synth_default_waveform)
      # Command 0x0B: Trigger Pre-Programmed Sequence with Waveform [command, tune_id, waveform_id]
      elif command == 0x0B:
        if len(cmdbuf) < 3:
          raise ValueError('Malformed command: missing index or waveform byte')
        self.synth_play_prefab_tune(cmdbuf[1], cmdbuf[2])
    except Exception as e:
      print(f'Error executing command: {e!r}')

  def enow_send(self, payload: bytes, peerid: int|Literal['*']) -> None:
    if self.enow is None:
      print(f'Cannot send payload: ESP-NOW not initialized')
      return
    if peerid == '*':
      peer = None
    else:
      try:
        peer = self.enow_peers[peerid]
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

  def send_or_execute(self, payload: bytes, peerid: int|Literal['*']|None) -> None:
    if peerid is None:
      self.app_execute(payload)
    else:
      self.enow_send(payload, peerid)

  def run_audio(self) -> None:
    if self.synth:
      if self.audio_stop_at is not None:
        if ticks_ms() >= self.audio_stop_at:
          if self.synth_sample:
            print('Releasing synth note')
            self.synth.release(self.synth_sample)
            self.synth_sample = None
          else:
            self.audio.stop()
          self.audio_stop_at = None
          if self.synth_note_queue:
            self.synth_play_next_queued_note()
    try:
      playing = self.audio.playing
    except ValueError:
      # Corner case: ValueError: Object has been deinitialized and can no longer be used. Create a new object.
      return
    if not playing:
      if self.synth_sample:
        print(f'Sample playing complete')
        self.synth_sample = None
      elif self.audio_fp:
        print('Playback complete')
        self.audio_fp.close()
        self.audio_fp = None
        self.audio_wave = None
      if self.audio_busy:
        self.audio_busy = False

  def run_enow(self) -> None:
    if not self.enow:
      return
    packet = self.enow.read()
    if packet and packet.msg:
      print(f'Received packet from {utils.btomacstr(packet.mac)}')
      self.app_execute(packet.msg)

  def run_btn(self) -> None:
    self.btn_button.update()
    if self.btn_button.long_press:
      self.btn_handle_button_long_press()
    elif self.btn_button.short_count:
      self.btn_handle_button_short_press(self.btn_button.short_count)

  def run_ctl(self) -> None:
    self.ctl_check_ctlmode_idle()
    self.ctl_ctlbtn.update()
    if self.ctl_ctlbtn.long_press:
      self.ctl_handle_ctlbtn_long_press()
    elif self.ctl_ctlbtn.short_count:
      self.ctl_handle_ctlbtn_short_press(self.ctl_ctlbtn.short_count)

  def run_tof(self) -> None:
    distance = max(0, self.tof.range + settings.tof_offset_mm)
    if distance >= self.tof_threshold_mm:
      self.tof_armed = True
      self.tof_trigger_start = None
      return
    if not self.tof_armed:
      return
    current_time = ticks_ms()
    debounce = settings.tof_debounce_secs * 1000
    # Debounce
    if self.tof_trigger_start is None:
      self.tof_trigger_start = current_time
    if ticks_diff(current_time, self.tof_trigger_start) < debounce:
      return
    # Cooldown
    if (
      self.tof_last_trigger_at is not None and
      ticks_diff(current_time, self.tof_last_trigger_at) < debounce):
      return
    print(f'Motion Detected! Target Range: {distance}mm')
    self.tof_last_trigger_at = current_time
    self.tof_armed = False
    self.send_or_execute(settings.tof_payload, settings.tof_peer)

  def run_tstat(self) -> None:
    self.tstat.update()

  def btn_handle_button_long_press(self) -> None:
    self.btn_handle_button_short_press(1)

  def btn_handle_button_short_press(self, count: int) -> None:
    if self.ctl_ctlmode:
      if self.ctl_param_selected is None:
        print(f'Warning: no param selected')
        return
      param = self.params[self.ctl_param_selected]
      param.adjust(count)
      self.oled_draw_display()
      self.ctl_last_active_at = ticks_ms()
      self.ctl_params_dirty = True
    else:
      self.send_or_execute(settings.button_payload, settings.button_peer)

  def ctl_check_ctlmode_idle(self) -> None:
    if self.ctl_ctlmode and self.ctl_last_active_at is not None:
      elapsed_ms = ticks_diff(ticks_ms(), self.ctl_last_active_at)
      if elapsed_ms / 1000 > settings.idle_secs:
        self.ctl_ctlmode_exit()

  def ctl_handle_ctlbtn_long_press(self) -> None:
    if self.ctl_ctlmode:
      self.ctl_ctlmode_exit()
    else:
      self.ctl_ctlmode_enter()

  def ctl_handle_ctlbtn_short_press(self, count: int) -> None:
    if not self.ctl_ctlmode:
      return
    self.ctl_param_selected = (count + self.ctl_param_selected) % len(self.params)
    self.oled_draw_display()
    self.ctl_last_active_at = ticks_ms()

  def ctl_ctlmode_enter(self) -> None:
    print('Entering Control Mode')
    self.ctl_param_selected = 0
    self.ctl_ctlmode = True
    self.ctl_params_dirty = False
    self.oled_draw_display()
    self.ctl_last_active_at = ticks_ms()

  def ctl_ctlmode_exit(self) -> None:
    print(f'Exiting Control Mode')
    self.ctl_param_selected = None
    self.ctl_ctlmode = False
    self.ctl_last_active_at = None
    if self.ctl_params_dirty:
      if self.synth:
        self.synth_init_envelope()
      if self.sd:
        self.sd_save_state()
    if self.oled:
      self.oled.sleep()

  def oled_draw_display(self) -> None:
    if not self.oled:
      return
    if self.ctl_param_selected is None:
      print(f'Warning: no param selected')
      return
    param = self.params[self.ctl_param_selected]
    self.oled.header = param.title
    self.oled.body = str(param.value)
    self.oled.wake()

  def sd_load_saved_state(self) -> None:
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
    self.app_load_state(bytegen())

  def sd_save_state(self) -> None:
    filepath = f'{settings.sd_path}/{STATE_FILENAME}'
    tmppath = f'{filepath}.tmp'
    print('Saving configuration state...')
    try:
      buf = bytearray(2)
      with open(tmppath, 'wb') as fp:
        for param in self.params:
          buf[0] = param.id
          buf[1] = param.selected
          fp.write(buf)
      os.rename(tmppath, filepath)
      print('State saved successfully')
    except Exception as e:
      print(f'Error saving state: {e!r}')
      try:
        os.remove(tmppath)
      except OSError:
        pass

  def app_load_state(self, buf: Iterable[int]) -> None:
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

  def synth_init_envelope(self) -> None:
    print(f'Initializing synth envelope')
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
    
  def audio_reload_wav_files(self) -> None:
    if not settings.audio_enabled or not settings.sd_enabled:
      return
    wav_files = [
      f for f in os.listdir(settings.sd_path)
      if f.lower().endswith('.wav') and f[0] not in '._']
    wav_files.sort()
    self.audio_wav_files = tuple(wav_files)
    print('--- Reloaded WAV files from SD ---')
    for f in self.audio_wav_files:
      print(f' - {f}')
    print(f'{len(self.audio_wav_files)} tracks indexed')

  def audio_play_random_wav_file(self) -> None:
    if not self.sd or not self.sd.ensure_ready():
      print('Cannot play wav file: No SD card detected')
      return
    if not self.audio_wav_files:
      print('No wav files available')
      return
    # Pick a random filename from our clean list
    filename = random.choice(self.audio_wav_files)
    self.audio_play_wav_by_filename(filename)

  def audio_play_wav_by_index(self, index: int) -> None:
    if not self.sd or not self.sd.ensure_ready():
      print('Cannot play wav file: No SD card detected')
      return
    try:
      filename = self.audio_wav_files[index]
    except IndexError:
      print(f'No wav file at index {index}')
    except TypeError:
      print('No wav files available')
    else:
      self.audio_play_wav_by_filename(filename)

  def audio_play_wav_by_filename(self, filename: str) -> None:
    if not self.audio:
      print(f'Cannot play wav file: No audio initialized')
      return
    if self.audio_busy:
      print(f'Audio busy, not playing wav')
      return
    self.audio_stop_at = None
    fullpath = f'{settings.sd_path}/{filename}'
    print(f'Opening: {filename}')
    try:
      from audiocore import WaveFile
      self.audio_fp = open(fullpath, 'rb')
      self.audio_wave = WaveFile(self.audio_fp)
      print(f'Playing track...')
      self.audio.play(self.audio_wave)
      self.audio_busy = True
    except Exception as e:
      print(f'Error playing {filename}: {e!r}')

  def synth_play_prefab_tune(self, index: int, waveform: int) -> None:
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
    if not 0 <= waveform < len(self.synth_waveforms):
      print(f'Invalid {waveform=}, using default')
      waveform = self.synth_default_waveform
    if self.audio_busy:
      print('Audio busy, not playing tune')
      return
    self.synth_active_waveform = waveform
    self.synth_note_queue = utils.couples(tune)
    self.audio.play(self.synth)
    self.audio_busy = True
    self.synth_play_next_queued_note()

  def synth_play_next_queued_note(self) -> None:
    try:
      midi_note, duration_scalar = next(self.synth_note_queue)
    except StopIteration:
      print(f'Tune playback complete')
      self.synth.release_all()
      self.synth_note_queue = None
      self.audio_busy = False
      return
    try:
      frequency = utils.notetofreq(midi_note)
      from synthio import Note
      self.synth_sample = Note(
        frequency=frequency,
        envelope=self.synth_envelope,
        waveform=self.synth_waveforms[self.synth_active_waveform])
      self.synth.press(self.synth_sample)
      duration_secs = duration_scalar * DURATION_SCALE
      self.audio_stop_at = ticks_ms() + int(duration_secs * 1000)
    except Exception as e:
      print(f'Queue execution error: {e!r}')
      try:
        self.synth_note_queue.close()
      except:
        pass
      self.synth_note_queue = None
      return

  def sd_after_mount(self) -> None:
    self.audio_reload_wav_files()

  def sd_before_umount(self) -> None:
    self.run_audio()

  def sd_after_umount(self) -> None:
    self.audio_wav_files = None

  def make_buttonio(self, pin: str|Pin) -> digitalio.DigitalInOut:
    io = digitalio.DigitalInOut(as_pin(pin))
    io.direction = digitalio.Direction.INPUT
    io.pull = digitalio.Pull.UP
    return io

app: App = App()

if __name__ == '__main__':
  app.main()
