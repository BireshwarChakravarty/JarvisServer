"""Email that needs your attention, read from Apple Mail.

Any account set up in Mail works (Gmail, iCloud, Outlook, work mail): JARVIS never sees a password,
everything stays on this Mac, and macOS asks once before he may read Mail.

How "needs your attention" is decided:
  1. recent unread mail is scored on this Mac: mailing lists, promotions and no-reply senders sink;
     flagged mail, real people, direct questions, deadlines, payments and approvals rise
  2. the best handful go to his brain, which picks at most three and says why each matters and what
     you might do about it
"""
from __future__ import annotations

import datetime as dt
import re

from jarvis.mac.system import IS_MAC, osascript, q, require_mac

RS, US = "\x1e", "\x1f"     # record / field separators that never occur in mail text

_FETCH = '''
set RS to (ASCII character 30)
set US to (ASCII character 31)
set cutoff to (current date) - ({days} * days)
set out to ""
tell application "Mail"
  if (count of accounts) is 0 then return "NO_ACCOUNTS"
  set msgs to (messages of inbox whose read status is false and date received > cutoff)
  set n to count of msgs
  if n > {limit} then set n to {limit}
  repeat with i from 1 to n
    set m to item i of msgs
    try
      set c to content of m
      if (length of c) > 600 then set c to text 1 thru 600 of c
    on error
      set c to ""
    end try
    set lists to 0
    try
      set lists to count of (every header of m whose name is "List-Unsubscribe")
    end try
    set acct to ""
    try
      set acct to name of account of mailbox of m
    end try
    set stamp to ((date received of m) as «class isot» as string)
    set row to (message id of m) & US & (sender of m) & US & (subject of m) & US & stamp
    set row to row & US & (flagged status of m) & US & lists & US & acct & US & c
    set out to out & row & RS
  end repeat
end tell
return out
'''

PRIORITY_WORDS = re.compile(
    r"\b(urgent|asap|action required|action needed|deadline|due|overdue|today|tomorrow|eod|invoice|payment|"
    r"pay|approve|approval|sign|signature|contract|offer|interview|meeting|reschedul\w*|confirm|"
    r"question|help|issue|problem|fail\w*|declined|security|password|verify|expir\w*)\b", re.IGNORECASE)
LOW_SENDERS = re.compile(r"(no-?reply|do-?not-?reply|newsletter|notifications?|news@|marketing|mailer|digest|"
                         r"updates@|info@|hello@|team@)", re.IGNORECASE)
PROMO_WORDS = re.compile(r"\b(sale|% off|deal|offer ends|discount|webinar|unsubscribe|newsletter|digest|"
                         r"weekly|promo\w*|limited time)\b", re.IGNORECASE)


def parse(raw: str) -> list[dict]:
    out = []
    for rec in raw.split(RS):
        f = rec.split(US)
        if len(f) < 8:
            continue
        mid, sender, subject, when, flagged, lists, account, body = f[:8]
        name, addr = sender, ""
        m = re.match(r'\s*"?([^"<]*)"?\s*<([^>]+)>', sender)
        if m:
            name, addr = m.group(1).strip() or m.group(2), m.group(2).strip()
        elif "@" in sender:
            addr = sender.strip()
        out.append({"id": mid, "from": name.strip(), "address": addr, "subject": subject.strip(),
                    "received": when, "flagged": flagged == "true", "mailing_list": lists not in ("", "0"),
                    "account": account, "excerpt": re.sub(r"\s+", " ", body).strip()[:400]})
    return out


def score(m: dict, now: dt.datetime | None = None) -> float:
    s = 0.0
    text = f"{m['subject']} {m['excerpt']}"
    if m.get("flagged"):
        s += 4
    if m.get("mailing_list"):
        s -= 4
    if LOW_SENDERS.search(m.get("address", "") + " " + m.get("from", "")):
        s -= 3
    s -= 2 * len(PROMO_WORDS.findall(text))
    s += 1.5 * min(3, len(PRIORITY_WORDS.findall(text)))
    if "?" in m["subject"] or re.search(r"\?\s", m["excerpt"][:200]):
        s += 1.5
    if re.match(r"^(re|fwd?):", m["subject"], re.IGNORECASE):
        s += 1          # a thread you're part of
    try:
        age_h = ((now or dt.datetime.now(dt.UTC)) - dt.datetime.fromisoformat(m["received"].replace("Z", "+00:00"))
                 .astimezone(dt.UTC)).total_seconds() / 3600
        s += 1.0 if age_h < 12 else 0.5 if age_h < 36 else 0
    except (ValueError, TypeError, KeyError):
        pass
    return round(s, 2)


def unread(days: int = 3, limit: int = 40, launch: bool = True) -> list[dict] | str:
    """Recent unread inbox mail, best candidates first. A string means 'explain this to the user'."""
    require_mac()
    if not launch and osascript('application "Mail" is running') != "true":
        return []
    raw = osascript(_FETCH.format(days=int(days), limit=int(limit)), timeout=90)
    if raw.strip() == "NO_ACCOUNTS":
        return ("Apple Mail has no email accounts yet. Add yours in System Settings › Internet Accounts "
                "(Gmail, iCloud and Outlook all work), and I'll read it from there.")
    msgs = parse(raw)
    for m in msgs:
        m["score"] = score(m)
    return sorted(msgs, key=lambda m: m["score"], reverse=True)


def read(message_id: str) -> dict | None:
    require_mac()
    raw = osascript(f'''
tell application "Mail"
  set m to first message of inbox whose message id is {q(message_id)}
  return (sender of m) & (ASCII character 31) & (subject of m) & (ASCII character 31) & (content of m)
end tell''', timeout=60)
    f = raw.split(US)
    if len(f) < 3:
        return None
    return {"from": f[0], "subject": f[1], "body": f[2][:6000]}


def open_message(message_id: str) -> str:
    require_mac()
    osascript(f'''
tell application "Mail"
  set m to first message of inbox whose message id is {q(message_id)}
  activate
  open m
end tell''', timeout=30)
    return "Opened in Mail."


def available() -> bool:
    return IS_MAC
