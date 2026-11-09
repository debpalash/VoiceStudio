"""Operator hangup survives media-handler cancellation without hiding transport failures."""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture()
def call_session(monkeypatch):
    from services.telephony import calls, agent

    async def summarize(*_args):
        return None, ""

    monkeypatch.setattr(calls, "_summarize", summarize)
    monkeypatch.setattr(calls, "store_save", lambda _session: None)
    monkeypatch.setattr(agent, "deps_factory", lambda: None)
    session = calls.CallSession(
        id="finalization-test", direction="outbound", remote_number="+15550000000",
        from_number="+15550000001", brief="A task", profile_id="voice",
        status="in_progress", stream_connected=True,
    )
    return calls, session


@pytest.mark.parametrize("stream_outcome,expected", [
    ("error", "not_done"),
    ("completed", "not_done"),
    ("synthesis_failed", "failed"),
    ("engine_unavailable", "failed"),
])
def test_accepted_operator_hangup_preserves_intent_and_explicit_errors(call_session, stream_outcome, expected):
    calls, session = call_session
    session.bind_agent(SimpleNamespace(hangup=lambda: True))
    calls.hangup(session)
    # A disconnected WebSocket can cancel its handler before it returns its
    # terminal outcome, leaving the gateway's generic "error" fallback.
    asyncio.run(calls.finish(session, stream_outcome))
    assert session.outcome == expected
    assert session.error == (stream_outcome if expected == "failed" else "")
    assert session.finalized


@pytest.mark.parametrize("rejected_hangup", [False, True])
def test_generic_stream_error_without_accepted_hangup_still_fails(call_session, rejected_hangup):
    calls, session = call_session
    if rejected_hangup:
        session.bind_agent(SimpleNamespace(hangup=lambda: False))
        calls.hangup(session)
    asyncio.run(calls.finish(session, "error"))
    assert session.outcome == "failed"
    assert session.error == "error"
