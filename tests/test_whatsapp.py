"""WhatsApp through the official Mac app: reading its local chat database (a private snapshot),
and sending through the app with a check that the message really went."""
import datetime as dt
import sqlite3

import pytest

from jarvis.mac import whatsapp as wa

NOW = dt.datetime(2026, 11, 20, 18, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = wa.make_sample(tmp_path / "group.net.whatsapp.WhatsApp.shared", NOW)
    monkeypatch.setenv("JARVIS_WHATSAPP_DIR", str(path.parent))
    return path


def test_unread_people_first_with_what_they_said(db):
    got = wa.unread()
    assert [c["chat"] for c in got] == ["Pepper Potts", "Old Newsletter", "Stark Family"]   # status rows never
    pepper, archived, family = got
    assert not pepper["group"] and pepper["unread"] == 2
    assert [m["text"] for m in pepper["messages"]] == ["Can you sign it before 5?", "It's urgent"]
    assert pepper["messages"][0]["from"] == "Pep"                      # their WhatsApp name
    assert archived.get("archived")
    assert family["group"] and [m["from"] for m in family["messages"]] == ["Morgan", "Rhodey", "Rhodey"]
    assert family["messages"][1]["text"] == "[photo]"
    assert family["messages"][0]["when"].startswith("2026-11-20T17:56")   # Core Data dates (from 2001)


def test_a_chat_by_name_both_sides(db):
    got = wa.recent("pepper")
    assert got["chat"] == "Pepper Potts"
    assert [(m["from"], m["text"]) for m in got["messages"]][:2] == [("Pep", "Did you see the contract?"),
                                                                    ("me", "Not yet")]
    assert wa.recent("the family group")["chat"] == "Stark Family"
    assert wa.recent("nobody at all") is None


def test_people_waiting_counts_people_not_groups_or_archived(db):
    assert wa.unread_people() == 1


def test_never_touches_whatsapps_own_files(db):
    before = {p.name: p.stat().st_mtime_ns for p in db.parent.iterdir()}
    wa.unread()
    wa.recent("Pepper")
    assert {p.name: p.stat().st_mtime_ns for p in db.parent.iterdir()} == before
    assert not (db.parent / "ChatStorage.sqlite-shm").exists() or "ChatStorage.sqlite-shm" in before


def test_reads_while_whatsapp_is_writing(db):
    w = sqlite3.connect(str(db))
    w.execute("BEGIN IMMEDIATE")
    w.execute("UPDATE ZWACHATSESSION SET ZUNREADCOUNT = 0 WHERE Z_PK = 1")
    try:
        assert wa.unread()[0]["chat"] == "Pepper Potts"
    finally:
        w.rollback()
        w.close()


def test_plain_words_when_it_cant_read(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WHATSAPP_DIR", str(tmp_path / "none"))
    with pytest.raises(wa.WhatsAppError, match="Install WhatsApp"):
        wa.unread()
    assert wa.unread_people() is None


def test_a_changed_layout_is_refused_not_guessed(db):
    con = sqlite3.connect(str(db))
    con.execute("ALTER TABLE ZWAMESSAGE RENAME COLUMN ZTEXT TO ZBODY")
    con.commit()
    con.close()
    with pytest.raises(wa.WhatsAppError, match="changed how it stores"):
        wa.unread()


def test_no_access_says_how_to_allow_it(db, monkeypatch):
    real_open = open

    def denied(path, *a, **k):
        if str(path).endswith("ChatStorage.sqlite"):
            raise PermissionError("Operation not permitted")
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", denied)
    with pytest.raises(wa.WhatsAppError, match="Full Disk Access"):
        wa.unread()
    assert wa.can_read() == "denied"


# ── sending ──────────────────────────────────────────────────────────────
class FakeMac:
    """WhatsApp on a virtual clock: the chat opens, the message box fills after `fill_after` seconds,
    Return sends only if the box holds the text, and the message lands in the database."""

    def __init__(self, db, fill_after=0.5, lands=True, ignore_first_return=False, ax=True):
        self.db, self.fill_after, self.lands, self.ignore_first = db, fill_after, lands, ignore_first_return
        self.ax, self.opened, self.returns, self.now, self.box, self.sent_text = ax, [], 0, 1000.0, "", None

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s
        if self.opened and self.box == "" and self.sent_text is None and self.now - self.t_open >= self.fill_after:
            self.box = self.pending

    def run(self, cmd, **k):
        import urllib.parse
        self.opened.append(cmd[-1])
        self.t_open = self.now
        self.pending = urllib.parse.unquote(cmd[-1].split("&text=")[1])

        class R:
            returncode, stderr = 0, ""
        return R()

    def composer(self):
        return self.box if self.ax else None

    def press(self):
        self.returns += 1
        if not self.box or (self.ignore_first and self.returns == 1):
            return                                   # too early, or the app ignored it
        self.sent_text, self.box = self.box, ""
        if self.lands:
            con = sqlite3.connect(str(self.db))
            t = (dt.datetime.fromtimestamp(self.now, dt.timezone.utc) - wa.EPOCH).total_seconds()
            con.execute("INSERT INTO ZWAMESSAGE (ZISFROMME, ZCHATSESSION, ZMESSAGEDATE, ZTEXT) VALUES (1, 1, ?, ?)",
                        (t, self.sent_text))
            con.commit()
            con.close()


def fake(monkeypatch, db, **kw):
    mac = FakeMac(db, **kw)
    monkeypatch.setattr(wa, "installed", lambda: True)
    monkeypatch.setattr(wa.subprocess, "run", mac.run)
    monkeypatch.setattr(wa, "_frontmost_bundle", lambda: wa.BUNDLE_ID)
    monkeypatch.setattr(wa, "_running", lambda: True)
    monkeypatch.setattr(wa, "_press_return", mac.press)
    monkeypatch.setattr(wa, "_composer_text", mac.composer, raising=False)
    monkeypatch.setattr(wa.time, "sleep", mac.sleep)
    monkeypatch.setattr(wa.time, "time", mac.time)
    return mac


def test_send_waits_for_the_message_box_then_one_return(db, monkeypatch):
    mac = fake(monkeypatch, db, fill_after=3.5)                 # WhatsApp slow to open the chat
    out = wa.send("Pepper", "On my way, ten minutes", wait=3)
    assert out == "Sent to Pepper Potts on WhatsApp.", out
    assert mac.opened == ["whatsapp://send?phone=447700900123&text=On%20my%20way%2C%20ten%20minutes"]
    assert mac.returns == 1                                      # first try, every time


def test_a_return_that_didnt_take_is_pressed_once_more(db, monkeypatch):
    mac = fake(monkeypatch, db, ignore_first_return=True)
    assert wa.send("Pepper", "On my way", wait=3) == "Sent to Pepper Potts on WhatsApp."
    assert mac.returns == 2


def test_never_more_than_two_returns_and_says_so(db, monkeypatch):
    mac = fake(monkeypatch, db, lands=False)
    mac.press = lambda: setattr(mac, "returns", mac.returns + 1)        # Return does nothing at all
    monkeypatch.setattr(wa, "_press_return", mac.press)
    out = wa.send("Pepper", "On my way", wait=3)
    assert "can't see it sent" in out and "press Return" in out and mac.returns == 2


def test_an_emptied_box_counts_as_sent_when_the_history_lags(db, monkeypatch):
    mac = fake(monkeypatch, db, lands=False)
    assert wa.send("Pepper", "On my way", wait=3) == "Sent to Pepper Potts on WhatsApp." and mac.returns == 1


def test_without_accessibility_it_waits_longer_and_still_checks(db, monkeypatch):
    mac = fake(monkeypatch, db, ax=False, fill_after=1.5)
    assert wa.send("Pepper", "On my way", wait=3) == "Sent to Pepper Potts on WhatsApp."
    assert mac.returns == 1


def test_groups_and_unknown_people_are_explained(db, monkeypatch):
    fake(monkeypatch, db)
    assert "not to a group" in wa.send("Stark Family", "hi")
    monkeypatch.setattr("jarvis.mac.pim.find_contact", lambda name: {})
    assert "country code" in wa.send("Nick Fury", "hi")


def test_a_number_with_country_code_works_without_a_chat(db, monkeypatch):
    mac = fake(monkeypatch, db, lands=False)
    out = wa.send("+44 7700 900999", "Hello", wait=3)
    assert mac.opened[0].startswith("whatsapp://send?phone=447700900999&text=Hello")
    assert out.startswith("Sent to +44 7700 900999")


def test_sending_needs_your_yes():
    from jarvis.tools import builtin  # noqa: F401
    from jarvis.tools.registry import registry

    t = registry.get("send_whatsapp")
    assert t.confirm and "send Pepper this on WhatsApp: hi" in t.confirm_message({"recipient": "Pepper", "text": "hi"})


def test_tools_explain_rather_than_crash(tmp_path, monkeypatch):
    from jarvis.tools import builtin

    monkeypatch.setenv("JARVIS_WHATSAPP_DIR", str(tmp_path / "none"))
    assert "Install WhatsApp" in builtin.check_whatsapp(None)
    assert "Install WhatsApp" in builtin.read_whatsapp(None, "Pepper")


def test_greeting_can_lead_with_whatsapp():
    import random

    from jarvis.core import greeting_style as gs

    data = {"whatsapp_waiting": [{"from": "Pepper Potts", "said": "It's urgent"}]}
    picks = {gs.pick_angle(data, [], 3600, [], dt.datetime(2026, 11, 20, 9), random.Random(i)) for i in range(60)}
    assert "whatsapp" in picks


def test_two_equally_likely_chats_are_asked_about_not_guessed():
    import pytest

    from jarvis.mac import whatsapp

    ranked = [(0.9, {"ZPARTNERNAME": "Mum"}), (0.88, {"ZPARTNERNAME": "Mum's Birthday"})]
    with pytest.raises(whatsapp.AmbiguousChat) as err:
        raise whatsapp.AmbiguousChat("mum", [r["ZPARTNERNAME"] for _, r in ranked])
    assert "Mum or Mum's Birthday" in str(err.value) and err.value.expected
