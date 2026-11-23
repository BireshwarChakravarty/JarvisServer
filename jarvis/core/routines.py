"""What JARVIS knows about your habits, from your own past requests.

"You usually ask about the weather around eight on weekdays." Found by looking at what you asked over
the last few weeks: the same kind of request at about the same time on several different days. JARVIS
uses it the way a person who knows you would, mostly by having the answer ready (the morning greeting
mentions the weather before you ask), and mentions a habit itself at most rarely. Nothing leaves the
Mac; it's computed from the conversation log that's already stored locally.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

TOPICS = [
    ("the weather", r"\b(weather|rain|umbrella|forecast|temperature|cold|hot outside)\b"),
    ("your calendar", r"\b(calendar|meetings?|schedule|agenda|what'?s (on )?today|my day)\b"),
    ("your email", r"\b(e-?mails?|inbox|mail)\b"),
    ("WhatsApp", r"\bwhats ?app\b"),
    ("the news", r"\b(news|headlines)\b"),
    ("music", r"\b(play|music|song|playlist|spotify)\b"),
    ("a timer", r"\btimer\b"),
    ("reminders", r"\b(remind|reminders?)\b"),
    ("the lights", r"\blights?\b"),
    ("traffic or travel time", r"\b(traffic|commute|how long .* (to|get)|leave for)\b"),
]

DAYPARTS = [(5, 9, "early morning"), (9, 12, "morning"), (12, 14, "midday"), (14, 18, "afternoon"),
            (18, 22, "evening"), (22, 29, "late at night")]


def topic_of(text: str) -> str | None:
    t = (text or "").lower()
    for name, pattern in TOPICS:
        if re.search(pattern, t):
            return name
    return None


def _hour_words(h: float) -> str:
    hour = int(round(h)) % 24
    shown = hour % 12 or 12
    return f"around {shown} {'in the morning' if hour < 12 else 'in the afternoon' if hour < 18 else 'in the evening'}"


def find(turns: list[dict], min_days: int = 4, window_hours: float = 1.5) -> list[dict]:
    """Habits in `turns` ([{role, text, ts}], your requests): a topic asked about on at least
    `min_days` different days within about `window_hours` of the same time."""
    by_topic: dict[str, list[dt.datetime]] = defaultdict(list)
    for t in turns:
        if t.get("role") != "user":
            continue
        topic = topic_of(t.get("text", ""))
        if topic:
            by_topic[topic].append(dt.datetime.fromtimestamp(t["ts"]))
    habits = []
    for topic, times in by_topic.items():
        best = None
        for anchor in times:
            a = anchor.hour + anchor.minute / 60
            near = [x for x in times if abs((x.hour + x.minute / 60) - a) <= window_hours]
            days = {x.date() for x in near}
            if len(days) >= min_days and (best is None or len(days) > best[1]):
                best = (sum(x.hour + x.minute / 60 for x in near) / len(near), len(days), near)
        if best:
            mean_hour, n_days, near = best
            weekdays = sum(1 for x in near if x.weekday() < 5) / len(near)
            when = "on weekdays" if weekdays > 0.85 else "at weekends" if weekdays < 0.15 else ""
            habits.append({"topic": topic, "hour": round(mean_hour, 2), "days": n_days,
                           "text": f"usually asks about {topic} {_hour_words(mean_hour)}{(' ' + when) if when else ''}"})
    habits.sort(key=lambda h: -h["days"])
    return habits[:5]


def from_memory(memory, days: int = 28) -> list[dict]:
    since = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    try:
        turns = memory.conversations(since=since, limit=4000)
    except Exception:
        return []
    return find(turns)


def due_now(habits: list[dict], now: dt.datetime | None = None, within_hours: float = 1.5) -> list[str]:
    """Topics you usually ask about at about this time: worth having ready (e.g. in the greeting)."""
    now = now or dt.datetime.now()
    h = now.hour + now.minute / 60
    return [x["topic"] for x in habits if abs(x["hour"] - h) <= within_hours]


# ── "what did I ask you yesterday?" ─────────────────────────────────────────────────────────────────
def period(when: str, now: dt.datetime | None = None) -> tuple[float, float, str]:
    """'yesterday', 'today', 'this morning', 'last week', 'monday', 'last night' → (since, until, label)."""
    now = now or dt.datetime.now()
    w = (when or "").strip().lower()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if not w or w in ("recently", "lately"):
        return (now - dt.timedelta(days=7)).timestamp(), now.timestamp(), "in the last week"
    if w in ("today",):
        return day.timestamp(), now.timestamp(), "today"
    if w in ("this morning",):
        return day.timestamp(), day.replace(hour=12).timestamp(), "this morning"
    if w in ("yesterday",):
        return (day - dt.timedelta(days=1)).timestamp(), day.timestamp(), "yesterday"
    if w in ("last night",):
        return (day - dt.timedelta(hours=6)).timestamp(), (day + dt.timedelta(hours=4)).timestamp(), "last night"
    if w in ("this week",):
        start = day - dt.timedelta(days=day.weekday())
        return start.timestamp(), now.timestamp(), "this week"
    if w in ("last week",):
        start = day - dt.timedelta(days=day.weekday() + 7)
        return start.timestamp(), (start + dt.timedelta(days=7)).timestamp(), "last week"
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, n in enumerate(names):
        if n in w:
            back = (day.weekday() - i) % 7 or 7
            start = day - dt.timedelta(days=back)
            return start.timestamp(), (start + dt.timedelta(days=1)).timestamp(), f"on {n.title()}"
    m = re.search(r"(\d+)\s+days?\s+ago", w)
    if m:
        start = day - dt.timedelta(days=int(m.group(1)))
        return start.timestamp(), (start + dt.timedelta(days=1)).timestamp(), w
    return (now - dt.timedelta(days=30)).timestamp(), now.timestamp(), "in the last month"
