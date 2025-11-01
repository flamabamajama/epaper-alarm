#!/usr/bin/env python3
import sys
import time
import subprocess
from datetime import datetime, timedelta
from enum import Enum, auto
from PIL import Image, ImageDraw, ImageFont
from gpiozero import RotaryEncoder, Button
import schedule

from alarm import Alarm
from icons import get_bell_bitmap  # Ensure icons.py is present and get_bell_bitmap() returns a PIL.Image

# Use correct path to your e-Paper lib
sys.path.append('/home/maxci/e-Paper/RaspberryPi_JetsonNano/python/lib')
from waveshare_epd import epd3in7

class State(Enum):
    CLOCK      = auto()
    MENU       = auto()
    SET_HOUR   = auto()
    SET_MINUTE = auto()
    SET_SNOOZE = auto()
    PLAYBACK   = auto()
    SETTINGS   = auto()
    ALARM      = auto()
    ALARM_OFF  = auto()

class Display:
    def __init__(self):
        self.epd = epd3in7.EPD()
        self.epd.init(1)  # 1 = partial, 0 = full
        self.width = self.epd.width
        self.height = self.epd.height

        self.font_time = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 60)
        self.font_date = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 16)
        self.small_font = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 28)

        self.encoder    = RotaryEncoder(a=17, b=18, max_steps=100)
        self.button     = Button(23, bounce_time=0.2)
        self.last_steps = self.encoder.steps
        self.encoder.when_rotated = self.on_rotate
        self.button.when_pressed  = self.on_press

        self.current_state = State.CLOCK
        self.prev_state    = None
        self.menu_index    = 0

        self.play_proc = None

        self.last_input = datetime.now()
        self.blink_state = False
        self.last_blink = datetime.now()

        self.snooze_button = Button(24, pull_up=True, bounce_time=0.2, hold_time=2)
        self.snooze_short = False
        self.snooze_long = False
        self.snooze_button.when_pressed = self.handle_snooze_short
        self.snooze_button.when_held = self.handle_snooze_long

        schedule.every(8).hours.do(self.full_refresh)
        self.alarm = Alarm()

        self.last_second = datetime.now().second

    # --- Drawing Methods ---

    def get_menu_items(self):
        alarm_time = self.alarm.time_str()
        set_alarm = f"Set Alarm: {alarm_time}"
        toggle = "Alarm: ON" if self.alarm.enabled else "Alarm: OFF"
        snooze = f"Snooze: {self.alarm.snooze_minutes} min"
        return [set_alarm, toggle, snooze, "Play Test", "Settings"]

    def draw_clock(self):
        now = datetime.now()
        t = now.strftime("%H:%M")
        d = now.strftime("%a, %d %b")
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        tb = draw.textbbox((0,0), t, font=self.font_time)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height  - (tb[3]-tb[1]) - 30) // 2
        draw.text((x,y), t, font=self.font_time, fill=0)
        db = draw.textbbox((0,0), d, font=self.font_date)
        dx = (self.width - (db[2]-db[0])) // 2
        dy = self.height - (db[3]-db[1]) - 5
        draw.text((dx,dy), d, font=self.font_date, fill=0)
        if self.alarm.enabled:
            bell_img = get_bell_bitmap()
            bell_x = self.width - bell_img.width - 10
            bell_y = 5
            img.paste(bell_img, (bell_x, bell_y))
            if self.alarm.is_snoozed():
                if self.blink_state:
                    snooze_text = "Zz"
                    bb = draw.textbbox((0, 0), snooze_text, font=self.font_date)
                    snooze_x = self.width - bb[2] - 10
                    snooze_y = 25
                    draw.text((snooze_x, snooze_y), snooze_text, font=self.font_date, fill=0)
        return img

    def draw_menu(self):
        menu_items = self.get_menu_items()
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        for i, txt in enumerate(menu_items):
            y = 20 + i*30
            prefix = "▶ " if i == self.menu_index else "   "
            draw.text((10,y), prefix + txt, font=self.font_date, fill=0)
        return img

    def draw_time_adjust(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        hh = f"{self.alarm.hour:02}"; mm = f"{self.alarm.minute:02}"
        tb = draw.textbbox((0,0), "00:00", font=self.font_time)
        x0 = (self.width - (tb[2]-tb[0])) // 2
        y0 = (self.height  - (tb[3]-tb[1])) // 2
        draw.text((x0,y0), hh, font=self.font_time, fill=0)
        colon_x = x0 + draw.textbbox((0,0), hh, font=self.font_time)[2] + 2
        draw.text((colon_x,y0), ":", font=self.font_time, fill=0)
        m_x = colon_x + draw.textbbox((0,0), ":", font=self.font_time)[2] + 2
        draw.text((m_x,y0), mm, font=self.font_time, fill=0)
        if self.current_state == State.SET_HOUR:
            hb = draw.textbbox((x0,y0), hh, font=self.font_time)
            y_line = hb[3] + 2
            draw.line((hb[0], y_line, hb[2], y_line), fill=0)
        elif self.current_state == State.SET_MINUTE:
            mb = draw.textbbox((m_x,y0), mm, font=self.font_time)
            y_line = mb[3] + 2
            draw.line((mb[0], y_line, mb[2], y_line), fill=0)
        return img

    def draw_set_snooze(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        snooze_str = f"{self.alarm.snooze_minutes} min"
        font = self.font_time
        tb = draw.textbbox((0,0), snooze_str, font=font)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height  - (tb[3]-tb[1])) // 2
        draw.text((x, y), snooze_str, font=font, fill=0)
        return img

    def draw_alarm_off(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        txt = "Alarm Off"
        tb = draw.textbbox((0, 0), txt, font=self.small_font)
        x = (self.width - (tb[2] - tb[0])) // 2
        y = (self.height  - (tb[3] - tb[1])) // 2
        draw.text((x, y), txt, font=self.small_font, fill=0)
        return img

    def draw_playback(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        txt = "Playing Test"
        tb = draw.textbbox((0,0), txt, font=self.font_date)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height  - (tb[3]-tb[1])) // 2
        draw.text((x,y), txt, font=self.font_date, fill=0)
        return img

    def draw_settings(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        draw.text((10,10), "Config:", font=self.font_date, fill=0)
        draw.text((10,40), f"Alarm {self.alarm.time_str()}", font=self.font_date, fill=0)
        return img

    def draw_alarm(self):
        img = Image.new('1', (self.width, self.height), 255)
        draw = ImageDraw.Draw(img)
        txt = "⏰  ALARM!  ⏰"
        tb = draw.textbbox((0,0), txt, font=self.font_time)
        x = (self.width - (tb[2]-tb[0])) // 2
        y = (self.height  - (tb[3]-tb[1])) // 2
        draw.text((x, y), txt, font=self.font_time, fill=0)
        return img

    # --- Button Handling ---

    def handle_snooze_short(self):
        self.snooze_short = True

    def handle_snooze_long(self):
        self.snooze_long = True

    def on_rotate(self):
        current = self.encoder.steps
        delta   = current - self.last_steps
        if delta == 0:
            return
        direction       = 1 if delta > 0 else -1
        self.last_steps = current
        self.last_input = datetime.now()
        if self.current_state == State.MENU:
            menu_items = self.get_menu_items()
            self.menu_index = (self.menu_index + direction) % len(menu_items)
        elif self.current_state == State.SET_HOUR:
            self.alarm.set_time((self.alarm.hour + direction) % 24, self.alarm.minute)
        elif self.current_state == State.SET_MINUTE:
            self.alarm.set_time(self.alarm.hour, (self.alarm.minute + direction) % 60)
        elif self.current_state == State.SET_SNOOZE:
            new_val = self.alarm.snooze_minutes + direction
            new_val = max(1, min(30, new_val))
            self.alarm.snooze_minutes = new_val
            self.alarm.save()
        self.render()

    def on_press(self):
        self.last_input = datetime.now()
        st = self.current_state
        if st == State.CLOCK:
            self.current_state = State.MENU
            self.menu_index    = 0
            self.last_steps    = self.encoder.steps
            self.render()
            return
        elif st == State.MENU:
            menu_items = self.get_menu_items()
            choice = menu_items[self.menu_index]
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
                self.last_steps = self.encoder.steps
                self.render()
                return
            elif choice == "Play Test":
                self.current_state = State.PLAYBACK
                if not self.play_proc or self.play_proc.poll() is not None:
                    self.play_proc = subprocess.Popen([
                        "speaker-test", "-D", "default",
                        "-t", "sine", "-f", "440", "-c", "2"
                    ])
            else:
                self.current_state = State.SETTINGS
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
            self.last_steps = self.encoder.steps
            self.alarm.save()
        elif st == State.PLAYBACK:
            if self.play_proc and self.play_proc.poll() is None:
                self.play_proc.terminate()
            self.current_state = State.MENU
        else:
            self.current_state = State.MENU
            self.last_steps    = self.encoder.steps
        self.prev_state = None
        self.render()

    # --- Display/Refresh Helpers ---
    def display_full(self, img):
        self.epd.init(0)  # 0 = full refresh
        self.epd.display(self.epd.getbuffer(img))
        self.epd.init(1)  # Return to partial refresh mode

    def display_partial(self, img):
        self.epd.display_Partial(self.epd.getbuffer(img))

    def render(self):
        if self.current_state != self.prev_state:
            blank = Image.new('1', (self.width, self.height), 255)
            self.display_partial(blank)
            self.prev_state = self.current_state
        if self.current_state == State.CLOCK:
            img = self.draw_clock()
        elif self.current_state == State.MENU:
            img = self.draw_menu()
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
        else:
            img = self.draw_settings()
        self.display_partial(img)

    def full_refresh(self):
        self.epd.init(0)  # full update
        self.epd.display(self.epd.getbuffer(self.draw_clock()))
        self.epd.init(1)  # switch back to partial for further use

    def run(self):
        self.full_refresh()
        last_minute = -1
        last_full   = datetime.now()
        alarm_off_time = None
        while True:
            now = datetime.now()

            # BUTTON HANDLING: snooze
            if self.snooze_short:
                self.snooze_short = False
                if self.current_state == State.ALARM:
                    self.alarm.snooze()
                    self.current_state = State.CLOCK
                    self.render()
            if self.snooze_long:
                self.snooze_long = False
                if self.current_state == State.ALARM or (self.alarm.is_snoozed() and self.current_state == State.CLOCK):
                    self.alarm.disable()
                    self.current_state = State.ALARM_OFF
                    alarm_off_time = datetime.now()
                    self.render()

            # ALARM CHECK
            if self.current_state == State.CLOCK and self.alarm.should_trigger():
                self.current_state = State.ALARM
                self.render()
                self.alarm.alarm_triggered()

            # Blinking "Zz" indicator (every 0.5s)
            if self.alarm.is_snoozed():
                if (now - self.last_blink).total_seconds() >= 0.5:
                    self.blink_state = not self.blink_state
                    self.last_blink = now
                    if self.current_state == State.CLOCK:
                        self.render()
            else:
                self.blink_state = False

            # Handle ALARM_OFF message timeout
            if self.current_state == State.ALARM_OFF and alarm_off_time is not None:
                if (now - alarm_off_time).total_seconds() >= 2:
                    self.current_state = State.CLOCK
                    alarm_off_time = None
                    self.render()

            # Inactivity timeout
            if (now - self.last_input) > timedelta(seconds=15) and self.current_state not in [State.CLOCK, State.ALARM]:
                if self.play_proc and self.play_proc.poll() is None:
                    self.play_proc.terminate()
                self.current_state = State.CLOCK
                self.full_refresh()
                self.prev_state = None
                last_minute = -1

            # Full refresh every 8 hours
            if (now - last_full) >= timedelta(hours=8):
                self.full_refresh()
                last_full   = now
                last_minute = -1

            # Update the clock display every new minute
            if now.minute != last_minute and self.current_state == State.CLOCK:
                img = self.draw_clock()
                self.display_partial(img)
                last_minute = now.minute

            schedule.run_pending()
            time.sleep(0.05)

if __name__ == "__main__":
    disp = Display()
    try:
        disp.run()
    except KeyboardInterrupt:
        pass
    finally:
        if disp.play_proc and disp.play_proc.poll() is None:
            disp.play_proc.terminate()
        disp.encoder.close()
        disp.button.close()
        disp.snooze_button.close()
        disp.epd.sleep()
