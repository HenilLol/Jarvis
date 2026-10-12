"""
J.A.R.V.I.S. MK VII — Proactive intelligence layer.

Owns everything that makes JARVIS more than a command router:
  - Vosk offline STT: always-on wake-word listening ("jarvis ...")
  - Proactive agent: periodic check-ins, battery/disk warnings, ambient commentary
  - Weather via wttr.in (no key, cached)
  - Reminders (in-memory + persistence to reminders.json)
  - Ambient soundscapes (procedural rain via numpy → sounddevice)
  - Focus/game mode macro states
  - Fuzzy command matching (difflib) over the known command surface
"""
from __future__ import annotations

import asyncio
import datetime as dt
import difflib
import json
import math
import random
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import psutil

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "vosk-model-small-en-us-0.15"
REMINDERS_PATH = BASE_DIR / "reminders.json"

WAKE_WORDS = ("jarvis",)
WARMUP_SENTENCES = ["system status", "hello jarvis", "good morning jarvis"]


# ---------------------------------------------------------------------------
# Reminders
class ReminderStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.items: list[dict] = []
        self.load()

    def load(self):
        try:
            if self.path.exists():
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.items = []

    def save(self):
        try:
            self.path.write_text(json.dumps(self.items, indent=2), encoding="utf-8")
        except Exception:
            pass

    def add(self, text: str, when: dt.datetime) -> dict:
        item = {"id": int(time.time() * 1000) % 10**9,
                "text": text, "when": when.isoformat(timespec="seconds"),
                "done": False}
        with self.lock:
            self.items.append(item)
            self.save()
        return item

    def due(self) -> list[dict]:
        now = dt.datetime.now()
        with self.lock:
            due = [i for i in self.items if not i["done"] and i["when"] <= now.isoformat()]
            for i in due:
                i["done"] = True
            if due:
                self.save()
        return due

    def pending(self) -> list[dict]:
        with self.lock:
            return [i for i in self.items if not i["done"]]


# ---------------------------------------------------------------------------
# Weather (wttr.in — keyless)
_weather_cache: dict = {"ts": 0.0, "data": None}

def get_weather() -> Optional[dict]:
    try:
        if _weather_cache["data"] and time.time() - _weather_cache["ts"] < 600:
            return _weather_cache["data"]
        req = urllib.request.Request("https://wttr.in/?format=j1",
                                     headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        cur = data["current_condition"][0]
        loc = data["nearest_area"][0]["areaName"][0]["value"]
        region = data["nearest_area"][0]["region"][0]["value"]
        w = {
            "temp_c": int(cur["temp_C"]), "feels_like": int(cur["FeelsLikeC"]),
            "desc": cur["weatherDesc"][0]["value"], "humidity": int(cur["humidity"]),
            "wind_kmph": int(cur["windspeedKmph"]),
            "location": f"{loc}, {region}",
        }
        _weather_cache.update(ts=time.time(), data=w)
        return w
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ambient soundscapes — procedural, zero assets
class AmbientEngine:
    """Procedural rain/brown-noise generator using numpy + sounddevice.

    Runs its own daemon thread; only one track at a time.
    """
    KINDS = {
        "rain":     {"desc": "steady rainfall",       "brown_mix": 0.55, "drop_rate": 14.0},
        "storm":    {"desc": "heavy thunderstorm",    "brown_mix": 0.80, "drop_rate": 30.0},
        "brown":    {"desc": "brown noise",           "brown_mix": 1.0,  "drop_rate": 0.0},
        "cafe":     {"desc": "soft café murmur",      "brown_mix": 0.35, "drop_rate": 6.0},
        "deep":     {"desc": "deep space ambience",   "brown_mix": 0.90, "drop_rate": 1.5},
    }

    def __init__(self):
        import sounddevice as sd
        import numpy as np
        self.sd = sd
        self.np = np
        self.lock = threading.Lock()
        self.stream: Optional["sd.OutputStream"] = None
        self.current: Optional[str] = None
        self.gain = 0.28

    def _brown(self, n: int) -> "np.ndarray":
        np = self.np
        white = np.random.randn(n)
        brown = np.cumsum(white)
        brown /= np.max(np.abs(brown)) + 1e-9
        return brown

    def _drops(self, n: int, rate: float, sr: int) -> "np.ndarray":
        """Sparse impulses → dense rain texture via filtered noise bursts."""
        np = self.np
        out = np.zeros(n)
        n_drops = max(1, int(n / sr * rate))
        idx = np.random.randint(0, n, n_drops)
        for i in idx:
            length = random.randint(40, 240)
            burst = np.random.randn(length) * np.exp(-np.linspace(0, 8, length))
            end = min(n, i + length)
            out[i:end] += burst[: end - i]
        out /= (np.max(np.abs(out)) + 1e-9) + 0.5
        return out

    def _callback(self, kind: str):
        np = self.np
        sr = 44100
        cfg = self.KINDS[kind]
        chunk = 2048
        last_brown = None
        state = {"n": 0}

        def cb(indata, outdata, frames, time_info, status):
            n = frames
            brown = self._brown(n)
            out = brown * cfg["brown_mix"]
            if cfg["drop_rate"] > 0:
                out = out + self._drops(n, cfg["drop_rate"], sr) * (1 - cfg["brown_mix"]) * 1.4
            out *= self.gain
            outdata[:] = out.reshape(-1, 1).astype(self.np.float32)
        return cb

    def start(self, kind: str):
        with self.lock:
            if kind not in self.KINDS:
                return False, f"No ambience named '{kind}'. Options: {', '.join(self.KINDS)}"
            if self.current == kind:
                return True, f"Already playing {kind}."
            self.stop(lock_held=True)
            try:
                self.stream = self.sd.OutputStream(
                    samplerate=44100, channels=1, dtype="float32",
                    callback=self._callback(kind))
                self.stream.start()
                self.current = kind
                return True, f"Ambient {kind} engaged — {self.KINDS[kind]['desc']}."
            except Exception as e:
                return False, f"Ambience engine error: {e}"

    def stop(self, lock_held: bool = False):
        if not lock_held:
            with self.lock: pass
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None
        self.current = None

    def status(self) -> str:
        return self.current or "silence"


# ---------------------------------------------------------------------------
# Vosk offline always-on listener
class VoskListener:
    """Always-on microphone → wake word ('jarvis ...') → command callback.

    Runs a daemon thread. Falls back gracefully (logs, no crash) if the
    model is missing or the mic is unavailable.
    """

    def __init__(self, on_command: Callable[[str], None],
                 on_power: Callable[[bool], None]):
        self.on_command = on_command
        self.on_power = on_power
        self.active = False
        self.enabled = False     # user toggle
        self.thread: Optional[threading.Thread] = None
        self.last_partial = ""
        self.energy_threshold = 90.0   # RMS gate to ignore silence
        self._last_command_ts = 0.0

    def start(self, model_path: Path = None):
        if self.enabled:
            return True, "Ears already open, sir."
        mp = model_path or MODEL_PATH
        if not mp.exists():
            return False, (f"Voice model not found at {mp.name}. "
                           "Run: python fetch_models.py")
        self.enabled = True
        self.thread = threading.Thread(target=self._run, name="vosk-listener", daemon=True)
        self.thread.start()
        return True, "Ears open. Say 'JARVIS' followed by your command."

    def stop(self):
        self.enabled = False

    def _run(self):
        import queue
        try:
            import json as _json
            import sounddevice as sd
            from vosk import Model, KaldiRecognizer
        except Exception as e:
            print(f"[vosk] import failed: {e}")
            self.enabled = False
            return

        q: "queue.Queue" = queue.Queue()
        model = Model(str(MODEL_PATH))
        samplerate = 16000

        def callback(indata, frames, time_info, status):
            q.put(bytes(indata))

        try:
            with sd.RawInputStream(samplerate=samplerate, blocksize=4000, dtype="int16",
                                  channels=1, callback=callback):
                rec = KaldiRecognizer(model, samplerate)
                rec.SetWords(True)
                print("[vosk] listening for wake word 'jarvis'")
                while self.enabled:
                    data = q.get()
                    if len(data) == 0:
                        continue
                    # cheap energy gate
                    import struct
                    n = len(data) // 2
                    if n:
                        samples = struct.unpack(f"<{n}h", data)
                        rms = (sum(s * s for s in samples) / n) ** 0.5
                        if rms < self.energy_threshold:
                            continue
                    if rec.AcceptWaveform(data):
                        text = _json.loads(rec.Result()).get("text", "").strip()
                        if text:
                            self._handle(text)
                    else:
                        partial = _json.loads(rec.PartialResult()).get("partial", "")
                        if partial:
                            self.last_partial = partial

        except Exception as e:
            print(f"[vosk] listener crashed: {e}")
            self.enabled = False

    def _handle(self, text: str):
        low = text.lower()
        now = time.time()
        # debounce: same-ish command within 1.2s is an echo
        if now - self._last_command_ts < 1.2:
            return
        # wake word check
        for w in WAKE_WORDS:
            if w in low:
                cmd = low.split(w, 1)[1].strip(" ,.!?")
                if not cmd:
                    self.on_power(True)   # bare "jarvis" = wake/attention
                    return
                self._last_command_ts = now
                self.on_command(cmd)
                return
        # no wake word: ignore (privacy — only 'jarvis' utterances act)


# ---------------------------------------------------------------------------
# Fuzzy matching over known command vocabulary
KNOWN_PATTERNS = [
    "volume up", "volume down", "volume set 50", "mute", "unmute",
    "brightness up", "brightness down", "brightness set 50",
    "open notepad", "open chrome", "close notepad", "open calculator",
    "play pause", "next track", "previous track",
    "take a screenshot", "read my clipboard", "system status",
    "battery", "cpu load", "ram usage", "disk space",
    "what's the time", "what's the date", "weather",
    "tell me a joke", "flip a coin", "roll a dice",
    "go to sleep", "wake up", "lock my pc", "shutdown my computer",
    "show desktop", "minimize everything", "read my screen",
    "set a reminder", "list reminders", "focus mode", "game mode",
    "normal mode", "start rain", "stop ambience",
    "search for", "youtube", "type", "copy",
]


TOKEN_HINTS = {
    "rain|storm|ambience|sounds": ["start rain", "stop ambience"],
    "reminder|remind|alarm": ["set a reminder"],
    "weather|outside": ["weather"],
    "screenshot|capture": ["take a screenshot"],
    "bright|dim|darker": ["brightness up", "brightness down"],
    "volume|louder|quieter": ["volume up", "volume down", "mute", "unmute"],
    "battery|charge": ["battery"],
    "joke|funny": ["tell me a joke"],
    "focus mode|work mode": ["focus mode"],
    "game mode": ["game mode"],
    "go to sleep|power off": ["go to sleep"],
}


def fuzzy_match(text: str, limit: float = 0.62) -> Optional[str]:
    """Return the closest known command phrase, or None.

    Keyword hints are checked FIRST (strong intent words like 'rain' or
    'reminder' are unambiguous), then difflib for near-miss phrasings.
    """
    t = " ".join(text.lower().split())
    if not t:
        return None
    for pat, cmds in TOKEN_HINTS.items():
        if re.search(rf"\b({pat})", t):
            # direction refinement for up/down families
            if len(cmds) > 1:
                if re.search(r"\b(up|more|louder|brighter|increase|raise|max)\b", t):
                    return cmds[0]
                if re.search(r"\b(down|less|quieter|darker|decrease|lower|dim)\b", t):
                    return cmds[1] if len(cmds) > 1 else cmds[0]
                if re.search(r"\b(stop|off|kill|silence)\b", t):
                    return cmds[-1]
            return cmds[0]
    m = difflib.get_close_matches(t, KNOWN_PATTERNS, n=1, cutoff=limit)
    return m[0] if m else None


# ---------------------------------------------------------------------------
# Proactive agent
PROACTIVE_LINES = {
    "battery_low": [
        "Sir, battery is at {pct} percent. May I suggest a charger?",
        "Power reserves at {pct} percent, sir. The outlet is your friend.",
    ],
    "battery_full": ["Battery fully charged, sir. You may unplug at your leisure."],
    "ram_high": [
        "Memory pressure at {pct} percent, sir. A reboot would clear the cobwebs.",
        "RAM usage is climbing — {pct} percent. Might I close something?",
    ],
    "cpu_high": [
        "Processor load at {pct} percent, sir. Something is working hard.",
    ],
    "disk_low": ["C drive is down to {free} gigabytes, sir. Housekeeping recommended."],
    "greet": [
        "All quiet on the western front, sir.",
        "Systems nominal. I've kept everything warm for you.",
        "I ran a self-diagnostic while you were away. Everything is in order.",
    ],
    "late_night": [
        "It's {hour} o'clock, sir. Even Stark sleeps.",
        "Burning the midnight oil again, sir?",
    ],
}

MODE_STATES = {"normal": {}, "focus": {"volume": 25, "brightness": 70},
               "game": {"volume": 80, "brightness": 90}}


class ProactiveAgent:
    def __init__(self, broadcast, speak, reminders: ReminderStore):
        self.broadcast = broadcast
        self.speak = speak          # callable(str) -> None (backend TTS)
        self.reminders = reminders
        self.mode = "normal"
        self.last_battery_warn = 0.0
        self.last_greet = 0.0
        self.tick = 0

    def say(self, text: str, kind: str = "info"):
        self.speak(text)
        self.broadcast({"type": "proactive", "text": text, "kind": kind})

    def set_mode(self, mode: str):
        if mode not in MODE_STATES:
            return False, f"Unknown mode '{mode}'."
        self.mode = mode
        return True, f"{mode.capitalize()} mode engaged."

    def check_cycle(self):
        """Called every ~20s from the server's background loop."""
        self.tick += 1
        try:
            due = self.reminders.due()
            for r in due:
                self.say(f"Reminder, sir: {r['text']}.", "reminder")
        except Exception:
            pass

        if self.tick % 15 == 0:  # every ~5 min
            try:
                self._vitals_check()
            except Exception:
                pass
        if self.tick % 45 == 0:  # every ~15 min ambient line
            hour = dt.datetime.now().hour
            if 0 <= hour < 6:
                self.say(random.choice(PROACTIVE_LINES["late_night"]).format(hour=hour), "ambient")
            elif random.random() < 0.5:
                self.say(random.choice(PROACTIVE_LINES["greet"]), "ambient")

    def _vitals_check(self):
        bat = psutil.sensors_battery()
        now = time.time()
        if bat and not bat.power_plugged:
            if bat.percent < 20 and now - self.last_battery_warn > 600:
                self.last_battery_warn = now
                self.say(random.choice(PROACTIVE_LINES["battery_low"]).format(pct=round(bat.percent)), "warning")
            elif bat.percent > 97:
                if now - self.last_greet > 900:
                    self.last_greet = now
                    self.say(PROACTIVE_LINES["battery_full"][0], "info")
        mem = psutil.virtual_memory()
        if mem.percent > 88:
            self.say(random.choice(PROACTIVE_LINES["ram_high"]).format(pct=round(mem.percent)), "warning")
        cpu = psutil.cpu_percent(interval=0.2)
        if cpu > 92:
            self.say(random.choice(PROACTIVE_LINES["cpu_high"]).format(pct=round(cpu)), "warning")
        c = psutil.disk_usage("C:\\")
        if c.free / 1e9 < 15:
            self.say(PROACTIVE_LINES["disk_low"][0].format(free=round(c.free / 1e9)), "warning")
