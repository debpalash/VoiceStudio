"""The mobile apps build against committed, generated contracts.

Both fail when the source they were generated from changes without a
regeneration in the same PR, so iOS and Android never drift from the backend
API or the desktop theme.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "mobile" / "contract"

sys.path.insert(0, str(ROOT / "scripts"))
import export_openapi  # noqa: E402


def test_openapi_contract_matches_backend():
    committed = (CONTRACT / "openapi.json").read_text(encoding="utf-8")
    assert committed == export_openapi.render(export_openapi.build_schema()), (
        "mobile/contract/openapi.json is stale; run `uv run python scripts/export_openapi.py`"
    )


def test_openapi_operation_ids_are_unique():
    schema = json.loads((CONTRACT / "openapi.json").read_text(encoding="utf-8"))
    ids = [op["operationId"] for ops in schema["paths"].values() for op in ops.values() if "operationId" in op]
    assert len(ids) == len(set(ids))


def _bun() -> str | None:
    """Full path to bun, as found on PATH (honouring PATHEXT on Windows).

    Spawn this, never a bare "bun": an npm-installed bun on Windows is only
    `bun.cmd`/`bun.ps1`, which `shutil.which` finds but CreateProcess does not.
    """
    return shutil.which("bun")


def _electron_deps_installed() -> bool:
    # Runs at collection (skipif), so it must never raise: one failed spawn
    # here would abort the whole pytest run instead of skipping one test.
    bun = _bun()
    if bun is None:
        return False
    try:
        probe = subprocess.run(
            [bun, "--no-install", "-e", "Bun.resolveSync('tailwindcss/theme.css', process.argv[1])", str(ROOT / "electron")],
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


# ci.yml also runs `bun scripts/export-mobile-tokens.ts --check` after
# `bun install`, so this skip can't hide drift on CI.
@pytest.mark.skipif(not _electron_deps_installed(), reason="needs bun and installed Electron deps")
def test_mobile_tokens_match_electron_theme():
    result = subprocess.run(
        [_bun(), "--no-install", "scripts/export-mobile-tokens.ts", "--check"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_bun_probe_spawns_resolved_path_and_never_raises(monkeypatch):
    shim = r"C:\npm\bun.CMD"
    monkeypatch.setattr(shutil, "which", lambda name: shim if name == "bun" else None)
    spawned = []

    def fake_run(argv, **_kwargs):
        spawned.append(argv[0])
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _electron_deps_installed() is False
    assert spawned == [shim]


def test_tests_never_spawn_bare_bun():
    bare = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in (ROOT / "tests").rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"subprocess\.\w+\(\s*\[\s*[\"']bun[\"']", line)
    ]
    assert not bare, f"spawn the shutil.which('bun') path instead of a bare 'bun': {bare}"


def test_mobile_string_keys_are_namespaced():
    for path in CONTRACT.glob("strings*.en.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert list(data) == ["mobile"], f"{path.name}: new mobile keys belong under `mobile.`"


def test_openapi_contract_is_version_neutral():
    # The app version differs by environment and changes every release; a
    # contract that embedded it would go stale on every version bump.
    schema = {"info": {"version": "9.8.7"}, "components": {"x": {"default": "9.8.7"}}}
    rendered = json.loads(export_openapi.render(schema))
    assert "9.8.7" not in json.dumps(rendered)
    committed = json.loads((CONTRACT / "openapi.json").read_text(encoding="utf-8"))
    assert committed["info"]["version"] == export_openapi.VERSION_PLACEHOLDER


# M2: the catalogue is a source contract consumed by both native decoders.
import export_mobile_models as mobile_models  # noqa: E402


def _services():
    # Resolved at run time: other suites purge services.* after collection, so a
    # module-level binding could point at a stale module.
    import importlib

    return importlib.import_module("services.model_acceptance"), importlib.import_module("services.model_licenses")


def _mobile_models_source():
    return mobile_models.read_json(CONTRACT / "models.source.json")


def _mobile_licenses():
    return mobile_models.read_json(ROOT / "docs/licensing/model-license-data.json")


def test_mobile_models_generated_catalogue_is_current(monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Default catalogue export must be entirely offline")

    monkeypatch.setattr(mobile_models, "urlopen", no_network)
    actual = (CONTRACT / "models.json").read_text(encoding="utf-8")
    expected = mobile_models.render(mobile_models.build_catalogue(_mobile_models_source(), _mobile_licenses()))
    assert actual == expected, "Run `uv run python scripts/export_mobile_models.py`"


def test_mobile_models_are_pinned_and_have_complete_manifests():
    source = _mobile_models_source()
    mobile_models.validate_source(source)
    catalogue = json.loads((CONTRACT / "models.json").read_text(encoding="utf-8"))
    assert catalogue["schema_version"] == 1
    assert len(catalogue["models"]) == len(source["models"])
    ids = [model["id"] for model in catalogue["models"]]
    assert len(ids) == len(set(ids))
    assert {model["capability"] for model in catalogue["models"]} == set(mobile_models.CAPABILITIES)
    for model in catalogue["models"]:
        assert model["runtime"] in mobile_models.RUNTIMES[model["capability"]]
        assert mobile_models.REVISION.fullmatch(model["revision"])
        assert model["total_bytes"] == sum(file["size"] for file in model["files"])
        for file in model["files"]:
            assert mobile_models.SHA256.fullmatch(file["sha256"])
            assert type(file["size"]) is int and file["size"] > 0
            assert file["role"] in mobile_models.ROLES
            assert "test_wavs" not in file["path"]
            if model["capability"] in {"asr", "asr-stream"} and file["path"].endswith(".onnx"):
                assert file["path"].endswith(".int8.onnx"), "Do not bundle fp32 duplicates"


def test_mobile_models_registry_licence_gates_and_fingerprints():
    model_acceptance, model_licenses = _services()
    registry = model_licenses.load_registry()
    rows = {row["id"]: row for row in registry["models"]}
    projected = {row["id"]: row for row in _mobile_licenses()["models"]}
    catalogue = mobile_models.build_catalogue(_mobile_models_source(), _mobile_licenses())
    for model in catalogue["models"]:
        assert model["repo"] in rows
        info = model_licenses.disclosure(model["repo"], registry)
        licence = model["licence"]
        assert projected[model["repo"]]["license_category"] == info["license_category"]
        assert licence["category"] == mobile_models.CATEGORIES[info["license_category"]]
        assert licence["requires_acceptance"] is (info["license_category"] != "commercial")
        assert licence["terms_fingerprint"] == model_acceptance.fingerprint(info, model["repo"])[3:]
        assert licence["terms"] == model_acceptance.terms(info, model["repo"])
        assert licence["credit"] and licence["rights_holder"] and licence["id"]
    omni = next(model for model in catalogue["models"] if model["capability"] == "clone")
    desktop = json.loads((ROOT / "backend/engines/omnivoice_gguf/quant_map.json").read_text())["_meta"]
    assert (omni["repo"], omni["revision"]) == (desktop["source_model"], desktop["source_commit_sha"])
    assert omni["licence"]["category"] == "non-commercial"
    assert omni["licence"]["requires_acceptance"] is True
    assert {file["path"] for file in omni["files"]} == {
        "omnivoice-base-Q8_0.gguf", "omnivoice-tokenizer-F32.gguf",
    }
    assert omni["min_ram_gb"] >= 8
    assert omni["config"]["requires_reference_audio"] is True
    translation = next(model for model in catalogue["models"] if model["capability"] == "translate")
    assert translation["total_bytes"] <= 1_200_000_000
    assert translation["licence"]["category"] == "commercial-ok"
    assert "{text}" in translation["config"]["prompt_template"]
    assert "{target_language}" in translation["config"]["prompt_template"]
    assert all(model["min_ram_gb"] >= 6 for model in (omni, translation))


@pytest.mark.parametrize("broken", ["hash", "size", "path", "role", "duplicate-file", "duplicate-id", "runtime", "revision", "ram"])
def test_mobile_models_reject_unusable_manifests(broken):
    source = _mobile_models_source()
    model = source["models"][0]
    if broken == "hash":
        model["files"][0]["sha256"] = "abc"
    elif broken == "size":
        model["files"][0]["size"] = 0
    elif broken == "path":
        model["files"][0]["path"] = "../outside.onnx"
    elif broken == "role":
        model["files"][0]["role"] = "unsupported"
    elif broken == "duplicate-file":
        model["files"].append(copy.deepcopy(model["files"][0]))
    elif broken == "duplicate-id":
        source["models"].append(copy.deepcopy(model))
    elif broken == "runtime":
        model["runtime"] = "llama.cpp"
    elif broken == "revision":
        model["revision"] = "main"
    else:
        next(row for row in source["models"] if row["capability"] == "clone")["min_ram_gb"] = 3
    with pytest.raises(ValueError):
        mobile_models.build_catalogue(source, _mobile_licenses())


def test_mobile_models_missing_registry_record_is_an_error():
    licenses = _mobile_licenses()
    repo = _mobile_models_source()["models"][0]["repo"]
    licenses["models"] = [row for row in licenses["models"] if row["id"] != repo]
    with pytest.raises(ValueError, match="missing registry entry"):
        mobile_models.build_catalogue(_mobile_models_source(), licenses)


def test_mobile_models_changed_terms_invalidate_acceptance():
    # Use a known registry disclosure: registry additions are tested above.
    source = _mobile_models_source()
    source["models"] = [next(row for row in source["models"] if row["capability"] == "clone")]
    licenses = _mobile_licenses()
    before = mobile_models.build_catalogue(source, licenses)["models"][0]["licence"]
    row = next(row for row in licenses["models"] if row["id"] == source["models"][0]["repo"])
    row["evidence_checked_at"] = "2099-01-01"
    unchanged = mobile_models.build_catalogue(source, licenses)["models"][0]["licence"]
    assert before["terms_fingerprint"] == unchanged["terms_fingerprint"]
    row["notes"] += " Terms have changed."
    changed = mobile_models.build_catalogue(source, licenses)["models"][0]["licence"]
    assert before["terms_fingerprint"] != changed["terms_fingerprint"]


@pytest.mark.parametrize("category", ["commercial", "conditions", "noncommercial", "unknown", "unmapped"])
def test_mobile_models_acceptance_fails_closed(category):
    source = _mobile_models_source()
    source["models"] = [next(row for row in source["models"] if row["capability"] == "clone")]
    licenses = _mobile_licenses()
    row = next(row for row in licenses["models"] if row["id"] == source["models"][0]["repo"])
    row["license_category"] = category
    licence = mobile_models.build_catalogue(source, licenses)["models"][0]["licence"]
    assert licence["requires_acceptance"] is (category != "commercial")


@pytest.mark.parametrize("corrupt", [False, True])
def test_mobile_models_pin_uses_lfs_oid_and_verifies_git_blobs(monkeypatch, corrupt):
    source = _mobile_models_source()
    source["models"] = [next(row for row in source["models"] if row["capability"] == "asr")]
    original = copy.deepcopy(source)
    model = source["models"][0]
    revision = "a" * 40
    siblings = []
    responses = {}
    lfs_sha = "b" * 64
    for index, file in enumerate(model["files"]):
        path = file["path"]
        payload = f"contents of {path}".encode()
        if index == 0:
            siblings.append({"rfilename": path, "size": 123, "lfs": {"size": 123, "sha256": lfs_sha}})
        else:
            git_blob = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload, usedforsecurity=False).hexdigest()
            siblings.append({"rfilename": path, "size": len(payload), "blobId": "0" * 40 if corrupt else git_blob})
            responses[f"/{model['repo']}/resolve/{revision}/{path}"] = payload
    responses[f"/api/models/{model['repo']}/revision/main?blobs=true"] = json.dumps({"sha": revision, "siblings": siblings}).encode()
    requests = []

    def request(path):
        requests.append(path)
        return io.BytesIO(responses[path])

    monkeypatch.setattr(mobile_models, "_open_hf", request)
    if corrupt:
        with pytest.raises(ValueError, match="blob/size mismatch"):
            mobile_models.pin_source(source)
    else:
        pinned = mobile_models.pin_source(source)["models"][0]
        assert pinned["revision"] == revision
        assert pinned["files"][0]["sha256"] == lfs_sha
        assert pinned["files"][0]["size"] == 123
        expected_git_payload = responses[f"/{model['repo']}/resolve/{revision}/{model['files'][1]['path']}"]
        assert pinned["files"][1]["sha256"] == hashlib.sha256(expected_git_payload).hexdigest()
        assert len(requests) == 3, "LFS model weights must not be downloaded to discover SHA-256"
    assert source == original, "Partial pinning must not change the caller's manifest"


def test_mobile_models_check_command_detects_stale_output(monkeypatch, tmp_path, capsys):
    output = tmp_path / "models.json"
    monkeypatch.setattr(mobile_models, "OUTPUT", output)
    monkeypatch.setattr(sys, "argv", ["export_mobile_models.py", "--check"])
    assert mobile_models.main() == 1
    assert "stale" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["export_mobile_models.py"])
    assert mobile_models.main() == 0
    monkeypatch.setattr(sys, "argv", ["export_mobile_models.py", "--check"])
    assert mobile_models.main() == 0
    data = json.loads(output.read_text())
    data["models"][0]["total_bytes"] += 1
    output.write_text(json.dumps(data))
    assert mobile_models.main() == 1


def test_mobile_models_pin_retains_desktop_omnivoice_revision(monkeypatch):
    source = _mobile_models_source()
    source["models"] = [next(row for row in source["models"] if row["capability"] == "clone")]
    model = source["models"][0]
    expected = json.loads((ROOT / "backend/engines/omnivoice_gguf/quant_map.json").read_text())["_meta"]
    requests = []

    def request(path):
        requests.append(path)
        siblings = [{"rfilename": f["path"], "size": f["size"], "lfs": {"size": f["size"], "sha256": f["sha256"]}} for f in model["files"]]
        return io.BytesIO(json.dumps({"sha": expected["source_commit_sha"], "siblings": siblings}).encode())

    monkeypatch.setattr(mobile_models, "_open_hf", request)
    pinned = mobile_models.pin_source(source)["models"][0]
    assert pinned["revision"] == expected["source_commit_sha"]
    assert requests == [f"/api/models/{expected['source_model']}/revision/{expected['source_commit_sha']}?blobs=true"]


def test_mobile_models_pin_falls_back_only_to_hf_mirror(monkeypatch):
    urls = []

    def request(url, timeout):
        urls.append(url)
        if len(urls) == 1:
            raise mobile_models.URLError("unreachable")
        return io.BytesIO(b"mirror response")

    monkeypatch.setattr(mobile_models, "urlopen", request)
    with mobile_models._open_hf("/api/models/example/model") as response:
        assert response.read() == b"mirror response"
    assert urls == ["https://huggingface.co/api/models/example/model", "https://hf-mirror.com/api/models/example/model"]


def test_mobile_models_have_display_names():
    # Screens show `name`, never the id, repo or licence code (SPEC.md M2 Models).
    models = json.loads((CONTRACT / "models.json").read_text(encoding="utf-8"))["models"]
    for model in models:
        assert isinstance(model.get("name"), str) and model["name"].strip(), model["id"]
        assert model["name"] != model["id"], model["id"]


def test_mobile_strings_are_not_placeholder_only():
    # A value of only {{placeholders}} and punctuation is a missing string, and
    # tests/test_locale_parity.py rejects it in every locale. Catch it here, at the
    # source, before 20 translations copy it (this happened twice).
    import re

    placeholder = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def walk(node, path=""):
        for key, value in node.items():
            if isinstance(value, dict):
                yield from walk(value, f"{path}{key}.")
            else:
                yield f"{path}{key}", value

    bad = []
    for path in CONTRACT.glob("strings*.en.json"):
        for key, value in walk(json.loads(path.read_text(encoding="utf-8"))):
            if isinstance(value, str) and placeholder.search(value) and not re.search(r"\w", placeholder.sub("", value)):
                bad.append(f"{path.name}: {key} = {value!r}")
    assert not bad, "write the words around the placeholders:\n" + "\n".join(bad)


@pytest.mark.parametrize("data_filter", [True, False])
@pytest.mark.parametrize("entry", ["../escape.txt", "/abs.txt", "link-out"])
def test_android_native_fetch_rejects_unsafe_archives(tmp_path, monkeypatch, data_filter, entry):
    # Pinned source tarballs are checksum-verified, but extraction must still refuse
    # path escapes and links out of the tree, on Pythons with and without
    # tarfile's data filter (CodeQL py/tarslip).
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location("vs_android_fetch", ROOT / "mobile/native/android/fetch.py")
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    if not data_filter:
        monkeypatch.delattr(tarfile, "data_filter", raising=False)
    archive = tmp_path / "src.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        if entry == "link-out":
            info = tarfile.TarInfo("src/link-out")
            info.type, info.linkname = tarfile.SYMTYPE, "../../outside"
            tar.addfile(info)
        else:
            payload = b"x"
            info = tarfile.TarInfo(entry)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    out = tmp_path / "out"
    try:
        fetch.extract(archive, out)
    except Exception:
        assert not out.exists()
    else:
        # The data filter may instead neutralize an entry (e.g. strip a leading
        # '/'); then everything must have landed inside the target.
        assert entry == "/abs.txt" and data_filter
        assert all(out.resolve() in p.resolve().parents or p.resolve() == out.resolve() for p in out.rglob("*"))
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path.parent / "outside").exists()


def test_ios_home_contributors_match_electron_snapshot():
    """The iOS Home cluster mirrors Electron's bundled snapshot, avatars included (offline)."""
    import re

    data = (ROOT / "electron/src/renderer/src/features/home/home-contributors-data.ts").read_text()
    electron = re.findall(r"login: '([^']+)'", data)
    swift = (ROOT / "mobile/ios/VoiceStudio/Features/HomeContributors.swift").read_text()
    ios = re.findall(r'"([^"]+)"', re.search(r"static let logins = \[(.*?)\]", swift, re.S).group(1))
    assert ios == electron
    core = re.findall(r"'([^']+)'", re.search(r"homeCoreMaintainers = \[(.*?)\]", data, re.S).group(1))
    ios_core = re.findall(r'"([^"]+)"', re.search(r"coreMaintainers: Set<String> = \[(.*?)\]", swift, re.S).group(1))
    assert set(ios_core) == set(core) == {"debpalash", "velixio"}
    for login in electron:
        assert (ROOT / f"mobile/ios/VoiceStudio/Resources/Contributors.xcassets/contributor-{login}.imageset/{login}.png").is_file()


def test_android_home_contributors_match_electron_snapshot():
    import re

    electron = re.findall(r"login: '([^']+)'", (ROOT / "electron/src/renderer/src/features/home/home-contributors-data.ts").read_text())
    kotlin = (ROOT / "mobile/android/features/src/main/kotlin/com/voicestudio/mobile/features/HomeContributors.kt").read_text()
    android = re.findall(r'"([^"]+)"', re.search(r"val logins = listOf\((.*?)\)", kotlin, re.S).group(1))
    assert android == electron
    data = (ROOT / "electron/src/renderer/src/features/home/home-contributors-data.ts").read_text()
    core = re.findall(r"'([^']+)'", re.search(r"homeCoreMaintainers = \[(.*?)\]", data, re.S).group(1))
    android_core = re.findall(r'"([^"]+)"', re.search(r"coreMaintainers = setOf\((.*?)\)", kotlin, re.S).group(1))
    assert set(android_core) == set(core) == {"debpalash", "velixio"}
    for login in electron:  # the Gradle catalog task copies these into the APK assets
        assert list((ROOT / "electron/src/renderer/src/features/home/contributors").glob(f"{login}.*"))


@pytest.mark.skipif(_bun() is None, reason="needs bun")
def test_mobile_voice_design_traits_match_electron():
    result = subprocess.run([_bun(), "scripts/export-mobile-design.ts", "--check"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    catalogue = json.loads((CONTRACT / "design.json").read_text())
    locales = [json.loads(path.read_text()) for path in (ROOT / "electron/src/renderer/src/i18n/locales").glob("*.json")]
    for category in catalogue["categories"]:
        for key in [category["label"], *[option["label"] for option in category["options"]]]:
            for locale in locales:
                value = locale
                for part in key.split("."):
                    value = value[part]
                assert isinstance(value, str) and value


def test_speech_catalogue_offers_native_model_families_with_honest_reference_requirements():
    models=json.loads((CONTRACT / "models.json").read_text())["models"]
    families={m["config"].get("model_type"):m for m in models if m["capability"] in {"tts","clone"}}
    assert {"kitten","kokoro","vits","supertonic","pocket","zipvoice"} <= families.keys()
    for family in ("pocket","zipvoice"):
        assert families[family]["config"]["reference_required"] is True
        assert families[family]["config"]["supports_cloning"] is True
        assert families[family]["config"]["num_speakers"] == 0


@pytest.mark.parametrize("url,accepted", [
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos-24khz.onnx", True),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/nested/vocos.onnx", False),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/./vocos.onnx", False),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/../vocos.onnx", False),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/model%20name.onnx", False),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/model name.onnx", False),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos.onnx?raw=1", False),
    ("https://user@github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos.onnx", False),
    ("https://github.com:443/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos.onnx", False),
    ("https://github.com:invalid/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos.onnx", False),
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos.onnx#fragment", False),
    ("https://github.com/other/project/releases/download/v1/vocos.onnx", False),
    ("http://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/vocos.onnx", False),
])
def test_mobile_release_asset_urls_match_native_boundaries(url, accepted):
    assert mobile_models.valid_asset_url(url) is accepted
