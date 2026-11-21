"""Resolver behaviour of scripts/verify_action_pins.py with the HTTP layer mocked."""

from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_action_pins.py"
_spec = importlib.util.spec_from_file_location("verify_action_pins", _SCRIPT)
assert _spec and _spec.loader
vap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vap)

COMMIT = "c" * 40
TAG_A = "a" * 40
TAG_B = "b" * 40


def _resolver(responses: dict[str, object]) -> object:
    resolver = vap.Resolver(token="t")

    def fake_get(url: str):
        for suffix, value in responses.items():
            if url.endswith(suffix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected request {url}")

    resolver._get = fake_get  # type: ignore[method-assign]
    return resolver


def test_nested_annotated_tags_are_peeled_to_the_commit() -> None:
    resolver = _resolver(
        {
            "/git/matching-refs/tags/v1": [
                {"ref": "refs/tags/v1", "object": {"type": "tag", "sha": TAG_A}}
            ],
            f"/git/tags/{TAG_A}": {"object": {"type": "tag", "sha": TAG_B}},
            f"/git/tags/{TAG_B}": {"object": {"type": "commit", "sha": COMMIT}},
        }
    )
    assert resolver.resolve("o/r", "v1") == ("tag", COMMIT)


def test_failed_tag_lookup_does_not_fall_through_to_the_branch() -> None:
    resolver = _resolver(
        {
            "/git/matching-refs/tags/v1": vap.LookupFailed("HTTP 502"),
            "/git/matching-refs/heads/v1": [
                {"ref": "refs/heads/v1", "object": {"type": "commit", "sha": COMMIT}}
            ],
        }
    )
    with pytest.raises(vap.LookupFailed):
        resolver.resolve("o/r", "v1")
    assert not resolver.cache  # a failure is never cached as a result


def test_confirmed_absent_tag_falls_back_to_the_branch() -> None:
    resolver = _resolver(
        {
            "/git/matching-refs/tags/stable": [],
            "/git/matching-refs/heads/stable": [
                {"ref": "refs/heads/stable", "object": {"type": "commit", "sha": COMMIT}}
            ],
        }
    )
    assert resolver.resolve("o/r", "stable") == ("branch", COMMIT)


def test_http_errors_other_than_404_raise_and_404_is_absence(monkeypatch) -> None:
    def failing(code: int):
        def urlopen(request, timeout=0):
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b""))

        return urlopen

    resolver = vap.Resolver(token=None)
    monkeypatch.setattr(vap.urllib.request, "urlopen", failing(404))
    assert resolver._get("https://api.github.com/x") is None
    monkeypatch.setattr(vap.urllib.request, "urlopen", failing(403))
    with pytest.raises(vap.LookupFailed):
        resolver._get("https://api.github.com/x")
    assert resolver.rate_limited
    monkeypatch.setattr(vap.urllib.request, "urlopen", failing(502))
    with pytest.raises(vap.LookupFailed):
        resolver._get("https://api.github.com/x")
