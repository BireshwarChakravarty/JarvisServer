"""A small reader for Mork, the format of Thunderbird's .msf folder indexes.

For IMAP accounts the read / flagged / deleted state of each message lives only here (the offline
mbox copy is written once, always marked read, and never updated), so JARVIS reads it to know
what's unread. Read-only; written against the format as Thunderbird writes it and checked against
Thunderbird's own test indexes.

Shape of the format, as far as we need it:
  //  comment                         to end of line
  < <(a=c)> (80=subject)(81=sender) > a column dictionary: id → column name
  < (A0=Hello)(A1=pepper@x.com) >       a value dictionary: id → value
  { 1:^80 {meta} [rows] }               a table (scope ^80); rows inside, or at top level:
  [ 1F (^81^A0)(^82=literal) ]          a row: id, then cells (column ^id, value ^id or =literal)
  [-1F …]                               "-" clears the row before these cells
  @$${N{@ … @$$}N}@                     a later transaction appended to the file (rows update)
Values escape ")" and "\\" with "\\", hex bytes as $XX (UTF-8), and lines continue with "\\" + newline.
"""
from __future__ import annotations

import re

_MARK = re.compile(r"@\$\$[{}][0-9A-Fa-f]+[{}]@")
_PAIR = re.compile(r"\(([^)]*)\)")
_DICT_ENTRY = re.compile(r"^\s*([0-9A-Fa-f]+)\s*=\s*(.*)$", re.S)
_CELL = re.compile(r"^\s*\^([0-9A-Fa-f]+)\s*([\^=])\s*(.*)$", re.S)
_HEX_RUN = re.compile(r"(?:\$[0-9A-Fa-f]{2})+")
_TABLE_SCOPE = re.compile(r"^\{\s*-?[0-9A-Fa-f]+(:[\^\w]+)")


def _decode(value: str) -> str:
    if "$" not in value:
        return value
    if "\\$" in value:                      # an escaped literal dollar sign
        parts = value.split("\\$")
        return "$".join(_decode(x) for x in parts)

    def sub(m: re.Match) -> str:
        raw = bytes(int(h, 16) for h in m.group(0).split("$")[1:])
        raw = raw.replace(b"\x00", b"")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")
    return _HEX_RUN.sub(sub, value)


def _block(text: str, start: int, open_c: str, close_c: str) -> int:
    """Index of the bracket closing the one at `start`, ignoring brackets inside (cells), or -1."""
    depth, in_paren, i, n = 0, False, start, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "(":
            in_paren = True
        elif c == ")":
            in_paren = False
        elif not in_paren:
            if c == open_c:
                depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


class Mork:
    def __init__(self) -> None:
        self.columns: dict[str, str] = {}
        self.values: dict[str, str] = {}
        self.rows: dict[str, dict[str, str]] = {}

    # ── public ───────────────────────────────────────────────────────────
    @classmethod
    def parse(cls, text: str) -> "Mork":
        m = cls()
        m._parse(text)
        return m

    def messages(self) -> list[dict[str, str]]:
        """Rows that describe messages (they carry a message-id), with "@id" = the message key."""
        out = []
        for rid, cells in self.rows.items():
            if "message-id" in cells:
                out.append({"@id": rid.split(":")[0], **cells})
        return out

    # ── parsing ──────────────────────────────────────────────────────────
    def _parse(self, text: str) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\\\\", "$5C").replace("\\)", "$29").replace("\\\n", "")
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if c in " \t\n\f":
                i += 1
                continue
            if text.startswith("//", i):
                nl = text.find("\n", i)
                i = n if nl == -1 else nl
                continue
            if c == "@":
                mk = _MARK.match(text, i)
                i = mk.end() if mk else i + 1
                continue
            if c in "<{[":
                close = {"<": ">", "{": "}", "[": "]"}[c]
                end = _block(text, i, c, close)
                if end == -1:
                    i += 1                      # unterminated (a half-written transaction): skip it
                    continue
                body = text[i:end + 1]
                if c == "<":
                    self._dict(body[1:-1])
                elif c == "{":
                    self._table(body)
                else:
                    self._rows(body, "")
                i = end + 1
                continue
            i += 1

    def _dict(self, body: str) -> None:
        is_columns = "(a=c)" in body
        if is_columns:
            body = re.sub(r"<?\(a=c\)>?", "", body, count=1)
            body = re.sub(r"(^|\s)//[^\n]*", r"\1", body)
        for m in _PAIR.finditer(body):
            e = _DICT_ENTRY.match(m.group(1))
            if not e:
                continue
            key, val = e.group(1).upper(), e.group(2)
            if is_columns:
                self.columns[key] = val
            else:
                self.values[key] = val

    def _table(self, body: str) -> None:
        sm = _TABLE_SCOPE.match(body)
        self._rows(body, sm.group(1) if sm else "")

    def _rows(self, body: str, default_scope: str) -> None:
        i, n, last = 0, len(body), ""
        while i < n:
            if body[i] != "[":
                i += 1
                continue
            end = _block(body, i, "[", "]")
            if end == -1:
                return
            content = body[i + 1:end]
            i = end + 1
            paren = content.find("(")
            rid = (content if paren == -1 else content[:paren]).strip()
            clear = rid.startswith("-")
            rid = rid.lstrip("-").strip()
            if not rid:
                rid = last
            elif ":" not in rid and default_scope:
                rid += default_scope
            if not rid:
                continue
            last = rid
            row = {} if clear or rid not in self.rows else self.rows[rid]
            for m in _PAIR.finditer(content):
                cm = _CELL.match(m.group(1).strip())
                if not cm:
                    continue
                col = self.columns.get(cm.group(1).upper())
                if col is None:
                    continue
                if cm.group(2) == "^":
                    val = self.values.get(cm.group(3).strip().upper())
                    if val is None:
                        continue
                else:
                    val = cm.group(3)
                row[col] = _decode(val)
            self.rows[rid] = row


def read(path) -> Mork:
    with open(path, "rb") as fh:
        return Mork.parse(fh.read().decode("latin-1"))
