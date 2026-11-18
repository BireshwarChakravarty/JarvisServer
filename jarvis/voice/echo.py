"""Recognise JARVIS's own voice in what the microphone heard.

On speakers (rather than headphones) the end of what he says can reach the microphone just as he
goes back to listening. The listener waits for the room to go quiet first (audio.settle), and this
is the safety net on the words themselves:

  • heard starts with the last words he said, then yours  →  his part is removed, yours is kept
  • heard is only a run of his own words (three or more)   →  dropped
  • heard is exactly one whole line of his ("Sir?")         →  dropped
  • short replies are never dropped: "send it" after "Shall I send it?" is you answering him
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("’", "'"))


def _contiguous_in(needle: list[str], hay: list[str]) -> bool:
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def strip_echo(heard: str, said: list[str]) -> str:
    """`heard` minus any of `said` (his recent lines, newest last) that leaked back in."""
    hw = words(heard)
    if not hw or not said:
        return heard
    recent = [words(s) for s in said[-3:] if s]
    if len(hw) >= 3 and any(_contiguous_in(hw, ws) for ws in recent):
        return ""                                            # only his own words
    if any(hw == ws for ws in recent):
        return ""                                            # the whole of one of his lines ("Sir?")
    best = 0
    for ws in recent[-2:]:
        for k in range(min(len(ws), len(hw) - 1), 1, -1):    # at least two of his words, and some of yours after
            if hw[:k] == ws[-k:]:
                best = max(best, k)
                break
    if not best:
        return heard
    tokens = heard.split()
    consumed, i = 0, 0
    while i < len(tokens) and consumed < best:
        consumed += len(words(tokens[i]))
        i += 1
    return " ".join(tokens[i:]).strip(" ,.;:-")
