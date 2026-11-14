"""Dub budgets, caches and silence decisions follow the actual spoken text."""
import importlib

import pytest
import torch

from test_pronunciation_speech_routes import (  # noqa: F401
    spoken_dub, dub, fake_engine, saved_pronunciations, client,
)


@pytest.mark.parametrize("route", ["render", "preview"])
def test_dub_budget_uses_the_once_prepared_saved_respelling(
    spoken_dub, client, fake_engine, saved_pronunciations, monkeypatch, route,
):
    dub, _backend = spoken_dub
    db = importlib.import_module("core.db")
    expected = " ".join(["kittens"] * 100)
    with db.db_conn() as conn:
        conn.execute("UPDATE pronunciation_entries SET replacement=? "
                     "WHERE term='cats' AND profile_id='pron-voice'", (expected,))
    normalization = importlib.import_module("services.text_normalization")
    normalize = normalization.normalize_for_tts
    prepared = []

    def normalize_once(text, language):
        prepared.append(text)
        return normalize(text, language)

    monkeypatch.setattr(normalization, "normalize_for_tts", normalize_once)
    manager = importlib.import_module("services.model_manager")
    timeout = manager.generate_timeout_s
    budgeted = []

    def budget(text, **kwargs):
        budgeted.append(text)
        return timeout(text, **kwargs)

    monkeypatch.setattr(manager, "generate_timeout_s", budget)
    if route == "render":
        events = dub.run(segments=[dict(start=0, end=1, text="cats", profile_id="pron-voice")],
                         segment_ids=["long"], language="English")
        assert any(event["type"] == "done" for event in events), events
        assert dub.job["seg_natural_durs_by_lang"]["en"]["long"]["chars"] == len(expected)
    else:
        response = client.post("/dub/preview-segment/job", json={
            "text": "cats", "language": "English", "profile_id": "pron-voice", "duration": 1,
        })
        assert response.status_code == 200, response.text
    assert [text for text, _kwargs in fake_engine.calls] == [expected]
    assert budgeted == [expected]
    assert prepared == ["cats"]  # never reapply the kittens entry to a respelling


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("deletion", ["inline", "saved"])
def test_dub_whole_line_deletion_publishes_silence_and_a_current_fingerprint(
    spoken_dub, client, fake_engine, saved_pronunciations, monkeypatch, native, deletion,
):
    dub, backend = spoken_dub
    generate = backend.generate
    batches = []

    def generate_one(**kwargs):
        if not kwargs["text"].strip():
            fake_engine.calls.append((kwargs["text"], kwargs))
            return torch.zeros(1, 24000)
        return generate(**kwargs)

    def generate_batch(_self, texts, **_kwargs):
        batches.append(list(texts))
        return [torch.ones(1, 24000) * 0.1 if text.strip() else torch.zeros(1, 24000)
                for text in texts]

    monkeypatch.setattr(backend, "generate", generate_one)
    if native:
        monkeypatch.setattr(fake_engine, "generate_batch", generate_batch)
    segments = [dict(start=i, end=i + 1, text=text, profile_id="pron-voice")
                for i, text in enumerate(["label", "dogs", "birds"])]
    ids = ["delete", "dogs", "birds"]
    events = dub.run(segments=segments, segment_ids=ids, language="English")
    assert any(event["type"] == "done" for event in events), events
    previous_hash = dub.job["seg_hashes_by_lang"]["en"]["delete"]
    if deletion == "inline":
        segments[0]["text"] = "[[label|]]"
    else:
        db = importlib.import_module("core.db")
        with db.db_conn() as conn:
            conn.execute("INSERT INTO pronunciation_entries "
                         "(id,term,replacement,type,language,enabled,created_at,profile_id) "
                         "VALUES ('delete','label','','respelling','en',1,10,'pron-voice')")
    fake_engine.calls.clear()
    batches.clear()
    events = dub.run(segments=segments, segment_ids=ids, language="English")
    assert any(event["type"] == "done" for event in events), events
    actual = [text for batch in batches for text in batch] + [text for text, _kw in fake_engine.calls]
    assert actual == ["dogs", "birds"]
    from services.audio_io import load_audio

    silent, _ = load_audio(str(dub.path / "seg_en_delete.wav"))
    assert silent.shape[-1] == 24000 and not torch.count_nonzero(silent)
    mixed, _ = load_audio(str(dub.path / "dubbed_en.wav"))
    assert not torch.count_nonzero(mixed[..., :24000])
    assert torch.count_nonzero(mixed[..., 24000:72000])
    stored = dub.job["seg_hashes_by_lang"]["en"]
    assert stored["delete"] != previous_hash
    assert dub.job["seg_natural_durs_by_lang"]["en"]["delete"] == {"chars": 0, "dur": 0.0}
    response = client.post("/tools/incremental", json={
        "segments": [dict(segment, id=sid) for sid, segment in zip(ids, segments)],
        "stored_hashes": stored, "lang": "en", "language": "English",
    })
    assert response.status_code == 200, response.text
    assert response.json()["fresh"] == ids


def test_dub_preview_whole_line_deletion_skips_inference(
    spoken_dub, client, fake_engine, saved_pronunciations,
):
    response = client.post("/dub/preview-segment/job", json={
        "text": "[[label|]]", "language": "English", "profile_id": "pron-voice", "duration": 1,
    })
    assert response.status_code == 200, response.text
    assert not fake_engine.calls


def test_intentional_silence_staging_warning_redacts_error_paths(
    spoken_dub, saved_pronunciations, monkeypatch, caplog,
):
    dub, _backend = spoken_dub
    save = dub.dg.atomic_save_wav
    private_path = "/private-reference/staging/deleted-line.wav"

    def fail_staging(path, *args, **kwargs):
        if str(path).endswith("seg_en_delete.wav"):
            raise OSError(f"could not write {private_path}")
        return save(path, *args, **kwargs)

    monkeypatch.setattr(dub.dg, "atomic_save_wav", fail_staging)
    events = dub.run(segments=[dict(start=0, end=1, text="[[label|]]")],
                     segment_ids=["delete"], language="English")
    assert any(event["type"] == "done" for event in events), events
    assert "intentional silence for delete" in caplog.text
    assert private_path not in caplog.text
    assert "OSError" in caplog.text
