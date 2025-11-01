#!/usr/bin/env python3
# this is the main final application file. it handles the main loop, the display, the user input and ties everything together
# it also contains most of the GPIO setup, so look here for that when connecting buttons and encoders
# it also contains MCP3008 potentiometer setup for volume control

import os, signal
import math
import sys
import time
import subprocess
import schedule
import glob
import random
import requests
import threading
import alsaaudio

from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from gpiozero import RotaryEncoder, Button, DigitalInputDevice

signal.signal(signal.SIGINT, signal.default_int_handler)
#spi_lock = threading.Lock()

from alarm import Alarm
from icons import get_bell_bitmap, get_download_bitmap, get_sunrise_bitmap
from spotify_service import SpotifyService
from light_control import sunrise_effect
import light_control

# e-Paper library path - change this if the path is different on your system or if you use a different screen size
# make sure the GPIO pins are correct in epdconfig.py
sys.path.append('~/e-Paper/RaspberryPi_JetsonNano/python/lib')
from waveshare_epd import epd3in7

#potentiometer setup
from gpiozero import MCP3008

##--- Class definitions ---##
class State(Enum):
    CLOCK        = auto()
    MENU         = auto()
    SET_HOUR     = auto()
    SET_MINUTE   = auto()
    SET_SNOOZE   = auto()
    PLAYBACK     = auto()
    ALARM        = auto()
    ALARM_OFF    = auto()
    SELECT_PL    = auto()
    DOWNLOAD_PL  = auto()
    SET_SUNRISE  = auto()
    SELECT_SOUND = auto()
    DOWNLOAD_FAILED = auto()

class Display:
    def __init__(self):
        # initialize e-paper
        self.epd = epd3in7.EPD()
        self.epd.init(0); self.epd.Clear(0xFF, 0)
        self.epd.init(1); self.epd.Clear(0xFF, 1)
        self.width  = self.epd.height
        self.height = self.epd.width

        # fonts
        self.font_time  = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', 120)
        self.font_date  = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf', 30)
        self.small_font = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', 28)
        self.menu_font  = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf', 25)

        # GPIO setup
        self.encoder    = RotaryEncoder(a=17, b=25, max_steps=100)
        self.button     = Button(23, bounce_time=0.1, hold_time=5)
        self.menu_long_press = False
        self.button.when_held = self.handle_menu_long_press
        self.last_steps = self.encoder.steps
        self.encoder.when_rotated = self.on_rotate
        self.button.when_pressed  = self.on_press

        #clock
        self.current_state = State.CLOCK
        self.prev_state    = None
        self.menu_index    = 0
        schedule.every(8).hours.do(self.full_refresh)
        self.alarm   = Alarm()

        # snooze button
        self.snooze_button = Button(24, pull_up=True, bounce_time=0.2, hold_time=2)
        self.snooze_short  = False
        self.snooze_long   = False
        self.snooze_button.when_pressed = self.handle_snooze_short
        self.snooze_button.when_held    = self.handle_snooze_long

        # Spotify integration and playback
        self.spotify  = SpotifyService()
        self.sp_index = 0
        self.download_failed_time = None
        self.max_digital_gain = 20
        self.play_thread = None
        self.stop_play_event = threading.Event()
        self.play_proc = None

        #light
        self.light_switch = DigitalInputDevice(16, pull_up=True, bounce_time=0.2)
        self.light_switch.when_activated   = self.light_on_handler
        self.light_switch.when_deactivated = self.light_off_handler
        self.sunrise_started = False
        self.sunrise_triggered = False
        self.sunrise_cancelled = False
        self.light_on = False

        # rotary buffering
        self._click_buffer    = 0
        self._last_click_time = datetime.now()
        self._click_throttle  = 0.05

        #display blink
        self.last_input  = datetime.now()
        self.blink_state = False
        self.last_blink  = datetime.now()

        # potentiometer
        self.pot = None
        self._pot_active_state = None
        self.last_pot_value = None
        self.last_volume = None
        self.last_pot_check = time.time()
        self.pot_check_interval = 0.1
        self.display_is_refreshing = False
        self._pending_open_pot = False
        self.ALLOWED_POT_STATES = {State.CLOCK, State.PLAYBACK, State.ALARM}

    # --- Draw different screens ----------------------------------------------------------------

    def get_menu_items(self):
        base = [
            f"Set Alarm: {self.alarm.time_str()}",
            "Alarm: ON" if self.alarm.enabled else "Alarm: OFF",
            f"Snooze: {self.alarm.snooze_minutes} min",
            "Sunrise: ON" if self.alarm.sunrise_enabled else "Sunrise: OFF",
            f"Sunrise Start: {self.alarm.sunrise_minutes} min"
        ]
        sound_type = getattr(self.alarm, 'sound_type', 'Classic')
        if sound_type == "Spotify":
            label = f"Alarm Sound: Spotify ({self.alarm.playlist_name or 'None'})"
        else:
            label = f"Alarm Sound: {sound_type}"
        base.append(label)        
        return base + ["Play Test"]

    def draw_clock(self):
        now = datetime.now()
        t   = now.strftime("%H:%M")
        d   = now.strftime("%a, %d %b")
        #clear e-paper
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)

        #draw clock
        tb = draw.textbbox((0,0), t, font=self.font_time)
        x  = (self.width - (tb[2]-tb[0])) // 2
        y  = (self.height - (tb[3]-tb[1]) - 50) // 2 - 10
        draw.text((x,y), t, font=self.font_time, fill=0)

        #draw date
        db = draw.textbbox((0,0), d, font=self.font_date)
        dx = (self.width - (db[2]-db[0])) // 2
        dy = self.height - (db[3]-db[1]) - 28
        draw.text((dx,dy), d, font=self.font_date, fill=0)

        #draw alarm and sunrise icons if enabled
        if self.alarm.enabled:
            bell = get_bell_bitmap()
            bx = self.width - 45
            by = 20
            img.paste(bell, (bx, by))
            if self.alarm.is_snoozed() and self.blink_state:
                txt = "Zzz"
                bb  = draw.textbbox((0,0), txt, font=self.font_date)
                sx  = bx + (bell.width - (bb[2]-bb[0])) // 2
                sy  = by + bell.height + 5
                draw.text((sx, sy), txt, font=self.font_date, fill=0)
            if self.alarm.sunrise_enabled:
                sun = get_sunrise_bitmap()
                sx = bx - sun.width - 10
                sy = by - 5
                img.paste(sun, (sx, sy))
        return img

    def draw_menu(self):
        items = self.get_menu_items()
        img   = Image.new('1', (self.width, self.height), 255)
        draw  = ImageDraw.Draw(img)
        for i, txt in enumerate(items):
            y = 20 + i*35
            prefix = "> " if i == self.menu_index else "   "
            base_indent = 20
            extra_indent = 10 if prefix.strip() == ">" else 0
            x = base_indent + extra_indent
            draw.text((x,y), prefix + txt, font=self.menu_font, fill=0)
        return img

    def draw_time_adjust(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        hh, mm = f"{self.alarm.hour:02}", f"{self.alarm.minute:02}"
        tb = draw.textbbox((0,0), "00:00", font=self.font_time)
        x0 = (self.width - (tb[2]-tb[0])) // 2
        y0 = (self.height - (tb[3]-tb[1])-50) // 2 - 10
        draw.text((x0,y0), hh, font=self.font_time, fill=0)
        cx = x0 + draw.textbbox((0,0), hh, font=self.font_time)[2] + 2
        draw.text((cx,y0), ":", font=self.font_time, fill=0)
        mx = cx + draw.textbbox((0,0), ":", font=self.font_time)[2] + 2
        draw.text((mx,y0), mm, font=self.font_time, fill=0)
        if self.current_state == State.SET_HOUR:
            hb = draw.textbbox((x0,y0), hh, font=self.font_time)
            draw.line((hb[0], hb[3]+2, hb[2], hb[3]+2), fill=0)
        elif self.current_state == State.SET_MINUTE:
            mb = draw.textbbox((mx,y0), mm, font=self.font_time)
            draw.line((mb[0], mb[3]+2, mb[2], mb[3]+2), fill=0)
        return img

    def draw_set_snooze(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        s = f"{self.alarm.snooze_minutes} min"
        tb = draw.textbbox((0,0), s, font=self.font_time)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height - (tb[3]-tb[1])) // 2
        draw.text((x,y), s, font=self.font_time, fill=0)
        return img

    def draw_alarm_off(self):
        #flashes when alarm is disabled with snooze button long hold
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        txt = "Alarm Disabled"
        tb = draw.textbbox((0,0), txt, font=self.small_font)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height - (tb[3]-tb[1])) // 2
        draw.text((x,y), txt, font=self.small_font, fill=0)
        return img

    def draw_playback(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        sound_type = getattr(self.alarm, 'sound_type', 'Classic')
        if sound_type == "Spotify":
            label = self.alarm.playlist_name or 'None'
        else:
            label = sound_type
        txt1 = f"Playing: {label}"
        txt2 = "Hold 'Select' to return"
        tb1 = draw.textbbox((0,0), txt1, font=self.font_date)
        tb2 = draw.textbbox((0,0), txt2, font=self.font_date)
        x1 = (self.width - (tb1[2]-tb1[0])) // 2
        y1 = self.height // 2 - 30
        x2 = (self.width - (tb2[2]-tb2[0])) // 2
        y2 = self.height // 2 + 10
        draw.text((x1, y1), txt1, font=self.font_date, fill=0)
        draw.text((x2, y2), txt2, font=self.font_date, fill=0)
        return img

    def draw_set_sunrise(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        s = f"{self.alarm.sunrise_minutes} min"
        tb = draw.textbbox((0,0), s, font=self.font_time)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height - (tb[3]-tb[1])) // 2
        draw.text((x,y), s, font=self.font_time, fill=0)
        return img

    def draw_alarm(self):
        #screen when alarm is going off
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)

        draw.text((15, 10), "Good Morning!", font=self.font_date, fill=0)

        #draw clock
        now = datetime.now()
        t = now.strftime("%H:%M")
        tb = draw.textbbox((0,0), t, font=self.font_time)
        x  = (self.width - (tb[2]-tb[0])) // 2
        y  = (self.height - (tb[3]-tb[1])) // 2 - 10
        draw.text((x,y), t, font=self.font_time, fill=0)

        #draw date
        d   = now.strftime("%a, %d %b")
        db = draw.textbbox((0,0), d, font=self.font_date)
        dx = (self.width - (db[2]-db[0])) // 2
        dy = self.height - (db[3]-db[1]) - 25
        draw.text((dx,dy), d, font=self.font_date, fill=0)
        
        #icon anchor
        bell = get_bell_bitmap()
        bx = self.width - bell.width - 20
        by = 25

        #blinking bell
        if self.blink_state:
            img.paste(bell, (bx, by))

        #draw sunrise if enabled
        if self.alarm.sunrise_enabled:
            sun = get_sunrise_bitmap()
            sx = bx - sun.width - 15
            sy = by - 5
            img.paste(sun, (sx, sy))
        return img

    def draw_playlist_selector(self):
        #for spotify polaylist selection
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        icon = get_download_bitmap().resize((16, 16), Image.NEAREST)

        # Precompute the height of a text line
        tb = draw.textbbox((0, 0), "Ay", font=self.menu_font)
        line_height = tb[3] - tb[1]

        for i, (name, pid) in enumerate(self.display_playlists[:8]):
            y = 20 + i * 30
            base_indent = 20
            extra_indent = 10 if i == self.sp_index else 0
            x = base_indent + extra_indent


            # 1) Draw selection marker
            sel = "> " if i == self.sp_index else "   "
            draw.text((x, y), sel, font=self.menu_font, fill=0)
            # Advance x by width of sel
            sel_tb = draw.textbbox((x, y), sel, font=self.menu_font)
            sel_width = sel_tb[2] - sel_tb[0]
            x += sel_width

            # 2) If downloaded, paste icon aligned vertically with text
            if self.spotify.is_downloaded(pid):
                # Center icon vertically on the text baseline
                y_icon = y + (line_height - icon.height) // 2
                img.paste(icon, (x, y_icon))
                x += icon.width + 4  # small gap

            # 3) Draw the playlist name
            draw.text((x, y), name[:20], font=self.menu_font, fill=0)

        return img

    def draw_downloading(self):
        #show downloading screen while waiting for spotify playlist to download
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        draw.text((30,self.height//2-10), "Downloading...", font=self.font_date, fill=0)
        draw.text((30,self.height//2+20), "Hold to cancel",    font=self.font_date, fill=0)
        return img

    def draw_sound_selector(self):
        # select alarm sound type, these options must correspond to the ones in alarm.py
        # these also must have .wav files in the /data/alarms/ folder
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        options = ["Classic", "Nature", "Guitar", "Ambient", "Silent", "Spotify"]
        for i, opt in enumerate(options):
            y = 20 + i * 35
            prefix = "> " if i == getattr(self, "sound_index", 0) else "   "
            base_indent = 20
            extra_indent = 10 if prefix.strip() == ">" else 0
            x = base_indent + extra_indent

            if opt == "Spotify":
                name = self.alarm.playlist_name or "None"
                label = f"{opt}: {name[:18]}"
            else:
                label = opt
            draw.text((x, y), prefix + label, font=self.menu_font, fill=0)
        return img

    def draw_download_failed(self):
        #screen when spotify download fails, automatically falls back to classic alarm
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        msg1 = "Download Failed"
        msg2 = "Using Classic Alarm"
        y = self.height // 2 - 30
        for i, msg in enumerate([msg1, msg2]):
            tb = draw.textbbox((0,0), msg, font=self.font_date)
            x = (self.width - (tb[2]-tb[0])) // 2
            draw.text((x, y + i*35), msg, font=self.font_date, fill=0)
        return img

    # --- Handlers ----------------------------------------------------------------
    # short scripts for handling button presses and encoder rotation
    
    def handle_snooze_short(self):
        #short press snooze button
        self.snooze_short = True

    def handle_snooze_long(self):
        #long press snooze button
        self.snooze_long = True

    def handle_menu_long_press(self):
        #long press on select button
        self.menu_long_press = True

    def on_rotate(self):
        #rotary encoder rotation handler
        current = self.encoder.steps
        delta   = current - self.last_steps
        if delta == 0:
            return
        self.last_steps = current

        #if in clock state the rotate handles brightness adjustment
        #else it is menu navigation 
        if self.current_state == State.CLOCK and self.light_on:
            self.handle_brightness_adjustment()
        else:
            self._click_buffer += delta

        self.last_input     = datetime.now()

    def on_press(self):
        #rotary encoder short button press handler
        self.last_input = datetime.now()
        st = self.current_state

        #short press goes immediately to menu when in clock state
        if st == State.CLOCK:
            self.current_state = State.MENU
            self.menu_index    = 0
            self.last_steps    = self.encoder.steps
            self.render()
            return

        #in the menu short press selects the current item, this is basically the menu tree
        elif st == State.MENU:
            choice = self.get_menu_items()[self.menu_index]

            if choice.startswith("Set Alarm"):
                blank = Image.new('1', (self.width, self.height), 255)
                self.display_partial(blank)
                self.current_state = State.SET_HOUR
                self.last_steps    = self.encoder.steps
                self.prev_state    = None
                self.render()
                return

            elif choice.startswith("Alarm:"):
                if self.alarm.enabled:
                    self.alarm.disable()
                else:
                    self.alarm.enable()
                self.render()
                return

            elif choice.startswith("Snooze:"):
                self.current_state = State.SET_SNOOZE
                self.last_steps    = self.encoder.steps
                self.render()
                return

            elif choice == "Play Test":
                self.current_state = State.PLAYBACK
                if not self.play_proc or self.play_proc.poll() is not None:
                    self.start_alarm_playback()
                self.render()
                return

            elif choice == "Sunrise Start:":
                self.current_state = State.SET_SUNRISE
                self.last_steps    = self.encoder.steps
                self.render()
                return

            elif choice.startswith("Sunrise:"):
                self.alarm.sunrise_enabled = not self.alarm.sunrise_enabled
                self.alarm.save()
                self.render()
                return

            elif choice.startswith("Alarm Sound:"):
                self.current_state = State.SELECT_SOUND
                self.last_steps = self.encoder.steps
                try:
                    self.sound_index = ["Classic", "Nature", "Guitar", "Ambient", "Silent", "Spotify"].index(self.alarm.sound_type or "Classic")
                except ValueError:
                    self.sound_index = 0
                    self.alarm.sound_type = "Classic"
                    self.alarm.save()
                self.render()
                return

        elif st == State.SELECT_SOUND:
            options = ["Classic", "Nature", "Guitar", "Ambient", "Silent", "Spotify"]
            chosen = options[self.sound_index]
            self.alarm.sound_type = chosen
            self.alarm.save()
            if chosen == "Spotify":
                self.current_state = State.SELECT_PL
                self.sp_index      = 0
                self.prev_state    = None
                try:
                    self.display_playlists = self.spotify.enter_playlist_menu()
                except Exception as e:
                    print(f"[Spotify] playlist load failed: {e}")
                    # fall back exactly like your existing download-failed path
                    self.alarm.sound_type = "Classic"
                    self.alarm.save()
                    self.current_state = State.DOWNLOAD_FAILED
                    self.download_failed_time = datetime.now()
                    self.prev_state = None
                    self.render()
                    return
            else:
                self.current_state = State.MENU
            self.render()
            return

        elif st == State.SELECT_PL:
            name, pid = self.spotify.select(self.sp_index)
            if not self.spotify.is_downloaded(pid):
                # record for persistence
                self.alarm.playlist_id = pid
                self.alarm.playlist_name = name
                self.alarm.sound_type = "Spotify"
                self.alarm.save()

                # spawn our background thread (not a Popen)
                print(f"[DEBUG] Selected playlist: {name} ({pid})")
                self.download_thread = self.spotify.download_playlist(pid, name)

                # switch into the downloading screen
                self.current_state = State.DOWNLOAD_PL
            else:
                # If already downloaded, just update alarm settings and return to menu
                self.alarm.playlist_id = pid
                self.alarm.playlist_name = name
                self.alarm.sound_type = "Spotify"
                self.alarm.save()
                self.current_state = State.MENU

            self.prev_state = None
            self.render()
            return

        elif st == State.SET_HOUR:
            self.current_state = State.SET_MINUTE
            self.last_steps    = self.encoder.steps
            self.alarm.save()
        elif st == State.SET_MINUTE:
            self.current_state = State.MENU
            self.last_steps    = self.encoder.steps
            self.alarm.save()
        elif st == State.SET_SNOOZE:
            self.current_state = State.MENU
            self.last_steps    = self.encoder.steps
            self.alarm.save()
        elif st == State.PLAYBACK:
            self.stop_alarm_playback()   # <-- use the proper stop
            self.current_state = State.MENU
            self.render()
            return
        elif choice.startswith("Sunrise:"):
            self.alarm.sunrise_enabled = not self.alarm.sunrise_enabled
            self.alarm.save()
            self.render()
            return
        elif st == State.SET_SUNRISE:
            self.current_state = State.MENU
            self.last_steps    = self.encoder.steps
            self.alarm.save()
        else:
            self.current_state = State.MENU
            self.last_steps    = self.encoder.steps

        self.prev_state = None
        self.render()

# --- Functions -------------------------------------------------------
# short scripts for handling different functions on the clock

    ## pot handler
    def get_volume_percent(self):
        if self.pot is not None:
            return int(self.pot.value * 100)
        return self.last_volume if self.last_volume is not None else 50

    #alarm and spotify playback
    def _spawn_aplay_and_wait(self, filepath: str):
        # Blocks until the process exits
        self.play_proc = subprocess.Popen(
            ["aplay", "-q", filepath],
            start_new_session=True
        )
        try:
            self.play_proc.wait()
        finally:
            self.play_proc = None

    def _loop_single_file(self, filepath: str):
        # Loop one file forever (until stop_play_event is set)
        while not self.stop_play_event.is_set():
            self._spawn_aplay_and_wait(filepath)

    def _loop_playlist_shuffle(self, files: list[str]):
        # Shuffle list each cycle and play all, forever
        while not self.stop_play_event.is_set():
            order = files[:]  # copy
            random.shuffle(order)
            for fp in order:
                if self.stop_play_event.is_set():
                    break
                self._spawn_aplay_and_wait(fp)

    def get_alarm_tracks(self):
        """
        Return a sorted list of audio files for the current alarm.playlist_id.
        """
        pid = self.alarm.playlist_id
        if not pid:
            return []
        folder = self.spotify.music_dir / pid
        # match both mp3 and m4a
        return sorted(folder.glob("*.mp3")) + sorted(folder.glob("*.m4a"))

    def _get_mixer(self):
        for name in ("Master", "PCM", "Speaker"):
            try:
                return alsaaudio.Mixer(name)
            except alsaaudio.ALSAAudioError:
                try:
                    return alsaaudio.Mixer(name, cardindex=1)
                except alsaaudio.ALSAAudioError:
                    continue
        return None

    def fade_in_volume(self, duration=6.0):
        #fade in volume at start of alarm from zero to current pot setting over duration seconds
        target_percent = max(1, int(self.get_volume_percent()))  # snapshot once
        mixer = self._get_mixer()
        if mixer is None:
            print("Fade: no suitable ALSA mixer found"); return

        try:
            start = int(mixer.getvolume()[0])
        except Exception:
            start = 0
            try: mixer.setvolume(start)
            except: pass

        step_time = 0.02  # 20ms -> smoother
        steps = max(1, int(duration / step_time))
        for n in range(steps + 1):
            t = n / steps
            eased = 0.5 - 0.5 * math.cos(math.pi * t)  # smooth ease-in
            v = int(start + (target_percent - start) * eased)
            try: mixer.setvolume(v)
            except: pass
            time.sleep(step_time)

        try: mixer.setvolume(int(target_percent))
        except: pass

    def start_alarm_playback(self):
        ## main alarm function
        
        # Stop any previous playback thread/process first
        self.stop_alarm_playback()

        sound_type = self.alarm.sound_type or "Classic"

        # Resolve file(s)
        if sound_type == "Silent":
            return
        if sound_type == "Nature":
            files = ["/data/Music/alarm_sounds/birds.wav"]
        elif sound_type == "Classic":
            files = ["/data/Music/alarm_sounds/classic_beep.wav"]
        elif sound_type == "Guitar":
            files = ["/data/Music/alarm_sounds/guitar.wav"]
        elif sound_type == "Ambient":
            files = ["/data/Music/alarm_sounds/ambient.wav"]
        elif sound_type == "Spotify":
            # Collect local tracks (prefer wavs; fall back to mp3/m4a if needed)
            pid = self.alarm.playlist_id
            if not pid:
                # fallback to Classic if no PID
                self.alarm.sound_type = "Classic"
                self.alarm.save()
                return self.start_alarm_playback()
            base = f"{self.spotify.music_dir}/{pid}"
            files = sorted(glob.glob(f"{base}/*.wav"))
            if not files:
                # nothing downloaded -> fallback
                self.alarm.sound_type = "Classic"
                self.alarm.save()
                return self.start_alarm_playback()
        else:
            return

        # Launch background loop thread
        self.stop_play_event.clear()
        if len(files) == 1:
            target = self._loop_single_file
            args = (files[0],)
        else:
            target = self._loop_playlist_shuffle
            args = (files,)

        self.play_thread = threading.Thread(target=target, args=args, daemon=True)
        self.play_thread.start()

        #start existing fade-in
        threading.Thread(target=self.fade_in_volume, daemon=True).start()

    def stop_alarm_playback(self):
        # Signal loop to stop
        self.stop_play_event.set()

        # Kill current aplay if running
        if self.play_proc and self.play_proc.poll() is None:
            try:
                os.killpg(self.play_proc.pid, signal.SIGTERM)
                self.play_proc.wait(timeout=1.0)
            except Exception:
                try:
                    os.killpg(self.play_proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        self.play_proc = None

        # Join background thread
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(timeout=1.0)
        self.play_thread = None

    #light controls
    def light_on_handler(self):
        if self.light_on:
            return
        self.light_on = True
        self.sunrise_cancelled = True
        light_control.turn_on()

    def light_off_handler(self):
        if not self.light_on:
            return
        self.light_on = False
        self.sunrise_cancelled = True
        light_control.turn_off()

    def sunrise_cancelled_check(self):
        return self.sunrise_cancelled

    def handle_brightness_adjustment(self):
        if self.current_state != State.CLOCK:
            return
        if not self.light_on:
            return
        now=time.time()
        if not hasattr(self, 'last_gain_update'):
            self.last_gain_update = 0
        if not hasattr(self, 'last_sent_gain'):
            self.last_sent_gain = None
        if now - self.last_gain_update < 0.3:
            return  # throttle updates

        delta = self.encoder.steps
        if delta == 0:
            return
        self.encoder.steps = 0
        self.last_gain_update = now
        current = light_control.current_gain
        new_gain = max(1, min(100, current + delta * 3))
        if new_gain != self.last_sent_gain:
            light_control.set_brightness(new_gain)
            self.last_sent_gain = new_gain

# --- Display -----------------------------------------------------------------

    # full and partial display refresh. clears all ghosting
    ## be careful, depending on the e-ink model these commands may be different or not exist at all
    ## these are called throughout the logic to clear the display of errant pixels
    def display_full(self, img):
        self.display_is_refreshing = True
        img = img.rotate(180)
        time.sleep(0.02)
        self.epd.init(0); self.epd.Clear(0xFF, 0)
        self.epd.display_1Gray(self.epd.getbuffer(img))
        self.epd.init(1)
        time.sleep(0.02)
        self.display_is_refreshing = False

    def display_partial(self, img):
        self.display_is_refreshing = True
        time.sleep(0.02)
        img = img.rotate(180)
        self.epd.display_1Gray(self.epd.getbuffer(img))
        time.sleep(0.02)
        self.display_is_refreshing = False

    def render(self):
        entering = self.current_state
        leaving  = self.prev_state

        # Handle state-change concerns first
        if entering != leaving:
            # If we’re leaving a pot-enabled state, close MCP *before* display work
            if leaving in self.ALLOWED_POT_STATES and entering not in self.ALLOWED_POT_STATES:
                if self.pot is not None:
                    try:
                        self.pot.close()
                    except Exception as e:
                        print("Error closing MCP3008:", e)
                    self.pot = None
                    self._pot_active_state = None
                time.sleep(0.05)  # small SPI settle so epaper can own the bus

            # If we’re entering a pot-enabled state, we’ll (re)open MCP *after* display
            self._pending_open_pot = (entering in self.ALLOWED_POT_STATES)

            # Clear previous frame artifacts on a state change
            blank = Image.new('1', (self.width, self.height), 255)
            self.display_partial(blank)
            self.prev_state = entering

        if self.current_state == State.CLOCK:
            img = self.draw_clock()
        elif self.current_state == State.MENU:
            img = self.draw_menu()
        elif self.current_state == State.SELECT_PL:
            img = self.draw_playlist_selector()
        elif self.current_state == State.DOWNLOAD_PL:
            img = self.draw_downloading()
        elif self.current_state in (State.SET_HOUR, State.SET_MINUTE):
            img = self.draw_time_adjust()
        elif self.current_state == State.SET_SNOOZE:
            img = self.draw_set_snooze()
        elif self.current_state == State.PLAYBACK:
            img = self.draw_playback()
        elif self.current_state == State.ALARM:
            img = self.draw_alarm()
        elif self.current_state == State.ALARM_OFF:
            img = self.draw_alarm_off()
        elif self.current_state == State.SET_SUNRISE:
            img = self.draw_set_sunrise()
        elif self.current_state == State.SELECT_SOUND:
            img = self.draw_sound_selector()
        elif self.current_state == State.DOWNLOAD_FAILED:
            img = self.draw_download_failed()

        else:
            img = self.draw_menu()

        self.display_partial(img)

        if self._pending_open_pot and self.pot is None:
            try:
                self.pot = MCP3008(channel=0)
                self._pot_active_state = self.current_state
            except Exception as e:
                print(f"Failed to open MCP3008: {e}")
            finally:
                self._pending_open_pot = False

# --- Main Loop --------------------------------------------------------------

    def full_refresh(self):
        self.display_full(self.draw_clock())

    def run(self):
        self.full_refresh()
        self.prev_state = None  # ensure render sees a change
        self.render()
        last_minute = -1
        last_full   = datetime.now()
        alarm_off_time = None

        while True:
            now = datetime.now()
            now_ts= time.time()

            # potentiometer polling
            if(
                self.pot is not None
                and not self.display_is_refreshing
                and now_ts - self.last_pot_check >= self.pot_check_interval
            ):
                volume = max(0, min(100, int(self.pot.value * 100)))
                if self.last_volume is None or abs(volume - self.last_volume) >= 2:
                    try:
                        mixer = alsaaudio.Mixer('Master', cardindex=1)
                        mixer.setvolume(volume)
                        self.last_volume = volume
                    except alsaaudio.ALSAAudioError as e:
                        print("Volume error (Master@card1):", e)
                self.last_pot_check = now_ts

            # snooze short/long handling
            if self.snooze_short:
                self.snooze_short = False
                if self.current_state == State.ALARM:
                    self.stop_alarm_playback()
                    self.alarm.snooze()
                    self.current_state = State.CLOCK
                    self.render()
            if self.snooze_long:
                self.snooze_long = False
                if self.current_state == State.ALARM or (
                    self.alarm.is_snoozed() and self.current_state == State.CLOCK
                ):
                    self.stop_alarm_playback()
                    self.alarm.snooze_until = None
                    self.alarm.just_dismissed=datetime.now()
                    self.alarm.save()

                    alarm_off_time = datetime.now()
                    self.current_state = State.ALARM_OFF
                    self.sunrise_triggered = False
                    self.sunrise_started = False
                    self.render()

            # alarm trigger
            if self.current_state == State.CLOCK and self.alarm.should_trigger():
                self.current_state = State.ALARM
                self.start_alarm_playback()
                self.render()
                self.alarm.alarm_triggered()
            
            #sunrise trigger
            if self.alarm.should_start_sunrise(now) and not self.sunrise_started:
                self.sunrise_started = True
                self.sunrise_triggered = True
                self.sunrise_cancelled = False  # reset in case light was switched off before
                duration = self.alarm.sunrise_minutes*60
                def sunrise_worker():
                    time.sleep(5)
                    sunrise_effect(duration, cancel_fn=self.sunrise_cancelled_check)
                
                threading.Thread(target=sunrise_worker, daemon=True).start()

            # blink Zzz
            if self.alarm.is_snoozed() or self.current_state == State.ALARM:
                if (now - self.last_blink).total_seconds() >= 0.5:
                    self.blink_state = not self.blink_state
                    self.last_blink  = now
                    if self.current_state in [State.CLOCK, State.ALARM]:
                        self.render()
            else:
                self.blink_state = False

            # alarm-off timeout
            if self.current_state == State.ALARM_OFF and alarm_off_time:
                if (now - alarm_off_time).total_seconds() >= 2:
                    self.current_state = State.CLOCK
                    alarm_off_time = None
                    self.render()
            
            # download failed timeout
            if self.current_state == State.DOWNLOAD_FAILED and self.download_failed_time:
                if (now - self.download_failed_time).total_seconds() >= 2:
                    self.current_state = State.SELECT_SOUND
                    self.download_failed_time = None
                    self.render()

            # inactivity timeout
            if (now - self.last_input) > timedelta(seconds=15) \
                and self.current_state not in (
                    State.CLOCK, State.ALARM, State.DOWNLOAD_PL, State.PLAYBACK
                ):
                self.stop_alarm_playback()
                self.current_state = State.CLOCK
                self.full_refresh()
                self.prev_state = None
                last_minute = -1

            # periodic full-refresh
            if (now - last_full) >= timedelta(hours=8):
                self.full_refresh()
                last_full   = now
                last_minute = -1

            # minute tick
            if now.minute != last_minute and self.current_state == State.CLOCK:
                self.display_partial(self.draw_clock())
                last_minute = now.minute

            # buffered rotary handling
            if self._click_buffer and (now - self._last_click_time).total_seconds() >= self._click_throttle:
                delta = self._click_buffer
                self._click_buffer = 0
                self._last_click_time = now

                if self.current_state == State.MENU:
                    items = self.get_menu_items()
                    self.menu_index = (self.menu_index + delta) % len(items)
                elif self.current_state == State.SELECT_PL:
                    if not self.spotify.playlists:
                        self.sp_index = 0
                    else:
                        max_i = min(7,len(self.spotify.playlists) - 1)
                        self.sp_index = max(0, min(max_i, self.sp_index + delta))
                elif self.current_state == State.SET_HOUR:
                    new_h = (self.alarm.hour + delta) % 24
                    self.alarm.set_time(new_h, self.alarm.minute)
                elif self.current_state == State.SET_MINUTE:
                    new_m = (self.alarm.minute + delta) % 60
                    self.alarm.set_time(self.alarm.hour, new_m)
                elif self.current_state == State.SET_SNOOZE:
                    v = self.alarm.snooze_minutes + delta
                    self.alarm.snooze_minutes = max(1, min(30, v))
                    self.alarm.save()
                elif self.current_state == State.SET_SUNRISE:
                    v = self.alarm.sunrise_minutes + delta
                    self.alarm.sunrise_minutes = max(5, min(30, v))
                    self.alarm.save()
                elif self.current_state == State.SELECT_SOUND:
                    options = ["Classic", "Nature", "Guitar", "Ambient", "Silent", "Spotify"]
                    self.sound_index = max(0, min(len(options) - 1, self.sound_index + delta))

                self.render()

            # download completion / cancellation
            if getattr(self, 'download_thread', None):

                #cancel via long-press
                if self.menu_long_press and self.current_state == State.DOWNLOAD_PL:
                    self.menu_long_press = False
                    del self.download_thread
                    self.alarm.sound_type = "Classic"
                    self.alarm.save()
                    self.current_state = State.DOWNLOAD_FAILED
                    self.download_failed_time = datetime.now()          
                    self.prev_state    = None
                    self.last_input = datetime.now()
                    self.render()

                #otherwise wait for the thread to finish
                elif not self.download_thread.is_alive():
                    pid = self.alarm.playlist_id
                    if not self.spotify.is_downloaded(pid):
                        self.alarm.sound_type = "Classic"
                        self.alarm.save()
                        self.current_state = State.DOWNLOAD_FAILED
                        self.download_failed_time = datetime.now()
                    else:
                        self.current_state = State.MENU

                    del self.download_thread
                    self.prev_state    = None
                    self.last_input = datetime.now()
                    self.render()

            if self.menu_long_press:
                self.menu_long_press = False
                if self.current_state == State.PLAYBACK:
                    self.stop_alarm_playback()
                    self.current_state = State.MENU
                    self.prev_state = None
                    self.last_input = datetime.now()  # Reset inactivity timer
                    self.render()

            schedule.run_pending()
            time.sleep(0.05)

if __name__ == "__main__":
    disp = Display()
    try:
        disp.run()
    except KeyboardInterrupt:
        pass
    finally:
        disp.stop_alarm_playback()
        disp.epd.sleep()
        disp.encoder.close()
        disp.button.close()
        disp.snooze_button.close()
        disp.light_switch.close()
