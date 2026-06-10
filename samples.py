from __future__ import annotations

import array
import math
import audiocore

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
