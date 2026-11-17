"""Gathers everything JARVIS needs for a greeting / daily briefing, in parallel,
with tight timeouts so a slow source never delays the welcome."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from jarvis.core.persona import part_of_day

log = logging.getLogger("jarvis.briefing")


async def _safe(name: str, fn, *args, timeout: float = 6.0) -> tuple[str, Any]:
    try:
        return name, await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout)
    except Exception as exc:
        log.info("briefing source %s skipped: %s", name, exc)
        return name, None


def _weather(cfg) -> dict:
    from jarvis.integrations.weather import forecast

    return forecast(cfg.get("location.city"), cfg.get("location.latitude"), cfg.get("location.longitude"),
                    cfg.get("location.units", "metric"))


def _events_today() -> list[dict]:
    from jarvis.mac.pim import events_between

    now = dt.datetime.now().astimezone()
    end = now.replace(hour=23, minute=59, second=59)
    return [e for e in events_between(now - dt.timedelta(minutes=10), end) if not e.get("all_day")][:8]


def _events_tomorrow() -> list[dict]:
    from jarvis.mac.pim import events_between

    start = (dt.datetime.now().astimezone() + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0)
    return events_between(start, start + dt.timedelta(days=1))[:5]


def _reminders_due() -> list[dict]:
    from jarvis.mac.pim import reminders

    end = dt.datetime.now().astimezone().replace(hour=23, minute=59)
    return reminders(due_before=end, limit=8)


def _battery() -> dict:
    from jarvis.mac.system import battery

    b = battery()
    b.pop("raw", None)
    return b


def _mail() -> int | None:
    from jarvis.mac import thunderbird

    return thunderbird.unread_count(days=2)


def _mail_attention() -> list[dict] | None:
    """Top unread mail by this Mac's score, from Thunderbird's local copy (never opens anything)."""
    from jarvis.mac import thunderbird

    got = thunderbird.unread(days=2, limit=30)
    if not isinstance(got, list):
        return None
    top = [m for m in got if m["score"] >= 2][:3]
    return [{"from": m["from"], "subject": m["subject"]} for m in top] or None


def _news(cfg) -> list[str]:
    from jarvis.integrations.news import headlines

    return [h["title"] for h in headlines(cfg.get("greeting.news_feeds", []), limit=4)]


async def gather(cfg, memory=None, kind: str = "login") -> dict:
    pod = part_of_day()
    jobs = [
        _safe("weather", _weather, cfg),
        _safe("battery", _battery, timeout=3),
        _safe("unread_mail", _mail, timeout=4),
    ]
    if kind == "login" and cfg.get("greeting.email", True):
        jobs.append(_safe("email_needing_attention", _mail_attention, timeout=20))
    if pod in ("morning", "afternoon", "evening"):
        jobs += [_safe("calendar_today", _events_today, timeout=10), _safe("reminders_due", _reminders_due, timeout=10)]
    if pod in ("evening", "late_night"):
        jobs.append(_safe("calendar_tomorrow", _events_tomorrow, timeout=10))
    if pod == "morning" and kind == "login":
        jobs.append(_safe("headlines", _news, cfg, timeout=6))
    results = dict(await asyncio.gather(*jobs))
    data: dict[str, Any] = {"part_of_day": pod, "occasion": kind}
    for k, v in results.items():
        if v not in (None, [], {}):
            data[k] = v
    w = data.get("weather")
    if isinstance(w, dict):  # trim for the prompt
        data["weather"] = {k: w[k] for k in ("location", "temperature", "unit", "condition", "high", "low",
                                             "rain_chance_12h", "sunset") if k in w}
    if memory is not None:
        last = memory.recent_summaries(limit=1)
        if last:
            data["last_conversation"] = last[-1]["summary"]
    return data


def offline_extras(data: dict) -> list[str]:
    """Short spoken lines from briefing data, for when Claude is unreachable."""
    out = []
    w = data.get("weather")
    if w:
        out.append(f"It's {w['temperature']} degrees and {w['condition']} outside.")
    events = data.get("calendar_today") or []
    if events:
        first = events[0]
        out.append(f"You have {len(events)} more event{'s' if len(events) != 1 else ''} today, "
                   f"starting with {first['title']} at {first['time'].split(' – ')[0]}.")
    b = data.get("battery") or {}
    if b.get("percent") is not None and b["percent"] < 25 and b.get("state") != "charging":
        out.append(f"Battery is at {b['percent']} percent.")
    return out
