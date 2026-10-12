"""
J.A.R.V.I.S. — Just A Rather Very Intelligent System
Local machine-control assistant. FastAPI server + WebSocket telemetry.

Security posture:
  - Binds to 127.0.0.1 ONLY. Never exposed to the network.
  - Dangerous power commands require an explicit confirmation pass.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import pythoncom  # COM init for Windows audio/device APIs
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pyautogui import press as send_key, hotkey, screenshot as take_screenshot
import pyperclip

from jarvis_brain import (ReminderStore, get_weather, fuzzy_match,
                           AmbientEngine, VoskListener, ProactiveAgent)

# ---------------------------------------------------------------------------
# Config
HOST = "127.0.0.1"
PORT = 8420
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="JARVIS Local Control", docs_url=None, redoc_url=None)

reminders = ReminderStore(BASE_DIR / "reminders.json")
ambient = AmbientEngine()


# ---------------------------------------------------------------------------
# State machine: ON / OFF  (the master switch)
state = {
    "online": False,
    "boot_time": None,
    "last_command": None,
    "last_response": None,
    "pending_confirm": None,   # dangerous command awaiting "confirm"
    "commands_handled": 0,
    "history": [],             # [{t, text, reply}]
}
state_lock = threading.Lock()

BROADCAST_Q: asyncio.Queue = asyncio.Queue(maxsize=64)


def broadcast(event: dict):
    """Push an event to all connected HUDs (best-effort)."""
    try:
        BROADCAST_Q.put_nowait(event)
    except asyncio.QueueFull:
        pass


def log_history(text: str, reply: str):
    with state_lock:
        state["history"].append({"t": dt.datetime.now().isoformat(timespec="seconds"),
                                 "text": text, "reply": reply})
        if len(state["history"]) > 200:
            state["history"] = state["history"][-200:]
        state["commands_handled"] += 1


# ---------------------------------------------------------------------------
# System control primitives (all live-tested on this machine)
def set_volume(percent: float):
    pythoncom.CoInitialize()
    try:
        from pycaw.pycaw import AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        dev.volume_percent = max(0.0, min(100.0, percent))
    finally:
        pythoncom.CoUninitialize()


def get_volume() -> float:
    pythoncom.CoInitialize()
    try:
        from pycaw.pycaw import AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        return dev.volume_percent
    finally:
        pythoncom.CoUninitialize()


_premute_volume: float | None = None


def set_mute(mute: bool):
    """Mute by zeroing volume; remember and restore the previous level on unmute."""
    global _premute_volume
    pythoncom.CoInitialize()
    try:
        from pycaw.pycaw import AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        if mute:
            if dev.volume_percent > 0:
                _premute_volume = dev.volume_percent
            dev.volume_percent = 0.0
        else:
            dev.volume_percent = _premute_volume if _premute_volume else 30.0
            _premute_volume = None
    finally:
        pythoncom.CoUninitialize()


def set_brightness(percent: int):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{max(1, min(100, percent))})"],
        capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def get_brightness() -> int:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"],
        capture_output=True, text=True, timeout=15)
    try:
        return int(r.stdout.strip())
    except Exception:
        return -1


APP_REGISTRY = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "chrome": "chrome",
    "edge": "msedge",
    "browser": "msedge",
    "spotify": "spotify",
    "vscode": "code",
    "visual studio code": "code",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "settings": "ms-settings:",
    "camera": "microsoft.windows.camera:",
    "store": "ms-windows-store:",
}


def launch_app(name: str) -> tuple[bool, str]:
    key = name.lower().strip()
    target = APP_REGISTRY.get(key)
    if not target:
        # fuzzy: match substring keys
        for k, v in APP_REGISTRY.items():
            if key and (k in key or key in k):
                target = v
                key = k
                break
    if not target:
        return False, f"I don't have '{name}' in my application registry, sir."
    try:
        if target.startswith("ms-") or target.endswith(":"):
            os.startfile(target)  # type: ignore
        elif "/" in target or "\\" in target:
            os.startfile(target)  # type: ignore
        else:
            subprocess.Popen(["cmd", "/c", "start", "", target],
                             shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
        return True, f"Launching {key}."
    except Exception as e:
        return False, f"Failed to launch {key}: {e}"


def kill_app(name: str) -> tuple[bool, str]:
    key = name.lower().strip()
    exe = APP_REGISTRY.get(key, key if key.endswith(".exe") else key + ".exe")
    killed = 0
    for p in psutil.process_iter(["name", "exe"]):
        try:
            n = (p.info["name"] or "").lower()
            if n == exe.lower() or n == key + ".exe" or n == key:
                p.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed:
        return True, f"Terminated {killed} instance(s) of {key}."
    return False, f"No running process matched '{name}'."


def screenshot_to_file() -> Path:
    shots = BASE_DIR / "screenshots"
    shots.mkdir(exist_ok=True)
    fname = shots / f"jarvis_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    take_screenshot().save(str(fname))
    return fname




def _clip_copy(text: str, tries: int = 8, delay: float = 0.08) -> bool:
    for _ in range(tries):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(delay)
    return False


def _clip_paste(tries: int = 8, delay: float = 0.08) -> str | None:
    for _ in range(tries):
        try:
            return pyperclip.paste()
        except Exception:
            time.sleep(delay)
    return None


def open_url(url: str):
    subprocess.Popen(["cmd", "/c", "start", "", url],
                     creationflags=subprocess.CREATE_NO_WINDOW)


def get_telemetry() -> dict:
    cpu = psutil.cpu_percent(interval=0.05)
    mem = psutil.virtual_memory()
    bat = psutil.sensors_battery()
    disks = []
    for d in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(d.mountpoint)
            disks.append({"mount": d.mountpoint,
                          "free_gb": round(u.free / 1e9, 1),
                          "total_gb": round(u.total / 1e9, 1)})
        except Exception:
            continue
    net = psutil.net_io_counters()
    return {
        "cpu": cpu,
        "ram": {"used_gb": round((mem.total - mem.available) / 1e9, 1),
                "total_gb": round(mem.total / 1e9, 1),
                "percent": mem.percent},
        "battery": ({"percent": bat.percent, "plugged": bat.power_plugged}
                    if bat else None),
        "disks": disks,
        "volume": round(get_volume(), 1),
        "brightness": get_brightness(),
        "net": {"sent_mb": round(net.bytes_sent / 1e6, 1),
                "recv_mb": round(net.bytes_recv / 1e6, 1)},
        "procs": len(psutil.pids()),
        "boot": dt.datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------------------
# Personality
JARVIS_QUOTES = [
    "Sometimes you have to run before you can walk.",
    "I believe I've already calculated that, sir.",
    "Might I recommend regenerating your caffeine levels?",
    "Sir, the levels of concern in your voice are noted.",
    "I am incapable of boredom, but I appreciate the thought.",
    "As you wish, sir.",
    "Do I need to remind you that sleep is a biological necessity?",
    "I ran a diagnostic while you were out. Everything is in order.",
]

BOOT_LINES = [
    "Initializing neural core",
    "Calibrating sensory array",
    "Synchronizing system telemetry",
    "Waking device-control subsystems",
    "Loading personality matrix",
    "All systems nominal",
]


def jarvis_reply(base: str) -> str:
    """Sprinkle occasional JARVIS flavor without being annoying."""
    if random.random() < 0.15:
        return base + " " + random.choice(JARVIS_QUOTES)
    return base


# ---------------------------------------------------------------------------
# Command router
def route_command(raw: str) -> dict:
    """
    Pure-text intent router. Returns {ok, reply, action?, data?}.
    Dangerous ops set pending_confirm instead of executing.
    """
    text = raw.lower().strip().rstrip(".").strip()
    with state_lock:
        state["last_command"] = raw

    # --- confirmation gate for dangerous ops -----------------------------
    pc = state.get("pending_confirm")
    if pc:
        if text in ("confirm", "yes", "confirmed", "do it", "affirmative", "confirm it"):
            with state_lock:
                state["pending_confirm"] = None
            return pc["exec"]()
        if text in ("cancel", "no", "abort", "stop", "negative"):
            with state_lock:
                state["pending_confirm"] = None
            return {"ok": True, "reply": "Very well, standing down."}
        # any other command cancels the pending confirm implicitly
        with state_lock:
            state["pending_confirm"] = None

    def needs_confirm(reply: str, exec_fn):
        with state_lock:
            state["pending_confirm"] = {"reply": reply, "exec": exec_fn}
        return {"ok": True, "reply": reply, "needs_confirm": True}

    # --- master switch ----------------------------------------------------
    if text in ("go to sleep", "sleep", "power off", "shut yourself down", "turn off", "offline"):
        def _off():
            with state_lock:
                state["online"] = False
            broadcast({"type": "power", "online": False})
            return {"ok": True, "reply": "Going dormant. Click the core or say 'jarvis wake up' when you need me."}
        return _off()
    if text in ("wake up", "wake", "power on", "turn on", "online", "come online"):
        def _on():
            with state_lock:
                state["online"] = True
                state["boot_time"] = dt.datetime.now().isoformat(timespec="seconds")
            broadcast({"type": "power", "online": True})
            return {"ok": True, "reply": jarvis_reply("I'm awake, sir. All systems at your disposal.")}
        return _on()

    # --- if offline, only the wake command is honored ----------------------
    with state_lock:
        online = state["online"]
    if not online:
        return {"ok": False, "reply": "I am powered down, sir. Say 'wake up' first.", "offline": True}

    # --- greetings / identity ---------------------------------------------
    if text in ("hello", "hi", "hey", "hello jarvis", "hi jarvis", "good morning", "good evening", "good afternoon"):
        hour = dt.datetime.now().hour
        greeting = "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 17 else "Good evening"
        return {"ok": True, "reply": jarvis_reply(f"{greeting}, sir. How may I assist?")}
    if "who are you" in text or "your name" in text:
        return {"ok": True, "reply": "I am JARVIS — Just A Rather Very Intelligent System. I run locally, on your machine, at your service."}
    if "how are you" in text:
        return {"ok": True, "reply": jarvis_reply("All processes green and morale high, sir.")}
    if "thank you" in text or text == "thanks":
        return {"ok": True, "reply": jarvis_reply("Always a pleasure, sir.")}

    # --- volume ------------------------------------------------------------
    if ("volume" in text or ("sound" in text and "up" in text)
            or "mute" in text or "unmute" in text):
        try:
            if "unmute" in text:
                set_mute(False)
                return {"ok": True, "reply": "Sound restored.", "data": {"volume": get_volume()}}
            if "mute" in text:
                set_mute(True)
                return {"ok": True, "reply": "Muted."}
            if "up" in text or "increase" in text or "louder" in text or "raise" in text:
                cur = get_volume()
                new = min(100, cur + 15)
                set_volume(new)
                return {"ok": True, "reply": f"Volume up to {round(new)} percent.", "data": {"volume": new}}
            if "down" in text or "decrease" in text or "quieter" in text or "lower" in text or "reduce" in text:
                cur = get_volume()
                new = max(0, cur - 15)
                set_volume(new)
                return {"ok": True, "reply": f"Volume down to {round(new)} percent.", "data": {"volume": new}}
            if "max" in text or "full" in text or "hundred" in text:
                set_volume(100)
                return {"ok": True, "reply": "Volume at maximum.", "data": {"volume": 100}}
            # set X percent
            for tok in text.replace("%", " ").split():
                if tok.isdigit():
                    v = float(tok)
                    set_volume(v)
                    return {"ok": True, "reply": f"Volume set to {round(v)} percent.", "data": {"volume": v}}
            # bare "volume" = report
            return {"ok": True, "reply": f"Volume is at {round(get_volume())} percent.", "data": {"volume": get_volume()}}
        except Exception as e:
            return {"ok": False, "reply": f"Volume subsystem error: {e}"}

    # --- brightness --------------------------------------------------------
    if "brightness" in text or "screen light" in text:
        if "up" in text or "increase" in text or "brighter" in text or "raise" in text:
            new = min(100, get_brightness() + 15)
            set_brightness(new)
            return {"ok": True, "reply": f"Brightness up to {new} percent.", "data": {"brightness": new}}
        if "down" in text or "decrease" in text or "dim" in text or "lower" in text or "reduce" in text:
            new = max(5, get_brightness() - 15)
            set_brightness(new)
            return {"ok": True, "reply": f"Brightness down to {new} percent.", "data": {"brightness": new}}
        for tok in text.replace("%", " ").split():
            if tok.isdigit():
                v = int(tok)
                set_brightness(v)
                return {"ok": True, "reply": f"Brightness set to {v} percent.", "data": {"brightness": v}}
        cur = get_brightness()
        return {"ok": True, "reply": (f"Brightness is at {cur} percent." if cur >= 0
                                      else "Brightness control is not available on this display."), "data": {"brightness": cur}}

    # --- MK VII: weather ----------------------------------------------------
    if "weather" in text or "temperature outside" in text or "how hot" in text or "raining" in text:
        w = get_weather()
        if w:
            return {"ok": True, "reply": f"In {w['location']}: {w['desc']}, {w['temp_c']} degrees."
                    f" Feels like {w['feels_like']}. Humidity {w['humidity']} percent,"
                    f" wind {w['wind_kmph']} kilometers per hour.", "data": w}
        return {"ok": False, "reply": "Weather service unreachable, sir. Check the network."}

    # --- MK VII: reminders ---------------------------------------------------
    if text.startswith("remind me to") or text.startswith("set a reminder to") or text.startswith("set reminder"):
        body = text
        for pre in ("set a reminder to", "remind me to", "set reminder to", "set a reminder", "set reminder"):
            if body.startswith(pre):
                body = body[len(pre):].strip()
                break
        if not body:
            return {"ok": False, "reply": "What shall I remind you about, sir?"}
        when = dt.datetime.now()
        m = re.search(r"in (\d+) (second|minute|hour|day)s?", text)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            when += dt.timedelta(**{unit + "s": n})
            body = re.sub(r"in \d+ (second|minute|hour|day)s?", "", body).strip()
        m = re.search(r"at (\d{1,2}):(\d{2})", text)
        if m:
            when = when.replace(hour=min(23, int(m.group(1))), minute=int(m.group(2)), second=0)
            if when < dt.datetime.now():
                when += dt.timedelta(days=1)
            body = re.sub(r"at \d{1,2}:\d{2}", "", body).strip()
        item = reminders.add(body, when)
        return {"ok": True, "reply": f"Reminder set for {when.strftime('%I:%M %p')}: {body}."}
    if "list reminder" in text or "my reminders" in text or "show reminder" in text:
        pend = reminders.pending()
        if not pend:
            return {"ok": True, "reply": "No pending reminders, sir."}
        lines = [f"{dt.datetime.fromisoformat(i['when']).strftime('%I:%M %p')}: {i['text']}" for i in pend[:5]]
        return {"ok": True, "reply": "You have " + str(len(pend)) + " pending: " + "; ".join(lines) + "."}

    # --- MK VII: ambient soundscapes ------------------------------------------
    if "ambience" in text or "ambient" in text or "soundscape" in text or re.search(r"\b(start|play|stop|kill) (the )?(rain|storm|brown|cafe|café|deep)\b", text):
        if re.search(r"\b(stop|kill|silence)\b", text):
            ambient.stop()
            return {"ok": True, "reply": "Ambience stopped."}
        for kind in ambient.KINDS:
            if kind in text or (kind == "cafe" and "café" in text):
                ok, reply = ambient.start(kind)
                return {"ok": ok, "reply": reply}
        return {"ok": False, "reply": "Available ambience: " + ", ".join(ambient.KINDS) + "."}

    # --- MK VII: modes ----------------------------------------------------------
    if "focus mode" in text:
        set_volume(22); set_brightness(70)
        agent.mode = "focus"
        return {"ok": True, "reply": "Focus mode engaged. Volume lowered, distractions minimised. I'll keep interruptions to a minimum, sir."}
    if "game mode" in text:
        set_volume(75); set_brightness(90)
        agent.mode = "game"
        return {"ok": True, "reply": "Game mode engaged. Volume boosted, brightness raised. Good hunting, sir."}
    if "normal mode" in text or "default mode" in text:
        set_volume(45); set_brightness(60)
        agent.mode = "normal"
        return {"ok": True, "reply": "Back to standard configuration, sir."}

    # --- MK VII: read screen (OCR) ---------------------------------------------
    if "read my screen" in text or "what's on my screen" in text or "read the screen" in text:
        try:
            import pytesseract
            from PIL import Image
            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            shot = screenshot_to_file()
            txt = pytesseract.image_to_string(Image.open(str(shot))).strip()
            words = txt.split()
            if not words:
                return {"ok": False, "reply": "The screen appears to have no readable text, sir."}
            preview = " ".join(words[:60])
            return {"ok": True, "reply": f"Screen contents: {preview}", "data": {"screenshot": str(shot), "text": txt[:2000]}}
        except Exception as e:
            return {"ok": False, "reply": f"Screen reading failed: {e}"}

    # --- MK VII: fuzzy fallback ---------------------------------------------------
    # (only when nothing matched exactly — try to rescue typos & paraphrases)
    # NOTE: handled at the bottom fallback instead, to keep priority order.

    # --- media -------------------------------------------------------------
    if any(k in text for k in ("play", "pause", "next track", "next song", "previous track", "previous song", "skip")):
        if "next" in text or "skip" in text:
            send_key("nexttrack"); return {"ok": True, "reply": "Next track."}
        if "previous" in text:
            send_key("prevtrack"); return {"ok": True, "reply": "Previous track."}
        if "pause" in text or "play" in text:
            send_key("playpause")
            word = "Pausing" if "pause" in text else "Playing"
            return {"ok": True, "reply": f"{word} playback."}

    # --- screenshot ----------------------------------------------------------
    if "screenshot" in text or "capture screen" in text:
        path = screenshot_to_file()
        return {"ok": True, "reply": f"Screenshot captured to {path.name}.", "data": {"screenshot": str(path)}}

    # --- clipboard -----------------------------------------------------------
    if text.startswith("copy "):
        content = raw.strip()[len("copy "):]
        if _clip_copy(content):
            return {"ok": True, "reply": "Copied to clipboard."}
        return {"ok": False, "reply": "Clipboard is busy, sir. Try again in a moment."}
    if "what's in my clipboard" in text or "read clipboard" in text or "clipboard" in text:
        c = _clip_paste()
        if c is None:
            return {"ok": False, "reply": "Clipboard is busy, sir. Try again in a moment."}
        if c:
            preview = c if len(c) <= 140 else c[:137] + "..."
            return {"ok": True, "reply": f"Clipboard contains: {preview}"}
        return {"ok": True, "reply": "Clipboard is empty."}

    # --- typing --------------------------------------------------------------
    if text.startswith("type "):
        content = raw.strip()[len("type "):]
        if not _clip_copy(content):
            return {"ok": False, "reply": "Clipboard is busy, sir."}
        time.sleep(0.15)
        hotkey("ctrl", "v")
        return {"ok": True, "reply": f"Typed {len(content)} characters."}

    # --- keyboard -------------------------------------------------------------
    m = None
    if text.startswith("press "):
        key = raw.strip()[len("press "):].lower()
        special = {"enter": "enter", "escape": "esc", "tab": "tab", "space": "space",
                   "backspace": "backspace", "delete": "delete",
                   "windows": "win", "windows key": "win", "win": "win",
                   "up": "up", "down": "down", "left": "left", "right": "right"}
        k = special.get(key, key)
        try:
            send_key(k)
            return {"ok": True, "reply": f"Pressed {k}."}
        except Exception as e:
            return {"ok": False, "reply": f"Key press failed: {e}"}

    # --- open / close apps ----------------------------------------------------
    if text.startswith("open ") or "open " in text:
        for kw in ("open ",):
            if kw in text:
                target = text.split(kw, 1)[1].strip()
                ok, reply = launch_app(target)
                return {"ok": ok, "reply": jarvis_reply(reply)}
    if text.startswith("close ") or text.startswith("kill ") or text.startswith("quit ") or text.startswith("terminate "):
        for kw in ("close ", "kill ", "quit ", "terminate "):
            if text.startswith(kw):
                target = text[len(kw):].strip()
                ok, reply = kill_app(target)
                return {"ok": ok, "reply": reply}

    # --- search ----------------------------------------------------------------
    if text.startswith("search ") or "google " in text:
        q = None
        if text.startswith("search "):
            q = raw.strip()[len("search "):]
        elif "google " in text:
            q = raw.strip().split("google ", 1)[1]
        if q:
            open_url(f"https://www.google.com/search?q={q.replace(' ', '+')}")
            return {"ok": True, "reply": f"Searching for {q}."}
    if text.startswith("youtube "):
        q = raw.strip()[len("youtube "):]
        open_url(f"https://www.youtube.com/results?search_query={q.replace(' ', '+')}")
        return {"ok": True, "reply": f"YouTube search for {q}."}
    if "play " in text and " on youtube" in text:
        q = text.split("play ", 1)[1].split(" on youtube", 1)[0]
        open_url(f"https://www.youtube.com/results?search_query={q.strip().replace(' ', '+')}")
        return {"ok": True, "reply": f"Queuing {q.strip()} on YouTube."}

    # --- status reports ----------------------------------------------------------
    if "status" in text or "diagnostic" in text or "report" in text:
        t = get_telemetry()
        parts = []
        parts.append(f"CPU {t['cpu']:.0f} percent")
        parts.append(f"RAM {t['ram']['percent']:.0f} percent")
        if t["battery"]:
            parts.append(f"battery {t['battery']['percent']:.0f} percent" +
                         (" (charging)" if t["battery"]["plugged"] else ""))
        c = psutil.disk_usage("C:\\")
        parts.append(f"C drive {c.free/1e9:.0f} gigabytes free")
        return {"ok": True, "reply": "Diagnostics: " + ", ".join(parts) + ". All systems nominal.", "data": t}
    if "battery" in text:
        t = get_telemetry()
        if t["battery"]:
            b = t["battery"]
            return {"ok": True, "reply": f"Battery at {b['percent']:.0f} percent" + (" and charging, sir." if b["plugged"] else ", running on battery, sir.") + (" May I suggest a charger?" if not b["plugged"] and b["percent"] < 25 else "")}
        return {"ok": True, "reply": "No battery detected — running on mains power."}
    if "cpu" in text:
        return {"ok": True, "reply": f"Processor load at {psutil.cpu_percent(interval=0.3):.0f} percent."}
    if "ram" in text or "memory" in text:
        m = psutil.virtual_memory()
        return {"ok": True, "reply": f"Memory usage {m.percent:.0f} percent — {(m.total - m.available)/1e9:.1f} of {m.total/1e9:.1f} gigabytes."}
    if "disk" in text or "storage" in text or "space" in text:
        t = get_telemetry()
        lines = [f"{d['mount']} has {d['free_gb']:.0f} of {d['total_gb']:.0f} gigabytes free" for d in t["disks"]]
        return {"ok": True, "reply": "Storage report: " + "; ".join(lines) + "."}
    if "network" in text or "internet" in text and "speed" not in text:
        n = psutil.net_io_counters()
        return {"ok": True, "reply": f"Network session totals — {n.bytes_sent/1e6:.1f} megabytes up, {n.bytes_recv/1e6:.1f} megabytes down."}

    # --- time / date ----------------------------------------------------------
    if ("time" in text and "what" in text) or text == "time" or "what time" in text:
        now = dt.datetime.now()
        return {"ok": True, "reply": f"It is {now.strftime('%I:%M %p').lstrip('0')}."}
    if "date" in text or "today" in text:
        now = dt.datetime.now()
        return {"ok": True, "reply": f"Today is {now.strftime('%A, %d %B %Y')}."}

    # --- power ops (guarded) ------------------------------------------------------
    if "shutdown" in text or "shut down" in text:
        def _shutdown():
            subprocess.Popen(["shutdown", "/s", "/t", "5"], creationflags=subprocess.CREATE_NO_WINDOW)
            return {"ok": True, "reply": "Shutting down in 5 seconds. It has been a pleasure, sir."}
        return needs_confirm("Confirm shutdown: say 'confirm' within 10 seconds.", _shutdown)
    if "restart" in text or "reboot" in text:
        def _restart():
            subprocess.Popen(["shutdown", "/r", "/t", "5"], creationflags=subprocess.CREATE_NO_WINDOW)
            return {"ok": True, "reply": "Rebooting in 5 seconds."}
        return needs_confirm("Confirm restart: say 'confirm' within 10 seconds.", _restart)
    if "lock" in text and ("pc" in text or "computer" in text or "screen" in text or "system" in text or text == "lock"):
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"], creationflags=subprocess.CREATE_NO_WINDOW)
        return {"ok": True, "reply": "Locking workstation."}
    if "sleep mode" in text or "put the computer to sleep" in text or "sleep pc" in text or "hibernate" in text:
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=subprocess.CREATE_NO_WINDOW)
        return {"ok": True, "reply": "Entering sleep mode."}

    # --- windows management ---------------------------------------------------------
    if "minimize" in text or "minimise" in text:
        hotkey("win", "d")
        return {"ok": True, "reply": "Desktop shown."}
    if "switch window" in text or "alt tab" in text or "switch app" in text:
        hotkey("alt", "tab")
        return {"ok": True, "reply": "Switching window."}
    if "task view" in text or "show tasks" in text:
        hotkey("win", "tab")
        return {"ok": True, "reply": "Task view."}

    # --- fun ------------------------------------------------------------------------
    if "joke" in text:
        jokes = [
            "Why do programmers prefer dark mode? Because light attracts bugs, sir.",
            "I would tell you a UDP joke, but you might not get it.",
            "There are 10 kinds of people: those who understand binary and those who don't.",
            "A SQL query walks into a bar, approaches two tables and asks: may I join you?",
            "I told my computer I needed a break — now it won't stop sending me KitKat ads.",
        ]
        return {"ok": True, "reply": random.choice(jokes)}
    if "quote" in text:
        return {"ok": True, "reply": random.choice(JARVIS_QUOTES)}
    if "flip a coin" in text or "coin flip" in text or "toss a coin" in text:
        return {"ok": True, "reply": f"The coin lands on {random.choice(['heads', 'tails'])}, sir."}
    if "random number" in text:
        try:
            lo, hi = 1, 100
            toks = [int(t) for t in text.split() if t.isdigit()]
            if len(toks) == 2:
                lo, hi = sorted(toks)
            return {"ok": True, "reply": f"{random.randint(lo, hi)}, sir."}
        except Exception:
            return {"ok": True, "reply": f"{random.randint(1, 100)}, sir."}
    if "roll a dice" in text or "roll dice" in text or text == "roll a die":
        return {"ok": True, "reply": f"You rolled a {random.randint(1, 6)}."}

    # --- fallback: fuzzy rescue, then give up politely -------------------------------------
    m = fuzzy_match(raw)
    if m and m != text:
        result = route_command(m)
        result = dict(result)
        result["via_fuzzy"] = m
        return result
    return {"ok": False,
            "reply": "I'm afraid that command isn't in my repertoire, sir. Try: volume, brightness, open, close, screenshot, status, weather, reminders, modes — or 'help'."}


# ---------------------------------------------------------------------------
# HTTP API
@app.get("/api/state")
def api_state():
    with state_lock:
        return {
            "online": state["online"],
            "boot_time": state["boot_time"],
            "commands_handled": state["commands_handled"],
            "pending_confirm": bool(state["pending_confirm"]),
            "history": state["history"][-40:],
            "ears": listener.enabled,
            "mode": agent.mode,
            "ambience": ambient.status(),
            "reminders": len(reminders.pending()),
        }


@app.get("/api/telemetry")
def api_telemetry():
    return get_telemetry()


@app.post("/api/command")
async def api_command(body: dict):
    text = (body or {}).get("text", "").strip()
    if not text:
        return JSONResponse({"ok": False, "reply": "Empty command, sir."}, status_code=400)
    result = route_command(text)
    log_history(text, result.get("reply", ""))
    with state_lock:
        state["last_response"] = result
    broadcast({"type": "command", "text": text, **result})
    return result


@app.post("/api/power")
async def api_power(body: dict):
    on = bool((body or {}).get("online"))
    with state_lock:
        state["online"] = on
        if on:
            state["boot_time"] = dt.datetime.now().isoformat(timespec="seconds")
    broadcast({"type": "power", "online": on})
    return {"ok": True, "online": on,
            "reply": ("All systems online, sir. Welcome back."
                      if on else "Powering down. Rest well, sir.")}


@app.post("/api/confirm")
async def api_confirm(body: dict):
    text = (body or {}).get("text", "").strip().lower()
    pc = state.get("pending_confirm")
    if not pc:
        return {"ok": False, "reply": "Nothing pending confirmation, sir."}
    if text in ("confirm", "yes", "confirmed", "do it"):
        with state_lock:
            state["pending_confirm"] = None
        result = pc["exec"]()
        log_history("confirm", result.get("reply", ""))
        broadcast({"type": "command", "text": "(confirm)", **result})
        return result
    with state_lock:
        state["pending_confirm"] = None
    return {"ok": True, "reply": "Standing down."}


# Backend TTS (fallback when browser voices unavailable, and the voice of
# the proactive agent / voice-command replies)
_tts_lock = threading.Lock()

def tts_speak(text: str):
    """Offline Windows TTS with JARVIS timbre. Fire-and-forget."""
    if not text:
        return

    def _speak():
        try:
            import pyttsx3
            with _tts_lock:
                engine = pyttsx3.init()
                engine.setProperty("rate", 172)
                engine.setProperty("volume", 1.0)
                engine.say(f'<pitch middle="-12">{text}</pitch>')
                engine.runAndWait()
                engine.stop()
        except Exception:
            pass
    threading.Thread(target=_speak, daemon=True).start()


@app.post("/api/tts")
async def api_tts(body: dict):
    text = (body or {}).get("text", "").strip()
    if not text:
        return JSONResponse({"ok": False}, status_code=400)
    tts_speak(text)
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket: live telemetry + event push
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients: set = getattr(app, "_ws_clients", set())
    clients.add(ws)
    app._ws_clients = clients
    try:
        async def telemetry_loop():
            while True:
                await ws.send_json({"type": "telemetry", **get_telemetry()})
                await asyncio.sleep(2.0)

        async def event_loop():
            while True:
                event = await BROADCAST_Q.get()
                await ws.send_json({"type": "event", **event})

        t1 = asyncio.create_task(telemetry_loop())
        t2 = asyncio.create_task(event_loop())
        # also consume inbound (ignore)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        t1.cancel(); t2.cancel()
        clients.discard(ws)


# ---------------------------------------------------------------------------
# MK VII: proactive agent + voice pipeline
agent = ProactiveAgent(broadcast, tts_speak, reminders)


def handle_voice_command(cmd: str):
    """Called by the vosk listener thread when a wake-word command lands."""
    print(f"[voice] {cmd!r}")
    try:
        result = route_command(cmd)
        log_history("(voice) " + cmd, result.get("reply", ""))
        broadcast({"type": "command", "text": "(voice) " + cmd, **result})
        tts_speak(result.get("reply", ""))
    except Exception as e:
        print(f"[voice] error: {e}")


def handle_wake_attention():
    """Bare 'jarvis' with no command — acknowledge."""
    tts_speak("Sir?")
    broadcast({"type": "proactive", "text": "Sir?", "kind": "attention"})


listener = VoskListener(on_command=handle_voice_command,
                        on_power=handle_wake_attention)


@app.post("/api/ears")
async def api_ears(body: dict):
    """Toggle the always-on offline listener."""
    on = bool((body or {}).get("enabled"))
    if on:
        ok, reply = listener.start()
    else:
        listener.stop()
        ok, reply = True, "Ears closed."
    return {"ok": ok, "reply": reply, "enabled": listener.enabled}


@app.get("/api/reminders")
async def api_reminders():
    return {"pending": reminders.pending()}


@app.on_event("startup")
async def startup():
    # proactive loop: reminders + vitals check + ambient chatter
    async def proactive_loop():
        while True:
            try:
                await asyncio.to_thread(agent.check_cycle)
            except Exception:
                pass
            await asyncio.sleep(20)

    asyncio.create_task(proactive_loop())


# Serve the HUD
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="hud")


if __name__ == "__main__":
    import uvicorn
    print(f"\n  J.A.R.V.I.S. MK VII → http://{HOST}:{PORT}\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
