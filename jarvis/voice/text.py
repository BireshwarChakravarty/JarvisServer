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
    text = text.replace("&", " and ").replace(" -- ", ", ").replace("—", ", ")
    text = re.sub(r"\s+–\s+(?!\d)", ", ", text)        # "raining – take a coat": a pause, not "to"
    text = text.replace("–", " to ")                    # "2–3pm", "Mon–Fri": a range
    text = re.sub(r"(?<=\d)\s*°C", " degrees", text)
    text = re.sub(r"(?<=\d)\s*°F", " degrees Fahrenheit", text)
    text = re.sub(r"(?<=\d)\s*°", " degrees", text)
    text = re.sub(r"(?<=\d)%", " percent", text)
    return _SPACES.sub(" ", text).strip()


class SentenceSplitter:
    """Feed streamed text deltas; get back complete sentences as soon as they end."""

    def __init__(self, min_chars: int = 12, soft_limit: int = 160, first_clause: int = 60) -> None:
        self.buf = ""
        self.min_chars = min_chars
        self.soft_limit = soft_limit
        # the first thing he says starts sooner: a long opening sentence is spoken from its first
        # natural pause (a comma past ~25 characters) instead of waiting for the full stop
        self.first_clause = first_clause
        self.emitted = False

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
                self.emitted = True
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
        if not self.emitted and self.first_clause and len(buf) > self.first_clause:
            idx = buf.find(", ", 25, self.first_clause + 40)
            if idx != -1:
                return idx + 1
        if len(buf) > self.soft_limit:
            # long run-on: break at the last comma/semicolon/colon
            idx = max(buf.rfind(", ", 0, self.soft_limit), buf.rfind("; ", 0, self.soft_limit),
                      buf.rfind(": ", 0, self.soft_limit))
            if idx > self.min_chars:
                return idx + 1
        return None


# ───────────── spoken form: what the voice says (subtitles keep the written form) ─────────────
_NUM = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
# 7:30, 19:30, 7:30pm, and the British 7.30pm (a dot only counts when am/pm follows: "2.10" is a number)
_TIME = re.compile(r"(?<![\d:.])\b([01]?\d|2[0-3])(?::([0-5]\d)(?:\s*([AaPp])\.?\s?[Mm]\.?)?|\.([0-5]\d)\s*([AaPp])\.?\s?[Mm]\.?)(?![\w:])")
_AMPM = re.compile(r"\b(1[0-2]|0?[1-9])\s*([AaPp])\.?\s?[Mm]\.?(?=\W|$)")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")
_SAY = [
    (re.compile(r"\be\.g\.,?\s*", re.I), "for example, "), (re.compile(r"\bi\.e\.,?\s*", re.I), "that is, "),
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"), (re.compile(r"\betc\.", re.I), "et cetera."),
    (re.compile(r"\bapprox\.\s*", re.I), "approximately "), (re.compile(r"(?<=\d)\s?GB\b"), " gigabytes"),
    (re.compile(r"(?<=\d)\s?MB\b"), " megabytes"), (re.compile(r"(?<=\d)\s?TB\b"), " terabytes"),
    (re.compile(r"(?<=\d)\s?km/h\b"), " kilometres an hour"), (re.compile(r"(?<=\d)\s?mph\b"), " miles an hour"),
    (re.compile(r"(?<=\d)\s?km\b"), " kilometres"), (re.compile(r"(?<=\d)\s?ms\b"), " milliseconds"),
    (re.compile(r"(?<=\d)\s?kWh\b"), " kilowatt hours"), (re.compile(r"\s?×\s?"), " by "),
    (re.compile(r"(?<=\d)x(?=\s)"), " times"), (re.compile(r"\bw/(?=\s)"), "with"),
]


def _hour_word(h: int) -> str:
    h = h % 12 or 12
    return _NUM[h]


def _two_digits(m: int) -> str:
    tens = ["", "", "twenty", "thirty", "forty", "fifty"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    if m < 10:
        return f"oh {_NUM[m]}"
    if m < 20:
        return teens[m - 10]
    return tens[m // 10] + ("" if m % 10 == 0 else f"-{_NUM[m % 10]}")


def _say_time(m: re.Match) -> str:
    h, mi, ap = int(m.group(1)), int(m.group(2) or m.group(4)), (m.group(3) or m.group(5) or "").lower()
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    if mi == 0:
        body = f"{_hour_word(h)} o'clock"
    elif mi == 15:
        body = f"quarter past {_hour_word(h)}"
    elif mi == 30:
        body = f"half past {_hour_word(h)}"
    elif mi == 45:
        body = f"quarter to {_hour_word(h + 1)}"
    else:
        body = f"{_hour_word(h)} {_two_digits(mi)}"
    if not ap and h <= 12 and not m.group(0).startswith("0") and len(m.group(1)) < 2 and mi in (0, 15, 30, 45):
        return body  # "at 7:30" with no am/pm: leave the part of day implied, as people do
    part = "in the morning" if (h % 24) < 12 else "in the afternoon" if h < 18 else "in the evening"
    if h in (0, 24) and mi == 0:
        return "midnight"
    if h == 12 and mi == 0:
        return "noon"
    return f"{body.replace(' o' + chr(39) + 'clock', '')} {part}"


def _keep_stop(m: re.Match, spoken: str) -> str:
    """'at 6 a.m.' ends the sentence too: give the full stop back when the matched 'm.' was one."""
    rest = m.string[m.end():]
    if m.group(0).endswith(".") and (not rest.strip() or re.match(r"\s+[A-Z\"']", rest)):
        return spoken + "."
    return spoken


def speech_form(text: str) -> str:
    """Rewrite written text the way a person would say it aloud (British conventions)."""
    text = _EMOJI.sub("", text)
    text = _TIME.sub(lambda m: _keep_stop(m, _say_time(m)), text)
    text = _AMPM.sub(lambda m: _keep_stop(m, _say_time(_TIME.match(f"{m.group(1)}:00{m.group(2)}m"))
                                          .replace(" o'clock in", " in")), text)
    for rx, rep in _SAY:
        text = rx.sub(rep, text)
    text = _DAYPART.sub(_daypart, text)
    return _SPACES.sub(" ", text).strip()


_DAYPART = re.compile(r"(?: o'clock)? in the (morning|afternoon|evening) (tomorrow|today|tonight)\b")


def _daypart(m: re.Match) -> str:
    part, day = m.group(1), m.group(2)
    if day == "tomorrow":
        return f" tomorrow {part}"
    if day == "tonight":
        return " tonight"
    return f" this {part}"


# ── spoken style: the persona's rules, enforced on what is actually said ─────────────────────────────
_OPENERS = re.compile(
    r"^(?:certainly|absolutely|of course|sure thing|sure|great question|good question|happy to help|"
    r"i'd be happy to help|i would be happy to help|no problem)\s*[!.,]+\s+(?=\S)", re.I)
_SIGNOFF = re.compile(
    r"^(?:is there anything else (?:i can (?:help|do)(?: you)?(?: with)?|you need)|"
    r"let me know if (?:you need|there's) anything else|i hope (?:this|that) helps)\b[^.?!]*[.?!]?$", re.I)


def polish_spoken(sentence: str, state: dict, title: str = "sir") -> str:
    """Tidy one sentence of a reply before it's spoken: no assistant filler ("Certainly!", "I hope this
    helps"), and the title ("sir") at most once per reply, as a person would. `state` is per reply."""
    s = (sentence or "").strip()
    if not s:
        return s
    if not state.get("started"):
        trimmed = _OPENERS.sub("", s)
        if trimmed != s and trimmed:
            s = trimmed[0].upper() + trimmed[1:]
        state["started"] = True
    if _SIGNOFF.match(s):
        return ""
    t = re.escape(title.strip()) if title else ""
    if t:
        pat = re.compile(rf"(,\s*{t}\b(?=[\s.,!?;:]|$))|(^{t},\s*)", re.I)
        if state.get("titled"):
            s = pat.sub(lambda m: "" if m.group(1) else "", s)
            s = s[:1].upper() + s[1:] if s else s
        elif pat.search(s):
            state["titled"] = True
    return s.strip()
