"""ElevenLabs-compatible API under /elevenlabs (api.routers.elevenlabs_compat).

Pinned: output formats (incl. resampled PCM, μ-law and A-law), voice lookup,
the ElevenLabs error shape, voice/model lists, speech-to-text words, and the
gapless streaming resampler.
"""
from __future__ import annotations

import importlib
import io
import os
import uuid

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

SECONDS = 0.5


def _el():
    return importlib.import_module("api.routers.elevenlabs_compat")


@pytest.fixture()
def engine(monkeypatch):
    tts = importlib.import_module("services.tts_backend")

    class _Engine(tts.TTSBackend):
        id = "elevenlabs-compat-engine"
        display_name = "ElevenLabs compat engine (test)"
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            type(self).calls.append(kw)
            t = torch.arange(int(24000 * SECONDS)) / 24000
            return (0.3 * torch.sin(2 * torch.pi * 220 * t)).unsqueeze(0)

    _Engine.calls = []
    inst = _Engine()
    monkeypatch.setitem(tts._REGISTRY, _Engine.id, _Engine)
    monkeypatch.setattr(tts, "get_active_tts_backend", lambda: inst)
    monkeypatch.setattr(tts, "get_engine_instance_for", lambda _id: inst)
    from services import engine_routing

    async def _profile(*_a, **_k):
        return {"routing_status": "native", "routing_reason": ""}

    monkeypatch.setattr(engine_routing, "runtime_compute_profile_async", _profile)
    return _Engine


@pytest.fixture()
def client():
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def profile():
    from core.db import db_conn, init_db

    init_db()
    pid = str(uuid.uuid4())
    name = f"el-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, created_at, ref_text, ref_audio_path, language) "
            "VALUES (?, ?, 'clone', 0, '', ?, 'en')",
            (pid, name, f"{pid}.wav"),
        )
    yield pid, name
    with db_conn() as conn:
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))


def _tts(client, voice="default", fmt=None, stream=False, **body):
    path = f"/elevenlabs/v1/text-to-speech/{voice}" + ("/stream" if stream else "")
    params = {"output_format": fmt} if fmt else None
    return client.post(path, params=params, json={"text": "Hello there.", **body})


@pytest.mark.parametrize("fmt,rate,width", [
    ("pcm_24000", 24000, 2), ("pcm_16000", 16000, 2), ("pcm_32000", 32000, 2), ("pcm_44100", 44100, 2),
    ("ulaw_8000", 8000, 1), ("alaw_8000", 8000, 1),
])
@pytest.mark.parametrize("stream", [False, True])
def test_raw_formats_have_the_requested_rate(client, engine, fmt, rate, width, stream):
    res = _tts(client, fmt=fmt, stream=stream, model_id="eleven_multilingual_v2")
    assert res.status_code == 200, res.text
    samples = len(res.content) // width
    assert abs(samples - rate * SECONDS) <= 2
    assert res.headers["content-type"].startswith(_el()._MEDIA[fmt.split("_")[0]])


def test_wav_and_profile_voice(client, engine, profile):
    pid, _ = profile
    res = _tts(client, voice=pid, fmt="wav_44100", language_code="en",
               voice_settings={"stability": 0.4, "similarity_boost": 0.8, "speed": 1.1}, seed=7)
    assert res.status_code == 200, res.text
    assert res.content[:4] == b"RIFF"
    kw = engine.calls[-1]
    assert kw["ref_audio"].endswith(f"{pid}.wav")
    assert kw["speed"] == pytest.approx(1.1)
    assert kw["seed"] == 7
    assert kw["language"] == "en"


def test_unknown_elevenlabs_voice_id_is_a_404(client, engine):
    res = _tts(client, voice="21m00Tcm4TlvDq8ikWAM", fmt="pcm_24000")
    assert res.status_code == 404
    detail = res.json()["detail"]
    assert detail["status"] == "voice_not_found"
    assert "/elevenlabs/v1/voices" in detail["message"]
    assert engine.calls == []


def test_bad_output_format_is_rejected_before_generation(client, engine):
    res = _tts(client, fmt="flac_48000")
    assert res.status_code == 422
    assert res.json()["detail"]["status"] == "invalid_output_format"
    assert engine.calls == []


def test_voice_and_model_lists(client, engine, profile):
    pid, name = profile
    voices = client.get("/elevenlabs/v1/voices").json()["voices"]
    entry = next(v for v in voices if v["voice_id"] == pid)
    assert entry["name"] == name and entry["category"] == "cloned"
    assert voices[0]["voice_id"] == "default"

    assert client.get(f"/elevenlabs/v1/voices/{pid}").json()["name"] == name
    missing = client.get("/elevenlabs/v1/voices/nope")
    assert missing.status_code == 404 and missing.json()["detail"]["status"] == "voice_not_found"

    page = client.get("/elevenlabs/v2/voices", params={"search": name, "page_size": 1}).json()
    assert [v["voice_id"] for v in page["voices"]] == [pid]
    assert page["has_more"] is False and page["total_count"] == 1

    assert client.get("/elevenlabs/v1/voices/settings/default").json()["similarity_boost"] == 0.75
    ids = [m["model_id"] for m in client.get("/elevenlabs/v1/models").json()]
    assert "eleven_multilingual_v2" in ids


def test_speech_to_text_returns_elevenlabs_words(client, monkeypatch):
    oc = importlib.import_module("api.routers.openai_compat")
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return {"text": "Hi there", "language": "en",
                "words": [{"word": "Hi", "start": 0.0, "end": 0.3},
                          {"word": "there", "start": 0.4, "end": 0.8}]}

    monkeypatch.setattr(oc, "_transcribe_request", fake)
    res = client.post("/elevenlabs/v1/speech-to-text",
                      data={"model_id": "scribe_v1", "language_code": "en"},
                      files={"file": ("a.wav", b"RIFF", "audio/wav")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["text"] == "Hi there" and body["language_code"] == "en"
    assert [w["type"] for w in body["words"]] == ["word", "spacing", "word"]
    assert body["words"][1] == {"text": " ", "start": 0.3, "end": 0.4, "type": "spacing", "logprob": 0.0}
    assert seen["response_format"] == "verbose_json" and seen["language"] == "en"



def test_speech_to_text_labels_speakers(client, monkeypatch):
    oc = importlib.import_module("api.routers.openai_compat")
    td = importlib.import_module("services.transcript_diarization")
    seen = {}

    async def fake(**kw):
        seen["upload"] = kw["file"].file.read()
        return {"text": "Hi there friend", "language": "en",
                "words": [{"word": "Hi", "start": 0.0, "end": 0.3},
                          {"word": "there", "start": 0.4, "end": 0.8},
                          {"word": "friend", "start": 1.0, "end": 1.4}]}

    def fake_diarize(path, spans, num_speakers):
        seen["path"] = path
        seen["kept"] = open(path, "rb").read()
        seen["spans"], seen["n"] = spans, num_speakers
        return ["SPEAKER_07", "SPEAKER_07", "SPEAKER_02"], None

    monkeypatch.setattr(oc, "_transcribe_request", fake)
    monkeypatch.setattr(td, "diarize_segments", fake_diarize)
    res = client.post("/elevenlabs/v1/speech-to-text", data={"diarize": "true", "num_speakers": "2"},
                      files={"file": ("a.wav", b"RIFF-audio", "audio/wav")})
    assert res.status_code == 200, res.text
    words = res.json()["words"]
    assert [w.get("speaker_id") for w in words if w["type"] == "word"] == ["speaker_0", "speaker_0", "speaker_1"]
    assert seen["upload"] == seen["kept"] == b"RIFF-audio"
    assert seen["n"] == 2 and len(seen["spans"]) == 3
    assert not os.path.exists(seen["path"])  # the kept copy is removed

    monkeypatch.setattr(td, "diarize_segments", lambda *a: ([None] * 3, "LOAD_FAILED"))
    res = client.post("/elevenlabs/v1/speech-to-text", data={"diarize": "true"},
                      files={"file": ("a.wav", b"RIFF-audio", "audio/wav")})
    assert res.status_code == 200
    assert res.headers["x-omnivoice-diarization-error"] == "LOAD_FAILED"
    assert all("speaker_id" not in w for w in res.json()["words"])


def test_audio_isolation_returns_the_vocal_stem(client, monkeypatch):
    import soundfile as sf

    vs = importlib.import_module("services.video_voice_swap")
    seen = {}

    async def fake_separate(track, workdir, *, offline=False):
        seen["source"] = open(track, "rb").read()
        seen["track"], seen["offline"] = track, offline
        vocals = os.path.join(workdir, "vocals.wav")
        sf.write(vocals, np.zeros((44100, 2), dtype=np.float32), 44100)
        return vocals, vocals

    monkeypatch.setattr(vs, "separation_available", lambda: True)
    monkeypatch.setattr(vs, "separate_vocals", fake_separate)
    monkeypatch.setattr(_el(), "isolation_weights_cached", lambda: False)
    res = client.post("/elevenlabs/v1/audio-isolation", params={"output_format": "pcm_16000"},
                      files={"audio": ("song.mp3", b"x", "audio/mpeg")})
    assert res.status_code == 409  # never starts a model download
    assert res.json()["detail"]["status"] == "audio_isolation_model_missing"
    assert "source" not in seen
    monkeypatch.setattr(_el(), "isolation_weights_cached", lambda: True)
    res = client.post("/elevenlabs/v1/audio-isolation", params={"output_format": "pcm_16000"},
                      files={"audio": ("song.mp3", b"song-bytes", "audio/mpeg")})
    assert res.status_code == 200, res.text
    assert len(res.content) == 16000 * 2
    assert seen["source"] == b"song-bytes" and not os.path.exists(seen["track"])
    assert seen["offline"] is True  # Demucs may only read its local caches

    monkeypatch.setattr(vs, "separation_available", lambda: False)
    res = client.post("/elevenlabs/v1/audio-isolation", params={"output_format": "pcm_16000"},
                      files={"audio": ("song.mp3", b"x", "audio/mpeg")})
    assert res.status_code == 409
    assert res.json()["detail"]["status"] == "audio_isolation_unavailable"


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 44100, 48000])
def test_stream_resampler_matches_whole_signal(rate):
    import torchaudio.functional as AF

    rng = np.random.default_rng(rate)
    pcm = (rng.standard_normal(24000) * 6000).clip(-32768, 32767).astype("<i2")
    whole = AF.resample(torch.from_numpy(pcm.astype(np.float32) / 32768.0), 24000, rate).numpy()
    rs = _el().StreamResampler(rate)
    raw = pcm.tobytes()
    parts, i = [], 0
    for size in (1, 333, 4097, 2, 9000, 77):  # odd sizes split int16 samples
        parts.append(rs.feed(raw[i:i + size]))
        i += size
    parts.append(rs.feed(raw[i:]))
    parts.append(rs.flush())
    out = np.concatenate(parts).astype(np.float32) / 32768.0
    assert len(out) == len(whole)
    assert np.max(np.abs(out - whole)) < 2 / 32768.0


def test_alaw_matches_reference_codec():
    audioop = pytest.importorskip("audioop")
    pcm = np.arange(-32768, 32768, dtype=np.int16)
    assert _el().lin2alaw(pcm) == audioop.lin2alaw(pcm.astype("<i2").tobytes(), 2)


@pytest.fixture()
def saved_take():
    """A 1 s 24 kHz take in OUTPUTS_DIR, as the app's handlers leave one."""
    import soundfile as sf
    from core.config import OUTPUTS_DIR

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    name = f"el-take-{uuid.uuid4().hex[:8]}.wav"
    path = os.path.join(OUTPUTS_DIR, name)
    sf.write(path, np.zeros(24000, dtype=np.float32), 24000)
    yield {"audio_url": f"/audio/{name}", "id": "x"}
    os.unlink(path)


def test_speech_to_speech_keeps_delivery_then_falls_back(client, monkeypatch, profile, saved_take):
    from fastapi import HTTPException

    vc = importlib.import_module("api.routers.voice_convert")
    calls = []

    async def fake(*, audio, profile_id, match_duration, language, keep_delivery):
        calls.append((profile_id, keep_delivery, await audio.read()))
        if keep_delivery:
            raise HTTPException(409, {"error": "voice_conversion_engine_missing", "message": "none"})
        return saved_take

    monkeypatch.setattr(vc, "convert_speech", fake)
    pid, name = profile
    res = client.post(f"/elevenlabs/v1/speech-to-speech/{name}",
                      params={"output_format": "pcm_16000"},
                      files={"audio": ("in.wav", b"source-bytes", "audio/wav")})
    assert res.status_code == 200, res.text
    assert len(res.content) == 16000 * 2
    assert res.headers["x-omnivoice-fallback"] == "respoken"
    # The profile is resolved by name; the upload is re-read for the fallback.
    assert calls == [(pid, True, b"source-bytes"), (pid, False, b"source-bytes")]

    # The SDK's path, and the /stream forms, answer the same way.
    for path in (f"/elevenlabs/v1/voice-changer/{pid}", f"/elevenlabs/v1/voice-changer/{pid}/stream",
                 f"/elevenlabs/v1/speech-to-speech/{pid}/stream"):
        res = client.post(path, params={"output_format": "pcm_16000"},
                          files={"audio": ("in.wav", b"source-bytes", "audio/wav")})
        assert res.status_code == 200, (path, res.text)

    # Video would take the app's video swap, whose separation can download a model.
    calls.clear()
    video = client.post(f"/elevenlabs/v1/voice-changer/{pid}", params={"output_format": "pcm_16000"},
                        files={"audio": ("clip.mp4", b"video-bytes", "video/mp4")})
    assert video.status_code == 415
    assert video.json()["detail"]["status"] == "unsupported_file_type"
    assert calls == []

    missing = client.post("/elevenlabs/v1/speech-to-speech/default",
                          params={"output_format": "pcm_16000"},
                          files={"audio": ("in.wav", b"x", "audio/wav")})
    assert missing.status_code == 404
    assert missing.json()["detail"]["status"] == "voice_not_found"


def test_sound_generation_returns_the_saved_effect(client, monkeypatch, saved_take):
    from fastapi import HTTPException

    sfx = importlib.import_module("api.routers.sfx")
    seen = []

    async def fake(req):
        seen.append(req)
        if req.prompt == "missing":
            raise HTTPException(409, {"code": "sfx_not_installed", "message": "Install it."})
        return saved_take

    monkeypatch.setattr(sfx, "sfx_generate", fake)
    monkeypatch.setattr(sfx, "_limits", lambda: {"default_seconds": 10.0})
    res = client.post("/elevenlabs/v1/sound-generation", params={"output_format": "wav_44100"},
                      json={"text": "door creak", "prompt_influence": 0.3})
    assert res.status_code == 200, res.text
    assert res.content[:4] == b"RIFF"
    assert seen[-1].prompt == "door creak" and seen[-1].duration == 10.0

    err = client.post("/elevenlabs/v1/sound-generation", params={"output_format": "pcm_24000"},
                      json={"text": "missing", "duration_seconds": 2})
    assert err.status_code == 409
    detail = err.json()["detail"]
    assert detail["status"] == "sfx_not_installed" and detail["message"] == "Install it."


def test_the_isolation_checkpoint_matches_the_installed_demucs():
    import os

    demucs = pytest.importorskip("demucs")
    remote = os.path.join(os.path.dirname(demucs.__file__), "remote")
    with open(os.path.join(remote, "files.txt"), encoding="utf-8") as fh:
        files = fh.read().split()
    assert _el().HTDEMUCS_CHECKPOINT in files


@pytest.mark.parametrize("fmt", ["mp3_44100", "mp3_44100_8", "opus_24000_64", "wav_44100_128",
                                 "pcm_44100_128", "ulaw_16000", "flac_44100",
                                 # MP3 bitrates libmp3lame would round, and PCM rates it lacks.
                                 "mp3_44100_127", "mp3_22050_320", "mp3_44100_144", "pcm_11025"])
def test_combinations_the_encoders_cannot_honour_are_rejected(fmt):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _el().output_spec(fmt)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("fmt", ["mp3_22050_32", "mp3_24000_48", "mp3_44100_32", "mp3_44100_64",
                                 "mp3_44100_96", "mp3_44100_128", "mp3_44100_192", "mp3_22050_144"])
def test_standard_mp3_formats_are_accepted(fmt):
    family, rate, kbps = _el().output_spec(fmt)
    assert (family, rate, kbps) == ("mp3", int(fmt.split("_")[1]), int(fmt.split("_")[2]))


def test_mp3_and_wav_use_the_requested_rate_and_bitrate(client, engine):
    import soundfile as sf

    wav = _tts(client, fmt="wav_16000")
    assert wav.status_code == 200, wav.text
    info = sf.info(io.BytesIO(wav.content))
    assert info.samplerate == 16000 and info.subtype == "PCM_16"

    from services.ffmpeg_utils import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    for fmt, rate, kbps in (("mp3_22050_32", 22050, 32), ("mp3_44100_128", 44100, 128)):
        res = _tts(client, fmt=fmt)
        assert res.status_code == 200, res.text
        frames = _mp3_frames(res.content)
        assert len(frames) > 2, fmt
        # The first frame may be the encoder's Info header; every audio frame
        # after it carries the requested rate and bitrate.
        assert set(frames[1:]) == {(rate, kbps)}, fmt


_MP3_KBPS = {
    "mpeg1": [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    "mpeg2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_MP3_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3_frames(data: bytes) -> list[tuple[int, int]]:
    """``(sample rate, kbps)`` of each MPEG Layer III frame, walking frame by frame."""
    pos = 0
    if data[:3] == b"ID3":
        pos = 10 + ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
    frames = []
    while pos + 4 <= len(data):
        b1, b2 = data[pos + 1], data[pos + 2]
        assert data[pos] == 0xFF and b1 & 0xE0 == 0xE0, f"lost frame sync at byte {pos}"
        version = (b1 >> 3) & 3
        kbps = _MP3_KBPS["mpeg1" if version == 3 else "mpeg2"][b2 >> 4]
        rate = _MP3_RATES[version][(b2 >> 2) & 3]
        frames.append((rate, kbps))
        pos += (144 if version == 3 else 72) * kbps * 1000 // rate + ((b2 >> 1) & 1)
    return frames


def test_audio_routes_declare_audio_in_the_api_contract(client):
    spec = client.get("/openapi.json").json()["paths"]
    for path in ("/elevenlabs/v1/text-to-speech/{voice_id}", "/elevenlabs/v1/audio-isolation"):
        content = spec[path]["post"]["responses"]["200"]["content"]
        assert "audio/mpeg" in content and "application/json" not in content


@pytest.mark.parametrize("route,diarize", [
    ("/v1/audio/transcriptions", None),
    ("/elevenlabs/v1/speech-to-text", "false"),
    ("/elevenlabs/v1/speech-to-text", "true"),
])
def test_transcription_uploads_are_capped_and_cleaned_up(client, monkeypatch, tmp_path, route, diarize):
    import tempfile

    oc = importlib.import_module("api.routers.openai_compat")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(oc, "MAX_TRANSCRIPTION_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(_el(), "_MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    data = {"model": "whisper-1"} if diarize is None else {"diarize": diarize}
    res = client.post(route, data=data, files={"file": ("big.wav", b"x" * 5000, "audio/wav")})
    assert res.status_code == 413, res.text
    assert list(tmp_path.iterdir()) == []


def test_opus_is_encoded_at_a_constant_bitrate(monkeypatch):
    """libopus' default VBR treats -b:a as a target; the format names a bitrate."""
    import asyncio

    oc = importlib.import_module("api.routers.openai_compat")
    fu = importlib.import_module("services.ffmpeg_utils")
    commands = []

    async def fake_run(cmd, timeout=None):
        commands.append(cmd)
        return 0, b"encoded", b""

    monkeypatch.setattr(oc, "_ffmpeg_or_error", lambda _family: "ffmpeg")
    monkeypatch.setattr(fu, "run_ffmpeg", fake_run)
    wav = torch.zeros(1, 2400)
    for family, bitrate in (("opus", 64), ("mp3", 128)):
        assert asyncio.run(_el()._ffmpeg_encode(wav, 24000, family, 48000, bitrate)) == b"encoded"
    opus, mp3 = commands
    assert opus[opus.index("-vbr") + 1] == "off" and "-b:a" in opus
    assert "-vbr" not in mp3


def _write_hf_cache(root, files):
    snapshot = root / "models--adefossez--HTDemucs" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (root / "models--adefossez--HTDemucs" / "refs").mkdir()
    (root / "models--adefossez--HTDemucs" / "refs" / "main").write_text("abc123")
    for name, text in files.items():
        (snapshot / name).write_text(text)


@pytest.mark.parametrize("files,legacy,expected", [
    ({"htdemucs.yaml": "models: ['955717e8']\n", "955717e8.safetensors": "w"}, False, True),
    ({"htdemucs.yaml": "models: ['955717e8']\n"}, False, False),  # a model is missing
    ({}, True, True),   # the torch hub checkpoint Demucs 4.0 (and 4.1's fallback) loads
    ({}, False, False),
])
def test_isolation_recognises_each_local_demucs_cache(monkeypatch, tmp_path, files, legacy, expected):
    hf_constants = pytest.importorskip("huggingface_hub.constants")
    hub = tmp_path / "hub"
    (hub / "checkpoints").mkdir(parents=True)
    if legacy:
        (hub / "checkpoints" / _el().HTDEMUCS_CHECKPOINT).write_bytes(b"w")
    hf = tmp_path / "hf"
    hf.mkdir()
    _write_hf_cache(hf, files)
    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(hub))
    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(hf))
    assert _el().isolation_weights_cached() is expected


def test_offline_separation_keeps_demucs_off_the_network(monkeypatch, tmp_path):
    import asyncio

    vs = importlib.import_module("services.video_voice_swap")
    envs = []

    async def fake_ffmpeg(cmd, timeout=1800.0, env=None):
        envs.append(env)
        return 1, b""

    monkeypatch.setattr(vs, "separation_available", lambda: True)
    monkeypatch.setattr(vs, "_ffmpeg", fake_ffmpeg)
    asyncio.run(vs.separate_vocals("t.wav", str(tmp_path), offline=True))
    asyncio.run(vs.separate_vocals("t.wav", str(tmp_path)))
    assert envs[0]["HF_HUB_OFFLINE"] == "1"
    assert envs[1] is None  # Dubbing and voice swap may still download on the user's action


def test_an_abandoned_diarization_keeps_its_audio_until_the_worker_lets_go(client, monkeypatch):
    oc = importlib.import_module("api.routers.openai_compat")
    mm = importlib.import_module("services.model_manager")
    seen = {}

    async def fake_transcribe(**kw):
        return {"text": "Hi", "language": "en", "words": [{"word": "Hi", "start": 0.0, "end": 0.3}]}

    async def timed_out(fn, *, on_abandon=None, **_kw):
        seen["fn"], seen["release"] = fn, on_abandon
        raise TimeoutError

    monkeypatch.setattr(oc, "_transcribe_request", fake_transcribe)
    monkeypatch.setattr(mm, "run_on_gpu_pool_guarded", timed_out)
    td = importlib.import_module("services.transcript_diarization")
    monkeypatch.setattr(td, "diarize_segments", lambda path, *_a: seen.setdefault("path", path))
    res = client.post("/elevenlabs/v1/speech-to-text", data={"diarize": "true"},
                      files={"file": ("a.wav", b"RIFF-audio", "audio/wav")})
    assert res.headers["x-omnivoice-diarization-error"] == "DIARIZATION_TIMEOUT"
    seen["fn"]()  # the worker is still running and can read its audio
    assert os.path.exists(seen["path"])
    seen["release"]()  # the pool reports the worker let go
    assert not os.path.exists(seen["path"])


@pytest.mark.parametrize("route", [
    "/v1/audio/transcriptions", "/elevenlabs/v1/speech-to-text", "/elevenlabs/v1/audio-isolation",
    "/elevenlabs/v1/voice-changer/someone", "/elevenlabs/v1/speech-to-speech/someone/stream",
])
def test_oversized_uploads_are_refused_before_the_form_is_parsed(client, monkeypatch, route):
    ul = importlib.import_module("core.upload_limit")
    monkeypatch.setattr(ul, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(ul, "FORM_OVERHEAD_BYTES", 100)
    field = "file" if "transcriptions" in route or "speech-to-text" in route else "audio"
    res = client.post(route, data={"model": "whisper-1"},
                      files={field: ("big.wav", b"x" * 5000, "audio/wav")})
    assert res.status_code == 413, res.text
    body = res.json()
    if route.startswith("/elevenlabs"):
        assert body["detail"]["status"] == "file_too_large"
    else:
        assert body["error"]["code"] == "file_too_large"


def test_the_upload_cap_counts_bytes_without_a_declared_length():
    import asyncio

    from starlette.exceptions import HTTPException

    ul = importlib.import_module("core.upload_limit")
    chunks = [{"type": "http.request", "body": b"x" * ul.FORM_OVERHEAD_BYTES, "more_body": True}] * 3

    async def receive():
        return chunks.pop()

    async def drain(_scope, recv, _send):
        while True:
            await recv()

    async def run():
        mw = ul.UploadSizeLimitMiddleware(drain)
        scope = {"type": "http", "method": "POST", "path": "/elevenlabs/v1/audio-isolation", "headers": []}
        await mw(scope, receive, None)

    old = ul.MAX_UPLOAD_BYTES
    ul.MAX_UPLOAD_BYTES = ul.FORM_OVERHEAD_BYTES
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())
    finally:
        ul.MAX_UPLOAD_BYTES = old
    assert exc.value.status_code == 413


def test_text_to_dialogue_joins_each_voice_with_a_pause(client, engine, profile):
    pid, _ = profile
    res = client.post("/elevenlabs/v1/text-to-dialogue", params={"output_format": "pcm_24000"},
                      json={"inputs": [{"text": "Hi.", "voice_id": "default"},
                                       {"text": "Hello!", "voice_id": pid}],
                            "model_id": "eleven_v3", "seed": 3})
    assert res.status_code == 200, res.text
    gap = int(24000 * _el()._TURN_GAP_S)
    assert len(res.content) // 2 == 2 * int(24000 * SECONDS) + gap
    first, second = engine.calls[-2:]
    assert "ref_audio" not in first and second["ref_audio"].endswith(f"{pid}.wav")
    assert first["seed"] == second["seed"] == 3

    streamed = client.post("/elevenlabs/v1/text-to-dialogue/stream", params={"output_format": "pcm_24000"},
                           json={"inputs": [{"text": "Hi.", "voice_id": "default"}]})
    assert streamed.status_code == 200 and len(streamed.content) // 2 == int(24000 * SECONDS)


def test_text_to_dialogue_checks_every_voice_first(client, engine):
    res = client.post("/elevenlabs/v1/text-to-dialogue", params={"output_format": "pcm_24000"},
                      json={"inputs": [{"text": "Hi.", "voice_id": "default"},
                                       {"text": "Yo.", "voice_id": "21m00Tcm4TlvDq8ikWAM"}]})
    assert res.status_code == 404
    assert res.json()["detail"]["status"] == "voice_not_found"
    assert engine.calls == []


def test_with_timestamps_uses_recognized_words_when_available(client, engine, monkeypatch):
    import base64

    oc = importlib.import_module("api.routers.openai_compat")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr, "asr_model_missing_error", lambda: None)

    async def fake(**kw):
        assert kw["file"].file.read(4) == b"RIFF"
        return {"text": "Hello there.", "words": [{"word": "Hello", "start": 0.05, "end": 0.2},
                                                  {"word": "there", "start": 0.25, "end": 0.45}]}

    monkeypatch.setattr(oc, "_transcribe_request", fake)
    res = client.post("/elevenlabs/v1/text-to-speech/default/with-timestamps",
                      params={"output_format": "pcm_16000"}, json={"text": "Hello there."})
    assert res.status_code == 200, res.text
    assert res.headers["x-omnivoice-alignment"] == "recognized"
    body = res.json()
    assert len(base64.b64decode(body["audio_base64"])) == 16000 * 2 * SECONDS
    alignment = body["alignment"]
    assert "".join(alignment["characters"]) == "Hello there."
    assert alignment["character_start_times_seconds"][6] == pytest.approx(0.25)


def test_with_timestamps_estimates_without_a_recognizer(client, engine, monkeypatch):
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(asr, "asr_model_missing_error", lambda: {"error": "asr_model_missing"})
    res = client.post("/elevenlabs/v1/text-to-speech/default/stream/with-timestamps",
                      params={"output_format": "pcm_24000"}, json={"text": "Hi you"})
    assert res.status_code == 200, res.text
    assert res.headers["x-omnivoice-alignment"] == "estimated"
    import json as _json

    line = _json.loads(res.content.decode().strip())
    ends = line["alignment"]["character_end_times_seconds"]
    assert len(ends) == 6 and ends[-1] == pytest.approx(SECONDS, abs=0.01)


def test_add_voice_creates_a_clone_profile(client, monkeypatch):
    pr = importlib.import_module("api.routers.profiles")
    seen = {}

    async def fake_create(**kw):
        seen.update(kw)
        seen["audio"] = await kw["ref_audio"].read()
        return {"id": "new-profile"}

    monkeypatch.setattr(pr, "create_profile", fake_create)
    res = client.post("/elevenlabs/v1/voices/add",
                      data={"name": "Narrator", "labels": '{"language": "en"}'},
                      files=[("files", ("a.wav", b"first", "audio/wav")),
                             ("files", ("b.wav", b"second", "audio/wav"))])
    assert res.status_code == 200, res.text
    assert res.json() == {"voice_id": "new-profile", "requires_verification": False}
    assert seen["name"] == "Narrator" and seen["kind"] == "clone"
    assert seen["language"] == "en" and seen["audio"] == b"first"

    bad = client.post("/elevenlabs/v1/voices/add", data={"name": "X", "labels": "[1]"},
                      files=[("files", ("a.wav", b"x", "audio/wav"))])
    assert bad.status_code == 422 and bad.json()["detail"]["status"] == "invalid_labels"


def test_delete_voice_removes_the_profile(client, monkeypatch, profile):
    pr = importlib.import_module("api.routers.profiles")
    deleted = []
    monkeypatch.setattr(pr, "delete_profile", lambda pid: deleted.append(pid) or {"deleted": pid})
    pid, name = profile
    res = client.delete(f"/elevenlabs/v1/voices/{name}")
    assert res.status_code == 200, res.text
    assert deleted == [pid]
    missing = client.delete("/elevenlabs/v1/voices/default")
    assert missing.status_code == 404


def test_deleting_a_voice_needs_administrator_access():
    from api.routers.elevenlabs_compat import router

    route = next(r for r in router.routes if r.path.endswith("/v1/voices/{voice_id}") and "DELETE" in r.methods)
    assert any(getattr(d.dependency, "__name__", "") == "require_admin" for d in route.dependencies)


def test_dubbing_runs_on_the_batch_queue(client, monkeypatch, tmp_path):
    batch = importlib.import_module("api.routers.batch")
    seen = {}

    async def fake_enqueue(**kw):
        seen.update(kw)
        seen["video"] = await kw["video"].read()
        return {"job_id": "dub123", "status": "queued", "queue_position": 1}

    monkeypatch.setattr(batch, "enqueue_batch_job", fake_enqueue)
    res = client.post("/elevenlabs/v1/dubbing", data={"target_lang": "es", "drop_background_audio": "true"},
                      files={"file": ("clip.mp4", b"video", "video/mp4")})
    assert res.status_code == 200, res.text
    assert res.json()["dubbing_id"] == "dub123"
    assert seen["langs"] == "es" and seen["preserve_bg"] is False and seen["video"] == b"video"

    no_file = client.post("/elevenlabs/v1/dubbing", data={"target_lang": "es", "source_url": "https://x"})
    assert no_file.status_code == 422 and no_file.json()["detail"]["status"] == "file_required"

    output = tmp_path / "out.mp4"
    output.write_bytes(b"dubbed-video")
    job = {"id": "dub123", "status": "running", "filename": "clip.mp4", "langs": ["es"],
           "outputs": {"es": str(output)}, "error": None}
    monkeypatch.setitem(batch._jobs, "dub123", job)
    status = client.get("/elevenlabs/v1/dubbing/dub123").json()
    assert status == {"dubbing_id": "dub123", "name": "clip.mp4", "status": "dubbing",
                      "target_languages": ["es"], "error": None}
    early = client.get("/elevenlabs/v1/dubbing/dub123/audio/es")
    assert early.status_code == 425 and early.json()["detail"]["status"] == "dubbing_not_ready"

    job["status"] = "done"
    assert client.get("/elevenlabs/v1/dubbing/dub123").json()["status"] == "dubbed"
    done = client.get("/elevenlabs/v1/dubbing/dub123/audio/es")
    assert done.status_code == 200 and done.content == b"dubbed-video"

    job.update(status="failed", error="Translation failed")
    assert client.get("/elevenlabs/v1/dubbing/dub123").json()["error"] == "Translation failed"
    assert client.get("/elevenlabs/v1/dubbing/nope").status_code == 404


def test_ready_text_waits_for_a_sentence_or_a_long_run():
    take = _el().take_ready_text
    assert take("Hello there. How are", flush=False) == (["Hello there."], "How are")
    assert take("No end yet", flush=False) == ([], "No end yet")
    assert take("Tail", flush=True) == (["Tail"], "")
    assert take('She said "hi." Then left. ', flush=False) == (['She said "hi."', "Then left."], "")
    long = "word " * 80
    ready, rest = take(long, flush=False)
    assert ready and all(len(r) <= _el()._WS_MAX_BUFFER for r in ready)
    assert len(rest) <= _el()._WS_MAX_BUFFER


def test_stream_input_speaks_each_sentence_then_finishes(client, engine):
    import base64

    path = "/elevenlabs/v1/text-to-speech/default/stream-input?output_format=pcm_16000"
    with client.websocket_connect(path) as ws:
        ws.send_json({"text": " ", "voice_settings": {"speed": 1.1}})
        ws.send_json({"text": "Hello there. How are"})
        first = ws.receive_json()
        assert len(base64.b64decode(first["audio"])) == 16000 * 2 * SECONDS
        assert "".join(first["normalizedAlignment"]["chars"]) == "Hello there."
        ws.send_json({"text": " you"})
        ws.send_json({"text": ""})
        second = ws.receive_json()
        assert "".join(second["normalizedAlignment"]["chars"]) == "How are you"
        assert ws.receive_json() == {"isFinal": True}
    assert engine.calls[0]["speed"] == pytest.approx(1.1)
    assert len(engine.calls) == 2


def test_stream_input_rejects_a_bad_format(client):
    with client.websocket_connect("/elevenlabs/v1/text-to-speech/default/stream-input?output_format=flac_1") as ws:
        assert ws.receive_json()["error"] == "invalid_output_format"


@pytest.fixture()
def history(profile):
    """Three takes in generation history (two by the profile), plus one whose
    audio is gone."""
    import soundfile as sf
    from core.config import OUTPUTS_DIR
    from core.db import db_conn

    pid, _ = profile
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    ids, files = [], []
    with db_conn() as conn:
        for i, (voice, has_audio) in enumerate([(pid, True), (None, True), (pid, True), (pid, False)]):
            hid = f"el-h-{uuid.uuid4().hex[:8]}"
            name = f"{hid}.wav"
            if has_audio:
                sf.write(os.path.join(OUTPUTS_DIR, name), np.zeros(240, dtype=np.float32), 24000)
                files.append(os.path.join(OUTPUTS_DIR, name))
            conn.execute(
                "INSERT INTO generation_history (id, text, mode, profile_id, audio_path, created_at) "
                "VALUES (?, ?, 'clone', ?, ?, ?)",
                (hid, f"take {i}", voice, name, 4_000_000_000 + i),
            )
            ids.append(hid)
    yield ids
    with db_conn() as conn:
        conn.executemany("DELETE FROM generation_history WHERE id=?", [(i,) for i in ids])
    for f in files:
        if os.path.exists(f):
            os.unlink(f)


def test_history_lists_pages_and_serves_takes(client, monkeypatch, profile, history):
    pid, name = profile
    first, second, third, gone = history
    page = client.get("/elevenlabs/v1/history", params={"page_size": 2}).json()
    newest = [item["history_item_id"] for item in page["history"]]
    assert newest == [third, second] and page["has_more"] is True
    assert gone not in newest  # audio missing on disk

    rest = client.get("/elevenlabs/v1/history", params={
        "page_size": 2, "start_after_history_item_id": page["last_history_item_id"]}).json()
    assert rest["history"][0]["history_item_id"] == first

    mine = client.get("/elevenlabs/v1/history", params={"voice_id": pid}).json()["history"]
    assert [item["history_item_id"] for item in mine][:2] == [third, first]
    assert mine[0]["voice_name"] == name and mine[0]["text"] == "take 2"

    audio = client.get(f"/elevenlabs/v1/history/{third}/audio")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert client.get(f"/elevenlabs/v1/history/{gone}").status_code == 404

    gen = importlib.import_module("api.routers.generation")
    deleted = []
    monkeypatch.setattr(gen, "delete_single_history", lambda hid: deleted.append(hid))
    assert client.delete(f"/elevenlabs/v1/history/{third}").status_code == 200
    assert deleted == [third]
