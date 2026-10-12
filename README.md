# J.A.R.V.I.S. — MK VI

> Just A Rather Very Intelligent System — a local, voice-controlled machine assistant for Windows.

Born from a 2-year-old weekend experiment (gTTS + a hardcoded app launcher), rebuilt into a full HUD-driven system that actually controls your laptop.

![screenshot](static/screenshot.png)

## What it does

- **Voice or text commands** — Chrome/Edge speech recognition in the browser, JARVIS replies in voice (browser TTS with a lowered-pitch British voice preference, automatic fallback to offline Windows TTS)
- **Master power switch** — click the arc-reactor core in the HUD or say *"go to sleep"* / *"wake up"*. When powered down, JARVIS refuses all commands except waking.
- **40+ real system commands**:

| Domain | Commands |
|---|---|
| Audio | volume up/down/set N/max, mute, unmute, report |
| Display | brightness up/down/set N, report |
| Apps | open/close notepad, calculator, chrome, vscode, word, excel… (registry + fuzzy match) |
| Windows | show desktop, alt-tab, task view |
| Media | play/pause, next/previous track |
| Clipboard | "copy …", read clipboard |
| Typing | "type …" (paste-via-clipboard into focused window) |
| Keyboard | press enter/escape/tab/win/… |
| Web | "search …", "youtube …", "play … on youtube" |
| System | status, diagnostics, battery, cpu, ram, disk, network |
| Power | lock pc, sleep mode, shutdown/restart (**confirmation-gated**) |
| Fun | jokes, quotes, coin flip, dice, random numbers |

- **Live telemetry HUD** — CPU, RAM, volume, brightness gauges; battery + charge state; per-disk free space; network totals — pushed over WebSocket at 0.5 Hz.
- **Iron-Man aesthetic** — arc-reactor core with rotating rings, boot sequence, scanlines, starfield, command log.

## Security posture

- Binds to **127.0.0.1 only** — nothing is exposed to your network.
- Dangerous operations (shutdown/restart) require a second *"confirm"* before executing.
- No cloud APIs, no keys, no telemetry. Fully offline.

## Run it

```bat
START_JARVIS.bat
```

or manually:

```bat
python jarvis_server.py
:: → http://127.0.0.1:8420
```

Requires: Python 3.12, `fastapi uvicorn psutil pyautogui pycaw comtypes pywin32 pyperclip pyttsx3`.

## Try these

- *"system status"* · *"volume up"* · *"brightness 30"* · *"open chrome"*
- *"what's the time"* · *"battery"* · *"take a screenshot"*
- *"tell me a joke"* · *"go to sleep"* → *"wake up"*

## Layout

```
jarvis_server.py      FastAPI: command router, telemetry, WebSocket, master switch
static/index.html     HUD shell
static/app.js         HUD logic: voice, wake word, gauges, boot
START_JARVIS.bat       One-click launcher
index.html, Intro     The original 2023 scripts — kept for sentiment.
```
