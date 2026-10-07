"""Re-locking a voice to another take re-keys its longform caches (#2535)."""
import asyncio
import os

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from api.routers.audiobook import _render_chapter_cached  # noqa: E402
from services.audiobook import Chapter, Span  # noqa: E402


def _render(cache_dir, calls, tag, resolve):
    def synth(text, voice_id, speed=None):
        calls.append(tag)
        return torch.full((2400,), 0.1)

    chapter = Chapter(title="C", spans=[Span(voice_id=None, text="Hello.", pause_ms_after=0)])
    return _render_chapter_cached(chapter, synth, 24000, "eng", resolve, str(cache_dir))


@pytest.fixture
def locked_profile(tmp_path, monkeypatch):
    from api.routers import profiles
    from core import config, db

    voices = tmp_path / "voices"
    outputs = tmp_path / "outputs"
    voices.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    monkeypatch.setattr(config, "VOICES_DIR", str(voices))
    db.init_db()
    with db.db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles(id,name) VALUES('voice','V')")
        for name, body in (("take1.wav", b"first-take"), ("take2.wav", b"second-take")):
            (outputs / name).write_bytes(body)
            conn.execute(
                "INSERT INTO generation_history(id, text, audio_path) VALUES(?, 'same text', ?)",
                (name[:5], name),
            )
    return voices


def test_relock_with_same_text_and_seed_re_keys_the_longform_cache(locked_profile, tmp_path):
    from api.routers import audiobook, profiles

    cache = tmp_path / "cache"
    cache.mkdir()
    calls: list = []

    def render(tag):
        return _render(cache, calls, tag, lambda _v: audiobook._resolve_voice("voice"))

    asyncio.run(profiles.lock_profile("voice", history_id="take1", seed=7))
    first_path, _, cached, _ = render("one")
    assert not cached

    asyncio.run(profiles.lock_profile("voice", history_id="take2", seed=7))
    second_path, _, cached, _ = render("two")
    assert not cached and calls == ["one", "two"]
    assert second_path != first_path

    # Inner segment layer: drop only the chapter WAV, the old take must not replay.
    os.remove(second_path)
    render("three")
    assert calls == ["one", "two"], "segment cached for the NEW take only"

    # The superseded take is swept once retired; only the current one remains.
    profiles.sweep_retired_voice_files(grace_s=0)
    assert len([p for p in os.listdir(locked_profile) if "locked" in p]) == 1


def test_relock_and_unlock_keep_the_take_a_running_render_still_reads(locked_profile):
    """A long-form render resolves the voice once and re-reads the reference
    for every segment; re-locking or unlocking mid-render must not delete it."""
    from api.routers import audiobook, profiles

    asyncio.run(profiles.lock_profile("voice", history_id="take1", seed=7))
    in_flight = audiobook._resolve_voice("voice")["ref_audio"]

    asyncio.run(profiles.lock_profile("voice", history_id="take2", seed=7))
    assert open(in_flight, "rb").read() == b"first-take"
    second = audiobook._resolve_voice("voice")["ref_audio"]

    asyncio.run(profiles.unlock_profile("voice"))
    assert open(second, "rb").read() == b"second-take"

    # Inside the grace period nothing goes; after it, both unreferenced takes do.
    assert profiles.sweep_retired_voice_files() == 0
    assert os.path.exists(in_flight) and os.path.exists(second)
    assert profiles.sweep_retired_voice_files(grace_s=0) == 2
    assert not os.path.exists(in_flight) and not os.path.exists(second)
    assert os.listdir(locked_profile / ".retired") == []


def test_sweep_keeps_a_retired_file_a_profile_references_again(locked_profile):
    from api.routers import profiles
    from core import db

    (locked_profile / "voice_locked.wav").write_bytes(b"legacy")  # pre-#2535 name
    with db.db_conn() as conn:
        conn.execute(
            "UPDATE voice_profiles SET locked_audio_path='voice_locked.wav', is_locked=1"
        )
    profiles._retire_voice_file("voice_locked.wav")
    assert profiles.sweep_retired_voice_files(grace_s=0) == 0
    assert (locked_profile / "voice_locked.wav").read_bytes() == b"legacy"
    assert os.listdir(locked_profile / ".retired") == []


def test_sweep_keeps_a_retired_take_an_active_render_holds(locked_profile):
    """Age alone must not decide: a render running past the grace period still
    reads its take, so the sweep keeps it until the render releases it."""
    from api.routers import audiobook, profiles
    from core import voice_leases

    asyncio.run(profiles.lock_profile("voice", history_id="take1", seed=7))
    with voice_leases.VoiceFileLease() as lease:
        in_flight = lease.hold(audiobook._resolve_voice("voice")["ref_audio"])
        asyncio.run(profiles.lock_profile("voice", history_id="take2", seed=7))

        assert profiles.sweep_retired_voice_files(grace_s=0) == 0
        assert open(in_flight, "rb").read() == b"first-take"
        assert os.listdir(locked_profile / ".retired"), "marker kept for a later sweep"

    assert profiles.sweep_retired_voice_files(grace_s=0) == 1
    assert not os.path.exists(in_flight)
    assert os.listdir(locked_profile / ".retired") == []


def test_voice_leases_count_each_holder_and_release_once(tmp_path):
    from core import voice_leases

    path = str(tmp_path / "take.wav")
    a, b = voice_leases.VoiceFileLease(), voice_leases.VoiceFileLease()
    a.hold(path)
    a.hold(path)  # one lease holds a path once
    b.hold(path)
    a.release()
    a.release()
    assert voice_leases.in_use(path)
    b.release()
    assert not voice_leases.in_use(path)
    a.hold(path)  # a released lease cannot pin a file again
    assert not voice_leases.in_use(path)


def test_longform_render_holds_its_take_until_the_render_ends(locked_profile, tmp_path, monkeypatch):
    """Through the real chapter renderer: a re-lock plus an overdue sweep in the
    middle of a book must leave the in-flight take; the job's end frees it."""
    import json

    from api.routers import audiobook, profiles
    from core import db, voice_leases
    from services.audiobook import AudiobookPlan, Chapter, Span

    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(tmp_path / "outputs"))
    # Hermetic: the mux after the chapters may fail; only the chapters matter.
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg-not-run")
    asyncio.run(profiles.lock_profile("voice", history_id="take1", seed=7))
    in_flight = audiobook._resolve_voice("voice")["ref_audio"]
    seen = []

    def synth(text, voice_id, speed=None):
        if not seen:  # first segment: the user re-locks; a day-late sweep runs
            with db.db_conn() as conn:
                conn.execute("UPDATE voice_profiles SET locked_audio_path='other.wav'")
            profiles._retire_voice_file(os.path.basename(in_flight))
            seen.append((profiles.sweep_retired_voice_files(grace_s=0), os.path.exists(in_flight)))
        return torch.zeros(2400)

    def build_synth(default_voice=None, language=None, opts=None, voice_map=None, lease=None):
        return {"mode": "generic", "resolve": lambda _v: {}, "engine_id": "stub",
                "synth": synth, "sample_rate": 24000}

    monkeypatch.setattr(audiobook, "_build_synth", build_synth)
    plan = AudiobookPlan(chapters=[
        Chapter(title=t, spans=[Span(voice_id=None, text=t)]) for t in ("One.", "Two.")
    ])

    async def run():
        return [json.loads(f[len("data:"):]) async for f in
                audiobook._render_longform_sse(plan, default_voice="voice")]

    events = asyncio.run(asyncio.wait_for(run(), timeout=120))
    assert events[0]["type"] == "started"
    assert seen == [(0, True)], "the render's take survived the overdue sweep"
    assert not voice_leases.in_use(in_flight)
    assert profiles.sweep_retired_voice_files(grace_s=0) == 1
    assert not os.path.exists(in_flight)


def test_batch_job_holds_its_voice_only_while_it_runs(tmp_path, monkeypatch):
    from api.routers import batch
    from core import voice_leases

    path = str(tmp_path / "take.wav")
    during = []

    async def leased(job_id, job, lease):
        lease.hold(path)
        during.append(voice_leases.in_use(path))
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(batch, "_run_batch_pipeline_leased", leased)
    with pytest.raises(RuntimeError):
        asyncio.run(batch._run_batch_pipeline("job", {}))
    assert during == [True] and not voice_leases.in_use(path)
