"""Phone call agent: outbound calling, conversation loop, API, persistence.

No network anywhere: Twilio's REST API is a fake transport, the LLM/ASR/TTS are
scripted fakes injected through ``agent.deps_factory``, and the media stream is
driven over the gateway app's WebSocket exactly as Twilio would.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import base64
import concurrent.futures
import importlib
import io
import json
import sqlite3
import threading
import time
import uuid
import xml.etree.ElementTree as ET

import numpy as np
import pytest

ACCOUNT = "AC" + "0" * 31 + "1"
CALL = "CA" + "b" * 31 + "9"
TOKEN = "test-auth-token"
BASE = "https://phone.example.com"
FROM = "+15550001111"
STREAM_SID = "MZ" + "2" * 32


# ── Module handles (other suites pop/re-import service modules) ─────────────


class M:
    """Resolved at fixture time so every test binds to the live modules."""


@pytest.fixture()
def mods(monkeypatch):
    import services

    router = importlib.import_module("api.routers.telephony_twilio")
    M.tw = router.provider
    M.session = router.session
    M.config = router.config
    M.gateway = router.gateway
    M.calls = router.calls
    M.agent = importlib.import_module("services.telephony.agent")
    M.router = router
    settings_store = importlib.import_module("services.settings_store")
    monkeypatch.setattr(services, "settings_store", settings_store, raising=False)
    text: dict = {}
    secrets_: dict = {}
    monkeypatch.setattr(settings_store, "get_text", lambda k, d=None: text.get(k, d))
    monkeypatch.setattr(settings_store, "set_text", lambda k, v: text.__setitem__(k, v))
    monkeypatch.setattr(settings_store, "get_secret", lambda n: secrets_.get(n))
    monkeypatch.setattr(
        settings_store, "set_secret", lambda n, v: secrets_.__setitem__(n, v) if v else secrets_.pop(n, None)
    )
    monkeypatch.setattr(settings_store, "list_secret_names", lambda: list(secrets_))
    M.session.reset_state()
    M.calls.reset_state()
    from core import db

    db.init_db()
    with db.db_conn() as conn:
        conn.execute("DELETE FROM call_sessions")
    yield M
    M.session.reset_state()
    M.calls.reset_state()


# ── Fakes ───────────────────────────────────────────────────────────────────


def _tone_ulaw(seconds=0.1, freq=300.0, amp=0.3):
    from services.telephony.audio import float_to_pcm16, lin2ulaw

    t = np.arange(int(8000 * seconds)) / 8000
    return lin2ulaw(float_to_pcm16(amp * np.sin(2 * np.pi * freq * t)))


class Fakes:
    def __init__(self):
        self.replies: list[str] = []
        self.summaries: list[str] = []
        self.heard: list[str] = []
        self.rendered: list[str] = []
        self.llm_calls: list[list[dict]] = []
        self.rest: list[tuple[str, list]] = []
        self.rest_status = 201
        self.rest_body = {"sid": CALL}

    def deps(self):
        async def llm_stream(messages):
            self.llm_calls.append(messages)
            reply = self.replies.pop(0) if self.replies else "SAY: Okay.\nACTION: none"
            for i in range(0, len(reply), 7):  # stream in small deltas
                yield reply[i:i + 7]
                await asyncio.sleep(0)

        async def llm_complete(messages):
            return self.summaries.pop(0) if self.summaries else "{}"

        async def transcribe(pcm, language):
            assert pcm.dtype == np.int16 and pcm.size > 0
            return self.heard.pop(0) if self.heard else ""

        async def render(text, **_kw):
            self.rendered.append(text)
            yield _tone_ulaw(0.1)  # 800 bytes = 5 frames per sentence

        return M.agent.AgentDeps(llm_stream=llm_stream, llm_complete=llm_complete, transcribe=transcribe, render=render)


@pytest.fixture()
def fakes(mods, monkeypatch):
    f = Fakes()
    monkeypatch.setattr(M.agent, "deps_factory", f.deps)

    def _post(url, fields, user, password):
        f.rest.append((url, list(fields)))
        assert user == ACCOUNT and password == TOKEN
        return f.rest_status, json.dumps(f.rest_body).encode()

    monkeypatch.setattr(M.tw, "_http_post_form", _post)
    monkeypatch.setattr(M.calls, "store_save", _notify(M.calls.store_save))
    monkeypatch.setattr(M.calls, "llm_status", lambda: (True, "ready (fake)"))
    monkeypatch.setattr(M.calls, "asr_status", lambda: (True, "ready (fake)"))
    monkeypatch.setattr(
        M.gateway, "state", lambda: {"running": True, "host": "127.0.0.1", "port": 3950, "tunnel_target": "http://127.0.0.1:3950"}
    )
    return f


def _profile(kind="clone", verified=0, name="Palash"):
    from core.db import db_conn

    pid = uuid.uuid4().hex[:12]
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, verified_own_voice, created_at) VALUES (?,?,?,?,?)",
            (pid, name, kind, verified, time.time()),
        )
    return pid


def _configure(enabled=True, **settings):
    M.config.save(M.config.TwilioConfig(enabled=enabled, account_sid=ACCOUNT, public_base_url=BASE, greeting="Hello."))
    M.config.set_auth_token(TOKEN)
    base = M.config.CallSettings(from_number=FROM)
    M.config.save_call_settings(M.config.CallSettings(**{**base.as_dict(), **settings}))


@pytest.fixture()
def api(fakes):
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def gw(mods):
    from fastapi.testclient import TestClient

    return TestClient(M.router.build_gateway_app())


def _signed_post(client, path, query, params):
    sig = M.tw.compute_signature(TOKEN, BASE + path + (f"?{query}" if query else ""), params)
    body = "&".join(f"{k}={v.replace('+', '%2B')}" for k, v in params)
    return client.post(
        path + (f"?{query}" if query else ""),
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded", "X-Twilio-Signature": sig},
    )


def _answer(gw, call_id, call_sid=CALL):
    params = [("AccountSid", ACCOUNT), ("CallSid", call_sid), ("Direction", "outbound-api")]
    resp = _signed_post(gw, M.tw.VOICE_PATH, f"call={call_id}", params)
    assert resp.status_code == 200, resp.text
    return resp


def _stream_params(resp):
    return {p.get("name"): p.get("value") for p in ET.fromstring(resp.text).findall("Connect/Stream/Parameter")}


def _status(gw, call_id, status, call_sid=CALL):
    params = [("AccountSid", ACCOUNT), ("CallSid", call_sid), ("CallStatus", status)]
    return _signed_post(gw, M.tw.STATUS_PATH, f"call={call_id}", params)


def _start(params, call_sid=CALL):
    return {
        "event": "start",
        "streamSid": STREAM_SID,
        "start": {"accountSid": ACCOUNT, "streamSid": STREAM_SID, "callSid": call_sid, "customParameters": params},
    }


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _recv(ws, timeout=15):
    return _pool.submit(ws.receive_json).result(timeout=timeout)


def _read_until_mark(ws):
    """Media frames up to (and including) the next mark: (frames, mark, clears)."""
    frames, clears = [], 0
    while True:
        msg = _recv(ws)
        if msg["event"] == "media":
            frames.append(base64.b64decode(msg["media"]["payload"]))
        elif msg["event"] == "clear":
            clears += 1
        elif msg["event"] == "mark":
            return frames, msg["mark"]["name"], clears


def _echo(ws, name):
    ws.send_json({"event": "mark", "streamSid": STREAM_SID, "mark": {"name": name}})


def _speak(ws, speech_frames=30, silence_frames=40):
    loud = _tone_ulaw(0.02)
    for _ in range(speech_frames):
        ws.send_json({"event": "media", "streamSid": STREAM_SID, "media": {"payload": base64.b64encode(loud).decode()}})
    quiet = base64.b64encode(b"\xff" * 160).decode()
    for _ in range(silence_frames):
        ws.send_json({"event": "media", "streamSid": STREAM_SID, "media": {"payload": quiet}})


#: Notified whenever a call record changes (a turn is added, a call is
#: finalized), so tests wait on the event itself instead of sleeping.
_changed = threading.Condition()
_generation = [0]


def _notify(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            with _changed:
                _generation[0] += 1
                _changed.notify_all()

    return wrapper


def _wait_record(api, call_id, pred, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        with _changed:
            seen = _generation[0]
        rec = api.get(f"/calls/{call_id}").json()  # never under the lock
        if pred(rec):
            return rec
        with _changed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"timed out; last record: {rec}")
            _changed.wait_for(lambda: _generation[0] != seen, remaining)


def _place(api, profile, **extra):
    body = {"to": "+1 (415) 555-0123", "brief": "Book a table for 2 at 8pm Friday under Palash.", "profile_id": profile}
    return api.post("/calls", json={**body, **extra})


# ── Parser, guard, VAD (units) ──────────────────────────────────────────────


def test_reply_parser_streams_say_text_and_reads_the_action():
    p = M_agent().ReplyParser()
    out = ""
    for chunk in ["SA", "Y: Hello there. We'd like", " a table. AC", "TION: end_call\nOUTC", "OME: booked"]:
        out += p.feed(chunk)
    assert "AC" not in out  # the partial tag was held back, never spoken
    out += p.finish()
    assert out.strip() == "Hello there. We'd like a table."
    assert (p.action, p.outcome) == ("end_call", "booked")


def test_reply_parser_json_plain_and_reasoning_blocks():
    agent = M_agent()
    p = agent.ReplyParser()
    assert p.feed('{"say": "Sure.", "action": "escalate", "outcome": "needs_you"}') == ""
    assert p.finish() == "Sure." and (p.action, p.outcome) == ("escalate", "needs_you")
    p = agent.ReplyParser()
    assert p.feed("<think>plan the reply") == ""
    assert p.feed("</think>Just words.") + p.finish() == "Just words."
    assert p.action == "none"


def test_guard_blocks_card_and_unknown_id_numbers_but_allows_brief_numbers():
    agent = M_agent()
    brief = "Callback number 415 555 0123 4."
    assert agent.guard_sensitive("My card is 4111 1111 1111 1111.", brief) == agent.REFUSAL
    assert agent.guard_sensitive("The ID is 123-45-6789.", brief) == agent.REFUSAL
    assert agent.guard_sensitive("Call back on 415 555 0123 4.", brief) == "Call back on 415 555 0123 4."
    assert agent.guard_sensitive("A table for 2 at 8pm.", brief) == "A table for 2 at 8pm."
    styled = "Call Palash back on +1 (415) 555-0123."
    assert agent.guard_sensitive("His number is 415-555-0123.", styled) == "His number is 415-555-0123."
    assert agent.guard_sensitive("It's 1 415 555 0123.", styled) == "It's 1 415 555 0123."
    assert agent.guard_sensitive("It's 415 555 0199.", styled) == agent.REFUSAL
    for spaced in ("4111,1111,1111,1111", "4111–1111–1111–1111", "123,456,789"):
        assert agent.guard_sensitive(f"It is {spaced}.", brief) == agent.REFUSAL, spaced
    # A card number is refused even when the user put it in the brief.
    assert agent.guard_sensitive("It is 4111111111111111.", "card 4111111111111111") == agent.REFUSAL


def test_endpointer_detects_onset_barge_and_utterance_end():
    agent = M_agent()
    from services.telephony.audio import ulaw2lin

    vad = agent.Endpointer()
    loud = ulaw2lin(_tone_ulaw(0.02))
    quiet = np.zeros(160, dtype=np.int16)
    events = []
    for _ in range(20):
        events += vad.push(quiet)
    for _ in range(30):
        events += vad.push(loud)
    for _ in range(40):
        events += vad.push(quiet)
    kinds = [e if isinstance(e, str) else e[0] for e in events]
    assert list(dict.fromkeys(kinds)) == ["start", "barge", "end"]
    fresh = agent.Endpointer()
    assert "barge" not in [e for _ in range(9) for e in fresh.push(loud)]  # < 200 ms is not a barge-in
    assert fresh.push(loud) == ["barge"]
    assert events[-1][1].dtype == np.int16 and events[-1][1].size >= 30 * 160
    # A click is not an utterance.
    events = []
    for _ in range(4):
        events += vad.push(loud)
    for _ in range(40):
        events += vad.push(quiet)
    assert [e for e in events if isinstance(e, tuple)] == []


def M_agent():
    return importlib.import_module("services.telephony.agent")


def test_only_an_affirmative_recording_notice_allows_recording(mods):
    announces = M.calls.disclosure_announces_recording
    for text in ("This call is recorded.", "This call may be recorded for quality.",
                 "We're recording this call.", "Calls are being recorded"):
        assert announces(text), text
    for text in ("This call is not recorded.", "This call won't be recorded.", "We never record calls.",
                 "No recording.", "Hi, this is an AI assistant.", "I'd like to record a message"):
        assert not announces(text), text


def test_upsample_doubles_the_rate():
    pcm = (np.sin(np.arange(800) / 5) * 8000).astype(np.int16)
    out = M_agent().upsample_to_16k(pcm)
    assert out.dtype == np.int16 and abs(out.size - 1600) <= 2


def test_openai_backend_streams_deltas():
    from services import llm_backend

    class _Delta:
        def __init__(self, c):
            self.delta = type("D", (), {"content": c})()

    class _Stream:
        closed = False

        def __iter__(self):
            yield type("C", (), {"choices": [_Delta("SAY: Hi")]})()
            yield type("C", (), {"choices": []})()
            yield type("C", (), {"choices": [_Delta(" there.")]})()

        def close(self):
            _Stream.closed = True

    seen = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return _Stream()

    backend = llm_backend.OpenAICompatBackend()
    backend._client = _Client()
    backend._provider = type("P", (), {})()
    import services.llm_providers as lp

    orig = lp.resolve_model
    lp.resolve_model = lambda p: "m"
    try:
        assert list(backend.chat_messages_stream(messages=[{"role": "user", "content": "x"}])) == ["SAY: Hi", " there."]
    finally:
        lp.resolve_model = orig
    assert seen["stream"] is True and _Stream.closed


# ── Settings, readiness, validation, the own-voice gate ─────────────────────


def test_call_settings_validate_and_disclosure_may_be_emptied(api):
    assert api.get("/calls/settings").json()["disclosure_template"] == M.config.DEFAULT_DISCLOSURE
    bad = api.put("/calls/settings", json={"from_number": "555-0100"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_from_number"
    assert api.put("/calls/settings", json={"inbound_mode": "robot"}).status_code == 400
    assert api.put("/calls/settings", json={"max_concurrent": 3}).status_code == 422
    ok = api.put(
        "/calls/settings",
        json={"from_number": "+1 555 000 1111", "disclosure_template": "", "inbound_mode": "agent", "max_concurrent": 2},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() | {} == {
        "from_number": FROM, "disclosure_template": "", "user_name": "", "inbound_mode": "agent",
        "inbound_brief": "", "max_concurrent": 2, "record_calls": False,
    }
    assert api.get("/calls/settings").json()["disclosure_template"] == ""


def test_readiness_is_a_guided_checklist(api, monkeypatch):
    monkeypatch.setattr(M.gateway, "state", lambda: {"running": False})
    monkeypatch.setattr(M.calls, "llm_status", lambda: (False, "Set up an LLM"))
    items = api.get("/calls/readiness").json()
    assert [i["id"] for i in items] == ["credentials", "tunnel", "number", "llm", "asr", "voice"]
    assert all(set(i) == {"id", "ok", "detail"} for i in items)
    assert not any(i["ok"] for i in items if i["id"] in ("credentials", "tunnel", "number", "llm"))
    _configure()
    _profile(verified=1)
    monkeypatch.setattr(M.gateway, "state", lambda: {"running": True, "tunnel_target": "http://127.0.0.1:3950"})
    monkeypatch.setattr(M.calls, "llm_status", lambda: (True, "ready"))
    items = {i["id"]: i for i in api.get("/calls/readiness").json()}
    assert all(items[k]["ok"] for k in ("credentials", "tunnel", "number", "llm", "asr", "voice")), items


def test_outbound_calls_need_the_users_own_or_a_designed_voice(api, fakes):
    _configure()
    cloned = _profile(kind="clone", verified=0)
    resp = _place(api, cloned)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "voice_not_allowed"
    assert "verified" in resp.json()["detail"]["message"]
    assert fakes.rest == []  # nothing was dialed
    assert _place(api, "missing-profile").status_code == 404
    designed = _profile(kind="design", verified=0)
    assert _place(api, designed).status_code == 201
    M.calls.reset_state()
    assert _place(api, _profile(kind="clone", verified=1)).status_code == 201


def test_numbers_must_be_e164_and_calls_need_a_ready_setup(api, fakes, monkeypatch):
    _configure()
    pid = _profile(verified=1)
    for bad in ("5550123", "+0123456789", "call me", "+1415555012345678"):
        resp = _place(api, pid, to=bad)
        assert resp.status_code == 400 and resp.json()["detail"]["code"] == "invalid_number", bad
    assert _place(api, pid, to=FROM).status_code == 400
    assert _place(api, pid, brief="  ").json()["detail"]["code"] == "missing_brief"
    monkeypatch.setattr(M.gateway, "state", lambda: {"running": False})
    assert _place(api, pid).json()["detail"]["code"] == "listener_not_running"
    _configure(enabled=False)
    assert _place(api, pid).json()["detail"]["code"] == "integration_disabled"
    monkeypatch.setattr(M.gateway, "state", lambda: {"running": True})
    _configure(from_number="")
    assert _place(api, pid).json()["detail"]["code"] == "missing_from_number"
    assert fakes.rest == []


def test_placing_a_call_uses_twilio_rest_with_signed_callbacks(api, fakes):
    _configure()
    resp = _place(api, _profile(verified=1), max_minutes=99)
    assert resp.status_code == 201, resp.text
    call = resp.json()["call"]
    assert call["to_masked"] == "+1••••••0123" and "4155550123" not in resp.text
    assert call["status"] == "initiated" and call["direction"] == "outbound"
    assert call["disclosure"] == "Hi, this is Palash's AI assistant calling on their behalf."
    url, fields = fakes.rest[0]
    assert url == f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT}/Calls.json"
    form = dict(fields)
    assert form["To"] == "+14155550123" and form["From"] == FROM
    assert form["Url"] == f"{BASE}{M.tw.VOICE_PATH}?call={call['id']}"
    assert form["StatusCallback"] == f"{BASE}{M.tw.STATUS_PATH}?call={call['id']}"
    assert [v for k, v in fields if k == "StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]
    assert form["TimeLimit"] == str(30 * 60 + 30)  # max_minutes capped at 30
    # One active outbound call at a time by default; no queueing.
    busy = _place(api, _profile(verified=1))
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "busy"
    assert len(fakes.rest) == 1


def test_twilio_errors_are_reported_and_the_call_is_closed(api, fakes):
    _configure()
    fakes.rest_status, fakes.rest_body = 400, {"code": 21219, "message": "The number is unverified."}
    resp = _place(api, _profile(verified=1))
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail == {"code": "twilio_error", "message": "The number is unverified.", "twilio_code": 21219}
    calls = api.get("/calls").json()["calls"]
    assert calls[0]["status"] == "failed" and calls[0]["outcome"] == "failed"


def test_status_callbacks_are_signed_and_close_unanswered_calls(api, gw, fakes):
    _configure()
    call_id = _place(api, _profile(verified=1)).json()["call"]["id"]
    forged = gw.post(
        M.tw.STATUS_PATH + f"?call={call_id}",
        content=f"AccountSid={ACCOUNT}&CallSid={CALL}&CallStatus=busy",
        headers={"content-type": "application/x-www-form-urlencoded", "X-Twilio-Signature": "nope"},
    )
    assert forged.status_code == 403
    assert _status(gw, call_id, "ringing").status_code == 204
    assert api.get(f"/calls/{call_id}").json()["status"] == "ringing"
    assert _status(gw, call_id, "busy").status_code == 204
    rec = api.get(f"/calls/{call_id}").json()
    assert (rec["status"], rec["outcome"], rec["summary"]) == ("busy", "not_done", "The line was busy.")
    assert [s["status"] for s in rec["timeline"]] == ["queued", "initiated", "ringing", "busy"]
    # Answering a finished call just hangs up.
    assert ET.fromstring(_answer(gw, call_id).text).find("Hangup") is not None


# ── Full calls over the media stream ────────────────────────────────────────


def test_full_outbound_call_disclosure_turns_end_call_and_summary(api, gw, fakes):
    _configure()
    fakes.replies = [
        "SAY: I'd like to book a table for two at 8pm on Friday.\nACTION: none",
        "SAY: It's under Palash. Thank you, goodbye!\nACTION: end_call\nOUTCOME: booked",
    ]
    fakes.heard = ["Sure, what name is it under?"]
    fakes.summaries = ['{"outcome": "done", "summary": "Booked a table for two at 8pm Friday under Palash."}']
    call_id = _place(api, _profile(verified=1)).json()["call"]["id"]
    assert _status(gw, call_id, "in-progress").status_code == 204
    params = _stream_params(_answer(gw, call_id))
    assert params["call"] == call_id and len(params["token"]) >= 32

    with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
        ws.send_json(_start(params))
        frames, mark1, _ = _read_until_mark(ws)
        assert frames and all(len(f) == 160 for f in frames)
        assert fakes.rendered[0] == "Hi, this is Palash's AI assistant calling on their behalf."
        frames2, mark2, _ = _read_until_mark(ws)
        assert fakes.rendered[1] == "I'd like to book a table for two at 8pm on Friday."
        assert "Book a table for 2 at 8pm Friday under Palash." in fakes.llm_calls[0][0]["content"]
        _echo(ws, mark1)
        _echo(ws, mark2)
        _speak(ws)
        frames3, mark3, _ = _read_until_mark(ws)
        assert frames3
        assert fakes.llm_calls[1][-1] == {"role": "user", "content": "Sure, what name is it under?"}
        _, end_mark, _ = _read_until_mark(ws)
        assert end_mark == M.session.END_MARK  # end_call: the agent hangs up after speaking
        _echo(ws, mark3)
        _echo(ws, end_mark)

    rec = _wait_record(api, call_id, lambda r: r["status"] == "completed" and r["ended_at"])
    assert rec["outcome"] == "booked"  # the agent's own outcome wins over the summary's
    assert rec["summary"] == "Booked a table for two at 8pm Friday under Palash."
    assert [(t["speaker"], t["text"]) for t in rec["transcript"]] == [
        ("agent", "Hi, this is Palash's AI assistant calling on their behalf."),
        ("agent", "I'd like to book a table for two at 8pm on Friday."),
        ("caller", "Sure, what name is it under?"),
        ("agent", "It's under Palash. Thank you, goodbye!"),
    ]
    assert rec["transcript"][0]["source"] == "disclosure"
    assert rec["duration_s"] is not None and rec["started_at"]
    # Persisted: gone from memory, still served from the database.
    assert M.calls.get_live(call_id) is None
    assert api.get("/calls").json()["calls"][0]["id"] == call_id
    events = [json.loads(line[6:]) for line in api.get(f"/calls/{call_id}/events").text.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["status", "outcome", "ended"]
    assert events[1]["outcome"] == "booked"


def test_barge_in_clears_playback_and_hangup_ends_the_call(api, gw, fakes):
    _configure()
    fakes.replies = [
        "SAY: I'd like to book a table. It is for two people. At eight in the evening.\nACTION: none",
        "SAY: Of course, go ahead.\nACTION: none",
    ]
    fakes.heard = ["Sorry, one moment please."]
    call_id = _place(api, _profile(verified=1)).json()["call"]["id"]
    params = _stream_params(_answer(gw, call_id))
    with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
        ws.send_json(_start(params))
        _, disclosure_mark, _ = _read_until_mark(ws)
        # Talking over the disclosure never interrupts it.
        _speak(ws, speech_frames=12, silence_frames=0)
        _, opener_mark, clears = _read_until_mark(ws)
        assert clears == 0
        _echo(ws, disclosure_mark)
        # The opener is still "playing" (mark not echoed): the caller barges in.
        _speak(ws, speech_frames=30, silence_frames=40)
        msg = _recv(ws)
        while msg["event"] != "clear":
            msg = _recv(ws)
        _, reply_mark, _ = _read_until_mark(ws)
        rec = _wait_record(api, call_id, lambda r: any(t["speaker"] == "caller" for t in r["transcript"]))
        assert any(t.get("interrupted") for t in rec["transcript"] if t["speaker"] == "agent")
        assert api.post(f"/calls/{call_id}/hangup").json() == {"ok": True}
        msg = _recv(ws)
        while not (msg["event"] == "mark" and msg["mark"]["name"] == M.session.END_MARK):
            msg = _recv(ws)
        _echo(ws, M.session.END_MARK)
    rec = _wait_record(api, call_id, lambda r: r["ended_at"] is not None and r["status"] == "completed")
    assert rec["outcome"] == "not_done"


def test_takeover_pauses_the_llm_and_say_speaks_exactly_the_text(api, gw, fakes):
    _configure()
    fakes.replies = ["SAY: Hello, I'm calling about a booking.\nACTION: none"]
    fakes.heard = ["Can I ask who is calling?"]
    call_id = _place(api, _profile(verified=1), disclosure="").json()["call"]["id"]
    params = _stream_params(_answer(gw, call_id))
    with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
        ws.send_json(_start(params))
        _, mark, _ = _read_until_mark(ws)
        assert fakes.rendered == ["Hello, I'm calling about a booking."]  # empty disclosure: none spoken
        _echo(ws, mark)
        assert api.post(f"/calls/{call_id}/takeover", json={"enabled": True}).json()["takeover"] is True
        _speak(ws)
        _wait_record(api, call_id, lambda r: any(t["speaker"] == "caller" for t in r["transcript"]))
        assert len(fakes.llm_calls) == 1  # paused: no LLM turn for the caller's question
        assert api.post(f"/calls/{call_id}/say", json={"text": "This is Palash's assistant."}).status_code == 200
        _, mark2, _ = _read_until_mark(ws)
        assert fakes.rendered[-1] == "This is Palash's assistant."
        _echo(ws, mark2)
        api.post(f"/calls/{call_id}/hangup")
        msg = _recv(ws)
        while not (msg["event"] == "mark" and msg["mark"]["name"] == M.session.END_MARK):
            msg = _recv(ws)
        _echo(ws, M.session.END_MARK)
    rec = _wait_record(api, call_id, lambda r: r["ended_at"] is not None and r["status"] == "completed")
    manual = [t for t in rec["transcript"] if t.get("source") == "manual"]
    assert [t["text"] for t in manual] == ["This is Palash's assistant."]
    assert len(fakes.llm_calls) == 1
    assert api.post(f"/calls/{call_id}/say", json={"text": "late"}).status_code == 409


def test_a_crashed_stream_still_finalizes_and_frees_the_slot(api, gw, fakes, monkeypatch):
    _configure()
    call_id = _place(api, _profile(verified=1)).json()["call"]["id"]
    params = _stream_params(_answer(gw, call_id))

    boomed = threading.Event()

    async def _boom(self, name):
        boomed.set()
        raise RuntimeError("mark handler failed")

    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(M.agent.CallAgent, "on_mark", _boom)
    with pytest.raises((WebSocketDisconnect, RuntimeError)):
        with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
            ws.send_json(_start(params))
            _, mark, _ = _read_until_mark(ws)
            _echo(ws, mark)
            while True:
                _recv(ws)
    assert boomed.is_set()
    rec = _wait_record(api, call_id, lambda r: r["ended_at"] is not None)
    assert rec["status"] in ("completed", "failed") and M.calls.get_live(call_id) is None
    assert _place(api, _profile(verified=1)).status_code == 201  # the slot was released


def test_stream_token_is_bound_to_the_call_session(api, gw, fakes):
    from starlette.websockets import WebSocketDisconnect

    _configure()
    call_id = _place(api, _profile(verified=1)).json()["call"]["id"]
    params = _stream_params(_answer(gw, call_id))
    with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
        ws.send_json(_start({**params, "call": "someone-else"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            _recv(ws)
    assert closed.value.code == 1008
    assert fakes.rendered == []


def test_recording_needs_the_setting_and_a_disclosure_that_mentions_it(api, gw, fakes):
    import soundfile as sf

    _configure(record_calls=True)
    fakes.replies = ["SAY: Goodbye.\nACTION: end_call\nOUTCOME: done"]
    pid = _profile(verified=1)
    silent = _place(api, pid, disclosure="Hi, this is an AI assistant.").json()["call"]
    assert silent["recording"] is False
    M.calls.hangup(M.calls.get_live(silent["id"]))
    call_id = _place(api, pid, disclosure="Hi, this AI assistant's call is recorded.").json()["call"]["id"]
    params = _stream_params(_answer(gw, call_id))
    with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
        ws.send_json(_start(params))
        msg = _recv(ws)
        while not (msg["event"] == "mark" and msg["mark"]["name"] == M.session.END_MARK):
            msg = _recv(ws)
        _echo(ws, M.session.END_MARK)
    _wait_record(api, call_id, lambda r: r["recording"])
    resp = api.get(f"/calls/{call_id}/recording")
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav"
    data, sr = sf.read(io.BytesIO(resp.content))
    assert sr == 8000 and data.shape[1] == 2 and np.abs(data[:, 1]).max() > 0.1
    assert api.get(f"/calls/{silent['id']}/recording").status_code == 404
    assert api.delete(f"/calls/{call_id}").json() == {"deleted": call_id}
    assert api.get(f"/calls/{call_id}").status_code == 404


def test_inbound_calls_are_answered_by_the_agent_in_agent_mode(api, gw, fakes):
    _configure(inbound_mode="agent", inbound_brief="Take a message for Palash.", user_name="Palash")
    fakes.replies = ["SAY: How can I help?\nACTION: none"]
    params = [("AccountSid", ACCOUNT), ("CallSid", CALL), ("From", "+15557778888"), ("Direction", "inbound")]
    resp = _signed_post(gw, M.tw.VOICE_PATH, "", params)
    stream = _stream_params(resp)
    assert stream.get("call")
    with gw.websocket_connect(M.tw.STREAM_PATH) as ws:
        ws.send_json(_start(stream))
        _read_until_mark(ws)
        _read_until_mark(ws)
        ws.send_json({"event": "stop", "streamSid": STREAM_SID})
    rec = _wait_record(api, stream["call"], lambda r: r["ended_at"] is not None)
    assert rec["direction"] == "inbound" and rec["to_masked"] == "+1••••••8888"
    assert fakes.rendered[:2] == ["Hi, this is Palash's AI assistant calling on their behalf.", "How can I help?"]
    assert "Take a message for Palash." in fakes.llm_calls[0][0]["content"]


def test_max_concurrent_limits_agent_calls_in_both_directions(api, gw, fakes):
    _configure(inbound_mode="agent")
    assert _place(api, _profile(verified=1)).status_code == 201  # holds the only slot
    params = [("AccountSid", ACCOUNT), ("CallSid", "CA" + "c" * 32), ("From", "+15557778888")]
    resp = _signed_post(gw, M.tw.VOICE_PATH, "", params)
    assert ET.fromstring(resp.text).find("Reject") is not None


def test_greeting_mode_is_unchanged_for_inbound_calls(gw, fakes):
    _configure()
    params = [("AccountSid", ACCOUNT), ("CallSid", CALL), ("From", "+15557778888")]
    stream = _stream_params(_signed_post(gw, M.tw.VOICE_PATH, "", params))
    assert set(stream) == {"token"}


def test_live_events_reach_subscribers_on_other_loops(mods):
    session = M.calls.CallSession(
        id="x" * 32, direction="outbound", remote_number="+14155550123", from_number=FROM, brief="b", profile_id="p"
    )
    got = []

    async def listen():
        queue = session.subscribe()
        ready.set()
        while True:
            event = await asyncio.wait_for(queue.get(), 5)
            got.append(event)
            if event["type"] == "ended":
                return

    ready = threading.Event()
    thread = threading.Thread(target=lambda: asyncio.run(listen()))
    thread.start()
    assert ready.wait(5)
    session.set_status("ringing")
    session.set_agent_state("thinking")
    session.add_turn("caller", "Hello?")
    M.calls.finish_unconnected(session, "no_answer")
    thread.join(5)
    assert [e["type"] for e in got] == ["status", "agent_state", "transcript", "status", "outcome", "ended"]
    assert got[2] == {"type": "transcript", "speaker": "caller", "text": "Hello?", "final": True, "t": got[2]["t"]}


def test_orphaned_rows_are_closed_after_a_restart(api, fakes):
    _configure()
    call_id = _place(api, _profile(verified=1)).json()["call"]["id"]
    M.calls.reset_state()  # the process restarted mid-call
    rec = api.get(f"/calls/{call_id}").json()
    assert rec["status"] == "failed" and rec["outcome"] == "failed"


def test_calls_api_is_local_only(mods):
    from fastapi.testclient import TestClient
    from main import app

    remote = TestClient(app, client=("203.0.113.9", 50000))
    assert remote.post("/calls", json={}).status_code in (401, 403)
    gateway_paths = {getattr(r, "path", "") for r in M.router.build_gateway_app().routes}
    assert not any(p.startswith("/calls") for p in gateway_paths)


# ── Migration ───────────────────────────────────────────────────────────────


def _alembic_upgrade(db_path):
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def test_migration_adds_call_sessions_and_matches_the_base_schema(tmp_path, monkeypatch):
    from core.db import _BASE_SCHEMA

    db_path = tmp_path / "calls.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('0011_remote_attempt_deadlines')")
        conn.execute("CREATE TABLE voice_profiles (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO voice_profiles VALUES ('keep', 'Mine')")
    monkeypatch.setenv("OMNIVOICE_DB_PATH", str(db_path))
    _alembic_upgrade(str(db_path))
    _alembic_upgrade(str(db_path))  # idempotent
    canon = sqlite3.connect(":memory:")
    canon.executescript(_BASE_SCHEMA)
    with sqlite3.connect(db_path) as conn:
        migrated = [(r[1], r[2].upper(), r[3], r[5]) for r in conn.execute("PRAGMA table_info(call_sessions)")]
        assert conn.execute("SELECT * FROM voice_profiles").fetchall() == [("keep", "Mine")]
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012_call_sessions"
    fresh = [(r[1], r[2].upper(), r[3], r[5]) for r in canon.execute("PRAGMA table_info(call_sessions)")]
    norm = lambda cols: [(n, {"FLOAT": "REAL"}.get(t, t), nn, pk) for n, t, nn, pk in cols]  # noqa: E731
    assert norm(migrated) == norm(fresh)
