"""Email that needs attention: parsing Apple Mail output, ranking, and the tool's contract."""
import datetime as dt

from jarvis.mac import mail

NOW = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.timezone.utc)


def rec(mid, sender, subject, when, flagged="false", lists="0", body=""):
    return mail.US.join([mid, sender, subject, when, flagged, lists, "Gmail", body]) + mail.RS


RAW = (rec("a", "Weekly Digest <newsletter@shop.example>", "50% off this weekend only", "2026-11-16T07:00:00",
           lists="1", body="Big sale, unsubscribe here")
       + rec("b", "Pepper Potts <pepper@stark.example>", "Can you approve the Q4 budget by tomorrow?",
             "2026-11-16T06:30:00", body="Hi Tony, the board needs your approval before the call.")
       + rec("c", "GitHub <noreply@github.com>", "[repo] CI passed", "2026-11-15T22:00:00", body="All checks passed")
       + rec("d", "Happy Hogan <happy@stark.example>", "Re: car tomorrow", "2026-11-14T10:00:00", flagged="true",
             body="Shall I bring the car round at eight?")
       + rec("e", "Accounts <billing@vendor.example>", "Invoice 4471 overdue", "2026-11-16T05:00:00",
             body="Payment was due on the 10th. Please pay to avoid suspension."))


def test_parse_and_rank():
    msgs = mail.parse(RAW)
    assert len(msgs) == 5 and msgs[1]["from"] == "Pepper Potts" and msgs[1]["address"] == "pepper@stark.example"
    assert msgs[0]["mailing_list"] and msgs[3]["flagged"]
    ranked = sorted(msgs, key=lambda m: mail.score(m, NOW), reverse=True)
    top3 = [m["id"] for m in ranked[:3]]
    assert set(top3) == {"b", "d", "e"}                    # people, approvals, money; not the sale or the CI bot
    assert ranked[-1]["id"] in ("a", "c")


def test_check_email_tool(monkeypatch):
    from jarvis.tools import builtin

    msgs = mail.parse(RAW)
    for m in msgs:
        m["score"] = mail.score(m, NOW)
    from jarvis.mac import mailspring

    monkeypatch.setattr(mailspring, "unread", lambda **k: sorted(msgs, key=lambda m: m["score"], reverse=True))
    out = builtin.check_email(None)
    assert out["pick_at_most"] == 3 and out["unread_total"] == 5 and out["candidates"][0]["id"] in ("b", "d", "e")
    monkeypatch.setattr(mailspring, "unread", lambda **k: mailspring.SETUP_HINT)
    assert "Mailspring" in builtin.check_email(None)
    monkeypatch.setattr(mailspring, "unread", lambda **k: [])
    assert "No unread email" in builtin.check_email(None)


def test_greeting_can_lead_with_mail():
    import random

    from jarvis.core import greeting_style as gs

    data = {"email_needing_attention": [{"from": "Pepper Potts", "subject": "Approve Q4 budget"}]}
    picks = {gs.pick_angle(data, [], 3600, [], dt.datetime(2026, 11, 17, 9), random.Random(i)) for i in range(40)}
    assert "mail" in picks
