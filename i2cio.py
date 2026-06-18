from __future__ import annotations

import struct

from adafruit_bus_device.i2c_device import I2CDevice
from micropython import const

try:
  from busio import I2C
except ImportError:
  pass

I2CIO_DEFAULT_ADDRESS = const(0x42)
REG_DIGITAL_OUT = const(0x0E)
_MAX_BUFLEN = const(62)

class I2CIO:
  def __init__(self, i2c: I2C, *, address: int|None = None, num_analog: int = 0, num_floats: int = 0) -> None:
    self.i2c_device = I2CDevice(i2c, address or I2CIO_DEFAULT_ADDRESS)
    self._num_floats = num_floats
    bufsize = 2 + 2 * num_analog + 4 * num_floats
    if bufsize > _MAX_BUFLEN:
      raise ValueError(f'Buffer size {bufsize} exceeds max {_MAX_BUFLEN}')
    self.buf = bytearray(bufsize)
    self.wbuf = bytearray(3)
    self.wbuf[0] = REG_DIGITAL_OUT

  @property
  def num_floats(self) -> int:
    return self._num_floats

  @property
  def num_analog(self) -> int:
    return (len(self.buf) - 2 - 4 * self.num_floats) // 2

  def update(self) -> None:
    with self.i2c_device as device:
      device.write_then_readinto(self.wbuf, self.buf)

  def digital_read(self, i: int) -> bool:
    if not 0 <= i < 16:
      raise IndexError('Digital channel must be between 0 and 15')
    digital_mask = struct.unpack_from(b'<H', self.buf, 0)[0]
    return bool(digital_mask & (1 << i))

  def analog(self, i: int) -> int:
    if not 0 <= i < self.num_analog:
      raise IndexError('Analog channel index out of initialized bounds')
    offset = 2 + (i * 2)
    return struct.unpack_from(b'<H', self.buf, offset)[0]

  def digital_write(self, i: int, value: bool) -> None:
    if not 0 <= i < 16:
      raise IndexError('Digital channel must be between 0 and 15')
    out_state = struct.unpack_from(b'<H', self.wbuf, 1)[0]
    if value:
      out_state |= (1 << i)
    else:
      out_state &= ~(1 << i)
    struct.pack_into(b'<H', self.wbuf, 1, out_state)

  def digital_outstate(self, i: int) -> bool:
    if not 0 <= i < 16:
      raise IndexError('Digital channel must be between 0 and 15')
    out_state = struct.unpack_from(b'<H', self.wbuf, 1)[0]
    return bool(out_state & (1 << i))

  def read_float(self, i: int) -> float:
    if not 0 <= i < self.num_floats:
      raise IndexError(f'Float index out of initialized bounds')
    offset = 2 + self.num_analog * 2 + i * 4
    return struct.unpack_from(b'<f', self.buf, offset)[0]