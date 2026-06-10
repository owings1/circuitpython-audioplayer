audio_pin_bclk = 'D0'
audio_pin_lrc = 'D1'
audio_pin_din = 'D2'

button_pin = 'D7'
button_long_press_secs = 1
button_payload = b'\x05'
ctlbtn_enabled = True
ctlbtn_pin = 'D6'

sd_enabled = True
sd_pin_cs = 'D3'
sd_path = '/sd'

oled_enabled = True
oled_driver = 'SSD1306'
oled_address = 0x3c
oled_width = 128
oled_height = 64
oled_line_spacing = 4
oled_x_offset = 0

esp_enabled = True
esp_channel = 6

sample_amplitude_max = 25_000

synth_attack_time = 0.02
synth_decay_time = 0.0
synth_attack_level = 1.0
synth_sustain_level = 1.0
synth_release_time = 0.02
synth_default_waveform = 0x00

prefab_tunes = [
  b'\x3c\x02\x40\x02\x43\x02\x48\x06',
  b'\x45\x03\x43\x03\x41\x03\x00\x03\x40\x08',
  (
    # Phrase 1: Hap-py Birth-day to you
    b'\x43\x03\x43\x02\x45\x05\x43\x05\x48\x05\x47\x0a'
    # Phrase 2: Hap-py Birth-day to you
    b'\x43\x03\x00\x01\x43\x02\x45\x05\x43\x05\x4a\x05\x48\x0a'
    # Phrase 3: Hap-py Birth-day dear [Name]
    b'\x43\x03\x00\x01\x43\x02\x4f\x05\x4c\x05\x48\x05\x47\x05\x45\x05'
    # Phrase 4: Hap-py Birth-day to you
    b'\x4d\x03\x00\x01\x4d\x02\x4c\x05\x48\x05\x4a\x05\x48\x0a'
  ),
]

loop_delay_secs = 0.001
idle_secs = 10

# xmitter only
receiver_mac = '00:00:00:00:00:00'
tof_enabled = False
tof_payload = b'\x05'
tof_threshold_mm = 400
tof_cooldown_secs = 2.5
tof_debounce_secs = 0.05