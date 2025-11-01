#!/usr/bin/env python3
# this is the light control file. it handles all communication with the shelly rgbw2 light controller over http requests
# you may need to adjust the shelly ip address below to match your local network setup

import requests
import time
import threading

# Shelly IP and settings
SHELLY_IP = "192.168.178.28"
BASE_URL = f"http://{SHELLY_IP}/light/0"

# Warm orange tone - this is used for the sunrise effect but might be different for your bulb
WARM_ORANGE = (255, 20, 2)
current_gain = 15

# turn on light
def turn_on():
    try:
        resp= requests.get(
            BASE_URL,
            params={
                "turn": "on",
                "mode": "white",
                "temp": 3000,
                "brightness": 15
            }, 
            timeout=2
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[light_control.turn_on] Error: {e}")

# turn off light
def turn_off():
    try:
        resp = requests.get(BASE_URL, params={"turn": "off"}, timeout=2)
        resp.raise_for_status()
    except Exception as e:
        print(f"[light_control.turn_off] Error: {e}")

# change brightness in white mode
def set_brightness(gain):
    global current_gain
    gain = max(1, min(100, gain))  # Clamp between 1 and 100
    current_gain = gain
    try:
        resp = requests.get(
            BASE_URL, 
            params={
                "turn": "on",
                "mode": "white",
                "temp": 3000,
                "brightness": gain
            }, 
            timeout=2
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[light_control.set_brightness] Error: {e}")

# set RGB color and brightness (used for sunrise effect)
def set_rgb_brightness(rgb, brightness):
    """Send an HTTP request to set the RGB color and brightness (gain)"""
    try:
        resp=requests.get(
            BASE_URL,
            params={
                "turn": "on",
                "mode": "color",
                "red": rgb[0],
                "green": rgb[1],
                "blue": rgb[2],
                "brightness": brightness
            }, 
            timeout=2
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[set_rgb_brightness] Error: {e}")

# sunrise effect implementation
## sets a sunrise over a specified duration by gradually changing color and brightness
## if the duration is too short the effect may cause erros with too many requests in a short time

def sunrise_effect(duration_seconds=600, cancel_fn = None):
    print("Sunrise effect started")
    steps = 80
    delay = duration_seconds / steps

    start_r, start_g, start_b, start_br = 255, 15, 0, 5
    end_r, end_g, end_b, end_br = 255, 80, 2, 100

    # Force Shelly into known initial state to avoid flashing
    try:
        requests.get(BASE_URL, params={
            "turn": "on",
            "mode": "color",
            "red": 255,
            "green": 15,
            "blue": 0,
            "brightness": 1
        }, timeout=2)
    except Exception as e:
        print(f"[sunrise_effect prep] Error: {e}")

    time.sleep(0.1)  # small delay to avoid clashing with the loop

    for i in range(1, steps + 1):

        if cancel_fn and cancel_fn():
            print("Sunrise cancelled")
            break


        progress = i/steps
        r = 255
        g = int(start_g + (end_g - start_g) * progress)
        b = int(start_b + (end_b - start_b) * progress)
        br = int(start_br + (end_br - start_br) * progress)
        set_rgb_brightness([r,g,b],br)
        time.sleep(delay)

##for threading support
def run_sunrise_thread(duration_seconds=600):
    """Trigger the sunrise effect as a background thread"""
    thread = threading.Thread(target=sunrise_effect, args=(duration_seconds,))
    thread.daemon = True
    thread.start()
