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


def test_auto_extract_dedupes_non_ascii_sources_case_insensitively(monkeypatch):
    """#2638: SQLite LOWER() is ASCII-only; accented/Cyrillic/ß terms must not duplicate."""
    from api.routers import glossary
    from services import llm_skills
    from core.db import ensure_schema, db_conn

    ensure_schema()
    pid = "proj-nonascii-2638"
    with db_conn() as conn:
        conn.execute("DELETE FROM glossary_terms WHERE project_id = ?", (pid,))

    def _use(body):
        class _Completions:
            def create(self, **kw):
                msg = type("M", (), {"content": body})
                return type("R", (), {"choices": [type("C", (), {"message": msg})]})

        class _Handle:
            client = type("Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()})()
            model = "m"
            timeout = 1.0

        monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: _Handle())

    req = _req(target_lang="en", segments=[{"text": "x"}])
    _use("Émile || Emile || name\nМосква || Moscow || city\nStraße || street || word\n")
    assert glossary.auto_extract(pid, req)["inserted"] == 3
    _use("émile || Emile || name\nМОСКВА || Moscow || city\nSTRASSE || street || word\n")
    out = glossary.auto_extract(pid, req)
    assert out["inserted"] == 0
    assert len(out["terms"]) == 3


def test_auto_extract_dedupes_compatibility_forms(monkeypatch):
    """Full-width Latin and ligature spellings of a term fold to the same key
    (NFKC), so an LLM emitting them does not create duplicate glossary rows."""
    from api.routers import glossary
    from services import llm_skills
    from core.db import ensure_schema, db_conn

    ensure_schema()
    pid = "proj-nfkc-fold"
    with db_conn() as conn:
        conn.execute("DELETE FROM glossary_terms WHERE project_id = ?", (pid,))

    def _use(body):
        class _Completions:
            def create(self, **kw):
                msg = type("M", (), {"content": body})
                return type("R", (), {"choices": [type("C", (), {"message": msg})]})

        class _Handle:
            client = type("Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()})()
            model = "m"
            timeout = 1.0

        monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: _Handle())

    fullwidth_moscow = "\uff2d\uff4f\uff53\uff43\uff4f\uff57"  # full-width "Moscow"
    ligature_fiona = "\ufb01ona"  # "fi" ligature + "ona"
    req = _req(target_lang="en", segments=[{"text": "x"}])
    _use("Moscow || Moscow || city\nFiona || Fiona || name\n")
    assert glossary.auto_extract(pid, req)["inserted"] == 2
    _use(f"{fullwidth_moscow} || Moscow || city\n{ligature_fiona} || Fiona || name\n")
    out = glossary.auto_extract(pid, req)
    assert out["inserted"] == 0
    assert len(out["terms"]) == 2
