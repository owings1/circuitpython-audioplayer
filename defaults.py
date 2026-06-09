audio_pin_bclk = 'D0'
audio_pin_lrc = 'D1'
audio_pin_din = 'D2'

button_pin = 'D7'
button_long_press_secs = 1
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

loop_delay_secs = 0.001
idle_secs = 10

# xmitter only
receiver_mac = '00:00:00:00:00:00'
button_payload = b'\x05'
tof_enabled = False
tof_payload = b'\x05'
tof_threshold_mm = 400
tof_cooldown_secs = 2.5