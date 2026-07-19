from __future__ import annotations

import busio
from adafruit_ticks import ticks_ms, ticks_diff, ticks_add, ticks_less
from digitalio import DigitalInOut, Direction, Pull
from microcontroller import Pin
from micropython import const

try:
  from typing import Any, Callable, Sequence, TYPE_CHECKING
  from abc import abstractmethod
except ImportError:
  TYPE_CHECKING = False
  pass

from utils import as_pin

__all__ = ('Thermostat', 'ThermostatI2C', 'ThermostatLocal')

NULLTEMP = const(-127)

class Thermostat:
  analog_resolution: int
  
  def __init__(
    self,
    *,
    heater_delay_secs: float = 30.0,
    heater_cooldown_secs: float = 5.0,
    desired_scale_coeff: int = 40,
    desired_scale_offset: int = 50,
  ) -> None:
    self.heater_relay = False
    self.fan_relay = False
    self.heater_has_run = False
    self.heater_last_on_at = ticks_ms()
    self.heater_delay_secs = heater_delay_secs
    self.heater_cooldown_secs = heater_cooldown_secs
    self.desired_scale_coeff = desired_scale_coeff
    self.desired_scale_offset = desired_scale_offset
    self.sync_io()
    self.print_state()

  if TYPE_CHECKING:

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
    normalized: float = self.analog_read(0) / (self.analog_resolution - 1)
    return round(normalized * self.desired_scale_coeff) + self.desired_scale_offset

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
    tempc = self.temperature_c()
    if tempc <= NULLTEMP:
      return NULLTEMP
    return round(tempc * 9/5 + 32)

  def update(self) -> None:
    desired = self.desired
    before_state = (self.heater_relay << 0) | (self.fan_relay << 1)
    self.sync_io()
    self.enforce_heater_state()
    self.enforce_fan_state()
    after_state = (self.heater_relay << 0) | (self.fan_relay << 1)
    if before_state != after_state:
      self.sync_io()
      self.print_state()
    else:
      if desired != self.desired:
        print(f'Desired change: {self.desired}')

  def enforce_heater_state(self) -> None:
    if self.heat_switch:
      # Heat switch is turned on
      if self.heater_relay:
        # Heater is running
        if self.temperature >= self.desired or self.temperature == NULLTEMP:
          # Desired temperature is reached (or bad temperature reading)
          print(f'Turning heater OFF {self.temperature=} {self.desired=}')
          self.heater_relay = False
      else:
        # Heater is not running
        if self.desired > self.temperature and self.temperature != NULLTEMP:
          # Desired temperature is not reached
          # Check delay
          heater_on_at = ticks_add(
            self.heater_last_on_at,
            round(self.heater_delay_secs * 1000))
          if ticks_less(heater_on_at, ticks_ms()):
            print(f'Turning heater ON {self.temperature=} {self.desired=}')
            self.heater_relay = True
    else:
      # Heat switch is turned off
      if self.heater_relay:
        print(f'Turning heater OFF (switch)')
        self.heater_relay = False
    if self.heater_relay:
      # Update heater running state
      self.heater_last_on_at = ticks_ms()
      self.heater_has_run = True

  def enforce_fan_state(self) -> None:
    # Determine fan state
    if self.fan_switch:
      # Fan switch is on
      if not self.fan_relay:
        print(f'Turning fan ON (switch)')
        self.fan_relay = True
    elif self.heater_relay:
      # Heater is running
      if not self.fan_relay:
        print(f'Turning fan ON (heater)')
        self.fan_relay = True
    elif self.fan_relay:
      # Fan is running but switch is off, and heater is not running
      if self.heater_has_run:
        # Check cooldown
        fan_off_at = ticks_add(
          self.heater_last_on_at,
          round(self.heater_cooldown_secs * 1000))
        if ticks_less(fan_off_at, ticks_ms()):
          print(f'Turning fan OFF')
          self.fan_relay = False
      else:
        print(f'Turning fan OFF')
        self.fan_relay = False

  def sync_io(self) -> None:
    pass

  def deinit(self) -> None:
    try:
      self.heater_relay = False
      self.fan_relay = False
      self.sync_io()
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

class ThermostatI2C(Thermostat):
  def __init__(
    self,
    i2c: busio.I2C,
    *,
    address: int|None = None,
    **kw
  ) -> None:
    import i2cio
    self.io = i2cio.I2CIO(i2c, address=address, num_analog=1, num_floats=1)
    self.analog_resolution = 0x400
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

  def sync_io(self) -> None:
    self.io.update()

class ThermostatLocal(Thermostat):
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
    self.analog_resolution = 0x10000
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
