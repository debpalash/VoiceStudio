"""Regression guard for the bundled model catalog (``backend/config/models.yaml``).

Issue #239: two ASR entries pointed at Hugging Face repo IDs that returned
HTTP 404 ("Repository Not Found"), so users could not install those ASR models:

* ``UsefulSensors/moonshine-small`` — no such model (Moonshine ships *tiny* /
  *base*, there is no 300M "small").
* ``Systran/faster-whisper-large-v3-turbo`` — Systran publishes no turbo repo.

These tests are intentionally **static** (no network) so they run in CI: they
assert every ``repo_id`` is well-formed and that the known-bad IDs can never be
reintroduced. They do not verify live HF availability — that would be flaky and
slow — but they stop the catalog from shipping the exact IDs that broke #239.
"""
import re
from pathlib import Path

import yaml

_YAML = Path(__file__).resolve().parents[1] / "backend" / "config" / "models.yaml"
# org-or-user / repo-name, both segments HF-legal (letters, digits, _, -, .).
_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
# Repo IDs that returned HTTP 404 on Hugging Face (issue #239). Must never reappear.
_KNOWN_BAD = {
    "UsefulSensors/moonshine-small",
    "Systran/faster-whisper-large-v3-turbo",
}


def _models():
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return data["models"] if isinstance(data, dict) and "models" in data else data


def test_catalog_loads_and_has_entries():
    models = _models()
    assert isinstance(models, list) and len(models) > 0


def test_every_repo_id_is_well_formed():
    for m in _models():
        rid = m.get("repo_id")
        assert rid and _REPO_RE.match(rid), f"malformed repo_id: {rid!r}"


def _non_blank_list(value):
    return (
        isinstance(value, list) and bool(value)
        and all(isinstance(x, str) and x.strip() for x in value)
    )


def test_config_only_entries_name_their_required_files():
    for m in _models():
        if m.get("config_only"):
            assert _non_blank_list(m.get("config_required_files")), (
                f"{m.get('repo_id')!r} is config_only but lists no config_required_files"
            )


def test_dependency_entries_are_well_formed():
    for m in _models():
        for dep in m.get("dependencies") or []:
            where = f"{m.get('repo_id')!r} dependency {dep!r}"
            assert _REPO_RE.match(str(dep.get("repo_id") or "")), where
            assert _non_blank_list(dep.get("required_files")), where


def test_required_fields_present():
    for m in _models():
        for field in ("repo_id", "label", "role"):
            assert m.get(field), f"{m.get('repo_id')!r} missing required field {field!r}"


def test_known_404_repo_ids_absent():
    ids = {m.get("repo_id", "") for m in _models()}
    leaked = ids & _KNOWN_BAD
    assert not leaked, f"known-404 repo IDs reintroduced (issue #239): {leaked}"


# ── TTS-only required gate + curated_on (per-platform curation) ─────────────
# The app must boot with just the TTS model: ASR is optional and surfaced as
# per-platform curated picks (curated_on) instead of a required download.

_CURATED_TAGS = {"all", "darwin-arm64", "darwin-x86_64", "cuda", "rocm", "cpu"}


def test_only_tts_is_required():
    required = [m for m in _models() if m.get("required")]
    assert [m["repo_id"] for m in required] == ["k2-fsa/OmniVoice"], (
        "exactly one required model is allowed — the TTS engine. ASR models "
        f"are optional curated picks (curated_on). Got: {required}"
    )
    assert required[0]["role"] == "TTS"


def test_curated_on_tags_are_valid():
    for m in _models():
        for tag in m.get("curated_on", []) or []:
            assert tag in _CURATED_TAGS, (
                f"{m['repo_id']}: unknown curated_on tag {tag!r} "
                f"(valid: {sorted(_CURATED_TAGS)})"
            )


def test_every_platform_has_a_curated_asr_pick():
    """Each host family must resolve at least one curated offline-capable ASR
    model, or the ASR-missing download CTA would have nothing to offer."""
    models = _models()
    for family_tags in (
        {"darwin", "darwin-arm64"},
        {"darwin", "darwin-x86_64", "cpu"},
        {"win32", "win32-AMD64", "cuda"},
        {"linux", "linux-x86_64", "cuda", "rocm"},
        {"linux", "linux-x86_64", "cpu"},
    ):
        curated_asr = [
            m for m in models
            if m.get("role") == "ASR"
            and (
                "all" in (m.get("curated_on") or [])
                or set(m.get("curated_on") or []) & family_tags
            )
            and (not m.get("platforms") or set(m["platforms"]) & family_tags)
        ]
        assert curated_asr, f"no curated ASR pick resolves for host tags {family_tags}"


def test_single_cross_platform_dictation_recommendation_is_whisper_tiny():
    """Installer curation must match the runtime/UI dictation default."""
    sherpa = [m for m in _models() if m.get("engine") == "sherpa-onnx"]
    recommended = [m["dictation_id"] for m in sherpa if "all" in (m.get("curated_on") or [])]
    assert recommended == ["sherpa-whisper-tiny"]
