# this is the main alarm logic file. it handles everything triggered by the alarm clock
# it saves and loads alarm settings from disk as json to persist setting in case of a power loss

import json
import os
from datetime import datetime, timedelta

class Alarm:
    def __init__(self):
        # basic time/snooze fields
        self.hour = 7
        self.minute = 30
        self.enabled = True
        self.snooze_minutes = 5
        self.snooze_until = None
        self.just_dismissed = None

        #sound options
        self.playlist_id = None
        self.playlist_name = None
        self.sound_type = "Classic"


        #sunrise
        self.sunrise_enabled = False
        self.sunrise_minutes = 15


        # load everything (including playlist) from disk
        self.load()

    def set_time(self, hour, minute):
        self.hour = hour
        self.minute = minute
        self.snooze_until = None
        self.save()

    def enable(self):
        self.enabled = True
        self.snooze_until = None
        self.save()

    def disable(self):
        self.enabled = False
        self.snooze_until = None
        self.save()

    def toggle(self):
        self.enabled = not self.enabled
        self.snooze_until = None
        self.save()

    def snooze(self):
        self.snooze_until = datetime.now() + timedelta(minutes=self.snooze_minutes)
        self.save()

    def is_snoozed(self):
        return self.snooze_until is not None and datetime.now() < self.snooze_until

    def should_start_sunrise(self, now=None):
        if not self.sunrise_enabled:
            return False
        now = now or datetime.now()
        target_time = datetime(now.year, now.month, now.day, self.hour, self.minute)
        sunrise_effect = target_time - timedelta(minutes=self.sunrise_minutes)

        # Check if we're exactly at the start minute
        window = timedelta(seconds = 2)
        return sunrise_effect <= now < (sunrise_effect + window)

    def should_trigger(self):
        now = datetime.now()
        if not self.enabled:
            return False
        if self.is_snoozed():
            return False
        
        #prevent re-trigger when disarmed
        if hasattr(self, "just_dismissed") and self.just_dismissed:
            if (now - self.just_dismissed).total_seconds() < 60:
                return False
            else:
                self.just_dismissed = None  # Reset after grace period

        # If snooze period has just ended, trigger now
        if self.snooze_until is not None and now >= self.snooze_until:
            self.snooze_until = None
            self.save()
            return True
        # Normal alarm time match
        return now.hour == self.hour and now.minute == self.minute

    def alarm_triggered(self):
        self.snooze_until = None
        self.save()

    def time_str(self):
        return f"{self.hour:02}:{self.minute:02}"

    def save(self, filename="/data/app/alarm_settings.json"):
        data = {
            "hour":            self.hour,
            "minute":          self.minute,
            "enabled":         self.enabled,
            "snooze_minutes":  self.snooze_minutes,
            "snooze_until":    self.snooze_until.isoformat() if self.snooze_until else None,
            # spotify
            "playlist_id":     self.playlist_id,
            "playlist_name":   self.playlist_name,
            "sound_type":      self.sound_type,
            #sunrise
            "sunrise_enabled": self.sunrise_enabled,
            "sunrise_minutes": self.sunrise_minutes,

        }
        try:
            with open(filename, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print("Failed to save alarm settings:", e)

    def load(self, filename="/data/app/alarm_settings.json"):
        if not os.path.exists(filename):
            return
        try:
            with open(filename, "r") as f:
                data = json.load(f)
            self.hour           = int(data.get("hour", 7))
            self.minute         = int(data.get("minute", 30))
            self.enabled        = bool(data.get("enabled", True))
            self.snooze_minutes = int(data.get("snooze_minutes", 5))

            snooze_str = data.get("snooze_until")
            if snooze_str:
                candidate = datetime.fromisoformat(snooze_str)
                self.snooze_until = candidate if candidate > datetime.now() else None
            else:
                self.snooze_until = None

            # restore persisted sound
            self.playlist_id   = data.get("playlist_id")
            self.playlist_name = data.get("playlist_name")
            self.sound_type = data.get("sound_type", "Classic")

            #restore sunrise
            self.sunrise_enabled = bool(data.get("sunrise_enabled", False))
            self.sunrise_minutes = int(data.get("sunrise_minutes", 15))

        except Exception as e:
            print("Failed to load alarm settings:", e)
