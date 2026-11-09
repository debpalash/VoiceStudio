"""A blocked event loop must leave a thread dump naming the blocker."""
import asyncio
import time

from core.loop_stall_monitor import start_loop_stall_monitor


def test_dumps_once_when_loop_is_blocked():
    dumps = []

    async def scenario():
        stop = start_loop_stall_monitor(threshold=0.3, poll=0.05, dump=lambda: dumps.append(1))
        await asyncio.sleep(0.2)
        time.sleep(0.9)  # the synchronous call that blocks /health
        await asyncio.sleep(0.2)
        stop.set()

    asyncio.run(scenario())
    assert len(dumps) == 1


def test_silent_when_loop_is_healthy():
    dumps = []

    async def scenario():
        stop = start_loop_stall_monitor(threshold=0.3, poll=0.05, dump=lambda: dumps.append(1))
        await asyncio.sleep(0.6)
        stop.set()

    asyncio.run(scenario())
    assert dumps == []


def test_disabled_by_zero(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LOOP_STALL_S", "0")
    assert start_loop_stall_monitor(loop=object()) is None
