from __future__ import annotations

import fontio
import terminalio
import displayio
from busdisplay import BusDisplay
from i2cdisplaybus import I2CDisplayBus

__all__ = ('ConfigParam', 'OledDisplay',)

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
    font: fontio.FontProtocol = terminalio.FONT,
  ) -> None:
    from adafruit_display_text.label import Label
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

# Typing
try:
  from adafruit_display_text.label import Label
  from typing import Any, Sequence
except ImportError:
  pass
