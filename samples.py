from __future__ import annotations

import array
import math
from micropython import const

def create_wave_tables() -> tuple[array.array, ...]:
  "Generates a collection of basic 512-sample single-cycle wave shapes."
  size = 512
  max_val = 30000 # Leave a tiny bit of headroom below 32767 to avoid clipping
  
  # 1. Sine Wave
  sine_wave = array.array('h', [
    int(max_val * math.sin(2.0 * math.pi * i / size)) for i in range(size)
  ])
  
  # 2. Triangle Wave
  triangle_wave = array.array('h', [0] * size)
  for i in range(size):
    if i < size // 2:
      triangle_wave[i] = int(-max_val + (2.0 * max_val * i / (size // 2)))
    else:
      triangle_wave[i] = int(max_val - (2.0 * max_val * (i - (size // 2)) / (size // 2)))
      
  # 3. Sawtooth Wave
  saw_wave = array.array('h', [
    int(-max_val + (2.0 * max_val * i / size)) for i in range(size)
  ])
  
  # 4. Smooth Square Wave (Bypasses harsh high frequencies)
  square_wave = array.array('h', [
    max_val if i < size // 2 else -max_val for i in range(size)
  ])
  
  return (
    sine_wave,
    triangle_wave,
    saw_wave,
    square_wave)

WTI_SINE = const(0x00)
WTI_TRIANGLE = const(0x01)
WTI_SAW = const(0x02)
WTI_SQUARE = const(0x03)