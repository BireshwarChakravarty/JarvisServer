"""Headlines from RSS feeds (no keys)."""
from __future__ import annotations

import time

_cache: dict[str, tuple[float, list[dict]]] = {}


def headlines(feeds: list[str], limit: int = 6) -> list[dict]:
    import feedparser

    items: list[dict] = []
    for url in feeds:
        hit = _cache.get(url)
        if hit and time.time() - hit[0] < 900:
            entries = hit[1]
        else:
            try:
                parsed = feedparser.parse(url)
                source = parsed.feed.get("title", url)
                entries = [
                    {"title": e.get("title", "").strip(), "link": e.get("link", ""), "source": source}
                    for e in parsed.entries[: limit * 2]
                ]
                _cache[url] = (time.time(), entries)
            except Exception:
                entries = []
        items.extend(entries[:limit])
    # interleave sources so one feed doesn't dominate
    by_source: dict[str, list[dict]] = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)
    merged: list[dict] = []
    while any(by_source.values()) and len(merged) < limit:
        for src in list(by_source):
            if by_source[src]:
                merged.append(by_source[src].pop(0))
    return merged[:limit]
