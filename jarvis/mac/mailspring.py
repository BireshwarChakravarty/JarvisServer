"""Email from Mailspring (https://www.getmailspring.com): read straight from its local copy of your mail.

Mailspring's sync engine keeps every account you've added (Gmail, iCloud, Outlook, Fastmail, any
IMAP) in one SQLite database on this Mac. JARVIS reads that database, read-only: no passwords, no
plugins, no automation, nothing leaves the Mac, and macOS asks for no permission (the folder isn't
a protected one).

  database   ~/Library/Application Support/Mailspring/edgehill.db
  inbox      Folder.role = 'inbox' (IMAP) or Label.role = 'inbox' (Gmail); ThreadCategory links
             each thread to the folders and labels it's in
  messages   Message: unread / starred / draft / date / threadId columns, plus a JSON `data`
             column with from, subject, snippet and extraHeaders
  bodies     MessageBody.value (HTML, or plain text when data.plaintext is set), once synced

Schema per Mailspring-Sync's MailSync/constants.h. Message dicts match jarvis.mac.mail.parse(),
so the same ranking (mail.score) applies.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("jarvis.mailspring")

APP_PATHS = ("/Applications/Mailspring.app", str(Path("~/Applications/Mailspring.app").expanduser()))
DB_NAME = "edgehill.db"
URL = "https://www.getmailspring.com"


# ── where things are ────────────────────────────────────────────────────
def candidates() -> list[Path]:
    env = os.environ.get("JARVIS_MAILSPRING_DIR")
    if env:
        return [Path(env).expanduser()]
    home = Path.home()
    return [home / "Library/Application Support/Mailspring",
            home / "Library/Containers/com.mailspring.mailspring/Data/Library/Application Support/Mailspring"]


def db_path() -> Path | None:
    for d in candidates():
        p = d / DB_NAME
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def installed() -> bool:
    return any(Path(p).exists() for p in APP_PATHS)


def _connect(path: Path) -> tuple[sqlite3.Connection, str | None]:
    """Read-only connection to Mailspring's live database (it writes in WAL mode while running).
    If it can't be opened in place, read a private copy instead. → (connection, temp dir to remove)"""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        con.execute("SELECT 1 FROM Message LIMIT 1")
        return con, None
    except sqlite3.Error as exc:
        log.info("reading a copy of Mailspring's database (%s)", exc)
    tmp = tempfile.mkdtemp(prefix="jarvis-mailspring-")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(path) + suffix)
        if src.exists():
            shutil.copy2(src, Path(tmp) / (DB_NAME + suffix))
    return sqlite3.connect(str(Path(tmp) / DB_NAME), timeout=3), tmp


class _DB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> sqlite3.Connection:
        self.con, self.tmp = _connect(self.path)
        self.con.row_factory = sqlite3.Row
        return self.con

    def __exit__(self, *exc) -> None:
        self.con.close()
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


# ── turning rows into messages ──────────────────────────────────────────
def _when(value) -> dt.datetime | None:
    """Mailspring stores dates as Unix seconds (sometimes milliseconds, or text in old databases)."""
    if value in (None, ""):
        return None
    try:
        n = float(value)
        if n > 1e11:
            n /= 1000.0
        return dt.datetime.fromtimestamp(n, dt.UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def _data(row) -> dict:
    try:
        d = json.loads(row["data"] or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _sender(data: dict) -> tuple[str, str]:
    people = data.get("from") or []
    first = people[0] if isinstance(people, list) and people else {}
    if not isinstance(first, dict):
        return "", ""
    addr = str(first.get("email") or "").strip()
    return str(first.get("name") or "").strip() or addr, addr


def _is_list(data: dict) -> bool:
    headers = data.get("extraHeaders") or {}
    if isinstance(headers, dict):
        keys = {str(k).lower() for k in headers}
        return bool(keys & {"list-unsubscribe", "list-id"})
    return False


def _text(body: str, plaintext: bool) -> str:
    if not body:
        return ""
    if not plaintext:
        body = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", body)
        body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", body)
        body = re.sub(r"<[^>]+>", "", body)            # inline tags (<b>, <a>) join the words around them
        body = html.unescape(body)
    body = re.sub(r"[ \t\u00a0]+", " ", body)
    body = "\n".join(line.strip() for line in body.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _accounts(con) -> dict[str, str]:
    out: dict[str, str] = {}
    if "Account" not in _tables(con):
        return out
    for r in con.execute("SELECT * FROM Account"):
        keys = r.keys()
        email_addr = (r["email_address"] if "email_address" in keys else "") or ""
        if not email_addr:
            d = _data(r)
            email_addr = d.get("emailAddress") or d.get("email_address") or ""
        for k in ("id", "accountId"):
            if k in keys and r[k]:
                out[str(r[k])] = email_addr
    return out


def _inbox_ids(con) -> list[str]:
    ids: list[str] = []
    tables = _tables(con)
    for table in ("Folder", "Label"):
        if table in tables:
            ids += [r[0] for r in con.execute(f"SELECT id FROM {table} WHERE lower(role) = 'inbox'")]
    return ids


def _to_dict(row, accounts: dict[str, str]) -> dict:
    data = _data(row)
    name, addr = _sender(data)
    when = _when(row["date"] if row["date"] is not None else data.get("date"))
    return {"id": row["id"], "message_id": (row["headerMessageId"] or data.get("hMsgId") or "").strip("<>"),
            "from": name, "address": addr, "subject": (row["subject"] or data.get("subject") or "").strip(),
            "received": when.isoformat() if when else "", "flagged": bool(row["starred"]),
            "mailing_list": _is_list(data), "account": accounts.get(str(row["accountId"]), ""),
            "excerpt": re.sub(r"\s+", " ", str(data.get("snippet") or ""))[:400],
            "read": not bool(row["unread"]), "_when": when}


COLUMNS = "m.id, m.accountId, m.data, m.headerMessageId, m.subject, m.date, m.unread, m.starred"


def messages(days: int = 3, now: dt.datetime | None = None, path: Path | None = None,
             unread_only: bool = True) -> list[dict]:
    """Inbox messages from the last `days` days, newest first, with their read/starred state."""
    path = path or db_path()
    if path is None:
        return []
    now = now or dt.datetime.now(dt.UTC)
    since = (now - dt.timedelta(days=days)).timestamp()
    with _DB(path) as con:
        accounts = _accounts(con)
        inbox = _inbox_ids(con)
        # seconds (or milliseconds, which are larger still) since the epoch
        where = ["(m.draft IS NULL OR m.draft = 0)", "(m.date IS NULL OR CAST(m.date AS REAL) >= ?)"]
        args: list = [since]
        if unread_only:
            where.append("m.unread = 1")
        if inbox and "ThreadCategory" in _tables(con):
            marks = ",".join("?" * len(inbox))
            where.append(f"m.threadId IN (SELECT id FROM ThreadCategory WHERE value IN ({marks}))")
            args += inbox
        sql = f"SELECT {COLUMNS} FROM Message m WHERE {' AND '.join(where)} ORDER BY m.date DESC LIMIT 2000"
        rows = con.execute(sql, args).fetchall()
        return [_to_dict(r, accounts) for r in rows]


# ── what the email tools use ────────────────────────────────────────────
SETUP_HINT = ("I can't find Mailspring's mail on this Mac yet. Install Mailspring from getmailspring.com, "
              "open it and add your email account; I'll read it from there.")


def unread(days: int = 3, limit: int = 40, now: dt.datetime | None = None, path: Path | None = None) -> list[dict] | str:
    from jarvis.mac.mail import score

    path = path or db_path()
    if path is None:
        return SETUP_HINT
    now = now or dt.datetime.now(dt.UTC)
    try:
        got = messages(days=days, now=now, path=path)
    except sqlite3.Error as exc:
        log.warning("couldn't read Mailspring's database: %s", exc)
        return f"I couldn't read Mailspring's mail just now ({exc}). Is Mailspring still setting up?"
    got = got[:limit]
    for m in got:
        m["score"] = score(m, now)
        for k in ("_when", "read"):
            m.pop(k, None)
    return sorted(got, key=lambda m: m["score"], reverse=True)


def unread_count(days: int = 2, path: Path | None = None) -> int | None:
    got = unread(days=days, limit=500, path=path)
    return None if isinstance(got, str) else len(got)


def read(message_id: str, path: Path | None = None) -> dict | None:
    """One message in full, by Mailspring's id or by its Message-ID header."""
    want = (message_id or "").strip()
    path = path or db_path()
    if not want or path is None:
        return None
    with _DB(path) as con:
        row = con.execute(f"SELECT {COLUMNS} FROM Message m WHERE m.id = ? OR m.headerMessageId IN (?, ?) LIMIT 1",
                          (want, want.strip("<>"), f"<{want.strip('<>')}>")).fetchone()
        if row is None:
            return None
        m = _to_dict(row, _accounts(con))
        data = _data(row)
        body = ""
        if "MessageBody" in _tables(con):
            b = con.execute("SELECT value FROM MessageBody WHERE id = ?", (row["id"],)).fetchone()
            body = _text(b[0] if b and b[0] else "", bool(data.get("plaintext")))
    return {"from": m["from"], "subject": m["subject"],
            "body": body[:6000] or m["excerpt"] or "(Mailspring hasn't downloaded the text of this message yet, so "
                                                  "I only have the sender and subject.)"}


def open_message(message_id: str) -> str:
    """Bring Mailspring to the front. (It has no link that opens one message by id, so we say which.)"""
    app = next((p for p in APP_PATHS if Path(p).exists()), None)
    if app is None:
        return "Mailspring isn't installed."
    m = None
    try:
        m = read(message_id)
    except Exception:
        pass
    try:
        subprocess.run(["open", "-a", app], capture_output=True, timeout=15)
    except Exception as exc:
        return f"Couldn't open Mailspring: {exc}"
    if m:
        return f"Mailspring is open. It's in your inbox: “{m['subject']}” from {m['from']}."
    return "Mailspring is open."


# ── a sample database, for the self-test and the tests ──────────────────
# Mailspring's own table definitions (Mailspring-Sync, MailSync/constants.h, current schema).
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS Label (id VARCHAR(40) PRIMARY KEY, accountId VARCHAR(8), version INTEGER, data TEXT,"
    " path VARCHAR(255), role VARCHAR(255), createdAt DATETIME, updatedAt DATETIME)",
    "CREATE TABLE IF NOT EXISTS Folder (id VARCHAR(40) PRIMARY KEY, accountId VARCHAR(8), version INTEGER, data TEXT,"
    " path VARCHAR(255), role VARCHAR(255), createdAt DATETIME, updatedAt DATETIME)",
    "CREATE TABLE IF NOT EXISTS Thread (id VARCHAR(42) PRIMARY KEY, accountId VARCHAR(8), version INTEGER, data TEXT,"
    " gThrId VARCHAR(20), subject VARCHAR(500), snippet VARCHAR(255), unread INTEGER, starred INTEGER,"
    " firstMessageTimestamp DATETIME, lastMessageTimestamp DATETIME, lastMessageReceivedTimestamp DATETIME,"
    " lastMessageSentTimestamp DATETIME, inAllMail TINYINT(1), isSearchIndexed TINYINT(1), participants TEXT,"
    " hasAttachments INTEGER)",
    "CREATE TABLE IF NOT EXISTS ThreadCategory (id VARCHAR(40), value VARCHAR(40), inAllMail TINYINT(1),"
    " unread TINYINT(1), lastMessageReceivedTimestamp DATETIME, lastMessageSentTimestamp DATETIME,"
    " PRIMARY KEY (id, value))",
    "CREATE TABLE IF NOT EXISTS `Account` (id VARCHAR(40) PRIMARY KEY, data BLOB, accountId VARCHAR(8), email_address TEXT)",
    "CREATE TABLE IF NOT EXISTS Message (id VARCHAR(40) PRIMARY KEY, accountId VARCHAR(8), version INTEGER, data TEXT,"
    " headerMessageId VARCHAR(255), gMsgId VARCHAR(255), gThrId VARCHAR(255), subject VARCHAR(500), date DATETIME,"
    " draft TINYINT(1), unread TINYINT(1), starred TINYINT(1), replyToHeaderMessageId VARCHAR(255),"
    " threadId VARCHAR(40))",
    "CREATE TABLE IF NOT EXISTS `MessageBody` (id VARCHAR(40) PRIMARY KEY, `value` TEXT)",
]


def make_sample(folder: Path, now: dt.datetime | None = None) -> Path:
    """An edgehill.db shaped like Mailspring's, with an IMAP inbox and a Gmail inbox label:
    an urgent unread mail, an unread newsletter, one already read, one in Archive, and a draft."""
    now = now or dt.datetime.now(dt.UTC)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / DB_NAME
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")            # as Mailspring runs it
    for q in SCHEMA:
        con.execute(q)
    t = int((now - dt.timedelta(hours=1)).timestamp())
    con.executemany("INSERT INTO Account VALUES (?,?,?,?)", [
        ("acc1", json.dumps({"emailAddress": "tony@stark.com"}), "acc1", "tony@stark.com"),
        ("acc2", json.dumps({"emailAddress": "tony@gmail.com"}), "acc2", "tony@gmail.com")])
    con.executemany("INSERT INTO Folder (id, accountId, path, role) VALUES (?,?,?,?)",
                    [("f-in", "acc1", "INBOX", "inbox"), ("f-ar", "acc1", "Archive", "archive"),
                     ("f-all", "acc2", "[Gmail]/All Mail", "all")])
    con.execute("INSERT INTO Label (id, accountId, path, role) VALUES ('l-in', 'acc2', 'INBOX', 'inbox')")

    def msg(mid, acc, thread, frm, subj, snippet, unread=1, starred=0, draft=0, lists=False, cats=("f-in",), body=""):
        name, addr = frm
        data = {"id": mid, "from": [{"name": name, "email": addr}], "subject": subj, "snippet": snippet,
                "date": t, "unread": bool(unread), "starred": bool(starred), "draft": bool(draft),
                "threadId": thread, "hMsgId": f"<{mid}@mail>",
                "extraHeaders": {"List-Unsubscribe": "<mailto:u@x>"} if lists else {}}
        con.execute("INSERT INTO Message (id, accountId, version, data, headerMessageId, subject, date, draft, unread,"
                    " starred, threadId) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, acc, 1, json.dumps(data), f"<{mid}@mail>", subj, t, draft, unread, starred, thread))
        for c in cats:
            con.execute("INSERT OR IGNORE INTO ThreadCategory (id, value, inAllMail, unread) VALUES (?,?,1,?)",
                        (thread, c, unread))
        if body:
            con.execute("INSERT INTO MessageBody VALUES (?, ?)", (mid, body))

    msg("m1", "acc1", "t1", ("Pepper Potts", "pepper@stark.com"), "Can you approve the contract today?",
        "The board needs it signed by 5pm.", starred=1,
        body="<html><body><p>Hi Tony,</p><p>The board needs it <b>signed by 5pm</b>.</p></body></html>")
    msg("m2", "acc2", "t2", ("Deals", "newsletter@shop.com"), "50% off everything", "Sale ends Sunday", lists=True,
        cats=("l-in", "f-all"))
    msg("m3", "acc1", "t3", ("Happy Hogan", "happy@stark.com"), "Car's ready", "Downstairs", unread=0)
    msg("m4", "acc1", "t4", ("Rhodey", "rhodey@af.mil"), "Archived thread", "old", cats=("f-ar",))
    msg("m5", "acc1", "t5", ("Tony", "tony@stark.com"), "Unsent draft", "draft", draft=1)
    con.commit()
    con.close()
    return path
