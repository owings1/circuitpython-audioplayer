from __future__ import annotations

import busio
from adafruit_ticks import ticks_ms, ticks_diff, ticks_add
from digitalio import DigitalInOut, Direction, Pull
from microcontroller import Pin
from micropython import const

try:
  from typing import Any, Callable, Sequence, TYPE_CHECKING
  from adafruit_display_text.label import Label
  from busdisplay import BusDisplay
  from i2cdisplaybus import I2CDisplayBus
  from abc import abstractmethod
except ImportError:
  TYPE_CHECKING = False
  pass

from utils import as_pin

__all__ = ('ConfigParam', 'OledDisplay', 'SDHelper', 'ThermostatI2C', 'ThermostatLocal')

if TYPE_CHECKING:
  __all__ += ('BaseThermostat',)

class ConfigParam:
  def __init__(
    self,
    id: int,
    name: str,
    choices: Sequence,
    selected: int = 0,
    title: str|None = None,
  ) -> None:
    if not choices:
      raise ValueError(f'Empty choices for {name}')
    if len(choices) > 0x100:
      raise ValueError(f'Too many choices for {name}')
    self.id = id
    self.name = name
    self.choices = choices
    self.selected = selected
    self.title = title or name
    self.value

  @property
  def value(self):
    return self.choices[self.selected]

  def adjust(self, i: int) -> None:
    self.selected = (i + self.selected) % len(self.choices)

class OledDisplay:
  display: BusDisplay
  driver: str
  sleepable: bool
  text_width: int
  lines: tuple[Label, Label]

  def __init__(
    self,
    bus: I2CDisplayBus,
    driver: str,
    width: int,
    height: int,
    line_spacing: int = 4,
    x_offset: int = 0,
  ) -> None:
    import displayio
    import terminalio
    from adafruit_display_text.label import Label
    font = terminalio.FONT
    self.driver = driver
    self.display = self.get_display_class(driver)(
      bus=bus,
      width=width,
      height=height)
    self.sleepable = hasattr(self.display, 'sleep')
    self.display.root_group = displayio.Group()
    # Clear display
    blank_palette = displayio.Palette(1)
    blank_palette[0] = 0x0
    self.display.root_group.append(
      displayio.TileGrid(
        displayio.Bitmap(width, height, 1),
        pixel_shader=blank_palette,
        x=x_offset))
    fbb = font.get_bounding_box()
    self.text_width = (width - x_offset) // fbb[0]
    self.lines = (
      Label(
        font,
        text=' ' * self.text_width,
        color=0xffffff,
        x=x_offset,
        y=fbb[1] // 2),
      Label(
        font,
        text=' ' * self.text_width,
        color=0xffffff,
        x=x_offset,
        y=fbb[1] // 2 + fbb[1] + line_spacing + 1))      
    for label in self.lines:
      self.display.root_group.append(label)

  @property
  def header(self) -> str:
    return self.lines[0].text.strip()

  @header.setter
  def header(self, value: str) -> None:
    value = value[:self.text_width]
    if self.lines[0].text != value:
      self.lines[0].text = value

  @property
  def body(self) -> str:
    return self.lines[1].text.strip()

  @body.setter
  def body(self, value: str) -> None:
    value = value[:self.text_width]
    if self.lines[1].text != value:
      self.lines[1].text = value

  def deinit(self) -> None:
    for _ in self.lines:
      self.display.root_group.pop()
    self.display.refresh()
    self.sleep()

  def sleep(self) -> None:
    if self.sleepable:
      self.display.sleep()

  def wake(self) -> None:
    if self.sleepable:
      self.display.wake()

  @property
  def is_awake(self) -> bool:
    return not self.sleepable or self.display.is_awake

  @staticmethod
  def get_display_class(driver: str) -> type[BusDisplay]:
    if driver == 'SSD1305':
      from adafruit_displayio_ssd1305 import SSD1305 as Display
    elif driver == 'SSD1306':
      from adafruit_displayio_ssd1306 import SSD1306 as Display
    else:
      raise ValueError(f'Unsupported driver: {driver}')
    return Display

class SDHelper:
  def __init__(
    self,
    spi: busio.SPI,
    pin_cs: Pin|str,
    path: str = '/sd',
    mntchk_filename: str = '_mountcheck',
    after_mount: Callable[[], None]|None = None,
    before_umount: Callable[[], None]|None = None,
    after_umount: Callable[[], None]|None = None,
  ) -> None:
    self.spi = spi
    self.pin_cs = as_pin(pin_cs)
    self.path = path
    self.mntchk_filename = mntchk_filename
    self.after_mount = after_mount
    self.before_umount = before_umount
    self.after_umount = after_umount
    self.sdcard = None

  def ensure_ready(self) -> bool:
    check_path = f'{self.path}/{self.mntchk_filename}'
    try:
      with open(check_path, 'rb'):
        pass
      return True
    except OSError:
      print('SD card not initialized')
      self.close()
    print('Attempting lazy SD card initialization...')
    try:
      self.close()
      import sdcardio
      import storage
      self.sdcard = sdcardio.SDCard(self.spi, self.pin_cs)
      vfs = storage.VfsFat(self.sdcard)
      storage.mount(vfs, self.path)
      try:
        with open(check_path, 'wb'):
          pass
      except OSError:
        pass
      print('SD Card mounted successfully')
      if self.after_mount:
        self.after_mount()
      return True
    except Exception as e:
      print(f'SD Card connection offline or unreadable: {e!r}')
      self.close()
      return False

  def close(self) -> None:
    if self.before_umount:
      self.before_umount()
    try:
      import storage
      storage.umount(self.path)
    except:
      pass
    if self.sdcard:
      try:
        self.sdcard.deinit()
      except:
        pass
    self.sdcard = None
    if self.after_umount:
      self.after_umount()

  def deinit(self) -> None:
    self.close()

class BaseThermostat:
  def __init__(
    self,
    *,
    heater_delay_secs: float = 30.0,
    heater_cooldown_secs: float = 5.0,
  ) -> None:
    self.heater_relay = False
    self.fan_relay = False
    self.heater_has_run = False
    self.heater_last_on_at = ticks_ms()
    self.heater_delay_secs = heater_delay_secs
    self.heater_cooldown_secs = heater_cooldown_secs
    self._syncio()
    self.print_state()

  if TYPE_CHECKING:
    _analog_resolution: int

    @abstractmethod
    def temperature_c(self) -> float: ...

    @abstractmethod
    def digital_read(self, i: int) -> bool: ...

    @abstractmethod
    def digital_write(self, i: int, value: bool) -> None: ...

    @abstractmethod
    def digital_outstate(self, i: int) -> bool: ...

    @abstractmethod
    def analog_read(self, i: int) -> int: ...

  @property
  def desired(self) -> int:
    return int((self.analog_read(0) / (self._analog_resolution - 1)) * 40) + 50

  @property
  def heat_switch(self) -> bool:
    return self.digital_read(0)

  @property
  def heater_relay(self) -> bool:
    return self.digital_outstate(0)

  @heater_relay.setter
  def heater_relay(self, value: bool) -> None:
    self.digital_write(0, bool(value))

  @property
  def fan_switch(self) -> bool:
    return self.digital_read(1)

  @property
  def fan_relay(self) -> bool:
    return self.digital_outstate(1)

  @fan_relay.setter
  def fan_relay(self, value: bool) -> None:
    self.digital_write(1, bool(value))

  @property
  def temperature(self) -> int:
    return round(self.temperature_c() * 9/5 + 32)

  def update(self) -> None:
    desired = self.desired
    self._syncio()
    change = self._enforce_heater() | self._enforce_fan()
    if change:
      self._syncio()
      self.print_state()
    else:
      if desired != self.desired:
        print(f'Desired change: {self.desired}')

  def _enforce_heater(self) -> bool:
    nowms = ticks_ms()
    change = False
    if self.heat_switch:
      # Heat switch is turned on
      if self.heater_relay:
        # Heater is running
        if self.temperature >= self.desired or self.temperature <= -127:
          # Desired temperature is reached (or bad temperature reading)
          print(f'Turning heater OFF {self.temperature=} {self.desired=}')
          self.heater_relay = False
          change = True
      else:
        # Heater is not running
        if self.desired > self.temperature and self.temperature > -127:
          # Desired temperature is not reached
          # Check delay
          heater_on_at = ticks_add(
            self.heater_last_on_at,
            int(self.heater_delay_secs * 1000))
          if ticks_diff(heater_on_at, nowms) < 0:
            print(f'Turning heater ON {self.temperature=} {self.desired=}')
            self.heater_relay = True
            change = True
    else:
      # Heat switch is turned off
      if self.heater_relay:
        print(f'Turning heater OFF (switch)')
        self.heater_relay = False
        change = True
    if self.heater_relay:
      # Update heater running state
      self.heater_last_on_at = nowms
      self.heater_has_run = True
    return change

  def _enforce_fan(self) -> bool:
    nowms = ticks_ms()
    change = False
    # Determine fan state
    if self.fan_switch:
      # Fan switch is on, or heater is running
      if not self.fan_relay:
        print(f'Turning fan ON (switch)')
        self.fan_relay = True
        change = True
    elif self.heater_relay:
      # heater is running
      if not self.fan_relay:
        print(f'Turning fan ON (heater)')
        self.fan_relay = True
        change = True
    elif self.fan_relay:
      # Fan is running but switch is off, and heater is not running
      if self.heater_has_run:
        # Check cooldown
        fan_off_at = ticks_add(
          self.heater_last_on_at,
          int(self.heater_cooldown_secs * 1000))
        if ticks_diff(fan_off_at, nowms) < 0:
          print(f'Turning fan OFF')
          self.fan_relay = False
          change = True
      else:
        print(f'Turning fan OFF')
        self.fan_relay = False
        change = True
    return change

  def _syncio(self) -> None:
    pass

  def deinit(self) -> None:
    try:
      self.heater_relay = False
      self.fan_relay = False
      self._syncio()
    except Exception as e:
      print(f'Failed to deinit tstat: {e!r}')

  def print_state(self) -> None:
    print(
      f'Thermostat state:\n'
      f'  temperature={self.temperature}\n'
      f'  desired={self.desired}\n'
      f'  heat_switch={+self.heat_switch}\n'
      f'  fan_switch={+self.fan_switch}\n'
      f'  heater_relay={+self.heater_relay}\n'
      f'  fan_relay={+self.fan_relay}')

class ThermostatI2C(BaseThermostat):
  def __init__(
    self,
    i2c: busio.I2C,
    *,
    address: int|None = None,
    **kw
  ) -> None:
    import i2cio
    self.io = i2cio.I2CIO(i2c, address=address, num_analog=1, num_floats=1)
    self._analog_resolution = 0x400
    super().__init__(**kw)

  def temperature_c(self) -> float:
    return self.io.read_float(0)

  def digital_read(self, i: int) -> bool:
    return self.io.digital_read(i)

  def digital_write(self, i: int, value: bool) -> None:
    self.io.digital_write(i, value)

  def digital_outstate(self, i: int) -> bool:
    return self.io.digital_outstate(i)

  def analog_read(self, i: int) -> int:
    return self.io.analog_read(i)

  def _syncio(self) -> None:
    self.io.update()

class ThermostatLocal(BaseThermostat):
  def __init__(
    self,
    *,
    pin_desired: str|Pin,
    pin_heater_relay: str|Pin,
    pin_fan_relay: str|Pin,
    pin_heat_switch: str|Pin,
    pin_fan_switch: str|Pin,
    pin_onewire_bus: str|Pin,
    **kw
  ) -> None:
    from adafruit_onewire.bus import OneWireBus
    from adafruit_ds18x20 import DS18X20
    from utils import SmoothedAnalog
    self._analog_resolution = 0x10000
    self._analogs = self._digital_ins = self._digital_outs = self._owbus = None
    try:
      self._analogs = (
        SmoothedAnalog(pin_desired, alpha=0.2, threshold=0x100),)
      self._digital_ins = (
        DigitalInOut(as_pin(pin_heat_switch)),
        DigitalInOut(as_pin(pin_fan_switch)))
      for io in self._digital_ins:
        io.direction = Direction.INPUT
        io.pull = Pull.UP
      self._digital_outs = (
        DigitalInOut(as_pin(pin_heater_relay)),
        DigitalInOut(as_pin(pin_fan_relay)))
      for io in self._digital_outs:
        io.direction = Direction.OUTPUT
      self._owbus = OneWireBus(as_pin(pin_onewire_bus))
      self._tempsensor = DS18X20(self._owbus, self._owbus.scan()[0])
      super().__init__(**kw)
    except:
      self.deinit()
      raise

  def digital_read(self, i: int) -> bool:
    io = self._digital_ins[i]
    value = io.value
    if io.pull is Pull.UP:
      value = not value
    return value

  def digital_write(self, i: int, value: bool) -> None:
    self._digital_outs[i].value = bool(value)

  def digital_outstate(self, i: int) -> bool:
    return self._digital_outs[i].value

  def analog_read(self, i: int) -> int:
    return self._analogs[i].read()

  def temperature_c(self) -> float:
    return self._tempsensor.temperature

  def deinit(self) -> None:
    super().deinit()
    for ios in (self._analogs, self._digital_ins, self._digital_outs):
      if ios:
        for io in ios:
          io.deinit()
    self._analogs = self._digital_ins = self._digital_outs = None
    if self._owbus:
      self._owbus._ow.deinit()
      self._owbus = None
    self._tempsensor = None
