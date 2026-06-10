from __future__ import annotations

import array
import math
import audiocore
from micropython import const

from utils import settings

def generate_sine_wave(frequency: int = 440, sample_rate: int = 22050) -> audiocore.RawSample:
  "Generates a continuous 16-bit PCM mono raw sine wave loop block."
  # Calculate how many data samples are needed to complete one perfect cycle wave
  length = int(sample_rate / frequency)
  # Create a signed 16-bit ('h') array buffer matching that wave period length
  raw_buffer = array.array('h', [0] * length)
  # Populate the array with sine values, avoiding clipping with an amplitude max of 25000
  for i in range(length):
    raw_buffer[i] = int(settings.sample_amplitude_max * math.sin(2 * math.pi * i / length))  
  return audiocore.RawSample(raw_buffer, sample_rate=sample_rate)


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