"""Email from Thunderbird's local copy: profile discovery, mbox and maildir, read/flag/delete status,
and the same top-3 ranking as before."""
import datetime as dt
from email.utils import format_datetime

from jarvis.mac import thunderbird as tb

NOW = dt.datetime(2026, 11, 17, 18, 0, tzinfo=dt.timezone.utc)


def msg(mid, frm, subject, body, status="0000", hours_ago=2, extra="", html=False):
    when = format_datetime(NOW - dt.timedelta(hours=hours_ago))
    if html:
        content = ('Content-Type: multipart/alternative; boundary="b"\n\n--b\nContent-Type: text/html; charset=utf-8\n\n'
                   f"<html><style>p{{x:1}}</style><body><p>{body}</p></body></html>\n--b--\n")
    else:
        content = f"Content-Type: text/plain; charset=utf-8\n\n{body}\n"
    return (f"From - Tue Nov 17 10:00:00 2026\nX-Mozilla-Status: {status}\nX-Mozilla-Status2: 00000000\n"
            f"Message-ID: <{mid}>\nFrom: {frm}\nSubject: {subject}\nDate: {when}\n{extra}MIME-Version: 1.0\n"
            f"{content}\n")


def msf(rows):
    """A Thunderbird-style Mork index: rows = [(message-id, flags, sender, subject, hours_ago)]."""
    cols = ("< <(a=c)> // (f=iso-8859-1)\n  (80=ns:msg:db:row:scope:msgs:all)(81=subject)(82=sender)\n"
            "  (83=message-id)(86=date)(88=flags)(96=ns:msg:db:table:kind:msgs)>\n")
    vals, body = [], []
    for i, (mid, flags, sender, subject, hours) in enumerate(rows):
        a, b, c = 0xA0 + 3 * i, 0xA1 + 3 * i, 0xA2 + 3 * i
        vals.append(f"({a:X}={sender})({b:X}\n    ={subject.replace(')', chr(92) + ')')})({c:X}={mid})")
        when = int((NOW - dt.timedelta(hours=hours)).timestamp())
        body.append(f"  [{i + 1:X}(^88={flags:x})(^82^{a:X})(^81^{b:X})(^83^{c:X})(^86={when:x})]")
    return cols + "<" + "".join(vals) + ">\n{1:^80 {(k^96:c)(s=9)} \n" + "\n".join(body) + "}\n"


def make_profile(tmp_path, store="mbox"):
    base = tmp_path / "Thunderbird"
    prof = base / "Profiles" / "abcd.default-release"
    (base / "Profiles" / "zzzz.default").mkdir(parents=True)                 # an older, unused profile
    prof.mkdir(parents=True)
    (base / "profiles.ini").write_text("[Profile1]\nName=default\nIsRelative=1\nPath=Profiles/zzzz.default\n\n"
                                       "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abcd.default-release\n\n"
                                       "[Install8216C80C92C4E828]\nDefault=Profiles/abcd.default-release\nLocked=1\n")
    gmail = prof / "ImapMail" / "imap.gmail.com"
    gmail.mkdir(parents=True)
    mails = [
        msg("old@x", "Old Friend <old@x.com>", "From last month", "hi", hours_ago=24 * 30),
        msg("read@x", "Pepper Potts <pepper@stark.com>", "Lunch?", "Already read this.", status="0001"),
        msg("news@x", "Weekly Digest <newsletter@shop.com>", "50% off this week only", "Sale! Unsubscribe here.",
            extra="List-Unsubscribe: <mailto:u@shop.com>\n"),
        msg("ask@x", "Pepper Potts <pepper@stark.com>", "Can you approve the contract today?",
            "Need your signature by 5pm. Can you sign?", hours_ago=1),
        msg("flag@x", "Happy Hogan <happy@stark.com>", "Car at 7", "From the garage: the car is ready.\n>From here on, details.",
            status="0004", hours_ago=5),
        msg("gone@x", "Someone <s@x.com>", "Deleted one", "bye", status="0008"),
        msg("html@x", "=?utf-8?q?Rhodey_=E2=9C=88?= <rhodey@af.mil>", "Flight plan question?", "Are we still on for Friday?",
            hours_ago=3, html=True),
    ]
    # IMAP: the stored copies are all marked read (as Thunderbird writes them); the index has the truth
    mails = [re_status(m) for m in mails]
    (gmail / "INBOX.msf").write_text(msf([
        ("old@x", 0, "Old Friend <old@x.com>", "From last month", 24 * 30),
        ("read@x", 0x1, "Pepper Potts <pepper@stark.com>", "Lunch?", 2),
        ("news@x", 0, "Weekly Digest <newsletter@shop.com>", "50% off this week only", 2),
        ("ask@x", 0, "Pepper Potts <pepper@stark.com>", "Can you approve the contract today?", 1),
        ("flag@x", 0x4, "Happy Hogan <happy@stark.com>", "Car at 7", 5),
        ("gone@x", 0x200000, "Someone <s@x.com>", "Deleted one", 2),
        ("html@x", 0, "=?utf-8?q?Rhodey_=E2=9C=88?= <rhodey@af.mil>", "Flight plan question?", 3),
        ("nocopy@x", 0x10, "Nick Fury <fury@shield.gov>", "Debrief (urgent)", 2),   # no offline copy; "Re:" flag
    ]))
    if store == "mbox":
        (gmail / "INBOX").write_text("".join(mails))
    else:
        cur = gmail / "INBOX" / "cur"
        cur.mkdir(parents=True)
        for i, m in enumerate(mails):
            body = m.split("\n", 1)[1]                                         # maildir files have no From_ line
            (cur / f"{i:04d}.eml").write_text(body)
    local = prof / "Mail" / "Local Folders"
    local.mkdir(parents=True)
    (local / "Inbox").write_text(msg("pop@x", "Tony <tony@stark.com>", "Suit diagnostics", "Check the logs?", hours_ago=4))
    return base, prof


def re_status(m):
    import re
    return re.sub(r"X-Mozilla-Status: \w+", "X-Mozilla-Status: 0001", m)


def test_finds_the_default_profile(tmp_path):
    base, prof = make_profile(tmp_path)
    assert tb.profile_dir(base) == prof
    (base / "installs.ini").write_text("[8216C80C92C4E828]\nDefault=Profiles/abcd.default-release\n")
    assert tb.profile_dir(base) == prof
    assert tb.profile_dir(tmp_path / "nothing") is None


def test_unread_ranked_like_before_with_status_bits(tmp_path):
    _, prof = make_profile(tmp_path)
    got = tb.unread(days=3, now=NOW, profile=prof)
    ids = [m["id"] for m in got]
    assert "read@x" not in ids and "gone@x" not in ids and "old@x" not in ids      # read, deleted, too old
    assert set(ids) == {"ask@x", "flag@x", "html@x", "news@x", "pop@x", "nocopy@x"}
    assert ids[0] in ("ask@x", "flag@x") and ids[-1] == "news@x"                     # the newsletter sinks
    flag = next(m for m in got if m["id"] == "flag@x")
    assert flag["flagged"] and flag["account"] == "imap.gmail.com"
    html = next(m for m in got if m["id"] == "html@x")
    assert html["from"] == "Rhodey ✈" and "Friday" in html["excerpt"] and "x:1" not in html["excerpt"]
    news = next(m for m in got if m["id"] == "news@x")
    assert news["mailing_list"] is True
    nocopy = next(m for m in got if m["id"] == "nocopy@x")
    assert nocopy["subject"] == "Re: Debrief (urgent)" and nocopy["from"] == "Nick Fury" and nocopy["excerpt"] == ""
    assert all({"id", "from", "address", "subject", "received", "flagged", "mailing_list", "excerpt", "score"} <= set(m)
               for m in got)
    assert tb.unread_count(days=3, profile=prof) is not None


def test_maildir_store_reads_the_same(tmp_path):
    _, prof = make_profile(tmp_path, store="maildir")
    got = tb.unread(days=3, now=NOW, profile=prof)
    assert {m["id"] for m in got} == {"ask@x", "flag@x", "html@x", "news@x", "pop@x", "nocopy@x"}


def test_read_a_message_in_full(tmp_path):
    _, prof = make_profile(tmp_path)
    m = tb.read("<ask@x>", profile=prof)
    assert m["from"] == "Pepper Potts" and "signature by 5pm" in m["body"]
    assert tb.read("nope@x", profile=prof) is None
    assert "only have the sender" in tb.read("nocopy@x", profile=prof)["body"]


def test_imap_without_an_index_is_not_guessed(tmp_path):
    _, prof = make_profile(tmp_path)
    (prof / "ImapMail" / "imap.gmail.com" / "INBOX.msf").unlink()
    ids = {m["id"] for m in tb.unread(days=3, now=NOW, profile=prof)}
    assert ids == {"pop@x"}                      # IMAP copies all say "read": never trusted on their own


def test_only_the_newest_part_of_a_huge_inbox_is_read(tmp_path, monkeypatch):
    _, prof = make_profile(tmp_path)
    inbox = prof / "ImapMail" / "imap.gmail.com" / "INBOX"
    filler = "".join(msg(f"f{i}@x", "Bulk <b@x.com>", "old", "x" * 200, status="0001", hours_ago=999) for i in range(300))
    inbox.write_text(filler + inbox.read_text())
    monkeypatch.setattr(tb, "TAIL_BYTES", 6000)
    raws = tb.raw_messages(inbox)
    assert raws and all(r.startswith(b"X-Mozilla-Status") for r in raws)          # cut cleanly at a message
    assert b"<html@x>" in raws[-1]


def test_no_thunderbird_explains_how_to_set_it_up(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_THUNDERBIRD_DIR", str(tmp_path / "none"))
    got = tb.unread()
    assert isinstance(got, str) and "Thunderbird" in got and "Keep messages" in got
    assert tb.unread_count() is None
