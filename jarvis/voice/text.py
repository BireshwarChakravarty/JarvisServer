"""Text utilities for speech: incremental sentence splitting for streaming TTS,
and cleanup so the voice never reads out markdown or URLs."""
from __future__ import annotations

import re

ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "approx",
    "no", "inc", "ltd", "co", "mt", "ft", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "a.m", "p.m",
}

_URL = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_SYMBOLS = re.compile(r"(\*\*|__|\*|`{1,3}|^#{1,6}\s*|^\s*[-•]\s+|^\s*\d+\.\s+(?=\w))", re.M)
_SPACES = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("the link", text)
    text = _MD_SYMBOLS.sub("", text)
    text = text.replace("&", " and ").replace(" -- ", ", ").replace("—", ", ").replace("–", " to ")
    text = re.sub(r"(?<=\d)\s*°C", " degrees", text)
    text = re.sub(r"(?<=\d)\s*°F", " degrees Fahrenheit", text)
    text = re.sub(r"(?<=\d)\s*°", " degrees", text)
    text = re.sub(r"(?<=\d)%", " percent", text)
    return _SPACES.sub(" ", text).strip()


class SentenceSplitter:
    """Feed streamed text deltas; get back complete sentences as soon as they end."""

    def __init__(self, min_chars: int = 12, soft_limit: int = 160) -> None:
        self.buf = ""
        self.min_chars = min_chars
        self.soft_limit = soft_limit

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sentence, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []

    def _find_cut(self) -> int | None:
        buf = self.buf
        # hard break on newlines (paragraphs / list items)
        nl = buf.find("\n")
        if nl != -1 and buf[:nl].strip():
            return nl + 1
        if nl != -1 and not buf[:nl].strip():
            self.buf = buf[nl + 1:]
            return self._find_cut() if self.buf else None
        for m in re.finditer(r"[.!?…]+[\"')\]]*(?=\s)", buf):
            end = m.end()
            if end < self.min_chars:
                continue
            word = re.findall(r"([\w.]+)[.!?…]+[\"')\]]*$", buf[:end])
            if word and m.group(0).startswith(".") and word[0].lower().rstrip(".") in ABBREVIATIONS:
                continue
            # decimals like 3.5 never match because we require whitespace after the period
            return end
        if len(buf) > self.soft_limit:
            # long run-on: break at the last comma/semicolon/colon
            idx = max(buf.rfind(", ", 0, self.soft_limit), buf.rfind("; ", 0, self.soft_limit),
                      buf.rfind(": ", 0, self.soft_limit))
            if idx > self.min_chars:
                return idx + 1
        return None
