"""Tests for GET /dub/onsets/{job_id} — timeline snap-to-onset data (#280, item 3).

The route prefers the Demucs vocals track, falls back to the mixed audio,
caches the result as onsets.json in the job dir, and recomputes when the
source audio is newer than the cache.
"""
from __future__ import annotations

import asyncio
import json
import os

import numpy as np
import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")

SR = 16000


def _write_wav(path, lead_silence_s=1.0, speech_s=1.0):
    import soundfile as sf
    t = np.arange(int(speech_s * SR)) / SR
    tone = (0.5 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    audio = np.concatenate([np.zeros(int(lead_silence_s * SR), dtype=np.float32), tone])
    sf.write(str(path), audio, SR)
    return audio


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    """A fake dub job dir + monkeypatched _get_job / DUB_DIR."""
    from api.routers import dub_export

    job_id = "job-onsets"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    job = {"id": job_id}

    monkeypatch.setattr(dub_export, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dub_export, "_get_job", lambda jid: job if jid == job_id else None)
    return {"job_id": job_id, "job_dir": job_dir, "job": job, "module": dub_export}


def _call(module, job_id):
    return asyncio.run(module.dub_get_onsets(job_id))


def test_404_when_job_missing(job_env):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _call(job_env["module"], "nope")
    assert exc.value.status_code == 404


def test_404_when_no_audio_available(job_env):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _call(job_env["module"], job_env["job_id"])
    assert exc.value.status_code == 404


# On CI-Linux (never reproduced on macOS) something in this test's call chain
# flips torch's default dtype to float16 and leaks it into later tests. The
# fixture save/restores the dtype and logs the setter's captured stack trace
# so the CI log names the culprit call chain (see conftest.py).
@pytest.mark.usefixtures("torch_dtype_isolation")
def test_prefers_vocals_over_mix(job_env):
    vocals = job_env["job_dir"] / "vocals.wav"
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(vocals, lead_silence_s=2.0)
    _write_wav(mix, lead_silence_s=0.5)
    job_env["job"]["vocals_path"] = str(vocals)
    job_env["job"]["audio_path"] = str(mix)

    res = _call(job_env["module"], job_env["job_id"])
    assert res["source"] == "vocals"
    assert len(res["onsets"]) == 1
    assert res["onsets"][0] == pytest.approx(2.0, abs=0.06)
    assert len(res["peaks"]) > 100
    assert max(res["peaks"]) > 0.4


def test_falls_back_to_mix_when_vocals_missing(job_env):
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(mix, lead_silence_s=0.5)
    job_env["job"]["vocals_path"] = str(job_env["job_dir"] / "gone.wav")  # doesn't exist
    job_env["job"]["audio_path"] = str(mix)

    res = _call(job_env["module"], job_env["job_id"])
    assert res["source"] == "mix"
    assert res["onsets"][0] == pytest.approx(0.5, abs=0.06)


def test_caches_onsets_json_and_reuses_it(job_env):
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(mix, lead_silence_s=1.0)
    job_env["job"]["audio_path"] = str(mix)

    res1 = _call(job_env["module"], job_env["job_id"])
    cache = job_env["job_dir"] / "onsets.json"
    assert cache.exists()
    assert json.loads(cache.read_text()) == res1

    # Poison the cache with a sentinel — the route must serve it verbatim
    # (i.e. no recompute) while the audio mtime is older than the cache.
    # Preserve the current analysis version: this tests reuse, not migration.
    sentinel = {**res1, "onsets": [99.9], "peaks": [0.25, 0.5]}
    cache.write_text(json.dumps(sentinel))
    os.utime(str(mix), (0, 0))  # audio much older than cache
    res2 = _call(job_env["module"], job_env["job_id"])
    assert res2 == sentinel



@pytest.mark.parametrize("version", [None, "mean_v0"])
def test_fresh_legacy_channel_analysis_cache_is_recomputed(job_env, version):
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(mix, lead_silence_s=1.0)
    job_env["job"]["audio_path"] = str(mix)
    cache = job_env["job_dir"] / "onsets.json"
    legacy = {"onsets": [99.9], "peaks": [0.0], "source": "mix"}
    if version is not None:
        legacy["channel_analysis"] = version
    cache.write_text(json.dumps(legacy))
    os.utime(str(mix), (0, 0))  # cache freshness alone must not preserve old analysis

    result = _call(job_env["module"], job_env["job_id"])
    assert result["onsets"][0] == pytest.approx(1.0, abs=0.06)
    assert max(result["peaks"]) > 0.4
    assert result["channel_analysis"] == "max_abs_v1"
    assert json.loads(cache.read_text()) == result


def test_recomputes_when_audio_newer_than_cache(job_env):
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(mix, lead_silence_s=1.0)
    job_env["job"]["audio_path"] = str(mix)
    cache = job_env["job_dir"] / "onsets.json"
    cache.write_text(json.dumps({"onsets": [99.9], "source": "mix"}))
    os.utime(str(cache), (0, 0))  # cache much older than audio

    res = _call(job_env["module"], job_env["job_id"])
    assert res["onsets"][0] == pytest.approx(1.0, abs=0.06)
    # Fresh cache written back.
    assert json.loads(cache.read_text()) == res


def test_corrupt_cache_recomputes(job_env):
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(mix, lead_silence_s=1.0)
    job_env["job"]["audio_path"] = str(mix)
    cache = job_env["job_dir"] / "onsets.json"
    cache.write_text("{not json")
    # Make the corrupt cache look fresh so only the parse guard saves us.
    os.utime(str(mix), (0, 0))

    res = _call(job_env["module"], job_env["job_id"])
    assert res["onsets"][0] == pytest.approx(1.0, abs=0.06)


def test_traversal_job_id_rejected(job_env, monkeypatch):
    from fastapi import HTTPException
    module = job_env["module"]
    # Pretend every job id resolves so the realpath containment guard is the
    # only thing standing between a traversal id and the filesystem.
    mix = job_env["job_dir"] / "audio.wav"
    _write_wav(mix)
    monkeypatch.setattr(module, "_get_job", lambda jid: {"id": jid, "audio_path": str(mix)})
    with pytest.raises(HTTPException) as exc:
        _call(module, "../../etc")
    assert exc.value.status_code == 400
