"""Email from Mailspring's local database (edgehill.db), as Mailspring's own schema lays it out."""
import datetime as dt
import json
import sqlite3

import pytest

from jarvis.mac import mailspring as ms

NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def db(tmp_path):
    return ms.make_sample(tmp_path / "Mailspring", NOW)


def test_unread_inbox_mail_is_found_and_ranked(db):
    got = ms.unread(days=2, now=NOW, path=db)
    assert [m["id"] for m in got] == ["m1", "m2"]                 # not read, archived or draft mail
    pepper, deals = got
    assert pepper["from"] == "Pepper Potts" and pepper["address"] == "pepper@stark.com"
    assert pepper["account"] == "tony@stark.com" and pepper["flagged"] and pepper["excerpt"].startswith("The board")
    assert deals["account"] == "tony@gmail.com" and deals["mailing_list"]   # Gmail inbox label counts as inbox
    assert pepper["score"] > deals["score"]
    assert set(pepper) >= {"id", "from", "address", "subject", "received", "flagged", "mailing_list", "excerpt", "score"}
    assert "_when" not in pepper and "read" not in pepper


def test_old_mail_is_left_out(db):
    assert ms.unread(days=2, now=NOW + dt.timedelta(days=5), path=db) == []


def test_reading_one_in_full(db):
    m = ms.read("m1", path=db)
    assert m["from"] == "Pepper Potts" and "signed by 5pm." in m["body"] and "<" not in m["body"]
    assert ms.read("<m2@mail>", path=db)["subject"] == "50% off everything"     # by Message-ID too
    assert "only have the sender" not in ms.read("m2", path=db)["body"]         # no body yet: the snippet
    assert ms.read("nope", path=db) is None


def test_no_mailspring_explains_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MAILSPRING_DIR", str(tmp_path / "missing"))
    assert ms.db_path() is None
    assert ms.unread() == ms.SETUP_HINT and "Mailspring" in ms.SETUP_HINT
    assert ms.unread_count() is None


def test_finds_the_database_where_mailspring_keeps_it(db, monkeypatch):
    monkeypatch.setenv("JARVIS_MAILSPRING_DIR", str(db.parent))
    assert ms.db_path() == db and ms.unread_count(days=2) is not None


def test_reads_while_mailspring_is_writing(db):
    writer = sqlite3.connect(str(db))
    writer.execute("BEGIN IMMEDIATE")                             # Mailspring mid-sync, holding the write lock
    writer.execute("UPDATE Message SET unread = 0 WHERE id = 'm2'")
    try:
        got = ms.unread(days=2, now=NOW, path=db)
        assert [m["id"] for m in got] == ["m1", "m2"]             # its unfinished change isn't seen yet
    finally:
        writer.rollback()
        writer.close()


def test_falls_back_to_a_private_copy(db, monkeypatch):
    real = sqlite3.connect

    def picky(target, *a, **k):
        if k.get("uri"):
            raise sqlite3.OperationalError("unable to open database file")
        return real(target, *a, **k)
    monkeypatch.setattr(ms.sqlite3, "connect", picky)
    assert [m["id"] for m in ms.unread(days=2, now=NOW, path=db)] == ["m1", "m2"]


def test_without_folder_roles_every_unread_message_counts(db):
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE ThreadCategory")
    con.commit()
    con.close()
    assert {m["id"] for m in ms.unread(days=2, now=NOW, path=db)} == {"m1", "m2", "m4"}


def test_millisecond_dates_and_odd_rows(db):
    con = sqlite3.connect(str(db))
    t = int((NOW - dt.timedelta(hours=2)).timestamp() * 1000)
    con.execute("INSERT INTO Message (id, accountId, data, subject, date, draft, unread, starred, threadId) "
                "VALUES ('m6', 'acc1', ?, 'Lunch?', ?, 0, 1, 0, 't1')", (json.dumps({"from": []}), t))
    con.execute("INSERT INTO Message (id, accountId, data, subject, date, draft, unread, starred, threadId) "
                "VALUES ('m7', 'acc1', 'not json', 'Broken', ?, 0, 1, 0, 't1')", (t,))
    con.commit()
    con.close()
    got = {m["id"]: m for m in ms.unread(days=2, now=NOW, path=db)}
    assert got["m6"]["received"].startswith("2026-11-18T07:00") and got["m6"]["from"] == ""
    assert got["m7"]["subject"] == "Broken"


def test_email_tools_use_mailspring(tmp_path, monkeypatch):
    from jarvis.tools import builtin

    db = ms.make_sample(tmp_path / "live")                        # dated now: the tools use the real clock
    monkeypatch.setenv("JARVIS_MAILSPRING_DIR", str(db.parent))
    out = builtin.check_email(None)
    assert out["candidates"][0]["id"] == "m1" and out["unread_total"] == 2
    assert "signed by 5pm" in builtin.read_email(None, "m1")["body"]
    monkeypatch.setattr(ms, "APP_PATHS", ("/nonexistent/Mailspring.app",))
    assert builtin.open_email(None, "m1") == "Mailspring isn't installed."
