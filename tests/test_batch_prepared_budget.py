"""Queued dubbing budgets the text that its saved pronunciation will speak."""
import asyncio
import importlib

import pytest
import torch

from test_text_normalization_routes import (  # noqa: F401
    batch_env, fake_engine, saved_pronunciations,
)


def test_batch_budget_uses_the_saved_respelling_sent_to_inference(
    batch_env, fake_engine, saved_pronunciations, monkeypatch,
):
    batch, make_job = batch_env
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-norm-route")
    db = importlib.import_module("core.db")
    replacement = " ".join(["kittens"] * 100)
    expected = f"Professor Smith has two {replacement}"
    with db.db_conn() as conn:
        conn.execute("UPDATE pronunciation_entries SET replacement=? WHERE id='1'", (replacement,))
    manager = importlib.import_module("services.model_manager")
    timeout = manager.generate_timeout_s
    budgeted = []

    def budget(text, **kwargs):
        budgeted.append(text)
        return timeout(text, **kwargs)

    monkeypatch.setattr(manager, "generate_timeout_s", budget)
    job = make_job("prepared-batch-budget")
    asyncio.run(batch._run_batch_pipeline(job["id"], job))
    assert "en" in job.get("outputs", {}), job
    assert [text for text, _kwargs in fake_engine.calls] == [expected]
    assert budgeted == [expected]


@pytest.mark.parametrize("native", [False, True])
def test_batch_whole_line_deletion_never_dispatches_empty_speech(
    batch_env, fake_engine, saved_pronunciations, monkeypatch, native,
):
    batch, make_job = batch_env
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-norm-route")
    monkeypatch.setenv(batch.BATCH_WIDTH_ENV, "8")

    async def transcript(*_args, **_kwargs):
        return ([dict(id=str(i), start=float(i), end=float(i + 1), text=text)
                 for i, text in enumerate(["[[label|]]", "dogs"])], "en")

    monkeypatch.setattr("services.asr_backend.run_transcribe_guarded", transcript)
    batches = []

    def generate_batch(_self, texts, **_kwargs):
        batches.append(list(texts))
        return [torch.zeros(1, 24000) for _ in texts]

    if native:
        monkeypatch.setattr(fake_engine, "generate_batch", generate_batch)
    job = make_job("prepared-batch-deletion")
    asyncio.run(batch._run_batch_pipeline(job["id"], job))
    assert "en" in job.get("outputs", {}), job
    actual = [text for group in batches for text in group] + [text for text, _kw in fake_engine.calls]
    assert actual == ["dogs"]
    assert not job.get("warnings")
