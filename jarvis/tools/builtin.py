"""JARVIS's built-in abilities. Each function becomes a Claude tool."""
from __future__ import annotations

import asyncio
import datetime as dt
import shlex
import subprocess
from pathlib import Path

from jarvis.mac import pim, system as mac
from jarvis.tools.registry import ImageResult, registry

tool = registry.tool

S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}


def _p(desc: str, base: dict | None = None, **extra) -> dict:
    return {**(base or S), "description": desc, **extra}


def _parse_when(value: str) -> dt.datetime:
    value = value.strip().replace("Z", "+00:00")
    when = dt.datetime.fromisoformat(value)
    if when.tzinfo is None:
        when = when.astimezone()  # interpret as local time
    return when


# ═══════════════════════════════ apps & web ══════════════════════════════════
@tool("Open (launch or bring to front) a Mac application by name, e.g. 'Safari', 'Visual Studio Code', 'Spotify'.",
      {"name": _p("Application name")}, ["name"], category="apps")
def open_application(ctx, name: str):
    return mac.open_app(name)


@tool("Quit a running Mac application.", {"name": _p("Application name")}, ["name"], category="apps")
def quit_application(ctx, name: str):
    return mac.quit_app(name)


@tool("Hide every app's windows except JARVIS (like ⌥⌘H from everywhere). Nothing is closed.", category="apps")
def hide_other_apps(ctx):
    mac.osascript('tell application "System Events" to set visible of (every process whose visible is true '
                  'and name is not "JARVIS") to false')
    return "Other apps hidden."


@tool("List the applications currently running, plus which one is frontmost.", category="apps")
def list_running_apps(ctx):
    return {"running": mac.running_apps(), "frontmost": mac.frontmost_app()}


@tool("Open a URL in the default browser, or any URI scheme (spotify:, maps:, x-apple.systempreferences: …).",
      {"url": _p("Full URL or URI")}, ["url"], category="apps")
def open_url(ctx, url: str):
    if "://" not in url and ":" not in url.split("/")[0]:
        url = "https://" + url
    return mac.open_target(url)


# ═══════════════════════════════ system ══════════════════════════════════════
@tool("Get a status report on the Mac: battery, CPU, memory, disk, network, uptime, volume, frontmost app.",
      category="system")
def system_status(ctx):
    import psutil

    vm = psutil.virtual_memory()
    du = psutil.disk_usage(str(Path.home()))
    boot = dt.datetime.fromtimestamp(psutil.boot_time())
    info = {
        "battery": {k: v for k, v in mac.battery().items() if k != "raw"},
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "memory_percent": vm.percent,
        "memory_used_gb": round(vm.used / 1e9, 1),
        "memory_total_gb": round(vm.total / 1e9, 1),
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_percent": du.percent,
        "uptime": str(dt.datetime.now() - boot).split(".")[0],
        "wifi": mac.wifi_name(),
        "machine": mac.system_profile(),
    }
    if mac.IS_MAC:
        try:
            info["volume"] = mac.get_volume()
            info["frontmost_app"] = mac.frontmost_app()
        except Exception:
            pass
    top = sorted(psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                 key=lambda p: p.info.get("memory_percent") or 0, reverse=True)[:5]
    info["top_memory_processes"] = [p.info["name"] for p in top]
    return info


@tool("Set the Mac's output volume (0-100).", {"level": _p("Volume percent", I)}, ["level"], category="system")
def set_volume(ctx, level: int):
    return mac.set_volume(level)


@tool("Mute or unmute the Mac's audio output.", {"muted": _p("true to mute", B)}, ["muted"], category="system")
def mute(ctx, muted: bool):
    return mac.set_mute(muted)


@tool("Set display brightness (0-100).", {"level": _p("Brightness percent", I)}, ["level"], category="system")
def set_brightness(ctx, level: int):
    return mac.set_brightness(level)


@tool("Perform a system action on the Mac.",
      {"action": _p("Which action", enum=["lock_screen", "sleep_display", "sleep", "dark_mode_on",
                                         "dark_mode_off", "toggle_dark_mode", "empty_trash"])},
      ["action"], category="system")
async def system_action(ctx, action: str):
    if action == "empty_trash" and ctx.confirm and not await ctx.confirm("permanently empty the trash"):
        return "Cancelled."
    fn = {
        "lock_screen": mac.lock_screen,
        "sleep_display": mac.sleep_display,
        "sleep": mac.sleep_system,
        "dark_mode_on": lambda: mac.toggle_dark_mode(True),
        "dark_mode_off": lambda: mac.toggle_dark_mode(False),
        "toggle_dark_mode": lambda: mac.toggle_dark_mode(None),
        "empty_trash": lambda: mac.osascript('tell application "Finder" to empty trash') or "Trash emptied.",
    }[action]
    return await asyncio.to_thread(fn)


@tool("Show a macOS notification banner.", {"title": _p("Title"), "message": _p("Body text")},
      ["title", "message"], category="system")
def show_notification(ctx, title: str, message: str):
    return mac.notify(title, message)


@tool("Read or write the clipboard.",
      {"action": _p("get or set", enum=["get", "set"]), "text": _p("Text to copy when action is set")},
      ["action"], category="system")
def clipboard(ctx, action: str, text: str = ""):
    return mac.get_clipboard() if action == "get" else mac.set_clipboard(text)


@tool("Type text into whatever app is in front, as if on the keyboard (e.g. dictation into a document).",
      {"text": _p("Text to type")}, ["text"], category="system")
def type_text(ctx, text: str):
    return mac.type_text(text)


@tool("Take a screenshot of the user's screen so you can see it. Use for 'what's on my screen', "
      "'read this error', 'summarise this page'.", category="vision")
def look_at_screen(ctx):
    import io

    path = mac.screenshot()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    try:  # downscale big Retina captures to keep the request lean
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=80)
        return ImageResult(buf.getvalue(), "image/jpeg", "Screenshot of the user's screen.")
    except Exception:
        return ImageResult(data, "image/png", "Screenshot of the user's screen.")


# ═══════════════════════════ shortcuts & scripting ═══════════════════════════
@tool("Run a macOS Shortcut by name. Shortcuts control HomeKit (lights, scenes, thermostats), Focus modes, "
      "and almost any app. Use list_shortcuts first if unsure of the exact name.",
      {"name": _p("Exact Shortcut name"), "input": _p("Optional text input")}, ["name"], category="home")
def run_shortcut(ctx, name: str, input: str = ""):
    return mac.run_shortcut(name, input or None)


@tool("List the user's macOS Shortcuts (their names).", category="home")
def list_shortcuts(ctx):
    names = mac.list_shortcuts()
    return {"count": len(names), "shortcuts": names[:150]}


@tool("Run an AppleScript to automate any scriptable Mac app when no other tool fits. Returns its result.",
      {"script": _p("AppleScript source")}, ["script"], confirm=True,
      confirm_message="run an AppleScript", category="scripting")
def run_applescript(ctx, script: str):
    return mac.osascript(script, timeout=60) or "Done."


@tool("Run a shell command in the user's home directory (zsh). Returns stdout/stderr. Use for developer tasks "
      "like git status, brew, disk usage.",
      {"command": _p("The command line"), "timeout": _p("Seconds before giving up (default 60)", I)},
      ["command"], confirm=True, confirm_message=lambda a: f"run the command: {a.get('command', '')}",
      category="scripting")
def run_shell(ctx, command: str, timeout: int = 60):
    if not ctx.cfg.get("safety.allow_shell", True):
        raise PermissionError("Shell access is disabled in the config (safety.allow_shell).")
    allow = ctx.cfg.get("safety.shell_allowlist", []) or []
    if allow:
        first = shlex.split(command)[0] if command.strip() else ""
        if first not in allow:
            raise PermissionError(f"{first!r} is not in safety.shell_allowlist.")
    shell = "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh"
    proc = subprocess.run([shell, "-lc", command], capture_output=True, text=True,
                          timeout=min(int(timeout), 600), cwd=str(Path.home()))
    out = (proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr.strip() else "")).strip()
    return {"exit_code": proc.returncode, "output": out[-6000:] or "(no output)"}


# ═══════════════════════════════ files ═══════════════════════════════════════
@tool("Search the Mac for files by name with Spotlight.",
      {"query": _p("Words in the file name"), "limit": _p("Max results (default 10)", I)},
      ["query"], category="files")
def find_files(ctx, query: str, limit: int = 10):
    paths = mac.spotlight(query, limit=limit)
    home = str(Path.home())
    return [p.replace(home, "~") for p in paths] or f"No files matching {query!r}."


@tool("Read a text file from the user's home folder (first ~20k characters).",
      {"path": _p("Path, ~ allowed")}, ["path"], category="files")
def read_text_file(ctx, path: str):
    target = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    if not target.is_relative_to(home):
        raise PermissionError("I only read files inside your home folder.")
    if target.stat().st_size > 5_000_000:
        raise ValueError("That file is too large to read aloud sensibly.")
    return target.read_text(errors="replace")[:20000]


@tool("Reveal a file or folder in Finder, or open it with its default app.",
      {"path": _p("Path, ~ allowed"), "reveal": _p("true = select in Finder; false = open it", B)},
      ["path"], category="files")
def open_path(ctx, path: str, reveal: bool = False):
    target = str(Path(path).expanduser())
    mac.require_mac()
    subprocess.run(["open", "-R", target] if reveal else ["open", target], check=True)
    return f"{'Revealed' if reveal else 'Opened'} {path}."


# ═══════════════════════════════ media ═══════════════════════════════════════
@tool("Control music playback in Music or Spotify (whichever is running).",
      {"action": _p("Action", enum=["play", "pause", "toggle", "next", "previous", "now_playing"]),
       "app": _p("Force 'Music' or 'Spotify'", enum=["Music", "Spotify"])},
      ["action"], category="media")
def media_control(ctx, action: str, app: str | None = None):
    return pim.media(action, app)


@tool("Play a song, artist, album or playlist by name.",
      {"query": _p("What to play"), "app": _p("Force 'Music' or 'Spotify'", enum=["Music", "Spotify"])},
      ["query"], category="media")
def play_music(ctx, query: str, app: str | None = None):
    return pim.play_music(query, app)


# ═══════════════════════════════ calendar & reminders ════════════════════════
@tool("Get calendar events. Defaults to the rest of today.",
      {"start": _p("ISO 8601 start datetime (local time). Omit for now."),
       "days": _p("How many days to cover (default 1)", I)},
      category="calendar")
def get_calendar(ctx, start: str = "", days: int = 1):
    now = dt.datetime.now().astimezone()
    begin = _parse_when(start) if start else now
    end = (begin + dt.timedelta(days=days)).replace(hour=0, minute=0, second=0) if days >= 1 else begin
    if days == 1 and not start:
        end = now.replace(hour=23, minute=59, second=59)
    events = pim.events_between(begin, end)
    return events or "No events in that window."


@tool("Create a calendar event.",
      {"title": _p("Event title"), "start": _p("ISO 8601 start datetime, local time"),
       "end": _p("ISO 8601 end datetime (optional)"),
       "duration_minutes": _p("Used when end is omitted (default 30)", I),
       "location": _p("Location"), "notes": _p("Notes"), "calendar": _p("Calendar name")},
      ["title", "start"], category="calendar")
def create_calendar_event(ctx, title: str, start: str, end: str = "", duration_minutes: int = 30,
                          location: str = "", notes: str = "", calendar: str = ""):
    s = _parse_when(start)
    e = _parse_when(end) if end else s + dt.timedelta(minutes=duration_minutes)
    return pim.create_event(title, s, e, location, notes, calendar or None)


@tool("List incomplete reminders (to-dos), optionally only those due before a time.",
      {"due_before": _p("ISO 8601 datetime")}, category="calendar")
def get_reminders(ctx, due_before: str = ""):
    items = pim.reminders(_parse_when(due_before) if due_before else None)
    return items or "No outstanding reminders."


@tool("Create a reminder in Apple Reminders (syncs to iPhone). Use for anything the user wants to remember "
      "beyond this session.",
      {"title": _p("What to remember"), "due": _p("ISO 8601 due datetime, local time"),
       "notes": _p("Notes"), "list": _p("Reminders list name")},
      ["title"], category="calendar")
def create_reminder(ctx, title: str, due: str = "", notes: str = "", list: str = ""):
    return pim.create_reminder(title, _parse_when(due) if due else None, notes, list or None)


# ═══════════════════════════════ communication ═══════════════════════════════
@tool("Send an iMessage/SMS via the Messages app. Recipient can be a contact name, phone number or email.",
      {"recipient": _p("Contact name, phone or email"), "text": _p("Message text")},
      ["recipient", "text"], confirm=True,
      confirm_message=lambda a: f"send {a.get('recipient')} the message: {a.get('text')}",
      category="communication")
def send_message(ctx, recipient: str, text: str):
    return pim.send_imessage(recipient, text)


@tool("Look up a contact's phone number and email.", {"name": _p("Name")}, ["name"], category="communication")
def find_contact(ctx, name: str):
    return pim.find_contact(name) or f"No contact matching {name}."


@tool("Save a note in Apple Notes.", {"title": _p("Title"), "body": _p("Note text")}, ["title", "body"],
      category="communication")
def create_note(ctx, title: str, body: str):
    return pim.create_note(title, body)


# ═══════════════════════════════ information ═════════════════════════════════
@tool("Current weather and forecast. Defaults to the user's home city.",
      {"location": _p("City name (optional)")}, category="info")
def get_weather(ctx, location: str = ""):
    from jarvis.integrations.weather import forecast

    cfg = ctx.cfg
    if location:
        return forecast(location, units=cfg.get("location.units", "metric"))
    return forecast(cfg.get("location.city"), cfg.get("location.latitude"), cfg.get("location.longitude"),
                    cfg.get("location.units", "metric"))


@tool("Top headlines from the user's news feeds. For specific or breaking news, prefer web_search.",
      {"limit": _p("How many (default 5)", I)}, category="info")
def get_news(ctx, limit: int = 5):
    from jarvis.integrations.news import headlines

    return headlines(ctx.cfg.get("greeting.news_feeds", []), limit=limit)


# ═══════════════════════════════ time ════════════════════════════════════════
@tool("Start a countdown timer. JARVIS announces it out loud when done.",
      {"seconds": _p("Duration in seconds", I), "label": _p("Optional label, e.g. 'pasta'")},
      ["seconds"], category="time")
def set_timer(ctx, seconds: int, label: str = ""):
    t = ctx.scheduler.add_timer(seconds, label)
    return f"Timer '{t['message']}' set."


@tool("Set a spoken alarm/reminder at a specific time today or later (JARVIS says it out loud). "
      "For reminders that should sync to the phone use create_reminder.",
      {"time": _p("ISO 8601 datetime, local time"), "message": _p("What to say")},
      ["time", "message"], category="time")
def set_alarm(ctx, time: str, message: str):
    when = _parse_when(time)
    ctx.scheduler.add_alarm(when.timestamp(), message)
    return f"Alarm set for {when:%-I:%M %p on %A}."


@tool("List active timers and alarms.", category="time")
def list_timers(ctx):
    return ctx.scheduler.list() or "No active timers."


@tool("Cancel timers/alarms. With no label, cancels all.", {"label": _p("Label text to match")}, category="time")
def cancel_timer(ctx, label: str = ""):
    n = ctx.scheduler.cancel(label=label or None)
    return f"Cancelled {n} timer{'s' if n != 1 else ''}."


# ═══════════════════════════════ memory ══════════════════════════════════════
@tool("Save a lasting fact or preference about the user to long-term memory.",
      {"fact": _p("The fact, phrased in third person, e.g. 'Takes coffee black'"),
       "category": _p("e.g. preference, person, work, health, home")},
      ["fact"], category="memory")
def remember(ctx, fact: str, category: str = "general"):
    fid = ctx.memory.remember(fact, category)
    return f"Saved (#{fid})."


@tool("Search long-term memory for facts about the user.", {"query": _p("What to look for")}, ["query"],
      category="memory")
def recall(ctx, query: str):
    return ctx.memory.recall(query) or "Nothing relevant in memory."


@tool("Delete facts from long-term memory that match some text.", {"matching": _p("Text to match")},
      ["matching"], category="memory")
def forget(ctx, matching: str):
    n = ctx.memory.forget(matching=matching)
    return f"Forgot {n} item{'s' if n != 1 else ''}."


# ═══════════════════════════════ vision ══════════════════════════════════════
@tool("Look through the Mac's camera. Use when the user asks what you can see, what they're holding, "
      "how they look, or to read something they hold up.",
      {"focus": _p("Optional note on what to pay attention to")}, category="vision")
async def look(ctx, focus: str = ""):
    cam = ctx.camera
    if cam is None or not cam.available:
        return "The camera isn't available right now."
    await asyncio.sleep(0.15)  # let auto-exposure settle on a fresh frame
    data = await asyncio.to_thread(cam.jpeg, 85, 1280)
    if not data:
        return "I couldn't get a frame from the camera."
    caption = "Live camera frame (mirrored, selfie view)."
    if ctx.faces is not None:
        names = [f.name or "unknown person" for f in ctx.faces.snapshot()]
        if names:
            caption += f" Face recognition: {', '.join(names)}."
    return ImageResult(data, "image/jpeg", caption)


@tool("Who is in front of the Mac right now, according to face recognition.", category="vision")
def who_is_here(ctx):
    if ctx.faces is None or not ctx.faces.available:
        return "Face recognition isn't running."
    faces = ctx.faces.snapshot()
    if not faces:
        return "Nobody is in view."
    return [{"name": f.name or "unknown", "confidence": round(f.similarity, 2)} for f in faces]


@tool("Learn a new person's face (they should look at the camera for a few seconds).",
      {"name": _p("Their name")}, ["name"], category="vision")
async def learn_face(ctx, name: str):
    cam, faces = ctx.camera, ctx.faces
    if cam is None or not cam.available or faces is None or not faces.available:
        return "The camera or face engine isn't available."
    frames = []
    for _ in range(15):
        f = cam.latest()
        if f is not None:
            frames.append(f)
        await asyncio.sleep(0.2)
    n = await asyncio.to_thread(faces.enroll, name, frames)
    return f"Learned {name} from {n} samples." if n else "I couldn't get a clear view of a single face."


# ═══════════════════════════════ protocols ═══════════════════════════════════
@tool("Run a named protocol (a user-defined routine), e.g. 'work mode', 'house party protocol', 'good night'.",
      {"name": _p("Protocol name or alias")}, ["name"], category="protocols")
async def run_protocol(ctx, name: str):
    return await ctx.protocols.run(name, registry, ctx)


@tool("List the user's protocols (routines).", category="protocols")
def list_protocols(ctx):
    ctx.protocols.reload()
    return ctx.protocols.list()


# ═══════════════════════════════ email ═══════════════════════════════════════
@tool("Check the user's email (Apple Mail, every account) and find what needs their attention. Returns "
      "candidate unread messages, best first, already scored on this Mac. Pick AT MOST the requested number "
      "(default 3) that genuinely need the user: a real person waiting on them, a deadline, a payment, an "
      "approval, a question, something time-sensitive. Skip newsletters, promotions, receipts and automated "
      "notices. For each one say who it's from, what it's about in a few words, and the next step you suggest. "
      "If nothing needs them, say so plainly. Never read message ids aloud.",
      {"top": _p("How many to pick (default 3)", I), "days": _p("How far back to look, in days (default 3)", I)},
      category="communication")
def check_email(ctx, top: int = 3, days: int = 3):
    from jarvis.mac import mail

    got = mail.unread(days=max(1, min(int(days or 3), 14)))
    if isinstance(got, str):
        return got
    if not got:
        return f"No unread email in the last {days} days."
    keep = got[:10]
    return {"pick_at_most": max(1, min(int(top or 3), 5)), "unread_total": len(got),
            "candidates": [{k: m[k] for k in ("id", "from", "address", "subject", "received", "flagged",
                                              "mailing_list", "excerpt", "score")} for m in keep]}


@tool("Read one email in full (use the id from check_email), e.g. when the user says 'read me the second one'.",
      {"id": _p("Message id from check_email")}, ["id"], category="communication")
def read_email(ctx, id: str):
    from jarvis.mac import mail

    m = mail.read(id)
    return m or "I couldn't find that email any more."


@tool("Open an email in the Mail app on screen (use the id from check_email).",
      {"id": _p("Message id from check_email")}, ["id"], category="communication")
def open_email(ctx, id: str):
    from jarvis.mac import mail

    return mail.open_message(id)


# ═══════════════════════════════ on-screen guidance ══════════════════════════
@tool("Find buttons, menus, fields, tabs or text on screen in the frontmost app (then anywhere on screen), "
      "to point at them. Returns the best matches with an id, label, kind and where they are (menu bar, "
      "window, menu, toolbar). Use when the user asks how to do something on their Mac or where something is.",
      {"query": _p("What to look for, in the app's own words when you know them, e.g. 'AutoSum', 'Format', "
                   "'Share', 'Export as PDF'")}, ["query"], category="guide")
async def find_on_screen(ctx, query: str):
    g = getattr(ctx.orchestrator, "guide", None)
    if g is None:
        return "On-screen guidance isn't available."
    hits = await g.find(query)
    if not hits:
        return {"matches": [], "advice": "Nothing like that is on screen. It may be inside a menu that isn't open "
                                         "yet (point at the menu first), in another app, or need the window open."}
    return {"matches": hits}


@tool("Show the user where to click: a light flies to the item, circles it and shows a caption; your `say` "
      "line is spoken as it arrives. Waits until they click it (or about a minute) and reports back. Guide one "
      "step per call; after a click that opens a menu or window, call find_on_screen again for the next step. "
      "Pass the same `task` for every step of a walkthrough so it's remembered: the first time it's shown in "
      "full, then only as a hint, and after a few times they'll know it.",
      {"target": _p("An id from find_on_screen (best), or a short description of the item"),
       "say": _p("One short spoken sentence: what to click and why. Not read aloud in hint mode."),
       "task": _p("What they're trying to do, the same for every step, e.g. 'total a column in Numbers'"),
       "step": _p("This step's number", I), "total": _p("How many steps in all (your best estimate)", I),
       "style": _p("auto (default: full the first time, then hints), full or hint",
                   enum=["auto", "full", "hint"])},
      ["target", "say"], category="guide")
async def point_at(ctx, target: str, say: str, task: str = "", step: int = 1, total: int = 1, style: str = "auto"):
    g = getattr(ctx.orchestrator, "guide", None)
    if g is None:
        return "On-screen guidance isn't available."
    return await g.point(target, say=say, task=task, step=max(1, int(step)), total=max(1, int(total), int(step)),
                         style=style)


@tool("Check or forget a remembered walkthrough. 'check' says whether you've shown them this before and how "
      "often they've done it (so you can offer to let them try alone). 'forget' makes the next one full again.",
      {"action": _p("check or forget", enum=["check", "forget"]),
       "task": _p("The task, as you named it in point_at; empty with forget = forget them all")},
      ["action"], category="guide")
async def walkthrough_memory(ctx, action: str, task: str = ""):
    g = getattr(ctx.orchestrator, "guide", None)
    if g is None:
        return "On-screen guidance isn't available."
    return g.forget(task) if action == "forget" else g.known(task)


# ═══════════════════════════════ hand control ════════════════════════════════
@tool("Turn hand control on or off. Hand control covers every gesture: held poses (✋ stop, 👍/👎 yes/no, "
      "✌️ listen, 🤟 play/pause), swipes for tracks and pinch-and-drag for volume. It is off unless the user "
      "asks for it. The pointer (air mouse) is separate: use air_mouse.",
      {"on": _p("true to turn on, false to turn off", B)}, ["on"], category="self")
async def hand_control(ctx, on: bool):
    orch = ctx.orchestrator
    if orch is None:
        return "No orchestrator attached."
    return orch.set_hand_control(bool(on))


@tool("Start or stop the air mouse: the user drives the Mac's pointer with their index finger and pinches to "
      "click. It's a separate mode from hand control (which it switches on); while it runs, other gestures "
      "pause. It ends when they hold up a palm, take their hand away, or ask.",
      {"on": _p("true to start, false to stop", B)}, ["on"], category="self")
async def air_mouse(ctx, on: bool):
    orch = ctx.orchestrator
    if orch is None:
        return "No orchestrator attached."
    return orch.set_air_mouse(bool(on))


@tool("Update JARVIS to the latest version: downloads the newest verified build, installs it (keeping "
      "every permission) and restarts. JARVIS goes quiet for a minute or two.", category="self", confirm=True,
      confirm_message="update myself and restart")
async def update_jarvis(ctx):
    from jarvis.app.updater import start_update

    await ctx.say("Updating now. I'll be back in a minute or two.")
    start_update(ctx.cfg.home)
    return "Update started; JARVIS will restart when it's installed."


# ═══════════════════════════════ self control ════════════════════════════════
@tool("Control JARVIS himself: 'standby' (stop listening until the wake word), 'mute_mic', 'unmute_mic', "
      "'new_conversation' (forget this conversation's context), 'quiet' (stop talking).",
      {"action": _p("Action", enum=["standby", "mute_mic", "unmute_mic", "new_conversation", "quiet"])},
      ["action"], category="self")
async def jarvis_control(ctx, action: str):
    orch = ctx.orchestrator
    if orch is None:
        return "No orchestrator attached."
    return await orch.control(action)
