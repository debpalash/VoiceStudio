"""Regression tests for core.path_authorization — the one-shot host-path
capability store every export/settings native-picker flow consumes.

#1781: exports 403'd with "Invalid or expired desktop authorization" because
Tauri wrote its capability file into a DIFFERENT `.path-authorizations`
directory than this backend scanned (dev backend spawned without
`OMNIVOICE_*` env, or a custom data folder / portable mode). The Rust-side
fix (`commands::path_authorization_dir`) makes Tauri ask the running
backend where its data dir actually is; these tests cover the backend half:
`consume()` behaves uniformly across every capability kind (the whole class
of the bug, not just `dub_export`), and — the fail-before/pass-after
regression — a missing capability *store* (the mismatch symptom) is now
distinguished from a missing *token* (an ordinary expired/already-used
capability) instead of both producing the byte-identical silent 403.

The module is imported fresh inside the `auth` fixture (not bound at
collection time via a top-level `from ... import ...`) — other suites in
this repo `importlib.reload` sibling `core.*`/`api.*` modules mid-session
(#1269), and a name bound once at collection can go stale if that ever
touches this module; resolving `core.path_authorization` at test-run time
through `sys.modules` sidesteps that regardless of load order.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import secrets
from types import ModuleType

import pytest


def _token() -> str:
    return secrets.token_hex(32)


def _write_capability(auth_dir: str, token: str, kind: str, path: str) -> str:
    os.makedirs(auth_dir, exist_ok=True)
    target = os.path.join(auth_dir, f"{token}.json")
    with open(target, "w", encoding="utf-8") as fh:
        json.dump({"token": token, "kind": kind, "path": path}, fh)
    os.chmod(target, 0o600)
    return target


class _Auth:
    """The live `core.path_authorization` module plus this test's isolated
    capability-store directory."""

    def __init__(self, mod: ModuleType, auth_dir: str):
        self.mod = mod
        self.dir = auth_dir

    def consume(self, token: str, kind: str) -> str:
        return self.mod.consume(token, kind)

    @property
    def Error(self):
        return self.mod.PathAuthorizationError

    @property
    def kinds(self):
        return self.mod._KINDS  # noqa: SLF001 — the exact set consume() accepts

    def write(self, token: str, kind: str, path: str) -> str:
        return _write_capability(self.dir, token, kind, path)


@pytest.fixture
def auth(tmp_path, monkeypatch):
    """Point a freshly-resolved `core.path_authorization` at an isolated,
    per-test store instead of the shared session DATA_DIR, mirroring what
    Tauri's `authorize_host_path` writes into `path_authorization_dir()`.
    The directory is NOT created here — individual tests create it (or
    deliberately don't, for the #1781 case).
    """
    mod = importlib.import_module("core.path_authorization")
    d = tmp_path / ".path-authorizations"
    monkeypatch.setattr(mod, "_AUTH_DIR", str(d))
    return _Auth(mod, str(d))


@pytest.mark.parametrize("kind", ["models_dir", "ffmpeg", "ffprobe", "dub_export", "soni_input", "soni_output_dir"])
def test_consume_returns_the_authorized_path_for_every_capability_kind(auth, kind):
    # All six authorize_host_path kinds share ONE resolution path (_AUTH_DIR)
    # — this is the "whole class" #1781 must fix, not just dub_export.
    assert kind in auth.kinds
    token = _token()
    auth.write(token, kind, "/selected/by/native/dialog")
    assert auth.consume(token, kind) == "/selected/by/native/dialog"


def test_consume_is_one_shot(auth):
    token = _token()
    auth.write(token, "dub_export", "/out.wav")
    assert auth.consume(token, "dub_export") == "/out.wav"
    with pytest.raises(auth.Error):
        auth.consume(token, "dub_export")


def test_wrong_kind_is_rejected(auth):
    token = _token()
    auth.write(token, "dub_export", "/out.wav")
    with pytest.raises(auth.Error, match="does not match this setting"):
        auth.consume(token, "soni_input")


def test_malformed_token_or_kind_is_rejected(auth):
    with pytest.raises(auth.Error):
        auth.consume("not-a-hex-token", "dub_export")
    with pytest.raises(auth.Error):
        auth.consume(_token(), "not_a_real_kind")


def test_corrupt_capability_file_is_ignored(auth):
    token = _token()
    os.makedirs(auth.dir, exist_ok=True)
    with open(os.path.join(auth.dir, f"{token}.json"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(auth.Error):
        auth.consume(token, "dub_export")


def test_oversized_capability_file_is_rejected(auth):
    token = _token()
    os.makedirs(auth.dir, exist_ok=True)
    target = os.path.join(auth.dir, f"{token}.json")
    payload = json.dumps({"token": token, "kind": "dub_export", "path": "/x" * 9000})
    assert len(payload) > 16_384
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(payload)
    with pytest.raises(auth.Error):
        auth.consume(token, "dub_export")


@pytest.mark.skipif(os.name == "nt", reason="symlink capability files are a POSIX-only concern here")
def test_symlinked_capability_file_is_never_followed(auth, tmp_path):
    token = _token()
    os.makedirs(auth.dir, exist_ok=True)
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"token": token, "kind": "dub_export", "path": "/elsewhere"}))
    os.symlink(real, os.path.join(auth.dir, f"{token}.json"))
    with pytest.raises(auth.Error):
        auth.consume(token, "dub_export")


# ── #1781: distinguishing "no such capability" from "store is elsewhere" ──


_GENERIC_MESSAGE = "Invalid or expired desktop authorization"


def test_missing_token_in_an_existing_store_is_the_generic_message(auth):
    os.makedirs(auth.dir, exist_ok=True)  # store exists; just nothing in it
    with pytest.raises(auth.Error) as exc:
        auth.consume(_token(), "dub_export")
    assert str(exc.value) == _GENERIC_MESSAGE


def test_missing_store_directory_is_distinguished_from_a_missing_token(auth, caplog):
    """FAIL-BEFORE / PASS-AFTER regression for #1781.

    Before this fix, a nonexistent store (Tauri and the backend resolving
    different data dirs — the actual #1781 symptom) and an existing-but-empty
    store (an ordinary expired/consumed token) raised the byte-identical
    "Invalid or expired desktop authorization" with no server-side signal at
    all, which is exactly what made the mismatch silent and hard to
    diagnose. `auth.dir` is deliberately never created here.

    The HTTP-facing message stays byte-identical to the ordinary
    missing-token case on purpose (CWE-200: a client must not be able to
    distinguish "store missing" from "token missing/expired" — that's
    reconnaissance information about server-side filesystem state). The
    distinction is verified through `caplog` only, never through the
    exception string reaching the response body. The log line itself must
    not name the actual store directory either (CWE-532: per-user
    filesystem paths — e.g. a home-directory username — are sensitive and
    don't belong in application logs).
    """
    assert not os.path.isdir(auth.dir)
    caplog.set_level(logging.WARNING, logger="omnivoice.path_authorization")
    with pytest.raises(auth.Error) as exc:
        auth.consume(_token(), "dub_export")
    # Client-facing: identical to the missing-token case, no HTTP-visible
    # distinction and no path disclosure.
    assert str(exc.value) == _GENERIC_MESSAGE
    assert auth.dir not in str(exc.value)
    # Server-log-only: the mismatch is diagnosable, but the log line never
    # names the actual (per-user) directory.
    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("does not exist" in rec.message for rec in warnings)
    assert not any(auth.dir in rec.message for rec in warnings)
