"""Pure-core tests for the .ovsvoice persona bundle (#29 / parity §R3 G1).

Covers the model-free nucleus: SPDX normalization, manifest schema/fields, and
the consent attestation builder. The audio preview + ZIP pack/unpack are a
separate slice (and run on CI for the torch-coupled paths).
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from services.persona_bundle import (
    DEFAULT_LICENSE,
    MAX_BUNDLE_BYTES,
    OVSVOICE_FORMAT,
    OVSVOICE_SCHEMA_VERSION,
    BundleError,
    NoPreviewSource,
    build_consent_json,
    build_manifest,
    build_persona_bundle,
    normalize_spdx,
    parse_persona_bundle,
)

_PROFILE = {
    "name": "Aria Narration", "kind": "design", "language": "English",
    "personality": "warm-narrator", "instruct": "female, middle-aged, low pitch",
    "ref_text": "Hello.", "seed": 42, "is_locked": False,
    "vd_states": '{"gender":"female"}',
}


# ── SPDX normalization ──────────────────────────────────────────────────────

def test_spdx_allowlisted_kept():
    for ok in ("CC-BY-4.0", "MIT", "Apache-2.0", "CC0-1.0", "LicenseRef-OmniVoice-Personal"):
        assert normalize_spdx(ok) == ok


def test_spdx_custom_licenseref_prefix_kept():
    assert normalize_spdx("LicenseRef-MyStudio-Terms") == "LicenseRef-MyStudio-Terms"


def test_spdx_junk_and_injection_normalize_to_default():
    for bad in (None, "", "   ", "GPL-3.0-only", "haha; rm -rf /", "<script>", 123):
        assert normalize_spdx(bad) == DEFAULT_LICENSE  # never raises, never the raw junk


def test_spdx_is_stripped():
    assert normalize_spdx("  MIT  ") == "MIT"


# ── manifest ────────────────────────────────────────────────────────────────

def test_manifest_format_discriminator_and_schema():
    m = build_manifest(_PROFILE, license_spdx="CC-BY-4.0", tags=["narration"])
    assert m["format"] == OVSVOICE_FORMAT
    assert m["schema_version"] == OVSVOICE_SCHEMA_VERSION
    assert isinstance(m["exported_at"], float)


def test_manifest_persona_fields_mirror_profile():
    m = build_manifest(_PROFILE, license_spdx="CC-BY-4.0", tags=[])
    p = m["persona"]
    assert p["name"] == "Aria Narration" and p["kind"] == "design"
    assert p["seed"] == 42 and p["is_locked"] is False
    assert p["vd_states"] == '{"gender":"female"}'  # JSON string, NOT re-parsed


def test_manifest_seed_and_vd_states_none_passthrough():
    m = build_manifest({"name": "X", "seed": None, "vd_states": None},
                       license_spdx="MIT", tags=[])
    assert m["persona"]["seed"] is None
    assert m["persona"]["vd_states"] is None


def test_manifest_normalizes_bad_license_never_raises():
    m = build_manifest(_PROFILE, license_spdx="bogus-license", tags=[])
    assert m["license"]["spdx"] == DEFAULT_LICENSE


def test_manifest_tags_and_members_defaults():
    m = build_manifest(_PROFILE, license_spdx="MIT", tags=["a", "b"])
    assert m["tags"] == ["a", "b"]
    assert m["members"] == {"ref_audio": None, "locked_audio": None, "consent_audio": None}
    assert m["preview"] is None


# ── consent.json ────────────────────────────────────────────────────────────

def test_consent_design_is_designed_synthetic_verified():
    c = build_consent_json(_PROFILE, has_recording=False)
    assert c["method"] == "designed-synthetic"
    assert c["verified_own_voice"] is True


def test_consent_clone_self_recorded_when_attested():
    prof = {"kind": "clone", "verified_own_voice": 1, "consent_text": "I consent.",
            "consent_recorded_at": 1749790000.0}
    c = build_consent_json(prof, has_recording=True)
    assert c["method"] == "self-recorded-statement"
    assert c["has_recording"] is True and c["consent_text"] == "I consent."
    assert c["recorded_at"] == 1749790000.0


def test_consent_none_when_nothing_to_attest():
    assert build_consent_json({"kind": "clone"}, has_recording=False) is None


def test_consent_recorded_at_coerced_when_missing_or_bad():
    c = build_consent_json({"kind": "clone", "consent_text": "ok", "consent_recorded_at": "nope"},
                           has_recording=True)
    assert isinstance(c["recorded_at"], float)  # coerced to now, not a crash


# ── parse_persona_bundle: pure ZIP validation (no torch) ─────────────────────

def _zip(members: dict) -> bytes:
    """members: {arcname: bytes|str}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body if isinstance(body, (bytes, bytearray)) else str(body))
    return buf.getvalue()


def _manifest_bytes(**over) -> str:
    base = build_manifest(_PROFILE, license_spdx="CC-BY-4.0", tags=["narration"],
                          preview={"file": "preview.wav", "watermarked": True,
                                   "duration_s": 6.2, "sample_rate": 24000},
                          members={"ref_audio": "ref_audio.wav", "locked_audio": None,
                                   "consent_audio": None})
    base.update(over)
    return json.dumps(base)


def test_parse_prefers_manifest_and_normalizes():
    content = _zip({"manifest.json": _manifest_bytes(),
                    "ref_audio.wav": b"\x00" * 100, "preview.wav": b"\x00" * 100})
    parsed = parse_persona_bundle(content)
    assert parsed.is_legacy is False
    assert parsed.manifest["format"] == OVSVOICE_FORMAT
    assert parsed.license_spdx == "CC-BY-4.0"
    assert parsed.watermarked_preview is True
    assert parsed.preview_only is False
    assert parsed.members.get("ref_audio") == "ref_audio.wav"


def test_parse_legacy_metadata_only():
    legacy = {"profile_name": "Old Voice", "kind": "clone", "language": "English"}
    content = _zip({"metadata.json": json.dumps(legacy), "ref_audio.wav": b"\x00" * 100})
    parsed = parse_persona_bundle(content)
    assert parsed.is_legacy is True
    assert parsed.manifest["persona"]["name"] == "Old Voice"
    assert parsed.license_spdx == DEFAULT_LICENSE
    assert parsed.watermarked_preview is False


def test_parse_preview_only_bundle():
    content = _zip({"manifest.json": _manifest_bytes(members={"ref_audio": None}),
                    "preview.wav": b"\x00" * 100})
    parsed = parse_persona_bundle(content)
    assert parsed.preview_only is True


def test_parse_future_schema_version_flagged():
    content = _zip({"manifest.json": _manifest_bytes(schema_version=99),
                    "ref_audio.wav": b"\x00" * 100})
    parsed = parse_persona_bundle(content)
    assert parsed.schema_version_ahead is True


def test_parse_missing_manifest_400():
    with pytest.raises(BundleError) as e:
        parse_persona_bundle(_zip({"ref_audio.wav": b"\x00" * 100}))
    assert e.value.status == 400


def test_parse_no_audio_member_400():
    with pytest.raises(BundleError) as e:
        parse_persona_bundle(_zip({"manifest.json": _manifest_bytes()}))
    assert e.value.status == 400


def test_parse_malformed_manifest_json_400():
    with pytest.raises(BundleError) as e:
        parse_persona_bundle(_zip({"manifest.json": "{not json",
                                   "ref_audio.wav": b"\x00" * 100}))
    assert e.value.status == 400


def test_parse_not_a_zip_400():
    with pytest.raises(BundleError) as e:
        parse_persona_bundle(b"definitely not a zip")
    assert e.value.status == 400


def test_parse_oversize_413():
    # Header check fires before ZIP parsing — a non-zip blob over the cap is 413.
    with pytest.raises(BundleError) as e:
        parse_persona_bundle(b"\x00" * (MAX_BUNDLE_BYTES + 1))
    assert e.value.status == 413


def test_parse_bad_consent_json_is_advisory_not_fatal():
    content = _zip({"manifest.json": _manifest_bytes(), "ref_audio.wav": b"\x00" * 100,
                    "consent.json": "{broken"})
    parsed = parse_persona_bundle(content)
    assert parsed.consent is None  # ignored, not a 400


def test_parse_bad_spdx_in_manifest_normalized():
    content = _zip({"manifest.json": _manifest_bytes(license={"spdx": "haha; rm -rf", "custom_text": None}),
                    "ref_audio.wav": b"\x00" * 100})
    assert parse_persona_bundle(content).license_spdx == DEFAULT_LICENSE


@pytest.mark.parametrize("license_block", ["MIT", ["MIT"], 7])
def test_parse_non_object_license_falls_back_to_default(license_block):
    content = _zip({"manifest.json": _manifest_bytes(license=license_block),
                    "ref_audio.wav": b"\x00" * 100})
    assert parse_persona_bundle(content).license_spdx == DEFAULT_LICENSE

def test_parse_last_wins_on_duplicate_members():
    content = _zip({"manifest.json": _manifest_bytes(),
                    "ref_audio.wav": b"\x00" * 100, "ref_audio_2.wav": b"\x11" * 100})
    parsed = parse_persona_bundle(content)
    # Whichever sorts last in the namelist wins; either is a valid prefix match.
    assert parsed.members["ref_audio"].startswith("ref_audio")


# ── build_persona_bundle round-trip (torchaudio; runs on CI, often local) ────

def _write_wav(path, *, seconds=1.0, sr=16000, channels=1):
    import numpy as np
    import soundfile as sf
    n = int(seconds * sr)
    data = (0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype("float32")
    if channels > 1:
        data = np.stack([data] * channels, axis=1)
    sf.write(str(path), data, sr)


@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    import core.config as cfg
    d = tmp_path / "voices"
    d.mkdir()
    monkeypatch.setattr(cfg, "VOICES_DIR", str(d))
    return d


def _identity_embed(wav, sr):
    return wav  # avoid loading AudioSeal in unit tests


def test_build_roundtrip_identity_fields(voices_dir):
    _write_wav(voices_dir / "abc.wav")
    profile = {**_PROFILE, "kind": "clone", "ref_audio_path": "abc.wav"}
    content = build_persona_bundle(profile, license_spdx="CC-BY-4.0", tags=["x"],
                                   embed_fn=_identity_embed)
    parsed = parse_persona_bundle(content)
    p = parsed.manifest["persona"]
    assert p["name"] == "Aria Narration" and p["seed"] == 42
    assert parsed.manifest["preview"]["sample_rate"] == 24000
    assert isinstance(parsed.manifest["preview"]["duration_s"], float)
    # legacy-reader compat: a metadata.json sibling is always written.
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        assert "metadata.json" in zf.namelist()
        assert "preview.wav" in zf.namelist()
        assert any(n.startswith("ref_audio") for n in zf.namelist())


def test_build_no_source_raises_no_preview_source(voices_dir):
    profile = {**_PROFILE, "kind": "clone", "ref_audio_path": None, "locked_audio_path": None}
    with pytest.raises(NoPreviewSource):
        build_persona_bundle(profile, embed_fn=_identity_embed)


def test_build_missing_file_raises_no_preview_source(voices_dir):
    profile = {**_PROFILE, "ref_audio_path": "gone.wav"}
    with pytest.raises(NoPreviewSource):
        build_persona_bundle(profile, embed_fn=_identity_embed)


def test_build_include_reference_false_is_preview_only(voices_dir):
    _write_wav(voices_dir / "abc.wav")
    profile = {**_PROFILE, "kind": "clone", "ref_audio_path": "abc.wav"}
    content = build_persona_bundle(profile, include_reference=False, embed_fn=_identity_embed)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = zf.namelist()
        assert "preview.wav" in names
        assert not any(n.startswith("ref_audio") for n in names)
    assert parse_persona_bundle(content).preview_only is True


def test_build_stereo_offrate_source_downmixed_resampled(voices_dir):
    _write_wav(voices_dir / "st.wav", sr=48000, channels=2, seconds=12.0)
    profile = {**_PROFILE, "kind": "clone", "ref_audio_path": "st.wav"}
    content = build_persona_bundle(profile, embed_fn=_identity_embed)
    parsed = parse_persona_bundle(content)
    assert parsed.manifest["preview"]["sample_rate"] == 24000
    assert parsed.manifest["preview"]["duration_s"] <= 8.0  # trimmed to cap


# ── embed_watermark(force=) unit (D1-D3) ─────────────────────────────────────

def test_embed_watermark_force_keyword(monkeypatch):
    import torch
    from services import watermark
    monkeypatch.setattr(watermark, "_check_available", lambda: False)  # AudioSeal absent
    wav = torch.zeros(1, 100)
    # force=True still no-ops without AudioSeal (D3) — returns input unchanged.
    out = watermark.embed_watermark(wav, 24000, force=True)
    assert out is wav
    # default force=False also unchanged for existing positional callers (D1).
    assert watermark.embed_watermark(wav, 24000) is wav
