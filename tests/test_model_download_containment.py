"""Model downloads and bundle imports keep every written file where it belongs.

* The segmented snapshot downloader names cache files after the endpoint's
  ``rfilename`` and etag values; both must stay inside the model cache.
* The Hugging Face token goes to Hugging Face only, never to a mirror.
* The mirror setting accepts only well-formed HTTPS origins.
* Voice bundles are bounded in entries and size and refuse unsafe names.
* A GGUF checksum manifest that cannot be read fails closed.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import zipfile
from types import SimpleNamespace

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

REPO = "org/model"
REVISION = "c" * 40
GOOD_ETAG = "a" * 64


@pytest.fixture
def download():
    return importlib.import_module("api.routers.setup.download")


def _drive_segmented(
    download, monkeypatch, tmp_path, files, *, etag=GOOD_ETAG, endpoint=None,
    resolved=None, default_endpoint="https://huggingface.co",
):
    import huggingface_hub
    from huggingface_hub import file_download as hf_file_download
    from services import segmented_download as sd_mod
    from services import token_resolver

    cache = tmp_path / "hub"
    cache.mkdir()
    monkeypatch.setattr(token_resolver, "resolve", lambda *a, **k: resolved)
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(huggingface_hub.constants, "ENDPOINT", default_endpoint)
    seen: dict = {}

    class _FakeApi:
        def __init__(self, *, endpoint=None, token=None):
            seen["hf_api"] = token

        def repo_info(self, repo_id, repo_type=None, revision=None):
            return SimpleNamespace(
                sha=revision, siblings=[SimpleNamespace(rfilename=f) for f in files],
            )

    def _fake_metadata(url, token=None, **_kw):
        seen["file_metadata"] = token
        return SimpleNamespace(etag=f'"{etag}"', location=url, size=4)

    async def _fake_segmented(url, blob_path, *, token=None, **_kw):
        seen["segmented"] = token
        os.makedirs(os.path.dirname(blob_path), exist_ok=True)
        with open(blob_path, "wb") as fh:
            fh.write(b"data")
        return blob_path

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)
    monkeypatch.setattr(hf_file_download, "get_hf_file_metadata", _fake_metadata)
    monkeypatch.setattr(sd_mod, "segmented_download", _fake_segmented)
    result = download._segmented_snapshot(REPO, endpoint=endpoint, revision=REVISION)
    return cache, seen, result


def _files_outside(root, tmp_path):
    root = os.path.realpath(root)
    out = []
    for dirpath, _dirs, names in os.walk(tmp_path):
        for name in names:
            path = os.path.realpath(os.path.join(dirpath, name))
            if os.path.commonpath([root, path]) != root:
                out.append(path)
    return out


# ── 1. remote file names and etags ──────────────────────────────────────────

@pytest.mark.parametrize("rfilename", [
    "../../../../escape.txt",
    "sub/../../../../escape.txt",
    "/abs/escape.txt",
    "C:/escape.txt",
    "C:escape.txt",
    "..\\..\\escape.txt",
    "\\\\server\\share\\escape.txt",
    "sub//model.bin",
    "./model.bin",
    "",
])
def test_unsafe_sibling_name_is_refused(download, monkeypatch, tmp_path, rfilename):
    with pytest.raises(RuntimeError):
        _drive_segmented(download, monkeypatch, tmp_path, ["config.json", rfilename])
    assert _files_outside(tmp_path / "hub", tmp_path) == []
    # Validation runs before any file is fetched.
    assert not list((tmp_path / "hub").rglob("config.json"))


@pytest.mark.parametrize("etag", [
    "../../../../escape",
    "deadbeef",
    "a" * 63,
    "g" * 64,
    "a" * 40 + "/x",
    "",
])
def test_bad_etag_is_refused(download, monkeypatch, tmp_path, etag):
    with pytest.raises(RuntimeError):
        _drive_segmented(download, monkeypatch, tmp_path, ["model.bin"], etag=etag)
    assert _files_outside(tmp_path / "hub", tmp_path) == []
    assert not [p for p in (tmp_path / "hub").rglob("*") if p.is_file()]


def test_nested_file_names_still_download(download, monkeypatch, tmp_path):
    cache, _seen, snap = _drive_segmented(
        download, monkeypatch, tmp_path, ["config.json", "subdir/model.safetensors"],
        etag="b" * 40,
    )
    assert os.path.realpath(snap).startswith(os.path.realpath(cache))
    nested = os.path.join(snap, "subdir", "model.safetensors")
    assert os.path.lexists(nested)
    with open(nested, "rb") as fh:
        assert fh.read() == b"data"
    assert (cache / "models--org--model" / "blobs" / ("b" * 40)).is_file()


def test_failed_validation_falls_back_to_snapshot_download(download):
    # A refused listing is a RuntimeError, which the install worker answers by
    # switching to huggingface_hub's own snapshot_download.
    assert download._segmented_retry_plan(RuntimeError("x"), 1, 3) == (True, False)


def test_path_helper_keeps_existing_pointer_links(tmp_path, symlink_or_skip):
    from core.path_security import contained_child

    blobs = tmp_path / "blobs"
    snap = tmp_path / "snapshots" / REVISION
    blobs.mkdir()
    snap.mkdir(parents=True)
    (blobs / GOOD_ETAG).write_bytes(b"x")
    symlink_or_skip(snap / "model.bin", blobs / GOOD_ETAG)
    assert contained_child(snap, "model.bin") == snap.resolve() / "model.bin"


def test_path_helper_refuses_symlinked_directory_escape(tmp_path, symlink_or_skip):
    from core.path_security import UnsafePath, contained_child

    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    symlink_or_skip(root / "link", tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(UnsafePath):
        contained_child(root, "link/file.bin")


# ── 2. the token is for Hugging Face only ───────────────────────────────────

def _token(value="hf_secret"):
    from services.token_resolver import ResolvedToken
    return ResolvedToken(token=value, source="app", username="tester")


def test_token_is_not_sent_to_a_mirror(download, monkeypatch, tmp_path):
    _cache, seen, _snap = _drive_segmented(
        download, monkeypatch, tmp_path, ["model.bin"],
        endpoint="https://hf-mirror.com", resolved=_token(),
    )
    assert seen["hf_api"] is False
    assert seen["file_metadata"] is False
    assert seen["segmented"] is None


def test_token_is_not_sent_to_a_mirror_from_hf_endpoint(download, monkeypatch, tmp_path):
    # endpoint=None means huggingface_hub's own default, fixed from HF_ENDPOINT.
    _cache, seen, _snap = _drive_segmented(
        download, monkeypatch, tmp_path, ["model.bin"],
        resolved=_token(), default_endpoint="https://mirror.example",
    )
    assert seen == {"hf_api": False, "file_metadata": False, "segmented": None}


def test_token_still_sent_to_hugging_face(download, monkeypatch, tmp_path):
    _cache, seen, _snap = _drive_segmented(
        download, monkeypatch, tmp_path, ["model.bin"],
        endpoint="https://huggingface.co", resolved=_token(),
    )
    assert seen == {"hf_api": "hf_secret", "file_metadata": "hf_secret", "segmented": "hf_secret"}


@pytest.mark.parametrize("endpoint, allowed", [
    ("https://huggingface.co", True),
    ("https://hf.co", True),
    ("https://cdn-lfs.huggingface.co", True),
    ("http://huggingface.co", False),
    ("https://hf-mirror.com", False),
    ("https://huggingface.co.evil.example", False),
    ("https://evilhuggingface.co", False),
])
def test_token_for_endpoint(endpoint, allowed):
    from services.hf_auth import token_for_endpoint

    assert token_for_endpoint(endpoint, "t") == ("t" if allowed else False)


def test_install_worker_withholds_token_from_mirror(monkeypatch, tmp_path):
    importlib.import_module("api.routers.setup.models")
    download = importlib.import_module("api.routers.setup.download")
    import huggingface_hub
    from services import hf_revisions, performance_profiles, token_resolver

    calls = []

    def fake_snapshot(**kwargs):
        calls.append(kwargs)
        return [] if kwargs.get("dry_run") else str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(token_resolver, "resolve", lambda *a, **k: _token())
    monkeypatch.setattr(download, "_download_endpoint", lambda gated=False: "https://hf-mirror.com")
    monkeypatch.setattr(download, "compute_plan", lambda _plan: {
        "total_bytes": 1, "cached_bytes": 0, "to_download_bytes": 1,
        "n_files": 1, "n_cached": 0,
    })
    monkeypatch.setattr(download, "disk_space_error", lambda *_a, **_k: None)
    monkeypatch.setattr(download, "_segmented_enabled", lambda: False)
    monkeypatch.setattr(download, "_validate_snapshot_has_weights", lambda *_a: None)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *_a: None)
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda: {})

    async def run_install():
        repo_id = download.KNOWN_MODELS[0]["repo_id"]
        await download.install_model(download.InstallModelRequest(repo_id=repo_id))
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(run_install())
    assert len(calls) >= 2
    assert all(call.get("token") is False for call in calls)


def test_engine_env_withholds_token_from_mirror(monkeypatch):
    from services import engine_env, token_resolver

    monkeypatch.setattr(token_resolver, "resolve", lambda *a, **k: _token())
    mirror = engine_env.build_engine_env(
        base_env={"HF_ENDPOINT": "https://hf-mirror.com", "HF_TOKEN": "hf_shell"}
    )
    assert "HF_TOKEN" not in mirror and "YOUR_HF_TOKEN" not in mirror
    assert mirror["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
    official = engine_env.build_engine_env(base_env={})
    assert official["HF_TOKEN"] == "hf_secret"
    assert "HF_HUB_DISABLE_IMPLICIT_TOKEN" not in official


def test_process_policy_follows_mirror_setting(monkeypatch):
    from services import hf_auth

    monkeypatch.setattr(hf_auth, "_implicit_disabled_in", {})
    env = {"HF_ENDPOINT": "https://hf-mirror.com"}
    hf_auth.apply_process_token_policy(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
    env.pop("HF_ENDPOINT")
    hf_auth.apply_process_token_policy(env)
    assert "HF_HUB_DISABLE_IMPLICIT_TOKEN" not in env
    # A value the user set themselves is left alone.
    env = {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"}
    hf_auth.apply_process_token_policy(env)
    assert env == {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"}


# ── 3. mirror URL validation ────────────────────────────────────────────────

@pytest.fixture
def settings_mod(monkeypatch, tmp_path):
    store: dict = {}
    import core.user_env as ue
    from core import prefs
    from services import hf_auth

    monkeypatch.setattr(ue, "get_user_env", lambda k, path=None: store.get(k))
    monkeypatch.setattr(ue, "set_user_env", lambda k, v, path=None: store.__setitem__(k, v))
    monkeypatch.setattr(ue, "unset_user_env", lambda k, path=None: store.pop(k, None))
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(hf_auth, "_implicit_disabled_in", {})
    for key in ("HF_ENDPOINT", "OMNIVOICE_HF_ENDPOINT_MODE", "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    yield importlib.import_module("api.routers.settings")
    for key in ("HF_ENDPOINT", "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        os.environ.pop(key, None)


@pytest.mark.parametrize("url", [
    "http://hf-mirror.com",
    "https://user:pass@hf-mirror.com",
    "https://token@hf-mirror.com",
    "https://hf-mirror.com?x=1",
    "https://hf-mirror.com#frag",
    "https://",
    "https://hf-mirror.com:99999",
    "https://hf mirror.com",
    "ftp://hf-mirror.com",
    "file:///etc/passwd",
    "javascript:alert(1)",
])
def test_mirror_url_is_refused(settings_mod, url):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url=url))
    assert ei.value.status_code == 400
    assert "HF_ENDPOINT" not in os.environ


@pytest.mark.parametrize("url", [
    "https://hf-mirror.com",
    "https://mirror.example:8443/hf",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
])
def test_mirror_url_is_accepted(settings_mod, url):
    state = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url=url))
    assert state["configured"] == url
    assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"


# ── 4. voice bundles ────────────────────────────────────────────────────────

def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


_METADATA = json.dumps({"profile_name": "Test"})


@pytest.fixture
def marketplace(monkeypatch, tmp_path):
    from api.routers import marketplace as mk

    voices = tmp_path / "voices"
    store = tmp_path / "store"
    voices.mkdir()
    store.mkdir()
    monkeypatch.setattr(mk, "VOICES_DIR", str(voices))
    monkeypatch.setattr(mk, "MARKETPLACE_DIR", store)
    return mk, voices, store


def _install(mk, store, content):
    (store / "b.omnivoice").write_bytes(content)
    return asyncio.run(mk.install_from_marketplace("b.omnivoice"))


@pytest.mark.parametrize("bad_name", ["../escape.wav", "/abs/ref_audio.wav", "a\\..\\ref_audio.wav"])
def test_marketplace_bundle_refuses_unsafe_names(marketplace, tmp_path, bad_name):
    from fastapi import HTTPException

    mk, voices, store = marketplace
    content = _zip({"metadata.json": _METADATA, "ref_audio.wav": b"RIFF", bad_name: b"x"})
    with pytest.raises(HTTPException) as ei:
        _install(mk, store, content)
    assert ei.value.status_code == 400
    assert list(voices.iterdir()) == []


def test_marketplace_bundle_refuses_too_many_entries(marketplace):
    from fastapi import HTTPException

    mk, voices, store = marketplace
    entries = {"metadata.json": _METADATA, "ref_audio.wav": b"RIFF"}
    entries.update({f"pad/{i}.txt": b"" for i in range(200)})
    with pytest.raises(HTTPException) as ei:
        _install(mk, store, _zip(entries))
    assert ei.value.status_code == 413
    assert list(voices.iterdir()) == []


def test_marketplace_bundle_refuses_decompression_bomb(marketplace, monkeypatch):
    from fastapi import HTTPException
    from core import safe_archive

    mk, voices, store = marketplace
    monkeypatch.setattr(safe_archive, "MAX_MEMBER_BYTES", 1024)
    monkeypatch.setattr(safe_archive, "MAX_TOTAL_BYTES", 4096)
    content = _zip({"metadata.json": _METADATA, "ref_audio.wav": b"\0" * (1024 * 1024)})
    assert len(content) < 10 * 1024  # small on disk, large once inflated
    with pytest.raises(HTTPException) as ei:
        _install(mk, store, content)
    assert ei.value.status_code == 413
    assert list(voices.iterdir()) == []


def test_marketplace_bundle_output_name_ignores_member_name(marketplace):
    mk, voices, _store = marketplace
    zf, _meta = mk._open_bundle(_zip({"metadata.json": _METADATA, "ref_audio.w@v": b"RIFF"}))
    ref, locked = mk._extract_bundle_audio(zf, "abcd1234")
    assert (ref, locked) == ("abcd1234.wav", None)
    assert [p.name for p in voices.iterdir()] == ["abcd1234.wav"]


def test_persona_bundle_limits():
    from services import persona_bundle as pb

    entries = {"manifest.json": "{}", "ref_audio.wav": b"RIFF"}
    entries.update({f"pad/{i}": b"" for i in range(200)})
    with pytest.raises(pb.BundleError) as ei:
        pb.parse_persona_bundle(_zip(entries))
    assert ei.value.status == 413

    with pytest.raises(pb.BundleError) as ei:
        pb.parse_persona_bundle(_zip({"manifest.json": "{}", "../ref_audio.wav": b"RIFF"}))
    assert ei.value.status == 400

    with pytest.raises(pb.BundleError) as ei:
        pb.parse_persona_bundle(_zip({
            "manifest.json": json.dumps({"pad": "x" * (2 * 1024 * 1024)}),
            "ref_audio.wav": b"RIFF",
        }))
    assert ei.value.status == 413


def test_persona_member_copy_is_capped(tmp_path):
    from core import safe_archive

    zf = safe_archive.open_bounded_zip(_zip({"ref_audio.wav": b"\0" * 4096}))
    dest = tmp_path / "out.wav"
    with pytest.raises(safe_archive.ArchiveError):
        safe_archive.copy_member(zf, "ref_audio.wav", str(dest), max_bytes=1024)
    assert not dest.exists()


def test_normal_persona_bundle_still_parses(tmp_path):
    from services import persona_bundle as pb

    parsed = pb.parse_persona_bundle(_zip({
        "manifest.json": json.dumps({"persona": {"name": "x"}}),
        "ref_audio.wav": b"RIFF....",
    }))
    dest = tmp_path / "ref.wav"
    assert parsed.extract_member("ref_audio", str(dest)) is True
    assert dest.read_bytes() == b"RIFF...."


# ── 5. GGUF checksum manifest ───────────────────────────────────────────────

@pytest.fixture
def gguf(monkeypatch, tmp_path):
    from engines.omnivoice_gguf import backend as gguf_backend

    root = tmp_path / "repo"
    (root / "bin").mkdir(parents=True)
    fake_bin = root / "bin" / "omnivoice-tts-linux-x86_64"
    fake_bin.write_bytes(b"\x7fELF-real-enough")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(gguf_backend, "_REPO_ROOT", root)
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_bin)
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)
    return gguf_backend, root / "bin" / "checksums.sha256", fake_bin


def _digest(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("line", [
    "{name}",                          # entry without a checksum
    "not-a-digest  {name}",            # malformed checksum
    "{digest}",                        # checksum without a name
    "SHA256 ({name}) = zzzz",          # malformed BSD form
])
def test_gguf_malformed_manifest_fails_closed(gguf, line):
    backend, manifest, fake_bin = gguf
    manifest.write_text(line.format(name=fake_bin.name, digest=_digest(fake_bin)) + "\n")
    ok, reason = backend._make_backend_class().is_available()
    assert ok is False
    assert "verified" in reason


def test_gguf_conflicting_entries_fail_closed(gguf):
    backend, manifest, fake_bin = gguf
    manifest.write_text(f"{_digest(fake_bin)}  {fake_bin.name}\n{'0' * 64}  {fake_bin.name}\n")
    ok, _reason = backend._make_backend_class().is_available()
    assert ok is False


def test_gguf_unreadable_manifest_fails_closed(gguf):
    backend, manifest, _fake_bin = gguf
    manifest.write_bytes(b"\xff\xfe\x00bad")
    ok, _reason = backend._make_backend_class().is_available()
    assert ok is False


def test_gguf_mismatch_in_bsd_form_fails_closed(gguf):
    backend, manifest, fake_bin = gguf
    manifest.write_text(f"SHA256 ({fake_bin.name}) = {'0' * 64}\n")
    ok, reason = backend._make_backend_class().is_available()
    assert ok is False and "mismatch" in reason


@pytest.mark.parametrize("fmt", ["{digest}  {name}", "{digest} *{name}", "SHA256 ({name}) = {digest}"])
def test_gguf_valid_manifest_passes(gguf, fmt):
    backend, manifest, fake_bin = gguf
    manifest.write_text(fmt.format(name=fake_bin.name, digest=_digest(fake_bin)) + "\n")
    ok, reason = backend._make_backend_class().is_available()
    assert ok is True, reason


def test_gguf_entries_without_checksum_requirement_still_pass(gguf):
    backend, manifest, fake_bin = gguf
    # Absent manifest (source checkout) and a manifest for other platforms only.
    ok, reason = backend._make_backend_class().is_available()
    assert ok is True, reason
    manifest.write_text(f"{'0' * 64}  omnivoice-tts-darwin-arm64\n")
    ok, reason = backend._make_backend_class().is_available()
    assert ok is True, reason


def test_process_policy_only_removes_what_it_set_in_that_mapping(monkeypatch):
    from services import hf_auth

    monkeypatch.setattr(hf_auth, "_implicit_disabled_in", {})
    ours = {"HF_ENDPOINT": "https://hf-mirror.com"}
    hf_auth.apply_process_token_policy(ours)
    assert ours["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"

    # Another mapping where the user chose the value and the endpoint is official:
    # the earlier switch must not make this call delete the user's setting.
    users = {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"}
    hf_auth.apply_process_token_policy(users)
    assert users == {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "1"}

    # The mapping we changed is still cleaned up when its endpoint is official again.
    ours.pop("HF_ENDPOINT")
    hf_auth.apply_process_token_policy(ours)
    assert "HF_HUB_DISABLE_IMPLICIT_TOKEN" not in ours


def test_false_valued_implicit_token_setting_is_overridden_for_a_mirror_and_restored(monkeypatch):
    from services import hf_auth

    monkeypatch.setattr(hf_auth, "_implicit_disabled_in", {})
    env = {"HF_ENDPOINT": "https://hf-mirror.com", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "0"}
    hf_auth.apply_process_token_policy(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
    env.pop("HF_ENDPOINT")
    hf_auth.apply_process_token_policy(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "0"

    for truthy in ("1", "true", "TRUE", "on", "Yes"):
        kept = {"HF_ENDPOINT": "https://hf-mirror.com", "HF_HUB_DISABLE_IMPLICIT_TOKEN": truthy}
        hf_auth.apply_process_token_policy(kept)
        assert kept["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == truthy

    # huggingface_hub does not trim, so a padded value is false there: override it.
    for padded in (" on ", " 1", "1 "):
        env = {"HF_ENDPOINT": "https://hf-mirror.com", "HF_HUB_DISABLE_IMPLICIT_TOKEN": padded}
        hf_auth.apply_process_token_policy(env)
        assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"


def test_engine_env_forces_the_implicit_token_off_for_a_mirror_even_when_set_false():
    from services import engine_env

    env = engine_env.build_engine_env(
        base_env={"HF_ENDPOINT": "https://hf-mirror.com", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "0"}
    )
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"


def test_implicit_token_parser_matches_huggingface_hub():
    """The policy must call a value disabled only when the Hub does."""
    from huggingface_hub import constants

    from services import hf_auth

    for value in ("1", "true", "TRUE", "on", "ON", "Yes", "YES", " on ", " 1", "1 ", "0", "false", "", "2"):
        assert hf_auth.implicit_token_disabled(value) == constants._is_true(value), value


@pytest.mark.parametrize("previous", [None, "0"])
@pytest.mark.parametrize("chosen", ["true", "on", "0", ""])
def test_process_policy_preserves_user_changes_after_injection(monkeypatch, previous, chosen):
    from services import hf_auth

    monkeypatch.setattr(hf_auth, "_implicit_disabled_in", {})
    env = {"HF_ENDPOINT": "https://hf-mirror.com"}
    if previous is not None:
        env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = previous
    hf_auth.apply_process_token_policy(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"
    env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = chosen
    env.pop("HF_ENDPOINT")
    hf_auth.apply_process_token_policy(env)
    assert env["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == chosen
    assert id(env) not in hf_auth._implicit_disabled_in
