<div align="center">

# J.A.R.V.I.S.
### *Just A Rather Very Intelligent System*, for your Mac

**A voice-first AI that runs your Mac.** Log in and he greets you by the time of day with a
briefing (weather, meetings, reminders, headlines), then asks what you'd like to do.
Say *"Hey Jarvis"* from across the room. He sees you through the camera, knows when you
sit down, reads your hand gestures, and operates your apps, calendar, music, messages,
smart home and terminal. Everything shows on a live, holographic HUD.

![JARVIS HUD](docs/hud.png)

<img src="docs/hud-listening.png" width="49%" alt="Listening"> <img src="docs/hud-boot.png" width="49%" alt="Boot">

<sub>Speaking · listening (gold = your voice) · ignition. The design rationale is in <a href="DESIGN.md">DESIGN.md</a>.</sub>

</div>

---

## What he can do

| | |
|---|---|
| 🌅 **Greets you at login** | A greeting that fits the time of day ("Good morning, sir… burning the midnight oil again?") plus a spoken briefing of weather, your next meetings, overdue reminders, unread mail and headlines. Then *"What shall we tackle first?"*, and he listens with no wake word needed. |
| 🔁 **Notices you're back** | Unlock the Mac or simply sit down in front of the camera after time away, and he welcomes you back. The first return of the day gets the full briefing. |
| 🎙️ **Hands-free voice** | Wake word "Hey Jarvis" (openWakeWord), or hold **Right ⌥** to talk. Whisper large-v3-turbo transcribes on your Apple GPU. A British **Kokoro** voice (`bm_george`) starts speaking within about a second, sentence by sentence while he's still thinking. |
| ✋ **Interrupt anytime** | Say "Jarvis" while he's talking, raise an open palm, or press Esc. After each answer he keeps listening for a few seconds, so follow-ups don't need the wake word. |
| 👁️ **Sees** | "What am I holding?", "How do I look?", "What's on my screen?", "Read this error": camera frames and screenshots go straight to Claude's vision. |
| 🧑‍🚀 **Knows you** | Face recognition (OpenCV YuNet + SFace) tells you apart from visitors. Optional **sentry mode** photographs unknown faces while you're away; optional auto-lock when you leave. |
| 🤚 **Gestures** | ✋ stop · 👍 / 👎 answer a confirmation · ✌️ start listening · 🤟 play/pause music |
| 🖥️ **Runs your Mac** | Opens and quits apps, volume, brightness, dark mode, lock, sleep, notifications, clipboard, typing, Spotlight file search, reading files, Finder, screenshots |
| 📅 **Your life** | Calendar (read and create), Reminders (they sync to your iPhone), Contacts, iMessage, Notes, unread Mail |
| 🎵 **Music** | Plays songs, artists and playlists in Music or Spotify; play, pause, skip, "what's playing?" |
| 🏠 **Smart home & everything else** | Runs any **macOS Shortcut**, which covers HomeKit scenes, lights, thermostats, Focus modes and any app with Shortcuts actions |
| 🛠️ **Developer mode** | Shell commands (confirmed first), AppleScript, git and brew |
| 🌐 **Knows the world** | Live **web search**, weather (Open-Meteo, no key), news headlines |
| ⏱️ **Time** | Timers and spoken alarms. He also warns you 10 minutes before meetings and when the battery runs low. |
| 🧠 **Remembers** | Long-term memory of your preferences and people ("I take my coffee black"), plus summaries of past conversations |
| 🎛️ **Protocols** | Your own named routines, e.g. *"Jarvis, engage house party protocol"* (see below) |
| 🔌 **Extensible** | Plug in any **MCP server** (GitHub, Notion, Home Assistant, databases…) with three lines of config |
| 🛡️ **Asks first** | Before sending messages, running commands or deleting things he asks *"Shall I…?"* You answer by voice, 👍 or the HUD button. |
| 📴 **Works offline** | Falls back to a local Ollama model if the internet drops |

---

## Quick start

```bash
git clone <this repo> JarvisServer && cd JarvisServer
```

```bash
./install.sh
```

The installer:
1. creates a Python 3.12 environment (via `uv`, or Homebrew Python),
2. installs everything for your chip (Apple Silicon gets the GPU-accelerated voice and speech models),
3. runs **`jarvis setup`**, which asks your name, how to address you, and your city, stores your
   [Anthropic API key](https://console.anthropic.com/settings/keys) in `~/.jarvis/.env`
   (readable only by you), pre-downloads the models, **adds JARVIS to your Login Items**, and
   enrolls your face.

Then:

```bash
jarvis
```

…or log out and back in, and he'll greet you.

> **Requirements:** macOS 13+, Apple Silicon recommended (Intel works on the CPU path), a mic,
> and optionally a camera (built-in, USB, or your iPhone via Continuity Camera).

### macOS permissions
macOS asks the first time each one is used. Approve them for **JARVIS** (or your terminal, if you run `jarvis` from one):

| Permission | Why |
|---|---|
| Microphone | Wake word and voice commands |
| Camera | Face recognition, presence, gestures, "what do you see?" |
| Automation (System Events, Music, Calendar, Messages, Notes…) | Controlling apps |
| Calendars · Reminders · Contacts | Briefings, events, reminders, messaging people by name |
| Accessibility *(System Settings › Privacy & Security › Accessibility)* | Push-to-talk key, typing text, the HUD hotkey |
| Screen Recording | "What's on my screen?" |

Run **`jarvis doctor`** at any time to see what's working and what's missing.

---

## Talking to JARVIS

> **"Hey Jarvis…"**

- *"What's my day look like?"* · *"Add lunch with Pepper tomorrow at one."* · *"Remind me to call Pepper at six."*
- *"Play some AC/DC."* · *"Next track."* · *"Turn it down to 20."*
- *"Text Happy that I'm running ten minutes late."* → *"Shall I send Happy the message…?"* → 👍
- *"What's on my screen?"* · *"Why is this build failing?"* · *"Summarise this article."*
- *"What am I holding?"* · *"Do I look presentable?"*
- *"Set a pasta timer for 9 minutes."* · *"Wake me at 7:30."*
- *"Run the Movie Night shortcut."* · *"Turn on the living room lights."* (through Shortcuts / HomeKit)
- *"How's the system doing?"* · *"How much disk space have I got?"* · *"What's eating my memory?"*
- *"Run git status in my projects folder."* (confirmed first)
- *"Remember that my sister's name is Priya."* · *"What do you know about me?"*
- *"Engage work mode."* · *"House party protocol."* · *"Good night, Jarvis."*
- *"That's all, thanks."* ends the conversation.

You can also type into the HUD's command bar. The HUD's centrepiece is the core: its rings show live audio, work in progress, and a dial of your next 12 hours with meetings drawn on it ([DESIGN.md](DESIGN.md)). Shortcuts: **Space** listens, **Esc** stops, **/** focuses the command bar. **⌘⇧J** shows or hides the HUD.

---

## Protocols (your own routines)

Edit `~/.jarvis/protocols.yaml`:

```yaml
house_party_protocol:
  aliases: ["house party protocol", "party mode"]
  say: "House party protocol, initiated."
  steps:
    - shortcut: "Party Lights"      # a HomeKit scene in the Shortcuts app
    - dark_mode: true
    - volume: 70
    - music: "Party"

work_mode:
  aliases: ["work mode", "let's get to work"]
  say: "Work mode engaged."
  steps:
    - shortcut: "Focus: Work"
    - open_app: "Visual Studio Code"
    - open_url: "https://github.com/notifications"
    - music: "Deep Focus"
    - ask: "Give me a ten second rundown of my next three meetings."
```

Step types: `open_app`, `quit_app`, `open_url`, `shortcut`, `volume`, `brightness`, `dark_mode`,
`music`, `media`, `notify`, `say`, `wait`, `applescript`, `shell`, `lock_screen`, and `ask`, which hands any
request to JARVIS's brain after the routine. Protocols also appear as buttons on the HUD.

---

## Configuration

Everything lives in `~/.jarvis/config.yaml`. It's deep-merged over
[`jarvis/config/default.yaml`](jarvis/config/default.yaml), so only include what you change:

```yaml
owner: { name: "Tony", title: "sir" }
location: { city: "Malibu", units: imperial }
brain:
  model: claude-opus-5-5   # the default
  effort: low              # low = snappiest spoken replies
  fast_mode: false         # up to ~2.5x faster output, premium pricing
voice:
  tts: { voice: bm_george }    # or bm_lewis, bf_emma …
  push_to_talk_key: alt_r
vision:
  sentry_mode: true
  auto_lock_when_away: true
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env: { GITHUB_PERSONAL_ACCESS_TOKEN: "…" }
```

### Commands

| Command | |
|---|---|
| `jarvis` | Start everything: voice, camera, HUD window |
| `jarvis run --browser` | HUD in your browser instead of a native window |
| `jarvis run --text` | No mic or camera; use the HUD's command bar |
| `jarvis chat [--speak]` | Text chat in the terminal (good for testing tools) |
| `jarvis setup` | First-time wizard |
| `jarvis enroll [--name Pepper]` | Teach JARVIS a face (run again to improve accuracy) |
| `jarvis doctor [--online]` | Diagnose the install, optionally with a live API test |
| `jarvis say "Good evening, sir."` | Hear the voice |
| `jarvis install` / `uninstall` | Start at login (JARVIS.app + Login Item) / stop starting at login. `--launchd` uses a LaunchAgent instead |
| `jarvis hud --demo` | Preview the HUD with simulated data |
| `jarvis devices` | List audio devices (set `voice.input_device` by name) |

---

## How it works

```
                          ┌──────────────── JARVIS.app (Login Item) ─────────────────┐
  mic ─► openWakeWord ─┐  │                                                          │
        "hey jarvis"   ├─►│  Orchestrator (state machine)                            │
  ⌥ hold / ✌️ / HUD ───┘  │   standby → listening → transcribing → thinking → speaking│
                          │        ▲            │ VAD endpointing                    │
                          │        └ follow-up ─┘ mlx-whisper large-v3-turbo         │
                          │                                                          │
  camera ─► YuNet/SFace ─►│  Presence ─► "welcome back" · sentry · auto-lock         │
          └► MediaPipe ──►│  Gestures ─► stop / yes / no / listen / play-pause       │
                          │                                                          │
  login / unlock (Quartz)►│  Greeter ─► briefing (weather·calendar·reminders·mail·news)
                          │                                                          │
                          │  Brain: Claude Opus 5.5, streaming tool loop             │
                          │    ├─ 44 built-in tools (macOS, PIM, media, vision, memory…)
                          │    ├─ server-side web search                             │
                          │    ├─ MCP bridge (any MCP server)                        │
                          │    └─ prompt-cached, append-only sessions + summaries    │
                          │        │ sentence-by-sentence                            │
                          │        ▼                                                 │
                          │  Kokoro TTS (bm_george) ─► speaker (interruptible)       │
                          │                                                          │
                          │  Event bus ─► FastAPI + WebSocket ─► HUD (pywebview)     │
                          └──────────────────────────────────────────────────────────┘
                   ~/.jarvis: config.yaml · .env · memory.db · faces/ · models/ · protocols.yaml
```

| Path | Role |
|---|---|
| `jarvis/core/orchestrator.py` | The voice state machine, greetings, presence, confirmations, announcements |
| `jarvis/core/brain.py` | Streaming Claude agent loop: parallel tools, sentence streaming, session roll-over and summaries, offline fallback |
| `jarvis/core/persona.py` | JARVIS's personality and voice-first system prompt |
| `jarvis/core/briefing.py` | Parallel gathering of briefing data with tight timeouts |
| `jarvis/core/memory.py` | SQLite + FTS5 long-term memory, transcripts, summaries |
| `jarvis/core/protocols.py` | YAML routines |
| `jarvis/core/scheduler.py` | Timers, alarms, meeting and battery alerts |
| `jarvis/voice/` | Mic, wake word, VAD, Whisper STT, Kokoro / `say` TTS |
| `jarvis/vision/` | Camera, faces, presence, gestures |
| `jarvis/mac/` | AppleScript/JXA, EventKit, Shortcuts, session lock detection, the .app builder |
| `jarvis/tools/` | Tool registry (schemas, validation, confirmation gating), built-in tools, MCP bridge |
| `jarvis/hud/` | FastAPI server and the HUD: a WebGL arc-reactor core plus vanilla JS, no build step ([design notes](DESIGN.md)) |

### Design notes
- **Latency.** Speech starts when the *first sentence* of the reply is ready, not the whole reply. Effort `low` keeps
  thinking short for conversation. The frozen system prompt, tools and history are prompt-cached.
  Kokoro synthesises the next sentence while the current one plays.
- **Sessions.** Each conversation is append-only, which keeps the cache warm and the API's
  thinking-block checks happy. After 15 idle minutes (or 40 turns) the conversation is summarised into
  memory and a new one starts, seeded with your long-term facts and recent summaries.
- **Safety.** Risky tools are marked in code and can't run without your confirmation. File reads are confined
  to your home folder. Shell access can be disabled or allow-listed (`safety.*`).
- **Privacy.** Wake word, speech recognition, voice, face recognition and gestures all run **on your Mac**.
  What goes to Anthropic: your transcribed requests, tool results, and images *only when* he's asked to look
  at the camera or the screen. Face data never leaves `~/.jarvis/faces`.

### Cost
A typical spoken exchange is a few thousand input tokens, mostly cached, and a short reply. On Claude Opus 5.5
($4 / $20 per million tokens, cache reads $0.20) that's a fraction of a cent per request. To spend less, set
`brain.model: claude-sonnet-5-5` or `claude-haiku-5-5` in your config.

---

## Development

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest
```

The test suite runs anywhere, Linux CI included. It covers the tool registry and validation, the sentence
streamer, memory, protocols, presence, the scheduler, the HUD server, the full voice state machine with
synthetic audio, spoken confirmations, the offline greeting fallback, and a **contract test that drives the
real Anthropic SDK against a local mock of the Messages API**.

See [RESEARCH.md](RESEARCH.md) for the projects surveyed and what was taken from each.

### Troubleshooting
- **He doesn't hear "Hey Jarvis".** Run `jarvis doctor`, check the Microphone permission, and try `voice.wake_threshold: 0.35`.
  Holding Right ⌥ always works.
- **Calendar is empty.** Inside JARVIS.app he uses EventKit (grant Calendars). From a terminal he uses AppleScript
  (grant Automation › Calendar).
- **No camera feed.** Grant the Camera permission and check `vision.camera_index` (1 is often an iPhone with Continuity Camera).
- **Logs** are in `~/.jarvis/jarvis.log`.

<div align="center"><sub>Not affiliated with Marvel. JARVIS is a fan tribute; the code is MIT.</sub></div>
