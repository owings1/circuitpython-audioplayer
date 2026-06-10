from __future__ import annotations

import board
import busio
import digitalio
import espnow
import time
import wifi
from adafruit_debouncer import Button
from adafruit_ticks import ticks_ms, ticks_diff

from utils import as_pin, btomacstr, settings

try:
  import adafruit_vl53l0x
except ImportError:
  pass

class App:
  enow: espnow.ESPNow|None = None
  i2c: busio.I2C|None = None
  receiver_mac: bytes|None = None
  receiver_peer: espnow.Peer|None = None
  button: Button|None = None
  tof: adafruit_vl53l0x.VL53L0X|None = None
  last_tof_trigger_at: int|None = None
  tof_armed: bool = True
  tof_trigger_start: int|None = None
  _button_pin: digitalio.DigitalInOut|None = None

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
    self.receiver_mac = bytes.fromhex(settings.receiver_mac.replace(':', ''))
    print('Initializing Transmitter...')
    wifi.radio.enabled = True
    wifi.radio.start_ap(' ', '', channel=settings.esp_channel, max_connections=0)
    wifi.radio.stop_ap()
    self.enow = espnow.ESPNow()
    self.receiver_peer = espnow.Peer(mac=self.receiver_mac)
    self.enow.peers.append(self.receiver_peer)
    print('Transmitter Ready!')
    print(f'my address: {btomacstr(wifi.radio.mac_address)}')
    print(f'receiver address: {btomacstr(self.receiver_mac)}')
    # Initialize the button
    self._button_pin = digitalio.DigitalInOut(as_pin(settings.button_pin))
    self._button_pin.direction = digitalio.Direction.INPUT
    self._button_pin.pull = digitalio.Pull.UP
    self.button = Button(self._button_pin, long_duration_ms=settings.button_long_press_secs * 1000)
    if settings.tof_enabled:
      import adafruit_vl53l0x
      self.i2c = board.I2C()
      self.tof = adafruit_vl53l0x.VL53L0X(self.i2c)
      # Optimizing sensor performance parameters
      self.tof.measurement_timing_budget = 33000  # High-speed low-latency sampling (33ms)
      
  def deinit(self) -> None:
    if self.enow:
      self.enow.deinit()
    if self.i2c:
      self.i2c.deinit()
    if self._button_pin:
      self._button_pin.deinit()
    self.enow = None
    self.button = None
    self.tof = None
    self.receiver_mac = None
    self.receiver_peer = None
    self.last_tof_trigger_at = None
    self.tof_armed = True
    self.tof_trigger_start = None
    self._button_pin = None

  def loop(self) -> None:
    if self.button:
      self.run_button()
    if self.tof:
      self.run_tof()

  def run_button(self) -> None:
    self.button.update()
    if self.button.short_count or self.button.long_press:
      self.send_payload(settings.button_payload)

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
    self.send_payload(settings.tof_payload)
    self.last_tof_trigger_at = current_time
    self.tof_armed = False

  def send_payload(self, buf: bytes) -> None:
    if self.enow is None:
      return
    print(f'Sending payload {buf.hex()}')
    try:
      self.enow.send(buf, self.receiver_peer)
      print('Sent successfully')
    except Exception as e:
      print(f'Transmission failed: {e!r}')

app: App = App()

if __name__ == '__main__':
  app.main()
