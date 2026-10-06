"""Glossary auto-extract — provider-error scrubbing + no-LLM guidance.

The auto-extract endpoint reuses the translator's LLM client. A provider that
echoes the API key / a user_id / a home path in its error body must not surface
that verbatim in the 502 detail, and the no-LLM 503 must point users at the
current setup surface (Settings → LLM Providers), not the legacy env vars.
"""
import os

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from fastapi import HTTPException


def _req(**kw):
    # AutoExtractRequest is defined in the glossary router module.
    from api.routers.glossary import AutoExtractRequest
    return AutoExtractRequest(**kw)


def test_auto_extract_no_llm_points_at_llm_providers(monkeypatch):
    from api.routers import glossary
    from services import llm_skills
    # Auto-extract resolves its client through the LLM Skills registry
    # (glossary_extract skill). None == disabled / no provider configured.
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: None)

    req = _req(target_lang="es", segments=[{"text": "Hello Marcus"}])
    with pytest.raises(HTTPException) as ei:
        glossary.auto_extract("proj1", req)
    detail = ei.value.detail
    assert ei.value.status_code == 503
    assert "LLM Providers" in detail
    # The stale env-var-only guidance must be gone.
    assert "TRANSLATE_BASE_URL" not in detail
    assert "TRANSLATE_API_KEY" not in detail


def test_auto_extract_scrubs_provider_error(monkeypatch):
    from api.routers import glossary
    from services import llm_skills

    secret = "sk-LEAKLEAKLEAKLEAKLEAK12345"
    home = "/Users/alice/videos"

    class _Completions:
        def create(self, **kw):
            raise RuntimeError(f"401 bad key {secret} user_id=acct_9 at {home}")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    # A resolved skill client whose provider call blows up — glossary uses
    # handle.client / handle.model / handle.timeout (llm_skills.SkillClient
    # shape) after routing through the glossary_extract skill.
    class _Handle:
        client = _Client()
        model = "m"
        timeout = 1.0

    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: _Handle())

    req = _req(target_lang="es", segments=[{"text": "Hello Marcus"}])
    with pytest.raises(HTTPException) as ei:
        glossary.auto_extract("proj1", req)
    detail = ei.value.detail
    assert ei.value.status_code == 502
    assert secret not in detail
    assert home not in detail
    assert "***REDACTED***" in detail


def test_auto_extract_ignores_terms_inside_reasoning(monkeypatch):
    """A reasoning model drafts candidate pairs in its monologue; only the
    answer after </think> may become glossary rows."""
    from api.routers import glossary
    from services import llm_skills
    from core.db import ensure_schema

    ensure_schema()
    body = (
        "Candidates:\nMarcus || WRONG || draft\n</think>\n"
        "Marcus || Marcus || character name\n"
    )

    class _Completions:
        def create(self, **kw):
            msg = type("M", (), {"content": body})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    class _Handle:
        client = type("Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()})()
        model = "m"
        timeout = 1.0

    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: _Handle())

    out = glossary.auto_extract("proj-reasoning", _req(target_lang="es", segments=[{"text": "Hello Marcus"}]))
    assert out["proposed"] == 1
    assert [t["target"] for t in out["terms"] if t["source"] == "Marcus"] == ["Marcus"]


@pytest.mark.parametrize("source, proposed_source", [
    ("École", "École"),
    ("ÉCOLE", "école"),
    ("МОСКВА", "Москва"),
    ("München", "MÜNCHEN"),
    ("Marcus", "MARCUS"),
])
def test_auto_extract_preserves_existing_unicode_term(monkeypatch, tmp_path, source, proposed_source):
    """Both sides of source deduplication must use Unicode-aware casing."""
    import sqlite3
    from types import SimpleNamespace
    from api.routers import glossary
    from core import db
    from services import llm_skills

    path = tmp_path / "glossary.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(db._BASE_SCHEMA)
    monkeypatch.setattr(db, "DB_PATH", path)
    original = glossary.add_term("project", glossary.GlossaryTerm(
        source=source, target="manual choice", note="keep this"))
    other = glossary.add_term("another-project", glossary.GlossaryTerm(
        source="Rudy", target="other project"))
    body = f"{proposed_source} || automatic choice || candidate\nRudy || Rudy || name\n"
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))])
    handle = SimpleNamespace(model="test", timeout=1, client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: completion))))
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: handle)

    result = glossary.auto_extract("project", _req(target_lang="en", segments=[{"text": source}]))

    assert result["proposed"] == 2
    assert result["inserted"] == 1
    terms = {term["source"]: term for term in result["terms"]}
    assert set(terms) == {source, "Rudy"}
    assert terms[source] == original
    assert terms[source]["auto"] is False
    assert terms["Rudy"]["auto"] is True
    assert glossary.list_terms("another-project") == [other]
