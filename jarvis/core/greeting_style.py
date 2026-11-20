"""Keep JARVIS's greetings fresh: a different angle each time, what changed since he last spoke,
his own recent openings to steer away from, and no dull filler.

Pure functions over the briefing data and a small memory record, so they're easy to test.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any

RECENT_KEY = "recent_greetings"      # [{"text", "angle", "ts"}] newest last
SNAPSHOT_KEY = "last_briefing"       # the data he briefed from last time


def trim(data: dict) -> dict:
    """Drop what's never worth saying: power status unless the battery is actually low, etc."""
    d = dict(data)
    b = d.get("battery")
    if not (isinstance(b, dict) and b.get("percent") is not None and b["percent"] < 25
            and b.get("state") != "charging"):
        d.pop("battery", None)
    if not d.get("unread_mail"):
        d.pop("unread_mail", None)
    if not d.get("whatsapp_waiting"):
        d.pop("whatsapp_waiting", None)
    return d


def changes(now: dict, before: dict | None) -> list[str]:
    """What's different since the last briefing, in plain words."""
    if not before:
        return []
    out: list[str] = []
    w1, w0 = now.get("weather") or {}, before.get("weather") or {}
    try:
        if w1 and w0 and abs(float(w1["temperature"]) - float(w0["temperature"])) >= 4:
            out.append(f"temperature moved from {w0['temperature']} to {w1['temperature']}")
    except (KeyError, TypeError, ValueError):
        pass
    if w1 and w0 and w1.get("condition") and w1.get("condition") != w0.get("condition"):
        out.append(f"weather turned {w1['condition']} (was {w0.get('condition')})")
    t1 = {e.get("title") for e in now.get("calendar_today") or [] if isinstance(e, dict)}
    t0 = {e.get("title") for e in before.get("calendar_today") or [] if isinstance(e, dict)}
    new = [t for t in t1 - t0 if t]
    if new:
        out.append("new on the calendar: " + ", ".join(sorted(new)[:3]))
    try:
        m1, m0 = int(now.get("unread_mail") or 0), int(before.get("unread_mail") or 0)
        if m1 - m0 >= 5:
            out.append(f"{m1 - m0} new unread emails")
    except (TypeError, ValueError):
        pass
    return out


ANGLES = {
    "agenda": "Lead with what's next on the calendar and why it matters right now.",
    "weather": "Lead with the weather, but only because it changes something about the day.",
    "change": "Lead with what has changed since you last spoke.",
    "absence": "Lead with how long they've been away, lightly, then what they missed.",
    "day": "Lead with the character of the day (a Monday, a Friday, a weekend, late at night).",
    "thread": "Pick up the thread of your last conversation and offer to continue it.",
    "headline": "Lead with one headline worth their attention.",
    "minimal": "Be brief: a crisp hello and one useful offer. No briefing unless something is urgent.",
    "mail": "Lead with the email that most needs them (who and what, in a few words), then offer to read it.",
    "whatsapp": "Lead with who's waiting on them on WhatsApp (names and what they asked, briefly), then offer to "
                "read or reply.",
}


def pick_angle(data: dict, delta: list[str], away_for: float | None, recent: list[dict],
               now: dt.datetime | None = None, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    now = now or dt.datetime.now()
    weights: dict[str, float] = {"minimal": 1.0, "day": 0.8}
    if data.get("calendar_today") or data.get("calendar_tomorrow"):
        weights["agenda"] = 3.0
    w = data.get("weather") or {}
    if w:
        rainy = (w.get("rain_chance_12h") or 0) >= 50
        extreme = isinstance(w.get("temperature"), (int, float)) and not 5 <= w["temperature"] <= 32
        weights["weather"] = 2.0 if rainy or extreme else 0.6
    if delta:
        weights["change"] = 3.5
    if away_for and away_for > 20 * 3600:
        weights["absence"] = 3.0
    if data.get("last_conversation"):
        weights["thread"] = 1.5
    if data.get("headlines"):
        weights["headline"] = 1.2
    if data.get("email_needing_attention"):
        weights["mail"] = 2.5
    if data.get("whatsapp_waiting"):
        weights["whatsapp"] = 2.2
    if now.weekday() in (0, 4, 5, 6) or now.hour >= 22 or now.hour < 5:
        weights["day"] = 1.6
    for r in recent[-2:]:                      # not the same angle as the last two greetings
        if len(weights) > 2:
            weights.pop(r.get("angle"), None)
    names = list(weights)
    return rng.choices(names, [weights[n] for n in names])[0]


def instructions(title: str, kind: str, data: dict, delta: list[str], angle: str, recent: list[dict],
                 away_text: str, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    length = rng.choice([25, 35, 45, 60]) if kind == "login" else rng.choice([12, 20, 28])
    avoid = [r["text"] for r in recent[-4:] if r.get("text")]
    occasion = (f"{title} has just logged in for the {data.get('part_of_day', '').replace('_', ' ')}"
                if kind == "login" else f"{title} is back at the Mac after about {away_text} away")
    parts = [
        f"{occasion}. Greet them in character.",
        f"Angle for this one: {ANGLES[angle]}",
        "Mention only what's genuinely worth hearing; skip anything empty, routine or dull.",
        f"Keep it to about {length} words.",
        "Close with ONE specific, useful offer or question that follows from what you said (for example "
        "offering to prepare for the next meeting), not a generic 'what would you like to do today'.",
    ]
    if delta:
        parts.append("Changed since you last spoke: " + "; ".join(delta) + ".")
    if avoid:
        parts.append("Your last greetings were, newest last: " + " | ".join(f'"{a}"' for a in avoid)
                     + ". Don't reuse their opening words, structure or closing line.")
    return " ".join(parts)


def remember(memory: Any, text: str, angle: str, data: dict) -> None:
    recent = (memory.get(RECENT_KEY, []) or [])[-5:]
    recent.append({"text": text[:300], "angle": angle, "ts": dt.datetime.now().isoformat(timespec="seconds")})
    memory.set(RECENT_KEY, recent[-6:])
    memory.set(SNAPSHOT_KEY, {k: data.get(k) for k in ("weather", "calendar_today", "unread_mail") if k in data})
