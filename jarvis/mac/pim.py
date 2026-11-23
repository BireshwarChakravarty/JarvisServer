"""Personal information: Calendar & Reminders (EventKit, AppleScript fallback),
Contacts, Messages, Notes, Mail and music playback (Music.app / Spotify)."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any

from jarvis.mac.system import IS_MAC, osascript, q, require_mac

log = logging.getLogger("jarvis.pim")


# ═════════════════════════════════ EventKit ══════════════════════════════════
class _EventKit:
    """Lazy EventKit wrapper (pyobjc-framework-EventKit)."""

    def __init__(self) -> None:
        self._store = None
        self._lock = threading.Lock()
        self.ok_events = False
        self.ok_reminders = False

    def _request(self, store, entity: int, full_selector: str) -> bool:
        import EventKit  # noqa: F401

        done = threading.Event()
        result = {"granted": False}

        def cb(granted, error):  # called on a background queue
            result["granted"] = bool(granted)
            done.set()

        if hasattr(store, full_selector):
            getattr(store, full_selector)(cb)
        else:
            store.requestAccessToEntityType_completion_(entity, cb)
        done.wait(60)
        return result["granted"]

    def store(self):
        with self._lock:
            if self._store is None:
                import EventKit

                store = EventKit.EKEventStore.alloc().init()
                self.ok_events = self._request(store, EventKit.EKEntityTypeEvent,
                                               "requestFullAccessToEventsWithCompletion_")
                self.ok_reminders = self._request(store, EventKit.EKEntityTypeReminder,
                                                  "requestFullAccessToRemindersWithCompletion_")
                self._store = store
            return self._store


_ek = _EventKit()


def _eventkit_available() -> bool:
    """EventKit is used only inside JARVIS.app (whose Info.plist carries the calendar/reminders
    usage descriptions macOS requires). From a bare terminal we use AppleScript instead."""
    import os

    if not IS_MAC or not (os.environ.get("JARVIS_APP") == "1" or os.environ.get("JARVIS_EVENTKIT") == "1"):
        return False
    try:
        import EventKit  # noqa: F401
        return True
    except ImportError:
        return False


def _nsdate(when: dt.datetime):
    import Foundation

    return Foundation.NSDate.dateWithTimeIntervalSince1970_(when.timestamp())


def _pydate(nsdate) -> dt.datetime | None:
    if nsdate is None:
        return None
    return dt.datetime.fromtimestamp(nsdate.timeIntervalSince1970()).astimezone()


def _fmt_time(d: dt.datetime | None) -> str:
    return d.strftime("%-I:%M %p") if d else ""


# ═════════════════════════════════ Calendar ══════════════════════════════════
def events_between(start: dt.datetime, end: dt.datetime) -> list[dict]:
    require_mac()
    if _eventkit_available():
        store = _ek.store()
        if _ek.ok_events:
            pred = store.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(start), _nsdate(end), None)
            out = []
            for e in store.eventsMatchingPredicate_(pred) or []:
                s, f = _pydate(e.startDate()), _pydate(e.endDate())
                out.append({
                    "title": str(e.title() or "Untitled"),
                    "start": s.isoformat() if s else None,
                    "end": f.isoformat() if f else None,
                    "time": "all day" if e.isAllDay() else f"{_fmt_time(s)} – {_fmt_time(f)}",
                    "all_day": bool(e.isAllDay()),
                    "location": str(e.location() or ""),
                    "calendar": str(e.calendar().title()) if e.calendar() else "",
                })
            out.sort(key=lambda x: x["start"] or "")
            return out
    return _events_applescript(start, end)


def _as_date(var: str, d: dt.datetime) -> str:
    # Locale-independent AppleScript date construction
    return (
        f"set {var} to current date\n"
        f"set year of {var} to {d.year}\nset month of {var} to {d.month}\nset day of {var} to {d.day}\n"
        f"set time of {var} to {d.hour * 3600 + d.minute * 60 + d.second}\n"
    )


def _events_applescript(start: dt.datetime, end: dt.datetime) -> list[dict]:
    script = (
        _as_date("d1", start) + _as_date("d2", end) +
        'set output to ""\n'
        'tell application "Calendar"\n'
        "  repeat with c in calendars\n"
        "    set evs to (every event of c whose start date ≥ d1 and start date < d2)\n"
        "    repeat with e in evs\n"
        "      set output to output & (summary of e) & \"|\" & ((start date of e) as string) & \"|\" & "
        "((end date of e) as string) & \"|\" & (allday event of e as string) & linefeed\n"
        "    end repeat\n"
        "  end repeat\n"
        "end tell\n"
        "return output"
    )
    out = osascript(script, timeout=60)
    events = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 4:
            events.append({"title": parts[0], "start": parts[1], "end": parts[2],
                           "time": "all day" if parts[3] == "true" else parts[1].split(" at ")[-1],
                           "all_day": parts[3] == "true"})
    return events


def create_event(title: str, start: dt.datetime, end: dt.datetime, location: str = "",
                 notes: str = "", calendar: str | None = None) -> str:
    require_mac()
    if _eventkit_available():
        import EventKit

        store = _ek.store()
        if _ek.ok_events:
            ev = EventKit.EKEvent.eventWithEventStore_(store)
            ev.setTitle_(title)
            ev.setStartDate_(_nsdate(start))
            ev.setEndDate_(_nsdate(end))
            if location:
                ev.setLocation_(location)
            if notes:
                ev.setNotes_(notes)
            cal = None
            if calendar:
                for c in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent):
                    if str(c.title()).lower() == calendar.lower():
                        cal = c
            ev.setCalendar_(cal or store.defaultCalendarForNewEvents())
            ok, err = store.saveEvent_span_error_(ev, EventKit.EKSpanThisEvent, None)
            if not ok:
                raise RuntimeError(str(err))
            return f"Added '{title}' on {start:%A %-d %B at %-I:%M %p}."
    cal_expr = f"calendar {q(calendar)}" if calendar else "first calendar whose writable is true"
    osascript(
        _as_date("d1", start) + _as_date("d2", end) +
        f'tell application "Calendar" to tell ({cal_expr}) to make new event with properties '
        f"{{summary:{q(title)}, start date:d1, end date:d2, location:{q(location)}, description:{q(notes)}}}"
    )
    return f"Added '{title}' on {start:%A %-d %B at %-I:%M %p}."


# ═════════════════════════════════ Reminders ═════════════════════════════════
def reminders(due_before: dt.datetime | None = None, limit: int = 25) -> list[dict]:
    require_mac()
    if _eventkit_available():
        store = _ek.store()
        if _ek.ok_reminders:
            end = _nsdate(due_before) if due_before else None
            pred = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, end, None)
            done = threading.Event()
            box: dict[str, Any] = {"items": []}

            def cb(items):
                box["items"] = list(items or [])
                done.set()

            store.fetchRemindersMatchingPredicate_completion_(pred, cb)
            done.wait(30)
            out = []
            for r in box["items"]:
                comps = r.dueDateComponents()
                due = None
                if comps is not None and comps.date() is not None:
                    due = _pydate(comps.date())
                out.append({"title": str(r.title() or ""), "due": due.isoformat() if due else None,
                            "list": str(r.calendar().title()) if r.calendar() else ""})
            out.sort(key=lambda x: (x["due"] is None, x["due"] or ""))
            return out[:limit]
    out = osascript(
        'set output to ""\n'
        'tell application "Reminders"\n'
        "  repeat with r in (every reminder whose completed is false)\n"
        "    set output to output & (name of r) & linefeed\n"
        "  end repeat\nend tell\nreturn output",
        timeout=60,
    )
    return [{"title": t} for t in out.splitlines()[:limit] if t.strip()]


def create_reminder(title: str, due: dt.datetime | None = None, notes: str = "",
                    list_name: str | None = None) -> str:
    require_mac()
    if _eventkit_available():
        import EventKit
        import Foundation

        store = _ek.store()
        if _ek.ok_reminders:
            r = EventKit.EKReminder.reminderWithEventStore_(store)
            r.setTitle_(title)
            if notes:
                r.setNotes_(notes)
            cal = None
            if list_name:
                for c in store.calendarsForEntityType_(EventKit.EKEntityTypeReminder):
                    if str(c.title()).lower() == list_name.lower():
                        cal = c
            r.setCalendar_(cal or store.defaultCalendarForNewReminders())
            if due:
                comps = Foundation.NSDateComponents.alloc().init()
                comps.setYear_(due.year); comps.setMonth_(due.month); comps.setDay_(due.day)
                comps.setHour_(due.hour); comps.setMinute_(due.minute)
                r.setDueDateComponents_(comps)
                r.addAlarm_(EventKit.EKAlarm.alarmWithAbsoluteDate_(_nsdate(due)))
            ok, err = store.saveReminder_commit_error_(r, True, None)
            if not ok:
                raise RuntimeError(str(err))
            return f"Reminder set: {title}" + (f" for {due:%a %-d %b, %-I:%M %p}." if due else ".")
    props = f"{{name:{q(title)}, body:{q(notes)}"
    script = ""
    if due:
        script += _as_date("d1", due)
        props += ", remind me date:d1"
    props += "}"
    target = f"list {q(list_name)}" if list_name else "default list"
    osascript(script + f'tell application "Reminders" to tell {target} to make new reminder with properties {props}')
    return f"Reminder set: {title}."


# ═════════════════════════════ Contacts & Messages ═══════════════════════════
class AmbiguousContact(RuntimeError):
    """Several contacts match: JARVIS asks which one rather than guessing."""

    expected = True

    def __init__(self, name: str, options: list[str]) -> None:
        self.options = options
        shown = ", ".join(options[:-1]) + f" or {options[-1]}" if len(options) > 1 else options[0]
        super().__init__(f"Several contacts match {name!r}: {shown}. Ask which one they mean.")


def contact_names(name: str) -> list[str]:
    out = osascript(
        'tell application "Contacts"\n'
        f"  set ps to (every person whose name contains {q(name)})\n"
        '  set out to ""\n'
        "  repeat with p in ps\n"
        '    set out to out & (name of p) & linefeed\n'
        "  end repeat\n"
        "  return out\n"
        "end tell",
        timeout=30,
    )
    return [n.strip() for n in (out or "").splitlines() if n.strip()]


def pick_contact(name: str, names: list[str]) -> str | None:
    """The one contact meant by `name`, or raise AmbiguousContact. An exact full-name match wins; so
    does a single contact whose first name is exactly it ('Pepper' among 'Pepper Potts', 'Peppa Pig')."""
    if not names:
        return None
    want = name.strip().lower()
    exact = [n for n in names if n.lower() == want]
    if len(exact) == 1:
        return exact[0]
    first = [n for n in names if n.lower().split()[0] == want] if want else []
    if len(first) == 1:
        return first[0]
    pool = exact or first or names
    if len(pool) == 1:
        return pool[0]
    raise AmbiguousContact(name, pool[:4])


def find_contact(name: str) -> dict:
    chosen = pick_contact(name, contact_names(name))
    if not chosen:
        return {}
    name = chosen
    out = osascript(
        'tell application "Contacts"\n'
        f"  set ps to (every person whose name is {q(name)})\n"
        '  if (count of ps) is 0 then return ""\n'
        "  set p to item 1 of ps\n"
        '  set ph to ""\n  set em to ""\n'
        "  if (count of phones of p) > 0 then set ph to value of first phone of p\n"
        "  if (count of emails of p) > 0 then set em to value of first email of p\n"
        '  return (name of p) & "|" & ph & "|" & em\n'
        "end tell",
        timeout=30,
    )
    if not out:
        return {}
    full, phone, email = (out.split("|") + ["", "", ""])[:3]
    return {"name": full, "phone": phone, "email": email}


def send_imessage(recipient: str, text: str) -> str:
    handle = recipient
    if not any(ch.isdigit() for ch in recipient) and "@" not in recipient:
        contact = find_contact(recipient)
        handle = contact.get("phone") or contact.get("email") or ""
        if not handle:
            raise RuntimeError(f"I couldn't find {recipient} in your contacts.")
    osascript(
        'tell application "Messages"\n'
        "  set svc to 1st account whose service type = iMessage\n"
        f"  send {q(text)} to participant {q(handle)} of svc\n"
        "end tell"
    )
    return f"Message sent to {recipient}."


def unread_messages_count() -> int | None:
    return None  # Messages exposes no unread count via AppleScript


# ═════════════════════════════════ Notes & Mail ══════════════════════════════
def create_note(title: str, body: str) -> str:
    html = "<h1>" + title + "</h1>" + "".join(f"<p>{line}</p>" for line in body.splitlines())
    osascript(f'tell application "Notes" to make new note at default account with properties {{body:{q(html)}}}')
    return f"Note '{title}' saved."


def mail_unread() -> int | None:
    try:
        running = osascript('application "Mail" is running')
        if running != "true":
            return None
        return int(osascript('tell application "Mail" to get unread count of inbox') or 0)
    except Exception:
        return None


# ═════════════════════════════════ Music ═════════════════════════════════════
def _player() -> str:
    """Prefer whichever player is already running."""
    try:
        if osascript('application "Spotify" is running') == "true":
            return "Spotify"
    except Exception:
        pass
    return "Music"


def media(action: str, app: str | None = None) -> str:
    app = app or _player()
    verbs = {
        "play": "play", "pause": "pause", "toggle": "playpause",
        "next": "next track", "previous": "previous track",
    }
    if action == "now_playing":
        return now_playing(app)
    if action not in verbs:
        raise ValueError(f"Unknown media action {action}")
    osascript(f"tell application {q(app)} to {verbs[action]}")
    return f"{app}: {action}."


def now_playing(app: str | None = None) -> str:
    app = app or _player()
    try:
        out = osascript(
            f'tell application {q(app)}\n'
            '  if player state is playing then\n'
            '    return (name of current track) & " by " & (artist of current track)\n'
            "  else\n    return \"\"\n  end if\nend tell"
        )
    except Exception:
        out = ""
    return out or "Nothing is playing."


def play_music(query: str, app: str | None = None) -> str:
    app = app or _player()
    if app == "Spotify":
        from jarvis.mac.system import open_target

        open_target(f"spotify:search:{query}")
        return f"Searching Spotify for {query}."
    out = osascript(
        'tell application "Music"\n'
        f"  set results to (search library playlist 1 for {q(query)})\n"
        '  if (count of results) is 0 then\n'
        f"    try\n      play playlist {q(query)}\n      return \"playlist\"\n    end try\n"
        '    return ""\n  end if\n'
        "  play item 1 of results\n"
        '  return (name of current track) & " by " & (artist of current track)\n'
        "end tell"
    )
    if not out:
        return f"I couldn't find {query} in your library."
    return f"Playing {query}." if out == "playlist" else f"Playing {out}."
