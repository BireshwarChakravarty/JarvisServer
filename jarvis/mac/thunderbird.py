"""Email from Thunderbird: read straight from its local copy of your mail.

Thunderbird keeps your messages on this Mac, in its profile folder, in standard formats (mbox, or
maildir if you chose it). JARVIS reads that copy: no passwords, no add-ons, no automation, nothing
leaves the Mac, and macOS asks for no permission (the profile isn't a protected folder).

  profile    ~/Library/Thunderbird/profiles.ini → the default profile
  inboxes    <profile>/ImapMail/<server>/INBOX (IMAP accounts' offline copies)
             <profile>/Mail/<server>/Inbox     (POP and Local Folders)
  status     IMAP: read / flagged / deleted live only in the folder index (INBOX.msf, Mork format):
             Thunderbird writes every offline copy marked read and never updates it.
             POP and Local Folders: X-Mozilla-Status in each stored message is kept up to date.

Only the newest part of each inbox is read (mail is appended, newest last), so a 10 GB mailbox is
as quick as a small one. IMAP accounts need "Keep messages for this account on this computer"
(Account Settings › Synchronization & Storage), Thunderbird's default.

Message dicts match jarvis.mac.mail.parse(), so the same ranking (mail.score) applies.
"""
from __future__ import annotations

import configparser
import datetime as dt
import email
import email.policy
import email.utils
import logging
import os
import re
import subprocess
from email.header import decode_header, make_header
from pathlib import Path

log = logging.getLogger("jarvis.thunderbird")

TAIL_BYTES = 48 * 1024 * 1024        # newest ~48 MB of each inbox
READ, REPLIED, FLAGGED, EXPUNGED, HAS_RE = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010
IMAP_DELETED = 0x00200000
INBOX_NAMES = {"inbox"}
APP_PATHS = ("/Applications/Thunderbird.app", str(Path("~/Applications/Thunderbird.app").expanduser()))


# ── where things are ────────────────────────────────────────────────────
def root() -> Path:
    return Path(os.environ.get("JARVIS_THUNDERBIRD_DIR") or Path("~/Library/Thunderbird").expanduser())


def profile_dir(base: Path | None = None) -> Path | None:
    """The default profile: installs.ini's Default (current Thunderbird), else profiles.ini's
    Default=1 profile, else the only profile there is."""
    base = base or root()
    def resolve(path: str, relative: bool) -> Path:
        return (base / path) if relative else Path(path)

    inst = base / "installs.ini"
    if inst.exists():
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(inst, encoding="utf-8")
            for sec in cp.sections():
                d = cp[sec].get("Default")
                if d and resolve(d, not os.path.isabs(d)).exists():
                    return resolve(d, not os.path.isabs(d))
        except configparser.Error:
            pass
    ini = base / "profiles.ini"
    candidates: list[Path] = []
    if ini.exists():
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(ini, encoding="utf-8")
        except configparser.Error:
            cp = None
        if cp is not None:
            for sec in cp.sections():
                s = cp[sec]
                if sec.startswith("Install") and s.get("Default"):
                    p = resolve(s["Default"], True)
                    if p.exists():
                        return p
            for sec in cp.sections():
                s = cp[sec]
                if not sec.startswith("Profile") or "Path" not in s:
                    continue
                p = resolve(s["Path"], s.get("IsRelative", "1") == "1")
                if s.get("Default") == "1" and p.exists():
                    return p
                candidates.append(p)
    candidates += sorted((base / "Profiles").glob("*")) if (base / "Profiles").exists() else []
    existing = [c for c in candidates if c.is_dir()]
    return existing[0] if existing else None


def inboxes(profile: Path) -> list[tuple[str, Path]]:
    """[(account folder name, inbox path)] for IMAP and local/POP accounts."""
    out = []
    for top in ("ImapMail", "Mail"):
        d = profile / top
        if not d.is_dir():
            continue
        for acct in sorted(d.iterdir()):
            if not acct.is_dir():
                continue
            for f in acct.iterdir():
                if f.name.lower() in INBOX_NAMES and not f.name.endswith(".msf"):
                    out.append((acct.name, f))
    return out


def installed() -> bool:
    return any(Path(p).exists() for p in APP_PATHS)


# ── reading messages ────────────────────────────────────────────────────
# a message boundary: a "From " line followed by a header line (an unescaped "From …" in a body isn't one)
_FROM_LINE = re.compile(rb"(?:^|\n)From [^\n]*\n(?=[A-Za-z0-9-]+:)")


def _mbox_tail(path: Path, limit: int | None = None) -> list[bytes]:
    """Raw messages from the newest `limit` bytes of an mbox file, oldest first."""
    limit = limit or TAIL_BYTES
    size = path.stat().st_size
    with open(path, "rb") as fh:
        start = max(0, size - limit)
        fh.seek(start)
        data = fh.read()
    if start:
        m = _FROM_LINE.search(data)
        if m is None:
            return []
        data = data[m.start():]
    parts = _FROM_LINE.split(data)
    return [p for p in parts if p.strip()]


def _maildir_tail(path: Path, count: int = 400) -> list[bytes]:
    files = []
    for sub in ("cur", "new", ""):
        d = path / sub if sub else path
        if d.is_dir():
            files += [f for f in d.iterdir() if f.is_file() and not f.name.startswith(".")]
    files.sort(key=lambda f: f.stat().st_mtime)
    out = []
    for f in files[-count:]:
        try:
            out.append(f.read_bytes())
        except OSError:
            continue
    return out


def raw_messages(path: Path) -> list[bytes]:
    return _maildir_tail(path) if path.is_dir() else _mbox_tail(path)


def _text(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _status(msg) -> int:
    try:
        return int(str(msg.get("X-Mozilla-Status", "0")).strip() or "0", 16)
    except ValueError:
        return 0


def _body(msg, limit: int = 6000) -> str:
    part = None
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == "text/plain" and not p.get_filename():
                part = p
                break
        if part is None:
            for p in msg.walk():
                if p.get_content_type() == "text/html" and not p.get_filename():
                    part = p
                    break
    else:
        part = msg
    if part is None:
        return ""
    try:
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()[:limit]


def to_dict(raw: bytes, account: str) -> dict | None:
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    status = _status(msg)
    if status & EXPUNGED:
        return None
    name, addr = email.utils.parseaddr(_text(msg.get("From")))
    when = None
    try:
        when = email.utils.parsedate_to_datetime(msg.get("Date"))
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
    except Exception:
        when = None
    body = _body(msg)
    return {"id": (msg.get("Message-ID") or "").strip().strip("<>"), "from": name or addr, "address": addr,
            "subject": _text(msg.get("Subject")).strip(), "received": when.isoformat() if when else "",
            "flagged": bool(status & FLAGGED), "mailing_list": bool(msg.get("List-Unsubscribe") or msg.get("List-Id")),
            "account": account, "excerpt": re.sub(r"\s+", " ", body)[:400], "read": bool(status & READ),
            "_when": when}


def _index(path: Path) -> dict[str, dict] | None:
    """message-id → index row (flags, sender, subject, date) from the folder's .msf, if there is one."""
    from jarvis.mac import mork

    msf = path.with_name(path.name + ".msf")
    if not msf.exists():
        return None
    st = msf.stat()
    key = (str(msf), st.st_mtime_ns, st.st_size)
    hit = _INDEX_CACHE.get(str(msf))
    if hit and hit[0] == key:
        return hit[1]
    try:
        rows = {r["message-id"].strip().strip("<>"): r for r in mork.read(msf).messages()}
    except Exception as exc:
        log.info("couldn't read %s: %s", msf, exc)
        return None
    _INDEX_CACHE[str(msf)] = (key, rows)
    return rows


_INDEX_CACHE: dict[str, tuple] = {}


def _from_index(row: dict, account: str) -> dict:
    flags = int(row.get("flags") or "0", 16)
    name, addr = email.utils.parseaddr(_text(row.get("sender", "")))
    subject = _text(row.get("subject", "")).strip()
    if flags & HAS_RE and not re.match(r"(?i)re:", subject):
        subject = "Re: " + subject                       # the index stores subjects without their "Re:"
    when = None
    try:
        when = dt.datetime.fromtimestamp(int(row.get("date") or "0", 16), dt.timezone.utc) if row.get("date") else None
    except (ValueError, OverflowError, OSError):
        when = None
    return {"id": row["message-id"].strip().strip("<>"), "from": name or addr, "address": addr, "subject": subject,
            "received": when.isoformat() if when else "", "flagged": bool(flags & FLAGGED), "mailing_list": False,
            "account": account, "excerpt": re.sub(r"\s+", " ", row.get("preview", "") or "")[:400],
            "read": bool(flags & READ), "deleted": bool(flags & (EXPUNGED | IMAP_DELETED)), "_when": when}


def messages(profile: Path | None = None) -> list[dict]:
    """Every message in the newest part of each inbox, with its true read/flagged state."""
    profile = profile or profile_dir()
    if profile is None:
        return []
    out = []
    for account, path in inboxes(profile):
        imap = path.parent.parent.name == "ImapMail"
        index = _index(path)
        bodies: dict[str, dict] = {}
        try:
            for raw in raw_messages(path):
                d = to_dict(raw, account)
                if d and d["id"]:
                    bodies[d["id"]] = d
        except OSError as exc:
            log.info("couldn't read %s: %s", path, exc)
        if index is not None:
            # the index is the truth for state; the stored copy adds the text and list headers
            for mid, row in index.items():
                m = _from_index(row, account)
                if m["deleted"]:
                    continue
                body = bodies.get(mid)
                if body:
                    m["excerpt"] = body["excerpt"] or m["excerpt"]
                    m["mailing_list"] = body["mailing_list"]
                    m["from"], m["address"] = m["from"] or body["from"], m["address"] or body["address"]
                    m["subject"] = m["subject"] or body["subject"]
                    m["_when"] = m["_when"] or body["_when"]
                    m["received"] = m["received"] or body["received"]
                out.append(m)
        elif not imap:
            out += list(bodies.values())               # POP / Local Folders: the stored status is kept current
        else:
            log.info("%s has no index yet: skipping (its stored copies are always marked read)", path)
    return out


# ── what the email tools use ────────────────────────────────────────────
SETUP_HINT = ("I can't find Thunderbird's mail on this Mac yet. Open Thunderbird and add your account; for "
              "IMAP accounts keep “Keep messages for this account on this computer” switched on "
              "(Account Settings › Synchronization & Storage).")


def unread(days: int = 3, limit: int = 40, now: dt.datetime | None = None, profile: Path | None = None) -> list[dict] | str:
    from jarvis.mac.mail import score

    profile = profile or profile_dir()
    if profile is None or not inboxes(profile):
        return SETUP_HINT
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    got = [m for m in messages(profile) if not m["read"] and (m["_when"] is None or m["_when"] >= cutoff)]
    got.sort(key=lambda m: m["_when"] or cutoff, reverse=True)
    got = got[:limit]
    for m in got:
        m["score"] = score(m, now)
        for k in ("_when", "read", "deleted"):
            m.pop(k, None)
    return sorted(got, key=lambda m: m["score"], reverse=True)


def unread_count(days: int = 2, profile: Path | None = None) -> int | None:
    got = unread(days=days, limit=500, profile=profile)
    return None if isinstance(got, str) else len(got)


def read(message_id: str, profile: Path | None = None) -> dict | None:
    want = (message_id or "").strip().strip("<>")
    profile = profile or profile_dir()
    if profile is None:
        return None
    for account, path in inboxes(profile):
        for raw in reversed(raw_messages(path)):
            if want.encode() not in raw[:20000]:
                continue
            msg = email.message_from_bytes(raw, policy=email.policy.compat32)
            if (msg.get("Message-ID") or "").strip().strip("<>") == want:
                name, addr = email.utils.parseaddr(_text(msg.get("From")))
                return {"from": name or addr, "subject": _text(msg.get("Subject")), "body": _body(msg)}
        row = (_index(path) or {}).get(want)
        if row:
            m = _from_index(row, account)
            return {"from": m["from"], "subject": m["subject"],
                    "body": m["excerpt"] or "(Thunderbird hasn't kept a copy of this message on the Mac, so I "
                                            "only have the sender and subject.)"}
    return None


def open_message(message_id: str) -> str:
    """Show it in Thunderbird. On macOS `open -a Thunderbird mid:<id>` reaches a running Thunderbird
    too (it arrives as -url mid:…, which Thunderbird opens by Message-ID)."""
    app = next((p for p in APP_PATHS if Path(p).exists()), None)
    if app is None:
        return "Thunderbird isn't installed."
    mid = "mid:" + (message_id or "").strip().strip("<>")
    try:
        r = subprocess.run(["open", "-a", app, mid], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            subprocess.run(["open", "-a", app], capture_output=True, timeout=10)
            return "Thunderbird is open; I couldn't jump straight to that message."
        return "Opened in Thunderbird."
    except Exception as exc:
        return f"Couldn't open it in Thunderbird: {exc}"
