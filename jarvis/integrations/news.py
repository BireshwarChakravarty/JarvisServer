"""Headlines from RSS/Atom feeds (stdlib only, no keys)."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import httpx

_cache: dict[str, tuple[float, list[dict]]] = {}
ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml_text: str, fallback_source: str = "") -> list[dict]:
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    channel = root.find("channel")
    if channel is not None:  # RSS 2.0
        source = (channel.findtext("title") or fallback_source).strip()
        for it in channel.findall("item"):
            items.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                          "source": source})
    else:  # Atom
        source = (root.findtext(f"{ATOM}title") or fallback_source).strip()
        for e in root.findall(f"{ATOM}entry"):
            link = e.find(f"{ATOM}link")
            items.append({"title": (e.findtext(f"{ATOM}title") or "").strip(),
                          "link": link.get("href", "") if link is not None else "", "source": source})
    return [i for i in items if i["title"]]


def headlines(feeds: list[str], limit: int = 6) -> list[dict]:
    items: list[dict] = []
    for url in feeds:
        hit = _cache.get(url)
        if hit and time.time() - hit[0] < 900:
            entries = hit[1]
        else:
            try:
                r = httpx.get(url, timeout=8, follow_redirects=True, headers={"User-Agent": "JARVIS/1.0"})
                r.raise_for_status()
                entries = parse_feed(r.text, url)[: limit * 2]
                _cache[url] = (time.time(), entries)
            except Exception:
                entries = []
        items.extend(entries[:limit])
    by_source: dict[str, list[dict]] = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)
    merged: list[dict] = []
    while any(by_source.values()) and len(merged) < limit:
        for src in list(by_source):
            if by_source[src]:
                merged.append(by_source[src].pop(0))
    return merged[:limit]
