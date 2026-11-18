"""Timers set through the tools: they run in a worker thread, where asyncio.create_task fails with
"no running event loop". The scheduler hands the work to JARVIS's loop instead, and a timer is only
listed once its countdown really runs (a failed call used to leave a timer that never went off)."""
import asyncio

from jarvis.core.memory import Memory
from jarvis.core.scheduler import Scheduler
from jarvis.tools.context import ToolContext


def setup(tmp_path, cfg):
    import jarvis.tools.builtin  # noqa: F401  (registers the tools)
    from jarvis.tools.registry import registry

    said = []

    async def announce(text, priority="normal"):
        said.append(text)
    mem = Memory(tmp_path / "m.db")
    sched = Scheduler(mem, announce, cfg)
    ctx = ToolContext(cfg=cfg, memory=mem, scheduler=sched)
    return registry, ctx, sched, said, mem


def test_set_list_cancel_through_the_tools(tmp_path, cfg):
    registry, ctx, sched, said, mem = setup(tmp_path, cfg)

    async def go():
        sched.restore()                                            # as JARVIS does at start: binds the loop
        r = await registry.call("set_timer", {"seconds": 3000, "label": "Quarter to midnight"}, ctx)
        assert not r.is_error, r.content                           # was: RuntimeError: no running event loop
        assert "Quarter to midnight" in str(r.content)
        listed = sched.list()
        assert len(listed) == 1 and listed[0]["in_seconds"] > 2990
        assert all(not t.done() for t in sched._tasks.values()) and len(sched._tasks) == 1   # really counting down
        assert len(mem.get("alarms")) == 1
        r = await registry.call("set_alarm", {"time": "2099-01-01T09:00:00", "message": "New year"}, ctx)
        assert not r.is_error, r.content
        r = await registry.call("cancel_timer", {"label": "Quarter to midnight"}, ctx)
        assert not r.is_error and "Cancelled 1 timer" in str(r.content)
        assert [a["message"] for a in sched.list()] == ["New year"] and len(sched._tasks) == 1
        r = await registry.call("cancel_timer", {}, ctx)
        assert "Cancelled 1 timer" in str(r.content) and sched.list() == [] and mem.get("alarms") == []
    asyncio.run(go())


def test_a_timer_set_from_a_worker_thread_goes_off(tmp_path, cfg):
    registry, ctx, sched, said, _ = setup(tmp_path, cfg)

    async def go():
        sched.restore()
        r = await registry.call("set_timer", {"seconds": 1, "label": "tea"}, ctx)
        assert not r.is_error, r.content
        await asyncio.sleep(1.6)
        return said
    said = asyncio.run(go())
    assert said and "tea timer is done" in said[0]


def test_before_jarvis_has_started_it_says_so_and_lists_nothing(tmp_path, cfg):
    registry, ctx, sched, said, mem = setup(tmp_path, cfg)
    import pytest

    with pytest.raises(RuntimeError, match="still starting"):
        sched.add_timer(60, "x")                                   # no loop anywhere yet
    assert sched.list() == [] and not mem.get("alarms")
