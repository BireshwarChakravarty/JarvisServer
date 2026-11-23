"""WhatsApp, through the official WhatsApp app for Mac.

Reading: the Mac app keeps your chats in a local database (Core Data, the same layout as the
iPhone app) in its group container. JARVIS reads a private snapshot of it, read-only: no linked
"device", no unofficial client, nothing that breaks WhatsApp's terms, nothing leaves the Mac.

  database   ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
  chats      ZWACHATSESSION   ZPARTNERNAME, ZCONTACTJID, ZUNREADCOUNT, ZLASTMESSAGEDATE,
                              ZSESSIONTYPE (0 person · 1 group · 2 broadcast · 3 status), ZARCHIVED
  messages   ZWAMESSAGE       ZTEXT, ZISFROMME, ZMESSAGEDATE, ZCHATSESSION, ZGROUPMEMBER, ZPUSHNAME
  names      ZWAGROUPMEMBER (ZCONTACTNAME, ZMEMBERJID) · ZWAPROFILEPUSHNAME (ZJID, ZPUSHNAME)
  dates      Core Data: seconds since 1 January 2001

The layout isn't documented and can change with a WhatsApp update, so the columns are checked
first; if they've moved JARVIS says so instead of guessing. macOS protects group containers: JARVIS
needs Full Disk Access to read it (System Settings › Privacy & Security).

Sending: JARVIS opens the chat in the WhatsApp app with your message typed in (whatsapp://send),
presses Return, then checks the database that it really went. Only after you've said yes.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

log = logging.getLogger("jarvis.whatsapp")

BUNDLE_ID = "net.whatsapp.WhatsApp"
APP_PATHS = ("/Applications/WhatsApp.app", str(Path("~/Applications/WhatsApp.app").expanduser()))
DB_NAME = "ChatStorage.sqlite"
EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
PERSON, GROUP, BROADCAST, STATUS = 0, 1, 2, 3

NEED = {"ZWACHATSESSION": {"Z_PK", "ZPARTNERNAME", "ZCONTACTJID"},
        "ZWAMESSAGE": {"Z_PK", "ZTEXT", "ZISFROMME", "ZCHATSESSION"}}


class WhatsAppError(RuntimeError):
    """Something to tell you in plain words (not installed, no access, layout changed)."""


# ── where things are ────────────────────────────────────────────────────
def db_path() -> Path:
    env = os.environ.get("JARVIS_WHATSAPP_DIR")
    base = Path(env).expanduser() if env else Path("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared").expanduser()
    return base / DB_NAME


def installed() -> bool:
    return any(Path(p).exists() for p in APP_PATHS)


NOT_SET_UP = ("I can't find WhatsApp's chats on this Mac. Install WhatsApp from the App Store or whatsapp.com, "
              "open it and link it to your phone.")
NO_ACCESS = ("I need Full Disk Access to read your WhatsApp chats: System Settings, Privacy & Security, "
             "Full Disk Access, then switch JARVIS on.")


def can_read() -> str:
    """'ok', 'missing' or 'denied'."""
    p = db_path()
    try:
        with open(p, "rb") as fh:
            fh.read(16)
        return "ok"
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "denied"
    except OSError:
        return "denied"


class _Snapshot:
    """A private copy of the database (with its WAL), so WhatsApp's live files are never touched;
    even a read-only SQLite reader can write to the -shm file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()

    def __enter__(self) -> sqlite3.Connection:
        state = "ok" if self.path.exists() else "missing"
        if state == "ok":
            try:
                with open(self.path, "rb") as fh:
                    fh.read(16)
            except OSError:
                state = "denied"
        if state == "missing":
            raise WhatsAppError(NOT_SET_UP)
        if state == "denied":
            raise WhatsAppError(NO_ACCESS)
        self.tmp = tempfile.mkdtemp(prefix="jarvis-wa-")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                shutil.copy2(src, Path(self.tmp) / (DB_NAME + suffix))
        self.con = sqlite3.connect(str(Path(self.tmp) / DB_NAME), timeout=3)
        self.con.row_factory = sqlite3.Row
        _check_layout(self.con)
        return self.con

    def __exit__(self, *exc) -> None:
        try:
            self.con.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


def _columns(con, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _check_layout(con) -> None:
    for table, cols in NEED.items():
        have = _columns(con, table)
        missing = cols - have
        if missing:
            raise WhatsAppError("WhatsApp has changed how it stores chats (an update), so I can't read them "
                                f"safely right now. Missing: {', '.join(sorted(missing))} in {table}.")


def _date_col(con) -> str | None:
    have = _columns(con, "ZWAMESSAGE")
    return next((c for c in ("ZMESSAGEDATE", "ZTIMESTAMP", "ZSENTDATE") if c in have), None)


def _when(value) -> dt.datetime | None:
    try:
        return EPOCH + dt.timedelta(seconds=float(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _digits(jid: str | None) -> str:
    return re.sub(r"\D", "", (jid or "").split("@")[0])


# ── reading ──────────────────────────────────────────────────────────────
def _chat_rows(con, where: str = "", args=()) -> list[sqlite3.Row]:
    have = _columns(con, "ZWACHATSESSION")
    pick = ", ".join(c for c in ("Z_PK", "ZPARTNERNAME", "ZCONTACTJID", "ZUNREADCOUNT", "ZLASTMESSAGEDATE",
                                 "ZSESSIONTYPE", "ZARCHIVED", "ZREMOVED", "ZHIDDEN") if c in have)
    order = "ORDER BY ZLASTMESSAGEDATE DESC" if "ZLASTMESSAGEDATE" in have else ""
    return con.execute(f"SELECT {pick} FROM ZWACHATSESSION {where} {order}", args).fetchall()


def _kind(row) -> int:
    try:
        return int(row["ZSESSIONTYPE"]) if "ZSESSIONTYPE" in row.keys() and row["ZSESSIONTYPE"] is not None else PERSON
    except (TypeError, ValueError):
        return PERSON


def _live(row) -> bool:
    keys = row.keys()
    return not any(k in keys and row[k] for k in ("ZREMOVED", "ZHIDDEN")) and _kind(row) in (PERSON, GROUP)


def _senders(con) -> tuple[dict, dict]:
    members, pushed = {}, {}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ZWAGROUPMEMBER" in tables:
        cols = _columns(con, "ZWAGROUPMEMBER")
        name = next((c for c in ("ZCONTACTNAME", "ZFIRSTNAME") if c in cols), None)
        if name and "Z_PK" in cols:
            for r in con.execute(f"SELECT Z_PK, {name} AS n FROM ZWAGROUPMEMBER"):
                if r["n"]:
                    members[r["Z_PK"]] = r["n"]
    if "ZWAPROFILEPUSHNAME" in tables and {"ZJID", "ZPUSHNAME"} <= _columns(con, "ZWAPROFILEPUSHNAME"):
        for r in con.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
            if r["ZPUSHNAME"]:
                pushed[r["ZJID"]] = r["ZPUSHNAME"]
    return members, pushed


def _messages(con, chat_pk: int, limit: int, incoming_only: bool = False) -> list[dict]:
    have = _columns(con, "ZWAMESSAGE")
    date = _date_col(con)
    extra = [c for c in ("ZFROMJID", "ZGROUPMEMBER", "ZPUSHNAME", "ZMESSAGETYPE") if c in have]
    sel = ", ".join(["ZTEXT", "ZISFROMME"] + ([date] if date else []) + extra)
    where = "ZCHATSESSION = ?" + (" AND ZISFROMME = 0" if incoming_only else "")
    order = f"ORDER BY {date} DESC" if date else "ORDER BY Z_PK DESC"
    rows = con.execute(f"SELECT {sel} FROM ZWAMESSAGE WHERE {where} {order} LIMIT ?", (chat_pk, limit)).fetchall()
    members, pushed = _senders(con)
    out = []
    for r in reversed(rows):
        keys = r.keys()
        text = (r["ZTEXT"] or "").strip()
        if not text:
            kind = r["ZMESSAGETYPE"] if "ZMESSAGETYPE" in keys else None
            text = {1: "[photo]", 2: "[video]", 3: "[voice message]", 4: "[contact]", 5: "[location]",
                    8: "[document]", 15: "[sticker]"}.get(kind, "")
        if not text:
            continue
        who = "me" if r["ZISFROMME"] else (
            members.get(r["ZGROUPMEMBER"]) if "ZGROUPMEMBER" in keys and r["ZGROUPMEMBER"] else None) or (
            r["ZPUSHNAME"] if "ZPUSHNAME" in keys and r["ZPUSHNAME"] else None) or (
            pushed.get(r["ZFROMJID"]) if "ZFROMJID" in keys else None) or ""
        when = _when(r[date]) if date else None
        out.append({"from": who, "text": text[:600], "when": when.isoformat() if when else ""})
    return out


def _chat(row) -> dict:
    return {"chat": row["ZPARTNERNAME"] or _digits(row["ZCONTACTJID"]) or "Unknown",
            "group": _kind(row) == GROUP,
            "unread": int(row["ZUNREADCOUNT"] or 0) if "ZUNREADCOUNT" in row.keys() else 0}


def unread(limit_chats: int = 8, path: Path | None = None) -> list[dict]:
    """Chats with unread messages, people before groups, newest first, each with what was said."""
    with _Snapshot(path) as con:
        rows = [r for r in _chat_rows(con, "WHERE ZUNREADCOUNT > 0") if _live(r)]
        rows.sort(key=lambda r: (_kind(r) == GROUP,), reverse=False)        # stable: keeps newest first
        out = []
        for r in rows[:limit_chats]:
            c = _chat(r)
            c["messages"] = _messages(con, r["Z_PK"], min(10, max(1, c["unread"])), incoming_only=True)
            if "ZARCHIVED" in r.keys() and r["ZARCHIVED"]:
                c["archived"] = True
            out.append(c)
        return out


def _score(name: str, want: str) -> float:
    from difflib import SequenceMatcher

    n, w = name.lower(), want.lower().strip()
    if not n or not w:
        return 0.0
    if n == w:
        return 1.0
    if n.startswith(w) or any(part.startswith(w) for part in n.split()):
        return 0.9
    if w in n:
        return 0.8
    return SequenceMatcher(None, n, w).ratio() * 0.75


class AmbiguousChat(WhatsAppError):
    """Two or more chats match about equally: JARVIS asks which one."""

    expected = True

    def __init__(self, name: str, options: list[str]) -> None:
        self.options = options
        shown = ", ".join(options[:-1]) + f" or {options[-1]}"
        super().__init__(f"More than one WhatsApp chat matches {name!r}: {shown}. Ask which one they mean.")


def find_chat(name: str, path: Path | None = None) -> dict | None:
    """The chat best matching a name ('Pepper', 'mum', 'the family group')."""
    want = re.sub(r"(?i)^(the|my)\s+|\s+(group|chat)$", "", (name or "").strip())
    with _Snapshot(path) as con:
        scored = []
        for r in _chat_rows(con):
            if not _live(r):
                continue
            s = _score(r["ZPARTNERNAME"] or "", want)
            if not s and _digits(want) and _digits(want) in _digits(r["ZCONTACTJID"]):
                s = 0.95
            if s:
                scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        if not scored or scored[0][0] < 0.6:
            return None
        best_s, best = scored[0]
        close = [r for s, r in scored[1:4] if s >= 0.8 and best_s - s < 0.05 and best_s < 1.0]
        if close:              # "Mum" and "Mum's birthday" equally likely: ask rather than guess
            raise AmbiguousChat(name, [best["ZPARTNERNAME"]] + [r["ZPARTNERNAME"] for r in close])
        return {**_chat(best), "jid": best["ZCONTACTJID"], "pk": best["Z_PK"]}


def recent(name: str, limit: int = 12, path: Path | None = None) -> dict | None:
    """The latest messages in one chat, both sides, oldest first."""
    chat = find_chat(name, path)
    if chat is None:
        return None
    with _Snapshot(path) as con:
        chat["messages"] = _messages(con, chat["pk"], limit)
    chat.pop("pk", None)
    return chat


def unread_people(path: Path | None = None) -> int | None:
    """How many people (not groups) are waiting on a reply; None if WhatsApp isn't readable."""
    try:
        return sum(1 for c in unread(50, path) if not c["group"] and not c.get("archived"))
    except WhatsAppError:
        return None


# ── sending ──────────────────────────────────────────────────────────────
def _frontmost_bundle() -> str:
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.bundleIdentifier() or "")
    except Exception:
        return ""


def _running() -> bool:
    try:
        from AppKit import NSRunningApplication

        return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID))
    except Exception:
        return True


def _press_return() -> None:
    import Quartz

    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, 36, down)          # 36 = Return
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.03)


def _sent(chat_pk: int, text: str, since: float, path: Path | None) -> bool:
    try:
        with _Snapshot(path) as con:
            date = _date_col(con)
            rows = con.execute(
                f"SELECT ZTEXT{', ' + date if date else ''} FROM ZWAMESSAGE WHERE ZCHATSESSION = ? AND ZISFROMME = 1 "
                f"ORDER BY {date or 'Z_PK'} DESC LIMIT 5", (chat_pk,)).fetchall()
    except WhatsAppError:
        return False
    for r in rows:
        when = _when(r[1]) if date else None
        if (r[0] or "").strip() == text.strip() and (when is None or when.timestamp() >= since - 5):
            return True
    return False


def send(recipient: str, text: str, path: Path | None = None, wait: float = 8.0) -> str:
    """Send a WhatsApp message to a person through the WhatsApp app, and check it went."""
    text = (text or "").strip()
    if not text:
        return "There's nothing to send."
    if not installed():
        return "WhatsApp isn't installed on this Mac."
    chat = None
    try:
        chat = find_chat(recipient, path)
    except AmbiguousChat as exc:
        return str(exc)                               # which one? never guess who a message goes to
    except WhatsAppError as exc:
        log.info("whatsapp chats unreadable for send: %s", exc)
    phone = ""
    if chat is not None:
        if chat["group"]:
            return (f"I can only send to one person on WhatsApp for now, not to a group like {chat['chat']}. "
                    "I can open the group for you instead.")
        phone = _digits(chat["jid"])
    if not phone and _digits(recipient) and len(_digits(recipient)) >= 8:
        phone = _digits(recipient)
    if not phone:
        try:
            from jarvis.mac.pim import find_contact

            c = find_contact(recipient) or {}
            raw = c.get("phone", "")
            if raw.strip().startswith("+") or raw.strip().startswith("00"):
                phone = _digits(raw).lstrip("0") if raw.strip().startswith("00") else _digits(raw)
        except Exception:
            pass
    if not phone:
        return (f"I couldn't find a WhatsApp chat or an international phone number for {recipient}. "
                "Say their number with the country code, or message them once from your phone first.")
    since = time.time()
    cold = not _running()                                # launching: the chat takes longer to appear
    url = f"whatsapp://send?phone={phone}&text={urllib.parse.quote(text)}"
    r = subprocess.run(["open", url], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return f"WhatsApp wouldn't open that chat ({(r.stderr or '').strip()[:120]})."
    for _ in range(150 if cold else 40):                 # up to 15 s (launching) or 4 s for WhatsApp to come forward
        if _frontmost_bundle() == BUNDLE_ID:
            break
        time.sleep(0.1)
    else:
        return "I've opened the chat in WhatsApp with your message typed in. Press Return to send it."
    who = chat["chat"] if chat else recipient
    # Wait until the message is really in WhatsApp's message box (a fixed pause pressed Return too early
    # whenever WhatsApp was slow to open the chat: nothing was sent and you had to ask again).
    seen = False
    ready_by = time.time() + (12.0 if cold else 6.0)
    while time.time() < ready_by:
        box = _composer_text()
        if box is None:
            break                                        # can't read the box (no Accessibility): fall back to time
        if _same(box, text):
            seen = True
            break
        time.sleep(0.25)
    if not seen:
        time.sleep(4.0 if cold else 2.0)
    time.sleep(0.3)                                      # let the field settle before Return
    for attempt in (1, 2):
        _press_return()
        until = time.time() + (4.0 if attempt == 1 else wait)
        emptied = False
        while time.time() < until:
            time.sleep(0.6)
            if chat is not None and _sent(chat["pk"], text, since, path):
                return f"Sent to {who} on WhatsApp."
            box = _composer_text()
            if box is not None and not box.strip():
                emptied = True                           # the box cleared: it went (the history may lag)
                if chat is None:
                    break
        if emptied:
            return f"Sent to {who} on WhatsApp."
        box = _composer_text()
        if not (box is not None and _same(box, text)):
            break                                        # only press again when our text is still waiting there
    if chat is None:
        return f"Sent to {who} on WhatsApp (I couldn't double-check it: no access to the chat history)."
    return (f"I've typed it into your chat with {who}, but I can't see it sent yet. "
            "Have a look at WhatsApp and press Return if it's still waiting.")


def _same(box: str, text: str) -> bool:
    norm = lambda t: re.sub(r"\s+", " ", t or "").strip()           # noqa: E731
    return bool(norm(text)) and norm(text) in norm(box)


def _composer_text() -> str | None:
    """What's in WhatsApp's focused text box (Accessibility), or None if that can't be read."""
    try:
        import ApplicationServices as AX
        from AppKit import NSRunningApplication

        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID)
        if not apps:
            return None
        app = AX.AXUIElementCreateApplication(apps[0].processIdentifier())
        err, el = AX.AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
        if err or el is None:
            return None
        err, role = AX.AXUIElementCopyAttributeValue(el, "AXRole", None)
        if err or str(role) not in ("AXTextArea", "AXTextField"):
            return None
        err, val = AX.AXUIElementCopyAttributeValue(el, "AXValue", None)
        return None if err else str(val or "")
    except Exception:
        return None


# ── a sample database, for the self-test and the tests ──────────────────
def make_sample(folder: Path, now: dt.datetime | None = None) -> Path:
    """A ChatStorage.sqlite shaped like WhatsApp's: Pepper (2 unread), a family group (3 unread,
    two senders), Happy (read), a status update row and an archived chat."""
    now = now or dt.datetime.now(dt.timezone.utc)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DB_NAME
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZARCHIVED INTEGER, ZHIDDEN INTEGER,
            ZREMOVED INTEGER, ZSESSIONTYPE INTEGER, ZUNREADCOUNT INTEGER, ZLASTMESSAGEDATE TIMESTAMP,
            ZCONTACTJID VARCHAR, ZPARTNERNAME VARCHAR);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZISFROMME INTEGER, ZMESSAGETYPE INTEGER,
            ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER, ZMESSAGEDATE TIMESTAMP, ZSENTDATE TIMESTAMP,
            ZFROMJID VARCHAR, ZTOJID VARCHAR, ZPUSHNAME VARCHAR, ZTEXT VARCHAR);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZCONTACTNAME VARCHAR,
            ZMEMBERJID VARCHAR);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID VARCHAR, ZPUSHNAME VARCHAR);
    """)
    t = lambda mins: (now - dt.timedelta(minutes=mins) - EPOCH).total_seconds()   # noqa: E731
    con.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, 1, 0, 0, 0, PERSON, 2, t(5), "447700900123@s.whatsapp.net", "Pepper Potts"),
        (2, 1, 0, 0, 0, GROUP, 3, t(2), "120363000000@g.us", "Stark Family"),
        (3, 1, 0, 0, 0, PERSON, 0, t(60), "447700900456@s.whatsapp.net", "Happy Hogan"),
        (4, 1, 0, 0, 0, STATUS, 4, t(1), "status@broadcast", ""),
        (5, 1, 1, 0, 0, PERSON, 1, t(30), "447700900789@s.whatsapp.net", "Old Newsletter"),
    ])
    con.executemany("INSERT INTO ZWAGROUPMEMBER VALUES (?,?,?,?)", [
        (1, 2, "Morgan", "447700900111@s.whatsapp.net"), (2, 2, "Rhodey", "447700900222@s.whatsapp.net")])
    con.execute("INSERT INTO ZWAPROFILEPUSHNAME VALUES (1, '447700900123@s.whatsapp.net', 'Pep')")
    msgs = [
        (1, 0, 0, 1, None, t(9), "447700900123@s.whatsapp.net", None, "Did you see the contract?"),
        (2, 1, 0, 1, None, t(8), None, None, "Not yet"),
        (3, 0, 0, 1, None, t(6), "447700900123@s.whatsapp.net", None, "Can you sign it before 5?"),
        (4, 0, 0, 1, None, t(5), "447700900123@s.whatsapp.net", None, "It's urgent"),
        (5, 0, 0, 2, 1, t(4), None, "Morgan", "Dinner at 7?"),
        (6, 0, 1, 2, 2, t(3), None, None, ""),
        (7, 0, 0, 2, 2, t(2), None, None, "I'm bringing dessert"),
        (8, 0, 0, 3, None, t(60), "447700900456@s.whatsapp.net", None, "Car's ready"),
        (9, 0, 0, 4, None, t(1), None, None, "status text"),
        (10, 0, 0, 5, None, t(30), None, None, "Weekly deals"),
    ]
    con.executemany("INSERT INTO ZWAMESSAGE (Z_PK, ZISFROMME, ZMESSAGETYPE, ZCHATSESSION, ZGROUPMEMBER, ZMESSAGEDATE,"
                    " ZFROMJID, ZPUSHNAME, ZTEXT) VALUES (?,?,?,?,?,?,?,?,?)", msgs)
    con.commit()
    con.close()
    return path
