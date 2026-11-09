"""SSE parsing and the reconnecting event-stream runner (desktop-app-guide §8)."""
import asyncio
import random

import pytest

from services.cloud_sync.client import CloudOff, SyncApiError
from services.cloud_sync.events import EventStream, SseParser, backoff_delay


def parse(lines):
    parser = SseParser()
    out = []
    for line in lines:
        event = parser.feed(line)
        if event:
            out.append(event)
    return out


def test_parses_head_bye_revoked_and_comments():
    events = parse([
        "event: head", "id: 61", 'data: {"head":61,"collections":["settings"]}', "",
        ": ping", "",
        "event: bye", 'data: {"reason":"token_expired"}', "",
        "event: revoked", 'data: {"reason":"device_revoked"}', "",
    ])
    assert [(e.event, e.id, e.data) for e in events] == [
        ("head", "61", {"head": 61, "collections": ["settings"]}),
        ("ping", None, {}),
        ("bye", None, {"reason": "token_expired"}),
        ("revoked", None, {"reason": "device_revoked"}),
    ]


def test_multi_line_data_and_garbage_are_safe():
    events = parse(["event: head", 'data: {"head":1,', 'data: "collections":[]}', "", "event: head", "data: {oops", ""])
    assert events[0].data == {"head": 1, "collections": []}
    assert events[1].data == {}


def test_backoff_doubles_to_sixty_with_twenty_percent_jitter():
    rng = random.Random(1)
    for attempt, nominal in enumerate([1, 2, 4, 8, 16, 32, 60, 60]):
        delay = backoff_delay(attempt, rng)
        assert nominal * 0.8 <= delay <= nominal * 1.2


class Script:
    """Stands in for client.events(): each connect plays the next scripted session."""

    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.last_ids = []

    def open(self, last_event_id):
        self.last_ids.append(last_event_id)
        session = self.sessions.pop(0) if self.sessions else CloudOff()

        async def lines():
            if isinstance(session, BaseException):
                raise session
            for line in session:
                if line == "<stall>":
                    await asyncio.sleep(1)
                yield line

        return lines()


def make(script, **over):
    heads, revoked, polls, sleeps = [], [], [], []

    async def on_head(collections):
        heads.append(collections)

    async def poll():
        polls.append(1)

    async def sleep(seconds):
        sleeps.append(seconds)

    stream = EventStream(script.open, on_head=on_head, on_revoked=revoked.append, poll=poll,
                         sleep=sleep, stall_seconds=over.get("stall", 5), rng=random.Random(0))
    return stream, heads, revoked, polls, sleeps


def test_head_runs_a_pull_and_reconnects_with_the_last_event_id():
    script = Script([
        ["event: head", "id: 7", 'data: {"head":7,"collections":["settings"]}', ""],
        ["event: bye", 'data: {"reason":"token_expired"}', ""],
    ])
    stream, heads, _, _, sleeps = make(script)
    asyncio.run(stream.run())
    assert heads == [["settings"]]
    assert script.last_ids[:3] == [None, "7", "7"]
    # A close after data backs off; token_expired reconnects at once.
    assert sleeps[0] > 0 and len(sleeps) == 1


def test_a_stalled_stream_is_closed_and_reopened():
    script = Script([["<stall>"], ["event: revoked", 'data: {"reason":"device_revoked"}', ""]])
    stream, _, revoked, _, _ = make(script, stall=0.05)
    asyncio.run(stream.run())
    assert revoked == ["device_revoked"]
    assert len(script.last_ids) == 2


def test_failures_back_off_and_poll_meanwhile():
    script = Script([SyncApiError(503, "sync_unavailable"), OSError("reset"), ["event: revoked", "data: {}", ""]])
    stream, _, revoked, polls, sleeps = make(script)
    asyncio.run(stream.run())
    assert len(polls) == 2
    assert 0.8 <= sleeps[0] <= 1.2 and 1.6 <= sleeps[1] <= 2.4
    assert revoked == ["revoked"]


def test_too_many_streams_waits_sixty_seconds():
    script = Script([["event: bye", 'data: {"reason":"too_many_streams"}', ""]])
    stream, _, _, _, sleeps = make(script)
    asyncio.run(stream.run())
    assert sleeps == [60]


def test_switched_off_or_fatal_errors_stop_without_reconnecting():
    script = Script([CloudOff()])
    stream, *_ = make(script)
    asyncio.run(stream.run())
    assert len(script.last_ids) == 1

    script = Script([SyncApiError(403, "device_revoked")])
    stream, _, revoked, _, _ = make(script)
    asyncio.run(stream.run())
    assert revoked == ["device_revoked"] and len(script.last_ids) == 1


def test_a_stalled_stream_or_too_many_streams_keeps_the_fallback_poll_going():
    script = Script([["<stall>"], ["event: bye", 'data: {"reason":"too_many_streams"}', ""]])
    stream, _, _, polls, sleeps = make(script, stall=0.05)
    asyncio.run(stream.run())
    assert len(polls) == 2  # once after the stall, once before waiting out too_many_streams
    assert sleeps == [60]
