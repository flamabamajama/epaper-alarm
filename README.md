Raspberry Pi E-Paper Alarm Clock

This is a DIY sunrise alarm clock built around a Raspberry Pi Zero 2 W, a Waveshare e-paper display, and a Shelly Duo RGBW light bulb.
It shows the time on an always-on e-ink display, plays alarm sounds through small speakers, and gradually brightens a light before the alarm goes off.
Everything is controlled by physical components such as rotary encoders, toggle switches, and custom circuits.

This is not a plug-and-play project. You will need to set up folders, install dependencies, and wire the hardware. I have probably accidentally left out
specific steps that I forgot I needed, so don't expect every detail to be included here. But please reach out if you have questions. I will try and answer!

FEATURES
- Always-on e-paper display with no glow at night
- Gradual sunrise effect using a Shelly Duo RGBW bulb
- Alarm playback from local sound files or Spotify playlists
- Physical controls: rotary encoders, snooze button, and toggle switch
- Sunrise timing and settings stored persistently in JSON
- Automatic startup on boot using systemd
- Works offline once configured

HARDWARE
- Microcontroller: Raspberry Pi Zero 2 W
- Display: Waveshare 3.7 inch e-paper (black and white model)*
- Audio DAC: Adafruit Speaker Bonnet
- Extended 40 pin stacking header*
- Speakers: Two 4 Ω 3 W full-range speakers connected to bonnet terminals
- Light: Shelly DUO RGBW E27*
- Rotary Encoder 1: Menu navigation
- Potentiometer (for volume control, must then also use an MCP3008 chip, see below, you can also use another rotary encoder for simplicity but will need to change code)
- MCP3008 chip plus breakout board and jumper cables*
- Toggle Switch: simple two state switch for shelly light toggle on and off
- Momentary push button for snooze
- Momentary Push Button, LED, and 100 Ω capcitor to illuminate the screen in the dark
- Power Supply: 5 V 2 A USB adapter
- RTC DS3231

*HARDWARE NOTES
- The model of the waveshare display is important, not only due to the layout of the screen codes, but also because the bigger displays take much longer to refresh
  and they do not all have the same codes to refresh the screen. This display refreshes relatively quickly and has partial and full refresh comands available.
- The Adafruit Speaker Bonnet must be installed first. Use an extended 40-pin stacking header so GPIO pins remain accessible.
- I wrote my code for the RGBW model for the sunrise effect from orange to white. The white only bulb will also work (going from warm to white and increasing brightness) but you will have to change the code yourself
- Detailed MCP3008 wiring guide (Adafruit tutorial): https://learn.adafruit.com/reading-a-analog-in-and-controlling-audio-volume-with-the-raspberry-pi/connecting-the-cobbler-to-a-mcp3008
  
GPIO WIRING
```
| Hardware Component       | Signal | GPIO |
|--------------------------|:------:|:----:|
| E-Paper Display          | RST    | 27   |
|                          | DC     | 22   |
|                          | CS     | 8    |
|                          | BUSY   | 5    |
|                          | PWR    | 12   |
|                          | MOSI   | 10   |
|                          | SCLK   | 11   |
| Rotary Encoder (Menu)    | CLK    | 17   |
|                          | DT     | 25   |
|                          | SW     | 23   |
| Snooze Button            |        | 24   |
| Toggle Switch (Light)    |        | 16   |
| MCP3008 (ADC)            | CLK    | 11   |
|                          | DOUT   | 9    |
|                          | DIN    | 10   |
|                          | CS     | 7    |
| RTC Module               | SDA    | 2    |
|                          | SCL    | 3    |
```


SOFTWARE OVERVIEW
- final.py – Main program, menu system, and alarm logic
- alarm.py – Alarm class and JSON persistence
- light_control.py – Controls Shelly bulb and sunrise effect
- spotify_service.py and auth.py – Spotify integration via Spotipy
- icons.py – Bitmap assets for the e-paper interface
- run-alarm.sh – Systemd launch script
- alarm_settings.json – Persistent alarm configuration
- epdconfig.py – comes in the setup for the waveshare e-ink display, but make sure to edit it with the correct GPIO pins

SETUP NOTES
- Configure Spotify in auth.py if integrating spotify
- Set your Shelly bulb IP in light_control.py
- Assign a static IP to your Shelly bulb
- Run python3 /data/app/final.py manually for testing.

SOUND FILES
- Alarm sounds are expected as .wav files and go in /Music/alarm-sounds/
- I used: classic.wav, ambient.wav, guitar.wav, nature.wav
- The code currently expects these files and also has these names available in the menu
