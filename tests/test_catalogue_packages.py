"""Model Catalogue holds several packages from one Hugging Face repo (#2665).

Every catalogue lookup used to be keyed by ``repo_id``, so a second entry for a
repository that already backed one (audio.cpp's GGUF repo holds Breeze-TTS-2,
Sortformer and dots.tts) could not exist: installs, install state, licences and
uninstall all collapsed onto the first entry. A package entry now carries its
own identity (``<repo_id>:<package>``); Hub calls and the cache keep using the
repo. Whole-repo entries keep their repo id as identity, so saved settings,
install records and licence acceptances are unchanged.
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

REPO = "audio-cpp/audio.cpp-gguf"
DOTS = "audio-cpp/audio.cpp-gguf:dots-tts-soar-q8_0"
DOTS_FILE = "DotTTS-SOAR-GGUF/dots-tts-soar-q8_0.gguf"
BREEZE_FILE = "Breeze-TTS-2-GGUF/breeze-tts-2-q8_0.gguf"
SORTFORMER_FILE = "Sortformer-Diar-4spk-v1-GGUF/sortformer-diar-4spk-v1-q8_0.gguf"


@pytest.fixture
def models():
    return importlib.import_module("api.routers.setup.models")


@pytest.fixture
def download():
    return importlib.import_module("api.routers.setup.download")


def test_package_identities_are_unique_and_well_formed(models):
    keys = [models.model_key(m) for m in models.KNOWN_MODELS]
    assert len(keys) == len(set(keys))
    for model in models.KNOWN_MODELS:
        if "id" not in model:
            continue
        repo, sep, package = model["id"].partition(":")
        assert sep and package and repo == model["repo_id"], model["id"]
        assert model.get("allow_patterns") and model.get("required_files"), model["id"]


def test_entries_sharing_a_repo_select_disjoint_files(models):
    import fnmatch

    by_repo: dict[str, list[dict]] = {}
    for model in models.KNOWN_MODELS:
        by_repo.setdefault(model["repo_id"], []).append(model)
    for repo, entries in by_repo.items():
        if len(entries) < 2:
            continue
        for entry in entries:
            assert entry.get("allow_patterns"), f"{repo}: shared repo entries need allow_patterns"
        for entry in entries:
            for other in entries:
                if other is entry:
                    continue
                for name in entry.get("required_files") or ():
                    assert not any(
                        fnmatch.fnmatchcase(name, p) for p in other["allow_patterns"]
                    ), f"{name} is selected by two entries of {repo}"


def test_whole_repo_entries_keep_their_repo_id_identity(models):
    catalog = models.get_model_catalog()
    for model in models.KNOWN_MODELS:
        if "id" not in model:
            assert catalog.get(model["repo_id"]) is model
    # The existing audio.cpp bundle is still addressed by its repo id.
    assert catalog.get(REPO)["allow_patterns"] == [BREEZE_FILE, SORTFORMER_FILE]
    assert catalog.get(DOTS)["repo_id"] == REPO
    assert catalog.get(DOTS)["engines"] == ["audiocpp-dots"]


def _snapshot(root: Path, files: dict[str, bytes]) -> Path:
    """A Hub-cache-shaped repo: snapshot links resolve to content blobs."""
    repo_dir = root / ("models--" + REPO.replace("/", "--"))
    snap = repo_dir / "snapshots" / ("a" * 40)
    blobs = repo_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        blob = blobs / f"blob-{abs(hash(rel))}"
        blob.write_bytes(data)
        target = snap / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(os.path.relpath(blob, target.parent))
        except OSError:  # Windows without symlink rights: the cache copies
            target.write_bytes(data)
    return snap


@pytest.fixture
def cache(models, monkeypatch, tmp_path):
    def snapshots(repo_id):
        repo_dir = tmp_path / ("models--" + repo_id.replace("/", "--")) / "snapshots"
        return [str(p) for p in repo_dir.iterdir()] if repo_dir.is_dir() else []

    catalogue = importlib.import_module("services.model_catalogue")
    for module in (models, catalogue):
        monkeypatch.setattr(module, "_snapshot_dirs", snapshots)
    monkeypatch.setattr(catalogue, "snapshot_has_weights", lambda _path: True)
    monkeypatch.setitem(catalogue._WEIGHT_FLOORS, ".gguf", 1)
    download = importlib.import_module("api.routers.setup.download")
    monkeypatch.setattr(download, "_snapshot_dirs", snapshots)
    return tmp_path


def test_a_sibling_package_never_makes_an_entry_look_installed(models, cache):
    _snapshot(cache, {BREEZE_FILE: b"breeze", SORTFORMER_FILE: b"sortformer"})
    catalog = models.get_model_catalog()
    assert models.package_cache_usage(catalog.get(REPO)) == {
        "size_on_disk": len(b"breeze") + len(b"sortformer"),
        "last_accessed": None,
        "nb_files": 2,
    }
    assert models.package_cache_usage(catalog.get(DOTS)) is None


def test_list_models_reports_each_package_by_its_identity(models, cache, monkeypatch):
    _snapshot(cache, {DOTS_FILE: b"dots-weights"})

    class _Entry:
        repo_id = REPO
        size_on_disk = 999
        last_accessed = None
        nb_files = 9

    class _Info:
        repos = [_Entry()]

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: _Info())
    monkeypatch.setattr(models, "_model_license_info", lambda key: {"key": key})
    monkeypatch.setattr(models, "_model_licence_acceptance", lambda key: {"key": key})
    models.invalidate_cache()
    rows = {row["repo_id"]: row for row in models.list_models()["models"]}
    models.invalidate_cache()
    dots, bundle = rows[DOTS], rows[REPO]
    assert dots["hf_repo"] == REPO and dots["installed"] is True
    assert dots["size_on_disk_bytes"] == len(b"dots-weights")
    assert dots["license_info"] == {"key": DOTS}
    # The bundle's files are absent: the dots package does not install it.
    assert bundle["installed"] is False and bundle["hf_repo"] == REPO


def test_uninstalling_one_package_keeps_the_other(models, download, cache, monkeypatch):
    snap = _snapshot(cache, {
        BREEZE_FILE: b"breeze", SORTFORMER_FILE: b"sortformer", DOTS_FILE: b"dots-weights",
    })
    monkeypatch.setattr(download, "invalidate_cache", lambda: None)
    result = download.delete_model(DOTS)
    assert result["deleted"] is True and result["freed_bytes"] == len(b"dots-weights")
    assert not (snap / DOTS_FILE).exists()
    assert (snap / BREEZE_FILE).read_bytes() == b"breeze"
    assert (snap / SORTFORMER_FILE).read_bytes() == b"sortformer"
    blobs = sorted(p.read_bytes() for p in (snap.parent.parent / "blobs").iterdir())
    if (snap / BREEZE_FILE).is_symlink():
        assert blobs == [b"breeze", b"sortformer"]
    # Nothing left for this package: a second uninstall reports not installed.
    with pytest.raises(Exception) as exc:
        download.delete_model(DOTS)
    assert getattr(exc.value, "status_code", None) == 404


def test_uninstalling_the_bundle_keeps_dots(models, download, cache, monkeypatch):
    snap = _snapshot(cache, {
        BREEZE_FILE: b"breeze", SORTFORMER_FILE: b"sortformer", DOTS_FILE: b"dots-weights",
    })
    monkeypatch.setattr(download, "invalidate_cache", lambda: None)
    download.delete_model(REPO)
    assert (snap / DOTS_FILE).read_bytes() == b"dots-weights"
    assert not (snap / BREEZE_FILE).exists()


def test_install_downloads_the_package_from_its_hub_repo(download, monkeypatch, tmp_path):
    import huggingface_hub
    from services import hf_revisions

    calls, remembered = [], []

    def fake_snapshot(**kwargs):
        calls.append(kwargs)
        return [] if kwargs.get("dry_run") else str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr(download, "compute_plan", lambda _plan: {
        "total_bytes": 1, "cached_bytes": 0, "to_download_bytes": 1,
        "n_files": 1, "n_cached": 0,
    })
    monkeypatch.setattr(download, "disk_space_error", lambda *_a, **_k: None)
    monkeypatch.setattr(download, "_validate_snapshot_has_weights", lambda *_a: None)
    monkeypatch.setattr(hf_revisions, "remember_revision", lambda *a: remembered.append(a))
    from services import performance_profiles
    monkeypatch.setattr(performance_profiles, "reconcile_active_profile", lambda: {})

    async def run():
        result = await download.install_model(download.InstallModelRequest(repo_id=DOTS))
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
        return result

    assert asyncio.run(run()) == {"status": "install_started", "repo_id": DOTS}
    assert {call["repo_id"] for call in calls} == {REPO}
    assert all(call["allow_patterns"] == [DOTS_FILE] for call in calls)
    assert all(call["revision"] == hf_revisions.revision_for(REPO) for call in calls)
    assert remembered and remembered[0][0] == REPO


def test_licence_records_resolve_package_identities():
    from services import model_acceptance, model_licenses

    info = model_licenses.disclosure(DOTS)
    assert info["license_category"] == "commercial"
    assert model_acceptance.status(DOTS)["state"] == "not_required"
    assert model_acceptance.repos_for_engine("audiocpp-dots") == [DOTS]
    # The Breeze bundle keeps its own (non-commercial) record.
    assert model_licenses.disclosure(REPO)["license_category"] == "noncommercial"


def test_engine_disk_usage_counts_only_its_package(models, cache):
    from services import engine_disk_usage

    _snapshot(cache, {BREEZE_FILE: b"breeze", SORTFORMER_FILE: b"sortformer", DOTS_FILE: b"dots"})
    assert engine_disk_usage._measure_model_cache("audiocpp-dots")["model_bytes"] == len(b"dots")
    assert engine_disk_usage._measure_model_cache("audiocpp")["model_bytes"] == len(
        b"breezesortformer"
    )


def test_services_read_the_catalogue_without_importing_a_router():
    # services/ must not import api.routers (CodeQL cyclic import): the shared
    # catalogue and cache helpers live in services.model_catalogue, and the
    # router re-exports the same objects.
    import ast

    backend = Path(__file__).resolve().parents[1] / "backend"
    source = (backend / "services" / "engine_disk_usage.py").read_text(encoding="utf-8")
    imported = {
        node.module for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(name.startswith("api.") for name in imported)
    catalogue = importlib.import_module("services.model_catalogue")
    router = importlib.import_module("api.routers.setup.models")
    for name in ("model_key", "shares_repo", "package_cache_usage", "KNOWN_MODELS"):
        assert getattr(router, name) is getattr(catalogue, name)
    assert catalogue.catalogue_entry("audio-cpp/audio.cpp-gguf:dots-tts-soar-q8_0")["repo_id"] == \
        "audio-cpp/audio.cpp-gguf"
