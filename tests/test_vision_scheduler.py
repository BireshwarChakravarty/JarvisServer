import asyncio
import time
from types import SimpleNamespace as NS

from jarvis.core.scheduler import Scheduler, _describe_duration
from jarvis.vision.presence import Presence


def face(name):
    return NS(name=name)


def test_presence_arrival_departure_and_sentry():
    events = []
    p = Presence("Tony", away_after=60, gone_after=5,
                 on_arrive=lambda away: events.append(("arrive", round(away))),
                 on_leave=lambda: events.append(("leave",)),
                 on_stranger=lambda: events.append(("stranger",)))
    t = time.time()
    p.left_at = t - 600
    p.update([face("Tony")], t)
    assert not p.present  # needs two consecutive sightings
    p.update([face("Tony")], t + 0.5)
    assert p.present and events[0][0] == "arrive"
    p.update([], t + 3)
    assert p.present  # brief look away
    p.update([], t + 10)
    assert not p.present and events[-1] == ("leave",)
    for i in range(6):
        p.update([face(None)], t + 20 + i)
    assert events[-1] == ("stranger",)


def test_leaving_is_about_you_not_any_face():
    events = []
    p = Presence("Tony", gone_after=5, on_arrive=lambda a: events.append(("arrive", round(a))),
                 on_leave=lambda: events.append(("leave",)))
    t = time.time()
    p.update([face("Tony")], t); p.update([face("Tony")], t + 0.5)
    for i in range(1, 12):                      # you walk off; a poster/visitor stays in view
        p.update([face(None)], t + 0.5 + i)
    assert ("leave",) in events and not p.present
    # back after a two-minute coffee: reported at once (the caller wakes a locked screen)
    p.update([face("Tony")], t + 120); p.update([face("Tony")], t + 120.5)
    assert events[-1][0] == "arrive" and 100 < events[-1][1] < 130


def test_presence_without_enrolment_treats_any_face_as_owner():
    arrived = []
    p = Presence("Tony", away_after=0, on_arrive=lambda a: arrived.append(a))
    p.recognition = False
    p.update([face(None)])
    p.update([face(None)])
    assert p.present and arrived


def test_timer_fires_announcement(jarvis_home):
    said = []

    async def announce(text, priority):
        said.append((text, priority))

    async def run():
        s = Scheduler(None, announce)
        s.add_timer(0.05, "tea")
        assert s.list()[0]["message"] == "tea"
        await asyncio.sleep(0.2)
        return s

    s = asyncio.run(run())
    assert said and "tea timer is done" in said[0][0] and said[0][1] == "high"
    assert s.list() == []
    assert _describe_duration(90) == "1 minute 30 second" and _describe_duration(3600) == "1 hour"
