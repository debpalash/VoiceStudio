"""Twilio phone-call integration: codec, signatures, TwiML, media streams, API.

The call path runs the real streaming-TTS pipeline with a registered fake
engine; only the settings store is in-memory and the watermark is identity.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import base64
import importlib
import io
import socket
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import torch

from services.telephony import audio, config, gateway, session
from services.telephony import twilio as tw

ACCOUNT = "AC" + "0" * 31 + "1"
CALL = "CA" + "a" * 31 + "7"
TOKEN = "test-auth-token"
BASE = "https://phone.example.com"


# ── μ-law codec ─────────────────────────────────────────────────────────────


def test_ulaw_encoder_matches_reference_vectors():
    # CPython's test_audioop vectors for lin2ulaw(width=2).
    pcm = np.array([0, 0x1234, 0x4567, -0x4567, 0x7FFF, -0x8000, -1], dtype=np.int16)
    assert audio.lin2ulaw(pcm) == bytes.fromhex("ffad8e0e80007e")


def test_ulaw_decoder_matches_g711_table():
    decoded = audio.ulaw2lin(bytes([0xFF, 0x7F, 0x80, 0x00, 0xF0, 0x70]))
    assert decoded.tolist() == [0, 0, 32124, -32124, 120, -120]


def test_ulaw_codec_is_bit_identical_to_audioop_when_available():
    audioop = pytest.importorskip("audioop")  # removed in Python 3.13
    everything = np.arange(-32768, 32768, dtype=np.int16)
    assert audio.lin2ulaw(everything) == audioop.lin2ulaw(everything.tobytes(), 2)
    codes = bytes(range(256))
    assert audio.ulaw2lin(codes).tobytes() == audioop.ulaw2lin(codes, 2)


def test_ulaw_round_trip_error_is_within_the_segment_step():
    pcm = np.linspace(-32000, 32000, 5001).astype(np.int16)
    back = audio.ulaw2lin(audio.lin2ulaw(pcm)).astype(np.int32)
    # μ-law's coarsest segment step is 1024; relative error stays small.
    assert np.max(np.abs(back - pcm)) <= 1024


# ── Resampling and framing ──────────────────────────────────────────────────


def _tone(freq, sr, seconds=1.0):
    t = np.arange(int(sr * seconds)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _peak_hz(x, sr):
    spectrum = np.abs(np.fft.rfft(x))
    return np.argmax(spectrum) * sr / len(x)


def test_resample_to_8k_keeps_speech_band_and_filters_above_nyquist():
    inband = audio.resample_to_phone(_tone(440, 24000), 24000)
    assert len(inband) == 8000
    assert abs(_peak_hz(inband, 8000) - 440) < 2
    # 6 kHz cannot exist at 8 kHz; it must be filtered, not aliased to 2 kHz.
    alias = audio.resample_to_phone(_tone(6000, 24000), 24000)
    assert np.sqrt(np.mean(alias**2)) < 0.01 * np.sqrt(np.mean(inband**2))


def test_to_phone_ulaw_accepts_torch_tensors_and_frames_are_20ms():
    wav = torch.from_numpy(_tone(300, 44100, 0.505)).unsqueeze(0)
    ulaw = audio.to_phone_ulaw(wav, 44100)
    assert len(ulaw) == round(8000 * 0.505)
    frames = list(audio.iter_frames(ulaw))
    assert audio.FRAME_BYTES == 160
    assert all(len(f) == 160 for f in frames)
    assert len(frames) == -(-len(ulaw) // 160)
    tail = len(ulaw) % 160
    assert frames[-1][tail:] == bytes([audio.ULAW_SILENCE]) * (160 - tail)


def test_preview_wav_is_8k_mono_pcm16():
    import soundfile as sf

    data, sr = sf.read(io.BytesIO(audio.ulaw_to_wav(audio.lin2ulaw(np.zeros(800, np.int16)))))
    assert sr == 8000 and data.ndim == 1 and len(data) == 800


# ── Signatures and TwiML ────────────────────────────────────────────────────

_DOC_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
_DOC_PARAMS = [
    ("CallSid", "CA1234567890ABCDE"),
    ("Caller", "+12349013030"),
    ("Digits", "1234"),
    ("From", "+12349013030"),
    ("To", "+18005551212"),
]


def test_signature_matches_twilio_documented_example():
    # https://www.twilio.com/docs/usage/webhooks/webhooks-security
    assert tw.compute_signature("12345", _DOC_URL, _DOC_PARAMS) == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    assert tw.signature_valid("12345", _DOC_URL, _DOC_PARAMS, "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")


def test_signature_rejects_tampering_and_accepts_default_port_spelling():
    sig = tw.compute_signature("12345", _DOC_URL, _DOC_PARAMS)
    assert not tw.signature_valid("12345", _DOC_URL, _DOC_PARAMS[:-1] + [("To", "+1999")], sig)
    assert not tw.signature_valid("wrong", _DOC_URL, _DOC_PARAMS, sig)
    assert not tw.signature_valid("12345", _DOC_URL, _DOC_PARAMS, "")
    with_port = tw.compute_signature("12345", "https://mycompany.com:443/myapp.php?foo=1&bar=2", _DOC_PARAMS)
    assert tw.signature_valid("12345", _DOC_URL, _DOC_PARAMS, with_port)


def test_connect_twiml_shape_escapes_values():
    root = ET.fromstring(tw.connect_twiml("wss://h.example/integrations/twilio/stream", {"token": 'a"<b'}))
    assert root.tag == "Response"
    assert [c.tag for c in root] == ["Connect", "Hangup"]
    stream = root.find("Connect/Stream")
    assert stream.get("url") == "wss://h.example/integrations/twilio/stream"
    param = stream.find("Parameter")
    assert (param.get("name"), param.get("value")) == ("token", 'a"<b')
    assert root.find("Say") is None
    assert ET.fromstring(tw.reject_twiml()).find("Reject").get("reason") == "busy"


def test_public_base_url_must_be_a_bare_https_origin():
    assert config.normalize_public_base_url("https://Abc.trycloudflare.com/") == "https://abc.trycloudflare.com"
    for bad in ("http://x.example", "https://u:p@x.example", "https://x.example/path", "https://x.example?q=1"):
        with pytest.raises(config.ConfigError):
            config.normalize_public_base_url(bad)
    with pytest.raises(config.ConfigError):
        config.normalize_account_sid("AC123")


def test_call_tokens_are_single_use_bound_and_expire():
    now = [0.0]
    tokens = session.CallTokens(ttl_s=60, clock=lambda: now[0])
    t = tokens.issue(CALL)
    assert not tokens.redeem(t, "CA" + "b" * 32)  # wrong call consumes it
    assert not tokens.redeem(t, CALL)
    t2 = tokens.issue(CALL)
    assert tokens.redeem(t2, CALL)
    assert not tokens.redeem(t2, CALL)  # single use
    t3 = tokens.issue(CALL)
    now[0] = 61.0
    assert not tokens.redeem(t3, CALL)


# ── Fixtures: in-memory settings, fake engine ───────────────────────────────


def _bind_modules(monkeypatch, router_globals):
    """Point this module's `session`/`config`/`gateway` names at the objects
    the routes under test actually use. Other suites pop and re-import
    service/router modules, so import-time bindings here can go stale."""
    for name in ("session", "config", "gateway"):
        monkeypatch.setitem(globals(), name, router_globals[name])


@pytest.fixture()
def store(monkeypatch):
    import services

    router = importlib.import_module("api.routers.telephony_twilio")
    _bind_modules(monkeypatch, vars(router))
    settings_store = importlib.import_module("services.settings_store")
    monkeypatch.setattr(services, "settings_store", settings_store, raising=False)

    text: dict = {}
    secrets_: dict = {}
    monkeypatch.setattr(settings_store, "get_text", lambda k, d=None: text.get(k, d))
    monkeypatch.setattr(settings_store, "set_text", lambda k, v: text.__setitem__(k, v))
    monkeypatch.setattr(settings_store, "get_secret", lambda n: secrets_.get(n))
    monkeypatch.setattr(
        settings_store,
        "set_secret",
        lambda n, v: secrets_.__setitem__(n, v) if v else secrets_.pop(n, None),
    )
    monkeypatch.setattr(settings_store, "list_secret_names", lambda: list(secrets_))
    session.reset_state()
    yield {"text": text, "secrets": secrets_}
    session.reset_state()


@pytest.fixture()
def fake_engine(monkeypatch):
    tb = importlib.import_module("services.tts_backend")

    class _PhoneEngine(tb.TTSBackend):
        id = "fake-phone"
        display_name = "Fake phone engine (test)"
        supports_cloning = True
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            type(self).calls.append(text)
            return torch.from_numpy(_tone(440, 24000, 0.5)).unsqueeze(0)

    from services import watermark

    monkeypatch.setattr(watermark, "mark_synthetic", lambda wav, _sr, **_kw: wav)
    tb.reset_active_backend()
    monkeypatch.setitem(tb._REGISTRY, "fake-phone", _PhoneEngine)
    yield _PhoneEngine
    tb.reset_active_backend()


def _configure(enabled=True, greeting="Hello. Thanks for calling."):
    config.save(
        config.TwilioConfig(
            enabled=enabled,
            account_sid=ACCOUNT,
            public_base_url=BASE,
            engine="fake-phone",
            greeting=greeting,
        )
    )
    config.set_auth_token(TOKEN)


@pytest.fixture()
def gw():
    from fastapi.testclient import TestClient

    router = importlib.import_module("api.routers.telephony_twilio")
    return TestClient(router.build_gateway_app())


def _post_voice(client, params=None, token=TOKEN, url=None):
    params = params or [("AccountSid", ACCOUNT), ("CallSid", CALL), ("From", "+15550100")]
    sig = tw.compute_signature(token, url or BASE + tw.VOICE_PATH, params)
    return client.post(
        tw.VOICE_PATH,
        content="&".join(f"{k}={v.replace('+', '%2B')}" for k, v in params),
        headers={"content-type": "application/x-www-form-urlencoded", "X-Twilio-Signature": sig},
    )


def _stream_token(resp):
    root = ET.fromstring(resp.text)
    return root.find("Connect/Stream/Parameter").get("value")


# ── Voice webhook ───────────────────────────────────────────────────────────


def test_webhook_is_forbidden_while_disabled(store, gw):
    _configure(enabled=False)
    assert _post_voice(gw).status_code == 403


def test_webhook_rejects_bad_signature_and_foreign_account(store, gw):
    _configure()
    assert _post_voice(gw, token="not-the-token").status_code == 403
    other = [("AccountSid", "AC" + "f" * 32), ("CallSid", CALL)]
    assert _post_voice(gw, params=other).status_code == 403
    outcomes = [r["outcome"] for r in session.registry.snapshot()["recent"]]
    assert outcomes == ["rejected_signature", "rejected_signature"]


def test_forged_webhook_flood_is_throttled_without_locking_out_twilio(store, gw):
    _configure()
    for _ in range(10):
        assert _post_voice(gw, token="nope").status_code == 403
    assert _post_voice(gw, token="nope").status_code == 429
    # A correctly signed webhook still gets through during the throttle.
    assert _post_voice(gw).status_code == 200


def test_webhook_in_flight_during_disable_gets_no_token():
    tokens = session.CallTokens()
    epoch = tokens.epoch  # webhook arrives
    tokens.reset()  # user turns calls off while it reads the body
    assert tokens.issue(CALL, epoch) is None
    assert tokens.pending() == 0
    assert tokens.issue(CALL, tokens.epoch)


def test_disabling_revokes_stream_tokens_already_issued(store, gw, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    async def _noop(*_a):
        return gateway.state()

    monkeypatch.setattr(gateway, "start", _noop)
    monkeypatch.setattr(gateway, "stop", _noop)
    _configure()
    token = _stream_token(_post_voice(gw))
    admin = TestClient(app, client=("127.0.0.1", 50000))
    assert admin.put("/api/integrations/twilio/config", json={"enabled": False}).status_code == 200
    assert admin.put("/api/integrations/twilio/config", json={"enabled": True}).status_code == 200
    _assert_token_rejected(gw, token)

    # Also when enabling fails as incomplete (e.g. the Auth Token was cleared).
    token = _stream_token(_post_voice(gw))
    resp = admin.put("/api/integrations/twilio/config", json={"enabled": True, "auth_token": ""})
    assert resp.status_code == 400
    config.set_auth_token(TOKEN)
    assert admin.put("/api/integrations/twilio/config", json={"enabled": True}).status_code == 200
    _assert_token_rejected(gw, token)


def _assert_token_rejected(gw, token):
    from starlette.websockets import WebSocketDisconnect

    with gw.websocket_connect(tw.STREAM_PATH) as ws:
        ws.send_json(_start_msg(token))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008


def test_webhook_answers_with_stream_twiml(store, gw):
    _configure()
    resp = _post_voice(gw)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/xml")
    stream = ET.fromstring(resp.text).find("Connect/Stream")
    assert stream.get("url") == "wss://phone.example.com" + tw.STREAM_PATH
    assert len(_stream_token(resp)) >= 32


def test_webhook_rejects_as_busy_at_capacity(store, gw, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TWILIO_MAX_CALLS", "1")
    _configure()
    assert _post_voice(gw).status_code == 200  # holds the only slot (pending token)
    busy = _post_voice(gw)
    assert ET.fromstring(busy.text).find("Reject") is not None


def test_webhook_rejects_oversized_bodies(store, gw):
    _configure()
    resp = gw.post(tw.VOICE_PATH, content="x=" + "a" * 20000,
                   headers={"content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 413


# ── Media stream ────────────────────────────────────────────────────────────


def _start_msg(token, call=CALL, account=ACCOUNT):
    return {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": "MZ" + "1" * 32,
        "start": {
            "accountSid": account,
            "streamSid": "MZ" + "1" * 32,
            "callSid": call,
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "customParameters": {"token": token},
        },
    }


def _play_call(gw, token):
    from starlette.websockets import WebSocketDisconnect

    frames = []
    with gw.websocket_connect(tw.STREAM_PATH) as ws:
        ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        ws.send_json(_start_msg(token))
        while True:
            msg = ws.receive_json()
            assert msg["streamSid"] == "MZ" + "1" * 32
            if msg["event"] == "mark":
                break
            assert msg["event"] == "media"
            frames.append(base64.b64decode(msg["media"]["payload"]))
        # Inbound caller audio is accepted (and ignored in announcement mode).
        ws.send_json({"event": "media", "streamSid": "MZ", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}})
        ws.send_json({"event": "mark", "streamSid": "MZ" + "1" * 32, "mark": {"name": msg["mark"]["name"]}})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    return frames, closed.value.code


def test_media_stream_happy_path_streams_20ms_ulaw_then_ends(store, gw, fake_engine):
    _configure(greeting="Hello there. Thanks for calling.")
    frames, code = _play_call(gw, _stream_token(_post_voice(gw)))
    assert code == 1000
    assert frames and all(len(f) == 160 for f in frames)
    # Each synthesized sentence is 0.5 s: 4000 μ-law bytes = 25 frames.
    rendered = len(fake_engine.calls)
    assert rendered >= 1 and len(frames) == 25 * rendered
    pcm = audio.ulaw2lin(b"".join(frames)).astype(np.float32) / 32768
    assert abs(_peak_hz(pcm, 8000) - 440) < 5
    recent = session.registry.snapshot()
    assert recent["active"] == 0
    assert recent["recent"][0]["outcome"] == "completed"
    assert recent["recent"][0]["call"] == "…aaa7"
    assert recent["recent"][0]["audio_seconds"] == 0.5 * rendered

    # Second call replays the cached render: no new synthesis.
    frames2, _ = _play_call(gw, _stream_token(_post_voice(gw)))
    assert frames2 == frames
    assert len(fake_engine.calls) == rendered


def test_cache_follows_the_resolved_engine_and_voice_not_saved_settings(store, fake_engine, monkeypatch):
    """A blank engine means "the active engine": switching it, or editing the
    saved voice behind the same profile id, must re-render — never replay."""
    stream = importlib.import_module("api.routers.tts_stream")
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-phone")
    profile = {"ref_text": "first take"}
    real = stream.build_stream_kwargs
    monkeypatch.setattr(
        stream, "build_stream_kwargs", lambda data: {**real(data), **profile}
    )

    def render():
        return asyncio.run(session.render_ulaw_all("Hello.", voice="p1"))

    first = render()
    assert len(fake_engine.calls) == 1
    assert render() == first and len(fake_engine.calls) == 1  # unchanged → cached
    profile["ref_text"] = "re-recorded take"  # same profile id, new voice
    render()
    assert len(fake_engine.calls) == 2
    key = session._speech_key("Hello.", "p1", "", "")
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "another-engine")
    assert session._speech_key("Hello.", "p1", "", "") != key  # engine switch


def test_concurrent_calls_share_one_synthesis(store, fake_engine):
    """Two callers ringing at once for an uncached greeting: one render,
    both get identical audio (single flight), then the cache serves."""

    async def both():
        return await asyncio.gather(
            session.render_ulaw_all("Hello.", engine="fake-phone"),
            session.render_ulaw_all("Hello.", engine="fake-phone"),
        )

    a, b = asyncio.run(both())
    assert a == b and len(a) == 4000
    assert len(fake_engine.calls) == 1
    assert session._inflight == {}


def test_render_is_cancelled_when_every_caller_leaves(store, fake_engine, monkeypatch):
    """Last caller hangs up mid-greeting: synthesis stops, nothing partial is
    cached, and the next call renders afresh."""
    import threading

    release = threading.Event()  # holds sentence 2+ in the GPU worker
    real = fake_engine.generate

    def gated(self, text, **kw):
        if fake_engine.calls:
            release.wait(timeout=10)
        return real(self, text, **kw)

    monkeypatch.setattr(fake_engine, "generate", gated)
    text = (
        "Thank you for calling our office today. "
        "Our team is currently helping other customers right now. "
        "Please leave a message after the tone and we will call back."
    )

    async def scenario():
        first_audio = asyncio.Event()

        async def caller():
            async for _chunk in session.render_ulaw(text, engine="fake-phone"):
                first_audio.set()
                await asyncio.sleep(3600)  # still on the line

        a = asyncio.create_task(caller())
        b = asyncio.create_task(caller())
        await first_audio.wait()
        render = next(iter(session._inflight.values()))
        a.cancel()  # one caller leaves: the other still shares the render
        await asyncio.gather(a, return_exceptions=True)
        assert not render.task.done() and render.consumers == 1
        b.cancel()  # last caller leaves
        await asyncio.gather(b, return_exceptions=True)
        await asyncio.gather(render.task, return_exceptions=True)
        assert render.task.cancelled()
        assert session._inflight == {}
        release.set()
        return session._speech_key(text, "", "fake-phone", "")

    key = asyncio.run(scenario())
    assert session.ulaw_cache.get(key) is None  # partial render never cached
    fake_engine.calls.clear()
    audio_ = asyncio.run(session.render_ulaw_all(text, engine="fake-phone"))
    assert len(fake_engine.calls) >= 2  # fresh, complete render
    assert session.ulaw_cache.get(key) == audio_


def test_shared_synthesis_failure_reaches_every_caller_and_is_not_cached(store, fake_engine, monkeypatch):
    def boom(self, text, **kw):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(fake_engine, "generate", boom)

    async def both():
        return await asyncio.gather(
            session.render_ulaw_all("Hello.", engine="fake-phone"),
            session.render_ulaw_all("Hello.", engine="fake-phone"),
            return_exceptions=True,
        )

    results = asyncio.run(both())
    assert all(isinstance(r, Exception) and "exploded" in str(r) for r in results)
    assert session._inflight == {}
    assert session.ulaw_cache.get(session._speech_key("Hello.", "", "fake-phone", "")) is None


def test_media_stream_rejects_bad_or_reused_token(store, gw, fake_engine):
    from starlette.websockets import WebSocketDisconnect

    _configure()
    for token in ("forged", ""):
        with gw.websocket_connect(tw.STREAM_PATH) as ws:
            ws.send_json(_start_msg(token))
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1008
    token = _stream_token(_post_voice(gw))
    with gw.websocket_connect(tw.STREAM_PATH) as ws:  # right token, other call
        ws.send_json(_start_msg(token, call="CA" + "c" * 32))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
    assert fake_engine.calls == []


def test_media_stream_closes_when_disabled(store, gw):
    from starlette.websockets import WebSocketDisconnect

    _configure(enabled=False)
    with pytest.raises(WebSocketDisconnect) as closed:
        with gw.websocket_connect(tw.STREAM_PATH) as ws:
            ws.receive_json()
    assert closed.value.code == 1008


def test_caller_hang_up_mid_greeting_is_recorded(store, gw, fake_engine, monkeypatch):
    _configure()
    import threading

    ended = threading.Event()
    real_end = session.registry.end

    def _end(record, outcome):
        real_end(record, outcome)
        ended.set()

    monkeypatch.setattr(session.registry, "end", _end)
    token = _stream_token(_post_voice(gw))
    with gw.websocket_connect(tw.STREAM_PATH) as ws:
        ws.send_json(_start_msg(token))
        ws.receive_json()
        ws.send_json({"event": "stop", "streamSid": "MZ", "stop": {"callSid": CALL}})
    assert ended.wait(timeout=10)
    assert session.registry.snapshot()["active"] == 0
    assert session.registry.snapshot()["recent"][0]["outcome"] == "caller_hung_up"


# ── Admin API on the main backend ───────────────────────────────────────────


@pytest.fixture()
def api(store, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app

    started = []

    async def _start(_app):
        started.append(True)
        return gateway.state()

    async def _stop():
        started.clear()
        return gateway.state()

    endpoint = next(
        r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/integrations/twilio/state"
    )
    _bind_modules(monkeypatch, endpoint.__globals__)
    session.reset_state()
    monkeypatch.setattr(gateway, "start", _start)
    monkeypatch.setattr(gateway, "stop", _stop)
    client = TestClient(app, client=("127.0.0.1", 50000))
    client.started = started
    return client


def test_state_is_off_by_default_and_never_returns_the_token(api, store):
    state = api.get("/api/integrations/twilio/state").json()
    assert state["enabled"] is False and state["has_auth_token"] is False
    # Setup shows the exact tunnel command before the gateway is running.
    assert state["listener"]["running"] is False
    assert state["listener"]["preferred_port"] == gateway._free_port(
        "127.0.0.1", gateway.gateway_port_base()
    )
    api.put("/api/integrations/twilio/config", json={"auth_token": "s3cret-token"})
    state = api.get("/api/integrations/twilio/state")
    assert state.json()["has_auth_token"] is True
    assert "s3cret-token" not in state.text
    assert store["secrets"][config.SECRET_NAME] == "s3cret-token"
    assert not any("s3cret" in v for v in store["text"].values())


def test_enabling_requires_complete_setup_and_starts_the_listener(api):
    resp = api.put("/api/integrations/twilio/config", json={"enabled": True, "account_sid": ACCOUNT})
    assert resp.status_code == 400
    assert set(resp.json()["detail"]["missing"]) == {"auth_token", "public_base_url", "greeting"}
    assert api.get("/api/integrations/twilio/state").json()["enabled"] is False
    assert api.started == []
    resp = api.put(
        "/api/integrations/twilio/config",
        json={"enabled": True, "auth_token": TOKEN, "public_base_url": BASE + "/", "greeting": "Hi"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["webhook_url"] == BASE + tw.VOICE_PATH
    assert api.started == [True]
    assert api.put("/api/integrations/twilio/config", json={"enabled": False}).json()["enabled"] is False
    assert api.started == []


def test_invalid_fields_are_rejected_with_codes(api):
    resp = api.put("/api/integrations/twilio/config", json={"public_base_url": "http://insecure.example"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_public_url"


def test_local_test_returns_phone_quality_wav(api, fake_engine):
    import soundfile as sf

    resp = api.post("/api/integrations/twilio/test", json={"text": "Hello.", "engine": "fake-phone"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "audio/wav"
    data, sr = sf.read(io.BytesIO(resp.content))
    assert sr == 8000 and len(data) == 4000
    assert api.post("/api/integrations/twilio/test", json={"text": " "}).status_code == 400


def test_admin_api_is_not_reachable_from_remote_clients(store):
    from fastapi.testclient import TestClient
    from main import app

    remote = TestClient(app, client=("203.0.113.9", 50000))
    assert remote.put("/api/integrations/twilio/config", json={"enabled": True}).status_code in (401, 403)


# ── The gateway listener serves telephony routes only ───────────────────────


def test_gateway_host_is_loopback_unless_an_ip_literal_is_configured(monkeypatch):
    for raw in (None, "", "  ", "localhost", "example.com"):
        if raw is None:
            monkeypatch.delenv("OMNIVOICE_TWILIO_HOST", raising=False)
        else:
            monkeypatch.setenv("OMNIVOICE_TWILIO_HOST", raw)
        assert gateway.gateway_host() == "127.0.0.1"
    monkeypatch.setenv("OMNIVOICE_TWILIO_HOST", "0.0.0.0")
    assert gateway.gateway_host() == "0.0.0.0"


def test_main_backend_never_serves_the_public_twilio_routes():
    """The public routes live only on the gateway: a tunnel mistakenly aimed
    at the main port must not find a Twilio endpoint that trusts loopback."""
    from main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(p.startswith("/integrations/twilio") for p in paths)
    assert {"/api/integrations/twilio/state", "/api/integrations/twilio/config"} <= paths


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_preferred_port_skips_a_taken_base_port_like_start_does(monkeypatch):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        base = taken.getsockname()[1]
        monkeypatch.setenv("OMNIVOICE_TWILIO_PORT", str(base))
        preferred = gateway.state()["preferred_port"]
        assert preferred != base
        assert preferred == gateway._free_port("127.0.0.1", base)


def test_gateway_listener_exposes_only_twilio_routes(store, monkeypatch):
    import httpx

    monkeypatch.setenv("OMNIVOICE_TWILIO_PORT", str(_free_port()))
    _configure()

    async def scenario():
        router = importlib.import_module("api.routers.telephony_twilio")
        state = await gateway.start(router.build_gateway_app())
        try:
            assert state["running"] and state["host"] == "127.0.0.1"
            async with httpx.AsyncClient(base_url=state["tunnel_target"]) as client:
                assert (await client.get("/api/integrations/twilio/state")).status_code == 404
                assert (await client.get("/profiles")).status_code == 404
                assert (await client.get("/docs")).status_code == 404
                assert (await client.post(tw.VOICE_PATH, content="")).status_code == 403
        finally:
            stopped = await gateway.stop()
        assert stopped["running"] is False

    asyncio.run(scenario())
