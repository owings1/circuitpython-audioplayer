from __future__ import annotations

import board
from microcontroller import Pin

def as_pin(pin: str|Pin) -> Pin:
  if isinstance(pin, str):
    pin = getattr(board, pin)
  return pin

def btomacstr(mac: bytes) -> str:
  return ':'.join(f"{b:02x}" for b in mac)

def macstrtob(mac: str) -> bytes:
  return bytes.fromhex(mac.replace(':', ''))

def notetofreq(note: int) -> float:
  "Translate MIDI note to exact frequency (Tuning standard A4 = 440Hz)"
  if note <= 0:
    return 0.0
  return 440.0 * (2.0 ** ((note - 69) / 12.0))

def couples(it: Iterable[T]) -> Generator[tuple[T, T]]:
  it = iter(it)
  for a in it:
    try:
      b = next(it)
    except StopIteration:
      break
    yield a, b

class SmoothedInt:
  def __init__(self, *, alpha: float = 1.0, threshold: int = 1, initial: int = 0) -> None:
    self.alpha = alpha
    self.threshold = threshold
    self.smoothed_value = float(initial)
    self.final_locked_value = int(initial)

  def observe(self, raw: int) -> int:
    self.smoothed_value = (self.alpha * raw) + ((1.0 - self.alpha) * self.smoothed_value)
    if (abs(round(self.smoothed_value) - self.final_locked_value) > self.threshold):
      self.final_locked_value = round(self.smoothed_value)
    return self.final_locked_value

class SmoothedAnalog:
  def __init__(self, pin: str|Pin, **kw) -> None:
    from analogio import AnalogIn
    self.io = AnalogIn(as_pin(pin))
    self.sm = SmoothedInt(**kw)

  def read(self) -> int:
    return self.sm.observe(self.io.value)

  def deinit(self) -> None:
    self.io.deinit()

def init_settings(defaults: MT, settings: ModuleType) -> MT:
  for name in defaults.__dict__:
    if not hasattr(settings, name):
      setattr(settings, name, getattr(defaults, name))
  return settings

import defaults
import settings
settings = init_settings(defaults, settings)

def i2c_scan():
  i2c = board.I2C()
  while not i2c.try_lock():
    pass
  try:
    for addr in i2c.scan():
      print(f'{hex(addr)}')
  finally:
    i2c.unlock()

# IDE Environment
try:
  from types import ModuleType
  from typing import Generator, Iterable, TypeVar
  MT = TypeVar('MT', bound=ModuleType)
  T = TypeVar('T')
except ImportError:
  pass
