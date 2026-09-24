"""MCP output mode + the base-path boundary.

Pure helpers, no MCP SDK needed: how generate_speech hands audio back
(OMNIVOICE_MCP_OUTPUT_MODE) and how path-shaped inputs are confined to
OMNIVOICE_MCP_BASE_PATH. The tool closures themselves are exercised through
the shape helpers they delegate to, so these run without a backend.
"""
import base64
import os
import asyncio
import io
import math
import struct
import wave

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest


# ── output mode ─────────────────────────────────────────────────────────────

def test_output_mode_defaults_to_resources(monkeypatch):
    from mcp_server import _output_mode
    monkeypatch.delenv("OMNIVOICE_MCP_OUTPUT_MODE", raising=False)
    assert _output_mode() == "resources"


@pytest.mark.parametrize("raw,expected", [
    ("files", "files"),
    ("FILES", "files"),
    (" both ", "both"),
    ("resources", "resources"),
    ("banana", "resources"),   # unrecognized falls back, never fails the tool
])
def test_output_mode_parses_and_falls_back(monkeypatch, raw, expected):
    from mcp_server import _output_mode
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", raw)
    assert _output_mode() == expected


# ── base path boundary ──────────────────────────────────────────────────────

def test_base_path_none_when_unset(monkeypatch):
    from mcp_server import _base_path
    monkeypatch.delenv("OMNIVOICE_MCP_BASE_PATH", raising=False)
    assert _base_path() is None


def test_resolve_refuses_paths_without_a_base(monkeypatch):
    from mcp_server import _resolve_under_base
    monkeypatch.delenv("OMNIVOICE_MCP_BASE_PATH", raising=False)
    with pytest.raises(ValueError, match="OMNIVOICE_MCP_BASE_PATH is not set"):
        _resolve_under_base("clip.wav")


def test_resolve_accepts_relative_and_absolute_inside(monkeypatch, tmp_path):
    from mcp_server import _resolve_under_base
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    inside = tmp_path / "sub" / "clip.wav"
    assert _resolve_under_base("sub/clip.wav") == os.path.realpath(str(inside))
    assert _resolve_under_base(str(inside)) == os.path.realpath(str(inside))


def test_resolve_refuses_escape(monkeypatch, tmp_path):
    from mcp_server import _resolve_under_base
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(base))
    with pytest.raises(ValueError, match="outside OMNIVOICE_MCP_BASE_PATH"):
        _resolve_under_base("../secret.wav")
    with pytest.raises(ValueError, match="outside OMNIVOICE_MCP_BASE_PATH"):
        _resolve_under_base(str(tmp_path / "secret.wav"))


# ── input lanes ─────────────────────────────────────────────────────────────

def test_read_input_requires_exactly_one_lane():
    from mcp_server import _read_input_audio
    raw, err = _read_input_audio(None, None)
    assert raw is None and "exactly one" in err
    raw, err = _read_input_audio("QUJD", "x.wav")
    assert raw is None and "exactly one" in err


def test_read_input_path_lane_reads_inside_base(monkeypatch, tmp_path):
    from mcp_server import _read_input_audio
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    (tmp_path / "clip.wav").write_bytes(b"RIFFxxxxWAVE")
    raw, err = _read_input_audio(None, "clip.wav")
    assert err is None and raw == b"RIFFxxxxWAVE"


def test_read_input_path_lane_reports_missing_and_escaped(monkeypatch, tmp_path):
    from mcp_server import _read_input_audio
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    raw, err = _read_input_audio(None, "nope.wav")
    assert raw is None and "no such file" in err
    raw, err = _read_input_audio(None, "../nope.wav")
    assert raw is None and "outside" in err


def test_read_input_path_lane_refused_without_base(monkeypatch, tmp_path):
    from mcp_server import _read_input_audio
    monkeypatch.delenv("OMNIVOICE_MCP_BASE_PATH", raising=False)
    raw, err = _read_input_audio(None, str(tmp_path / "clip.wav"))
    assert raw is None and "is not set" in err


def test_read_input_base64_lane_keeps_data_uri_tolerance_and_labels():
    from mcp_server import _read_input_audio
    body = base64.b64encode(b"RIFFxxxxWAVE").decode()
    raw, err = _read_input_audio(f"data:audio/wav;base64,{body}", None)
    assert err is None and raw == b"RIFFxxxxWAVE"
    raw, err = _read_input_audio("not!!base64", None, label="ref_audio_base64")
    assert raw is None and err == "ref_audio_base64 is not valid base64"


def test_base64_limit_applies_to_decoded_bytes(monkeypatch):
    import mcp_server

    monkeypatch.setattr(mcp_server, "_MAX_INPUT_BYTES", 3)
    encoded = base64.b64encode(b"abc").decode()
    assert len(encoded) > mcp_server._MAX_INPUT_BYTES
    raw, err = mcp_server._read_input_audio(encoded, None)
    assert err is None and raw == b"abc"

    oversized = base64.b64encode(b"abcd").decode()
    raw, err = mcp_server._read_input_audio(oversized, None)
    assert raw is None and err == "audio exceeds 200 MB limit"


def test_oversized_base64_is_rejected_before_decode(monkeypatch):
    import mcp_server

    monkeypatch.setattr(mcp_server, "_MAX_INPUT_BYTES", 3)

    def fail_decode(_value):  # pragma: no cover - must short-circuit first
        raise AssertionError("oversized base64 reached the decoder")

    monkeypatch.setattr(mcp_server, "_decode_ref_audio", fail_decode)
    oversized = base64.b64encode(b"abcd").decode()
    raw, err = mcp_server._read_input_audio(oversized, None)
    assert raw is None and err == "audio exceeds 200 MB limit"


def test_concurrent_parent_replacement_cannot_escape_base(
    monkeypatch, tmp_path
):
    import mcp_server

    base = tmp_path / "base"
    lane = base / "lane"
    lane.mkdir(parents=True)
    (lane / "clip.wav").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "clip.wav").write_bytes(b"secret")
    parked = base / "parked"
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(base))
    real_resolve = mcp_server._resolve_under_base

    def replace_parent_after_resolution(path):
        resolved = real_resolve(path)
        lane.rename(parked)
        try:
            lane.symlink_to(outside, target_is_directory=True)
        except OSError as exc:  # Windows without Developer Mode/admin rights
            pytest.skip(f"directory symlinks unavailable: {exc}")
        return resolved

    monkeypatch.setattr(
        mcp_server, "_resolve_under_base", replace_parent_after_resolution
    )
    raw, err = mcp_server._read_input_audio(None, "lane/clip.wav")
    assert raw is None
    assert "outside" in err or "safely read" in err


# ── the generate_speech reply shape ─────────────────────────────────────────

def test_speech_result_resources_is_the_original_contract(monkeypatch):
    from mcp_server import _speech_result
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "resources")
    out = asyncio.run(_speech_result("ab12cd34", 1.5, 2.0, b"RIFF", "http://localhost:3900"))
    assert out["wav_base64"] == base64.b64encode(b"RIFF").decode()
    assert "audio_url" not in out and "output_path" not in out
    assert out["output_mode"] == "resources"


def test_speech_result_files_returns_url_and_writes_under_base(monkeypatch, tmp_path):
    from mcp_server import _speech_result
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    out = asyncio.run(_speech_result("ab12cd34", 1.5, 2.0, b"RIFF", "http://localhost:3900/"))
    assert out["audio_url"] == "http://localhost:3900/audio/ab12cd34.wav"
    assert "wav_base64" not in out
    written = out["output_path"]
    assert os.path.dirname(os.path.realpath(written)) == os.path.realpath(str(tmp_path))
    with open(written, "rb") as f:
        assert f.read() == b"RIFF"


def test_speech_result_rejects_traversal_audio_id(monkeypatch, tmp_path):
    from mcp_server import _speech_result

    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="invalid X-Audio-Id"):
        asyncio.run(_speech_result("../../escape", 1.5, 2.0, b"RIFF", "http://localhost:3900"))
    assert not (tmp_path.parent / "escape.wav").exists()


def test_speech_result_does_not_follow_existing_output_symlink(
    monkeypatch, tmp_path
):
    from mcp_server import _speech_result

    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(b"keep")
    link = tmp_path / "ab12cd34.wav"
    try:
        link.symlink_to(outside)
    except OSError as exc:  # Windows without Developer Mode/admin rights
        pytest.skip(f"file symlinks unavailable: {exc}")
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    with pytest.raises(ValueError, match="outside OMNIVOICE_MCP_BASE_PATH"):
        asyncio.run(_speech_result("ab12cd34", 1.5, 2.0, b"replace", "http://localhost:3900"))
    assert outside.read_bytes() == b"keep"


def test_speech_result_files_without_base_is_url_only_with_a_note(monkeypatch):
    from mcp_server import _speech_result
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.delenv("OMNIVOICE_MCP_BASE_PATH", raising=False)
    out = asyncio.run(_speech_result("ab12cd34", 1.5, 2.0, b"RIFF", "http://localhost:3900"))
    assert out["audio_url"].endswith("/audio/ab12cd34.wav")
    assert "output_path" not in out and "OMNIVOICE_MCP_BASE_PATH" in out["note"]
    assert "wav_base64" not in out


def test_speech_result_both_carries_everything(monkeypatch, tmp_path):
    from mcp_server import _speech_result
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "both")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    out = asyncio.run(_speech_result("ab12cd34", "?", "?", b"RIFF", "http://localhost:3900"))
    assert {"wav_base64", "audio_url", "output_path"} <= set(out)
    assert out["generation_time_s"] == "?"   # header text passes through untouched


def _sample_wav():
    samples = [int(8000 * math.sin(i * 2 * math.pi * 440 / 24000)) for i in range(12000)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["ogg", "opus"])
def test_files_format_is_encoded_opus_and_url_serves_same_codec(monkeypatch, tmp_path, fmt):
    from fastapi.testclient import TestClient
    from services.ffmpeg_utils import find_ffmpeg
    from api.routers import generation
    from main import app
    from mcp_server import _speech_result

    if not find_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    raw = _sample_wav()
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path / "mcp"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(outputs))
    (outputs / "ab12cd34.wav").write_bytes(raw)

    result = asyncio.run(_speech_result("ab12cd34", 1.5, 0.5, raw, "http://testserver", fmt))
    saved = (tmp_path / "mcp" / f"ab12cd34.{fmt}").read_bytes()
    assert result["output_path"] == str(tmp_path / "mcp" / f"ab12cd34.{fmt}")
    assert result["audio_url"] == f"http://testserver/audio/ab12cd34.{fmt}"
    assert result["format"] == fmt
    assert saved[:4] == b"OggS" and b"OpusHead" in saved[:64]
    assert len(saved) < len(raw) / 2
    response = TestClient(app).get(f"/audio/ab12cd34.{fmt}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/ogg"
    assert response.content[:4] == b"OggS" and b"OpusHead" in response.content[:64]
    monkeypatch.delenv("OMNIVOICE_MCP_BASE_PATH")
    url_only = asyncio.run(_speech_result("ab12cd34", 1.5, 0.5, raw, "http://testserver", fmt))
    assert url_only["audio_url"] == result["audio_url"]
    assert "output_path" not in url_only
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "both")
    both = asyncio.run(_speech_result("ab12cd35", 1.5, 0.5, raw, "http://testserver", fmt))
    assert base64.b64decode(both["wav_base64"]) == raw
    assert both["audio_url"].endswith(f".{fmt}")


def test_compressed_format_requires_files_or_both_and_rejects_unknown(monkeypatch):
    from mcp_server import _speech_result

    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "resources")
    with pytest.raises(ValueError, match="files or both"):
        asyncio.run(_speech_result("ab12cd34", 1, 1, b"RIFF", "http://localhost", "opus"))
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    with pytest.raises(ValueError, match="unsupported speech format"):
        asyncio.run(_speech_result("ab12cd34", 1, 1, b"RIFF", "http://localhost", "mp3"))


def test_opus_conversion_missing_ffmpeg_does_not_write_mislabelled_wav(monkeypatch, tmp_path):
    from mcp_server import _speech_result
    from services import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: None)
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="requires ffmpeg"):
        asyncio.run(_speech_result("ab12cd34", 1, 1, _sample_wav(), "http://localhost", "opus"))
    assert not (tmp_path / "ab12cd34.opus").exists()


def test_opus_encoder_failure_does_not_create_file(monkeypatch, tmp_path):
    from mcp_server import _speech_result
    from services import ffmpeg_utils

    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")

    async def failed_encode(_cmd, **_kwargs):
        return 1, b"", b"Unknown encoder 'libopus'"

    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", failed_encode)
    monkeypatch.setenv("OMNIVOICE_MCP_OUTPUT_MODE", "files")
    monkeypatch.setenv("OMNIVOICE_MCP_BASE_PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="Unknown encoder"):
        asyncio.run(_speech_result("ab12cd34", 1, 1, _sample_wav(), "http://localhost", "opus"))
    assert not (tmp_path / "ab12cd34.opus").exists()


def test_opus_url_missing_render_is_a_404(monkeypatch, tmp_path):
    from api.routers import generation
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(generation.router)
    assert TestClient(app).get("/audio/ab12cd34.opus").status_code == 404
    assert TestClient(app).get("/audio/not-a-render.ogg").status_code == 404


def test_opus_url_encoder_timeout_is_service_unavailable(monkeypatch, tmp_path):
    from api.routers import generation
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services import audio_io

    (tmp_path / "ab12cd34.wav").write_bytes(_sample_wav())
    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(tmp_path))

    async def timeout(_wav):
        raise asyncio.TimeoutError

    monkeypatch.setattr(audio_io, "encode_ogg_opus", timeout)
    app = FastAPI()
    app.include_router(generation.router)
    response = TestClient(app).get("/audio/ab12cd34.opus")
    assert response.status_code == 503
    assert "timed out" in response.json()["detail"]


def test_opus_url_does_not_follow_render_symlink_outside_outputs(monkeypatch, tmp_path):
    from api.routers import generation
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    private = tmp_path / "private.wav"
    private.write_bytes(_sample_wav())
    try:
        (outputs / "ab12cd34.wav").symlink_to(private)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(outputs))
    app = FastAPI()
    app.include_router(generation.router)
    assert TestClient(app).get("/audio/ab12cd34.opus").status_code == 404


def test_opus_url_reuses_encoded_audio_until_wav_changes(monkeypatch, tmp_path):
    from api.routers import generation
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services import audio_io
    from services.ffmpeg_utils import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    wav = tmp_path / "ab12cd34.wav"
    raw = _sample_wav()
    wav.write_bytes(raw)
    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(tmp_path))
    real_encode = audio_io.encode_ogg_opus
    calls = []

    async def counted_encode(value):
        calls.append(os.fspath(value))
        return await real_encode(value)

    monkeypatch.setattr(audio_io, "encode_ogg_opus", counted_encode)
    app = FastAPI()
    app.include_router(generation.router)
    with TestClient(app) as client:
        first = client.get("/audio/ab12cd34.opus")
        assert first.status_code == 200 and first.content.startswith(b"OggS")
        assert client.get("/audio/ab12cd34.opus").content == first.content
        assert client.get("/audio/ab12cd34.ogg").content == first.content
        assert calls == [str(wav)]
        altered = raw[:44] + bytes(len(raw) - 44)
        wav.write_bytes(altered)
        info = wav.stat()
        os.utime(wav, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        changed = client.get("/audio/ab12cd34.opus")
        assert changed.status_code == 200 and changed.content != first.content
        assert calls == [str(wav), str(wav)]
        assert generation._ogg_cache_bytes <= generation._OGG_CACHE_LIMIT


def test_slow_opus_encode_does_not_block_other_renders(monkeypatch, tmp_path):
    import httpx
    from api.routers import generation
    from fastapi import FastAPI
    from services import audio_io
    from services.ffmpeg_utils import find_ffmpeg

    if not find_ffmpeg():
        pytest.skip("ffmpeg is not installed")
    for audio_id in ("ab12cd34", "ab12cd35", "ab12cd36"):
        (tmp_path / f"{audio_id}.wav").write_bytes(_sample_wav())
    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(tmp_path))
    real_encode = audio_io.encode_ogg_opus
    started = asyncio.Event()
    release = asyncio.Event()

    async def paused_encode(value):
        if os.fspath(value).endswith("ab12cd35.wav"):
            started.set()
            await release.wait()
        return await real_encode(value)

    monkeypatch.setattr(audio_io, "encode_ogg_opus", paused_encode)
    app = FastAPI()
    app.include_router(generation.router)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            cached = await client.get("/audio/ab12cd34.opus")
            assert cached.status_code == 200 and cached.content.startswith(b"OggS")
            slow = asyncio.create_task(client.get("/audio/ab12cd35.opus"))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                hit = await asyncio.wait_for(client.get("/audio/ab12cd34.opus"), timeout=3)
                other = await asyncio.wait_for(client.get("/audio/ab12cd36.opus"), timeout=5)
                assert hit.content == cached.content
                assert other.status_code == 200 and other.content.startswith(b"OggS")
            finally:
                release.set()
                assert (await asyncio.wait_for(slow, timeout=10)).status_code == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("raw,expected", [
    (None, 120.0),
    ("600", 600.0),
    ("0", 120.0),        # non-positive falls back
    ("soon", 120.0),     # garbage falls back, never fails the tool
])
def test_post_timeout_reads_env_with_fallbacks(monkeypatch, raw, expected):
    from mcp_server import _post_timeout_s
    if raw is None:
        monkeypatch.delenv("OMNIVOICE_MCP_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_MCP_TIMEOUT_S", raw)
    assert _post_timeout_s() == expected


def test_maybe_number_keeps_header_text_honest():
    from mcp_server import _maybe_number
    assert _maybe_number("1.25") == 1.25
    assert _maybe_number("?") == "?"
    assert _maybe_number(None) is None
