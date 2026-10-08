import os
import wave


def test_pruning_keeps_root_index_so_relocated_legacy_cache_still_hits(tmp_path, monkeypatch):
    from api.routers import audiobook
    from services import longform_render as lr
    from services.audiobook import Span
    from core import config
    cache = tmp_path / "cache"
    cache.mkdir()
    old, new = tmp_path / "old" / "voices", tmp_path / "new" / "voices"
    lr.remember_voices_root(str(cache), str(old))
    span = Span(voice_id="v", text="Hello.")
    signature = lambda path: f"{path}|hello|None|None"
    store = lr.SegmentCache(str(cache), sample_rate=24000, engine_id="test",
                            voice_sig={"v": signature(old / "v.wav")})
    legacy = store._path(span)
    os.makedirs(os.path.dirname(legacy))
    with wave.open(legacy, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 100)
    extra = cache / "old.wav"
    extra.write_bytes(b"evict" * 100)
    index = cache / lr.VOICES_ROOTS_FILE
    os.utime(index, (1, 1))
    os.utime(extra, (2, 2))
    os.utime(legacy, (3, 3))
    budget = index.stat().st_size + os.path.getsize(legacy)
    remaining, removed = lr.prune_cache_dir(str(cache), max_bytes=budget)
    assert remaining == budget and removed == 1 and not extra.exists()
    assert index.exists() and os.path.exists(legacy)
    monkeypatch.setattr(config, "VOICES_DIR", str(new))
    roots = lr.remember_voices_root(str(cache), str(new))
    old_refs = audiobook._legacy_ref_audios(str(new / "v.wav"), roots)
    relocated = lr.SegmentCache(str(cache), sample_rate=24000, engine_id="test",
        voice_sig={"v": "voices:v.wav|hello|None|None"},
        legacy_voice_sigs=[{"v": signature(path)} for path in old_refs])
    recovered = relocated._existing_path(span)
    assert recovered == relocated._path(span) and lr.wav_is_complete(recovered)
    assert not os.path.exists(legacy)


def test_metadata_only_budget_is_best_effort(tmp_path):
    from services import longform_render as lr
    metadata = tmp_path / "voices_roots.json"
    metadata.write_text('["/old/voices"]')
    size = metadata.stat().st_size
    assert lr.prune_cache_dir(str(tmp_path), max_bytes=0) == (size, 0)
    assert metadata.exists()


def test_interrupted_audio_files_still_share_the_budget(tmp_path):
    from services import longform_render as lr
    index = tmp_path / lr.VOICES_ROOTS_FILE
    index.write_text('["/old/voices"]')
    inputs = tmp_path / lr.CHAPTER_INPUTS_SUBDIR
    inputs.mkdir()
    (inputs / "chapter.json").write_text('{"inputs": []}')
    partial = tmp_path / "chapter.wav.part"
    partial.write_bytes(b"partial" * 100)
    os.utime(index, (1, 1))
    os.utime(partial, (2, 2))
    metadata_size = index.stat().st_size + (inputs / "chapter.json").stat().st_size
    assert lr.prune_cache_dir(str(tmp_path), max_bytes=metadata_size) == (metadata_size, 1)
    assert not partial.exists() and index.exists() and (inputs / "chapter.json").exists()
