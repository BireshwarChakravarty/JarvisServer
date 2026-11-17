"""Instant answers: the everyday commands a person answers without thinking.

"What time is it?", "Ten minute timer", "Pause", "Next song", "Turn it up", "Lock the Mac" don't need
an AI round trip, and waiting two seconds for one is what makes an assistant feel slow. These are
recognised here with strict, whole-sentence patterns and done straight away; anything that doesn't
match exactly (or carries extra detail) goes to the brain as usual. Pure functions: unit-tested.
"""
from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass, field

NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
           "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
           "twenty five": 25, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}

LEAD = re.compile(r"^(?:(?:hey|ok|okay|hi)\s+)?(?:jarvis[,\s]*)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?", re.I)
TAIL = re.compile(r"[\s,]*(?:please|for me|jarvis|thanks|thank you)?[\s.!?]*$", re.I)


@dataclass
class Quick:
    tool: str | None               # registry tool to call, or None for a spoken answer only
    args: dict = field(default_factory=dict)
    reply: str = ""                # what he says (may be filled after the tool runs)
    note: str = ""                 # one line for the brain's context next turn


def clean(text: str) -> str:
    t = (text or "").strip().lower().replace("’", "'")
    t = LEAD.sub("", t)
    t = TAIL.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def duration(phrase: str) -> int | None:
    """'10 minutes', 'ten minutes', 'an hour and a half', 'half an hour', '90 seconds', '1 hour 20 minutes'."""
    p = phrase.strip().lower().replace("-", " ")
    if p in ("half an hour", "half hour", "a half hour"):
        return 1800
    if p in ("an hour and a half", "one and a half hours", "1.5 hours", "one hour and a half"):
        return 5400
    if p in ("a quarter of an hour", "quarter of an hour", "quarter hour"):
        return 900
    total, found = 0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|[a-z]+(?: [a-z]+)?)\s+(second|sec|minute|min|hour|hr)s?\b", p):
        n = float(num) if re.match(r"\d", num) else NUMBERS.get(num, NUMBERS.get(num.split()[-1]))
        if n is None:
            return None
        total += int(n * UNITS[unit])
        found = True
    if not found:
        return None
    leftover = re.sub(r"(\d+(?:\.\d+)?|[a-z]+(?: [a-z]+)?)\s+(second|sec|minute|min|hour|hr)s?\b", "", p)
    if re.sub(r"\b(and|a|half)\b", "", leftover).strip():
        return None                                           # extra words we don't understand: let the brain
    return total if 0 < total <= 24 * 3600 else None


def say_duration(s: int) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if sec:
        parts.append(f"{sec} second{'s' if sec != 1 else ''}")
    return " and ".join(parts) if len(parts) <= 2 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _pick(options: list[str], rng: random.Random | None) -> str:
    return (rng or random).choice(options)


def match(text: str, now: dt.datetime | None = None, rng: random.Random | None = None) -> Quick | None:
    t = clean(text)
    if not t or len(t) > 60:
        return None
    now = now or dt.datetime.now()

    if re.fullmatch(r"(what'?s the time|what time is it( now| right now)?|what is the time|the time|time|"
                    r"do you have the time)", t):
        return Quick(None, reply=_pick([f"It's {now:%-I:%M %p}.", f"{now:%-I:%M %p}."], rng))
    if re.fullmatch(r"(what'?s the date|what is the date|what'?s today'?s date|what day is (it|today)|"
                    r"what'?s today|what is today|today'?s date)( today)?", t):
        day = now.day
        suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return Quick(None, reply=f"{now:%A}, the {day}{suffix} of {now:%B}.")

    m = (re.fullmatch(r"(?:set|start|put on|make)(?: me)?(?: an?)?(?: (\w+))? timer (?:for|of) (.+)", t)
         or re.fullmatch(r"(?:set |start )?(?:an? )?(.+? (?:second|sec|minute|min|hour|hr)s?)(?: (\w+))? timer", t)
         or re.fullmatch(r"(?:time|timer) (?:for |of )?(.+)", t))
    if m:
        groups = m.groups()
        if m.re.pattern.startswith("(?:set|start|put on"):
            label, phrase = (groups[0] or ""), groups[1]
        elif m.re.pattern.startswith("(?:set |start )?"):
            phrase, label = groups[0], (groups[1] or "").strip()
        else:
            label, phrase = "", groups[0]
        if label in ("a", "an", "the", "new"):
            label = ""
        secs = duration(phrase)
        if secs:
            d = say_duration(secs)
            what = f"{label} timer" if label else "timer"
            reply = _pick([f"{d[0].upper() + d[1:]}, on the clock.", f"Timer set, {d}.", f"{d[0].upper() + d[1:]}. I'll tell you."], rng)
            return Quick("set_timer", {"seconds": secs, "label": label}, reply, note=f"set a {d} {what}")
    if re.fullmatch(r"(cancel|stop|clear|kill)( the| my| all)?( the)? (timers?|alarms?|countdown)", t):
        return Quick("cancel_timer", {}, reply="", note="cancelled the timers")

    if re.fullmatch(r"(pause|pause (the )?(music|song|track|playback)|stop (the )?(music|song|playback))", t):
        return Quick("media_control", {"action": "pause"}, _pick(["Paused.", "Done.", "Pausing."], rng), "paused the music")
    if re.fullmatch(r"(resume|unpause|resume (the )?(music|song|playback)|play (the )?music|"
                    r"carry on playing|keep playing)", t):
        return Quick("media_control", {"action": "play"}, _pick(["Playing.", "Back on.", "Resuming."], rng), "resumed the music")
    if re.fullmatch(r"(skip (this song|this track|the song|the track)|next (song|track)|play the next (song|track))", t):
        return Quick("media_control", {"action": "next"}, _pick(["Next one.", "Skipping.", "Next."], rng), "skipped a track")
    if re.fullmatch(r"(previous (song|track)|last (song|track)|play the (previous|last) (song|track)|back a track)", t):
        return Quick("media_control", {"action": "previous"}, _pick(["Going back.", "Previous one."], rng), "went back a track")

    m = re.fullmatch(r"(?:set |turn )?(?:the )?volume (?:to |at )?(\d{1,3})(?: ?%| percent)?", t)
    if m and 0 <= int(m.group(1)) <= 100:
        return Quick("set_volume", {"level": int(m.group(1))}, _pick([f"Volume {int(m.group(1))}.", "Done."], rng),
                     f"set the volume to {m.group(1)}")
    if re.fullmatch(r"(turn (it|the volume|the music) up|volume up|louder|a bit louder|turn up the (volume|music)|"
                    r"crank it up)", t):
        return Quick("volume_step", {"delta": 15}, _pick(["Louder.", "Turned up.", "Up it goes."], rng), "turned the volume up")
    if re.fullmatch(r"(turn (it|the volume|the music) down|volume down|quieter|a bit quieter|turn down the "
                    r"(volume|music)|lower the volume)", t):
        return Quick("volume_step", {"delta": -15}, _pick(["Quieter.", "Turned down.", "Down a bit."], rng), "turned the volume down")
    if re.fullmatch(r"(mute|mute (the )?(sound|audio|volume|mac|speakers))", t):
        return Quick("mute", {"muted": True}, _pick(["Muted.", "Sound off."], rng), "muted the sound")
    if re.fullmatch(r"(unmute|unmute (the )?(sound|audio|volume|mac|speakers)|sound on)", t):
        return Quick("mute", {"muted": False}, _pick(["Sound's back.", "Unmuted."], rng), "unmuted the sound")

    if re.fullmatch(r"(turn |switch )?(on )?(the )?air ?mouse( on)?|start (the )?air ?mouse", t):
        return Quick("air_mouse", {"on": True}, "", note="started the air mouse")
    if re.fullmatch(r"(turn |switch )?(off )?(the )?air ?mouse off|(turn|switch) off (the )?air ?mouse|"
                    r"stop (the )?air ?mouse", t):
        return Quick("air_mouse", {"on": False}, "", note="stopped the air mouse")
    if re.fullmatch(r"lock( the| my)?( mac| screen| computer)?( now)?", t):
        return Quick("system_action", {"action": "lock_screen"}, _pick(["Locking it now.", "Locking."], rng),
                     note="locked the Mac")
    return None
