"""Recognise JARVIS's own voice in what the microphone heard.

On speakers (rather than headphones) the end of what he says can reach the microphone just as he
goes back to listening. The listener waits for the room to go quiet first (audio.settle), and this
is the safety net on the words themselves:

  • heard starts with the last words he said, then yours  →  his part is removed, yours is kept
  • heard is only a run of his own words (three or more)   →  dropped
  • heard is exactly one whole line of his ("Sir?")         →  dropped
  • close matches count too: Whisper rarely hears an echo word-perfect ("the board meets at ten" for
    "the board meets at 10")
  • only ever applied to audio heard just after he spoke (the caller decides): later, you're free to
    repeat his words
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


def _matched(a: list[str], b: list[str]) -> int:
    """How many of a's words appear, in order, in b, counting near-misses as matches (speech
    recognition rarely hears an echo word-perfect). Longest common subsequence, words compared
    with _near()."""
    if not a or not b:
        return 0
    a, b = a[:80], b[:120]
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if _near(x, y) else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def _mostly_his(hw: list[str], ws: list[str]) -> bool:
    """Three or more words, nearly all of them his, in his order: his voice, misheard a little."""
    return len(hw) >= 3 and _matched(hw, ws) >= max(3, 0.75 * len(hw))


def _near(a: str, b: str) -> bool:
    """The same word as speech recognition might hear it twice ("pepper" / "peppers", "ten" stays apart)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    from difflib import SequenceMatcher

    return a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.8


def _echo_prefix(hw: list[str], ws: list[str]) -> int:
    """Length of the start of `hw` that is the (roughly heard) end of his line `ws`; 0 if none.
    The prefix must end on one of his last words. len(hw) means all of it is his."""
    for k in range(min(len(hw), len(ws) + 2), 1, -1):
        if k == len(hw) and k < 3:
            continue                                     # two words alone could be you answering him
        tail = ws[-(k + 2):]
        if hw[:k] == ws[-k:] or (_matched(hw[:k], tail) >= max(2, 0.75 * k)
                                 and any(_near(hw[k - 1], w) for w in tail[-3:])):
            return k
    return 0


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
        best = max(best, _echo_prefix(hw, ws))
    if best >= len(hw):
        return ""                                            # the end of his line, slightly misheard
    tail = 0
    for ws in recent[-2:]:
        tail = max(tail, _echo_suffix(hw[best:], ws))        # his voice still in the room after yours
    if not best and not tail:
        if any(_mostly_his(hw, ws) for ws in recent):
            return ""                                        # his words, slightly misheard
        return heard
    return _cut(heard, best, len(hw) - tail)


def _echo_suffix(hw: list[str], ws: list[str]) -> int:
    """Length of the end of `hw` that is the (roughly heard) end of his line `ws`: on a long echo
    your quick answer lands in the middle of his fading voice. Some of your words stay before it."""
    for k in range(min(len(hw) - 1, len(ws) + 2), 1, -1):
        tail = ws[-(k + 2):]
        if hw[-k:] == ws[-k:] or (_matched(hw[-k:], tail) >= max(2, 0.75 * k)
                                  and any(_near(hw[-1], w) for w in tail[-2:])):
            return k
    return 0


def _cut(heard: str, start: int, end: int) -> str:
    """The original text of words[start:end] (keeping its spelling and punctuation)."""
    out, n = [], 0
    for tok in heard.split():
        c = len(words(tok))
        if start <= n and (n + c <= end or (c == 0 and n < end)) and c:
            out.append(tok)
        n += c
    return " ".join(out).strip(" ,.;:-")
