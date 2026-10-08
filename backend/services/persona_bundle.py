"""`.ovsvoice` persona-bundle format (#29 / parity §R3 G1).

A portable ZIP that packages a voice profile's identity + an optional reference
clip + a consent attestation + an SPDX license tag + a watermarked preview.

This module owns the **pure, model-free** core: the format constants, SPDX
normalization, and the manifest/consent builders. The audio preview + ZIP
pack/unpack (which lazily import torchaudio/watermark) layer on top of these.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

from core.safe_archive import ArchiveError, copy_member, open_bounded_zip, read_member

# ── Format constants ─────────────────────────────────────────────────────────
OVSVOICE_FORMAT = "ovsvoice"
OVSVOICE_SCHEMA_VERSION = 1
MAX_BUNDLE_BYTES = 100 * 1024 * 1024          # 100 MB (mirrors marketplace cap)
_MIN_CONSENT_AUDIO_BYTES = 1000               # the consent-recording floor
DEFAULT_LICENSE = "LicenseRef-VoiceStudio-Personal"
PREVIEW_MAX_SECONDS = 8.0                      # preview length cap (A6)
PREVIEW_SAMPLE_RATE = 24_000                  # preview rate; mono, 16-bit PCM (A8)

# Audio member prefixes the importer recognises. The ZIP member NAME is never
# used to build an output path (zip-slip safe, B10) — only its extension, and
# only after the linear allowlist below.
_AUDIO_MEMBER_PREFIXES = ("ref_audio", "locked_audio", "consent_audio", "preview")
# Reused verbatim from profiles.py:306 — single linear quantifier, no ReDoS.
_MEMBER_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")

# Membership allowlist for SPDX validation — a fixed-string set + the
# ``LicenseRef-`` prefix. NO regex over the (user-supplied) SPDX string, so this
# carries no CodeQL py/polynomial-redos surface.
_SPDX_ALLOWLIST: frozenset[str] = frozenset({
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
    "CC-BY-ND-4.0", "MIT", "Apache-2.0", "LicenseRef-VoiceStudio-Personal",
})


class BundleError(Exception):
    """A bundle build/parse failure carrying the HTTP status the router maps to."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class NoPreviewSource(Exception):
    """No readable source clip exists to build a preview from (A2/A3/A4/A5/A12).
    The router maps this to HTTP 503."""


def _safe_member_ext(member_name: str) -> str:
    """The extension for a ZIP member, allowlisted to ``^\\.[A-Za-z0-9]{1,8}$``
    (else ``.wav``). Used ONLY to choose the output extension — never the path
    (B11). Linear regex, no ReDoS."""
    ext = os.path.splitext(member_name)[1]
    return ext if _MEMBER_EXT_RE.match(ext) else ".wav"


def normalize_spdx(spdx: Optional[str]) -> str:
    """Return a safe SPDX id: the value if it's allowlisted or a ``LicenseRef-``
    custom id, else :data:`DEFAULT_LICENSE`. Never raises, never 400s — a junk
    id (incl. shell-injection attempts) normalizes to the default. Membership /
    fixed-prefix only — no regex (CodeQL-clean)."""
    if not spdx or not isinstance(spdx, str):
        return DEFAULT_LICENSE
    s = spdx.strip()
    if s in _SPDX_ALLOWLIST or s.startswith("LicenseRef-"):
        return s
    return DEFAULT_LICENSE


def build_manifest(
    profile: dict,
    *,
    license_spdx: str,
    tags: list[str],
    engine_id: str = "",
    custom_license_text: Optional[str] = None,
    preview: Optional[dict] = None,
    members: Optional[dict] = None,
    omnivoice_version: str = "",
) -> dict:
    """Build the ``manifest.json`` object for a profile row. Mirrors the legacy
    ``_bundle_metadata`` persona fields, adds the format discriminator, license
    (normalized — never raises on a bad id, A19), tags, preview + members blocks.
    Pure: no I/O, no model."""
    return {
        "format": OVSVOICE_FORMAT,
        "schema_version": OVSVOICE_SCHEMA_VERSION,
        "omnivoice_version": omnivoice_version or "",
        "exported_at": time.time(),
        "persona": {
            "name": profile.get("name") or "",
            "kind": profile.get("kind") or "clone",
            "language": profile.get("language") or "Auto",
            "personality": profile.get("personality") or "",
            "instruct": profile.get("instruct") or "",
            "ref_text": profile.get("ref_text") or "",
            "seed": profile.get("seed"),                 # int or None (A16)
            "is_locked": bool(profile.get("is_locked")),
            "vd_states": profile.get("vd_states"),        # JSON string or None (A15) — never re-parsed
        },
        "engine": {"id": engine_id or "", "design_params": None},
        "license": {"spdx": normalize_spdx(license_spdx), "custom_text": custom_license_text or None},
        "tags": list(tags or []),
        "preview": preview,                              # set by the audio step; None for legacy/no-preview
        "members": members or {"ref_audio": None, "locked_audio": None, "consent_audio": None},
    }


def build_consent_json(profile: dict, *, has_recording: bool) -> Optional[dict]:
    """The optional ``consent.json`` for a profile, or None when there's nothing
    to attest. A ``design`` persona attests as designed-synthetic by definition;
    a verified clone attests as a self-recorded statement. Import treats these
    fields as ADVISORY — real verification needs the actual consent_audio member
    (see the import rules), so this can't forge verified-own-voice."""
    kind = profile.get("kind") or "clone"
    consent_text = (profile.get("consent_text") or "").strip()
    verified = bool(profile.get("verified_own_voice"))
    if kind == "design":
        method = "designed-synthetic"
        verified = True
    elif verified or consent_text or has_recording:
        method = "self-recorded-statement"
    else:
        return None  # nothing to attest
    recorded_at = profile.get("consent_recorded_at")
    try:
        recorded_at = float(recorded_at)
    except (TypeError, ValueError):
        recorded_at = time.time()
    return {
        "verified_own_voice": verified,
        "method": method,
        "consent_text": consent_text,
        "recorded_at": recorded_at,
        "has_recording": bool(has_recording),
    }


def _legacy_metadata(profile: dict, omnivoice_version: str) -> dict:
    """A ``metadata.json`` payload shaped like marketplace ``_bundle_metadata`` so
    an OLDER VoiceStudio (which only reads metadata.json) can still import the ref
    audio from a ``.ovsvoice`` bundle."""
    return {
        "bundle_version": 1,
        "profile_name": profile.get("name") or "",
        "ref_text": profile.get("ref_text") or "",
        "instruct": profile.get("instruct") or "",
        "language": profile.get("language") or "Auto",
        "personality": profile.get("personality") or "",
        "seed": profile.get("seed"),
        "kind": profile.get("kind") or "clone",
        "vd_states": profile.get("vd_states"),
        "is_locked": bool(profile.get("is_locked")),
        "omnivoice_version": omnivoice_version or "",
    }


def _resolve_voice_file(filename: Optional[str]) -> Optional[str]:
    """Resolve a DB-stored audio filename strictly inside VOICES_DIR, returning
    an absolute path only if the file actually exists. None on missing/escape —
    mirrors profiles._voices_path (basename + realpath confinement, E1)."""
    if not filename or os.path.basename(filename) != filename:
        return None
    from core.config import VOICES_DIR
    root = os.path.realpath(VOICES_DIR)
    path = os.path.realpath(os.path.join(root, filename))
    if not path.startswith(root + os.sep):
        return None
    return path if os.path.isfile(path) else None


def _generate_preview(profile: dict, embed_fn) -> tuple[bytes, bool, float]:
    """Load the profile's source clip, downmix→mono, resample→24 kHz, trim ≤8 s,
    watermark (forced), and return ``(wav_bytes, watermarked, duration_s)``.

    Source precedence is locked-over-ref (profiles.py:230). Raises
    :class:`NoPreviewSource` when neither clip is readable (A2-A5). All heavy
    imports (torch/torchaudio/watermark) are lazy so the module stays model-free
    at collection time (avoids the known local torch/Triton segfault)."""
    import torch  # noqa: F401  (torchaudio needs it loaded)
    import torchaudio
    from services.audio_io import _safe_torchaudio_save
    from services.watermark import _check_available

    candidates = [profile.get("locked_audio_path"), profile.get("ref_audio_path")]
    wav = None
    for name in candidates:
        path = _resolve_voice_file(name)
        if not path:
            continue
        try:
            from services.audio_io import load_audio
            waveform, sr = load_audio(path)
        except Exception:  # noqa: BLE001 — try the next candidate (A4)
            continue
        if waveform.numel() == 0:  # empty/zero-length (A5)
            continue
        if waveform.shape[0] > 1:  # downmix to mono (A7)
            waveform = waveform.mean(dim=0, keepdim=True)
        if sr != PREVIEW_SAMPLE_RATE:  # resample (A8)
            waveform = torchaudio.functional.resample(waveform, sr, PREVIEW_SAMPLE_RATE)
        cap = int(PREVIEW_SAMPLE_RATE * PREVIEW_MAX_SECONDS)
        waveform = waveform[:, :cap]  # trim, shorter used whole (A6)
        wav = waveform
        break

    if wav is None or wav.numel() == 0:
        raise NoPreviewSource("no readable reference or locked audio for a preview")

    # Forced watermark — bypasses the user pref but still no-ops without AudioSeal.
    fn = embed_fn or _default_embed
    wav = fn(wav, PREVIEW_SAMPLE_RATE)
    watermarked = bool(_check_available())  # best-effort honesty (A11)

    duration_s = round(wav.shape[-1] / PREVIEW_SAMPLE_RATE, 3)
    buf = io.BytesIO()
    _safe_torchaudio_save(buf, wav, PREVIEW_SAMPLE_RATE, format="wav", bits_per_sample=16)
    return buf.getvalue(), watermarked, duration_s


def _default_embed(wav, sample_rate):
    """Default preview watermarker — routes through the mark_synthetic
    chokepoint (#1169) with force=True: a persona bundle's preview mandates a
    mark at package time regardless of the user's watermark pref."""
    from services.watermark import mark_synthetic
    return mark_synthetic(wav, sample_rate, force=True,
                          context="persona_bundle.preview")


def build_persona_bundle(
    profile: dict,
    *,
    license_spdx: str = DEFAULT_LICENSE,
    tags: Optional[list[str]] = None,
    custom_license_text: Optional[str] = None,
    include_reference: bool = True,
    engine_id: str = "",
    omnivoice_version: str = "",
    embed_fn=None,
) -> bytes:
    """Assemble a ``.ovsvoice`` ZIP in memory and return its bytes.

    Always writes a watermarked ``preview.wav`` + ``manifest.json`` +
    (legacy-shaped) ``metadata.json``. Writes ``consent.json`` when there's
    something to attest, the raw ``ref_audio``/``locked_audio`` members unless
    ``include_reference=False`` (privacy / preview-only, A12), and
    ``consent_audio`` when a recording exists. Raises :class:`NoPreviewSource`
    (router → 503) when no source clip is readable."""
    preview_bytes, watermarked, duration_s = _generate_preview(profile, embed_fn)

    members: dict = {"ref_audio": None, "locked_audio": None, "consent_audio": None}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if include_reference:
            ref_path = _resolve_voice_file(profile.get("ref_audio_path"))
            if ref_path:
                name = f"ref_audio{os.path.splitext(ref_path)[1] or '.wav'}"
                zf.write(ref_path, name)
                members["ref_audio"] = name
            locked_path = _resolve_voice_file(profile.get("locked_audio_path"))
            if locked_path:
                name = f"locked_audio{os.path.splitext(locked_path)[1] or '.wav'}"
                zf.write(locked_path, name)
                members["locked_audio"] = name

        # Consent recording travels only when it exists and clears the floor.
        consent_path = _resolve_voice_file(profile.get("consent_audio_path"))
        has_recording = False
        if consent_path and os.path.getsize(consent_path) >= _MIN_CONSENT_AUDIO_BYTES:
            name = f"consent_audio{os.path.splitext(consent_path)[1] or '.wav'}"
            zf.write(consent_path, name)
            members["consent_audio"] = name
            has_recording = True

        zf.writestr("preview.wav", preview_bytes)

        preview_block = {
            "file": "preview.wav", "watermarked": watermarked,
            "duration_s": duration_s, "sample_rate": PREVIEW_SAMPLE_RATE,
        }
        manifest = build_manifest(
            profile, license_spdx=license_spdx, tags=tags or [],
            engine_id=engine_id, custom_license_text=custom_license_text,
            preview=preview_block, members=members,
            omnivoice_version=omnivoice_version,
        )
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("metadata.json",
                    json.dumps(_legacy_metadata(profile, omnivoice_version),
                               ensure_ascii=False, indent=2))
        consent = build_consent_json(profile, has_recording=has_recording)
        if consent is not None:
            zf.writestr("consent.json", json.dumps(consent, ensure_ascii=False, indent=2))

    return buf.getvalue()


@dataclass
class ParsedPersona:
    manifest: dict                 # parsed manifest.json OR synthesized from metadata.json
    consent: Optional[dict]        # parsed consent.json, or None
    is_legacy: bool                # only metadata.json was found (B6/B23)
    schema_version_ahead: bool     # manifest.schema_version > OVSVOICE_SCHEMA_VERSION (B7)
    license_spdx: str              # normalized (B21)
    preview_only: bool             # only preview.wav, no ref/locked member (A12/B8)
    members: dict                  # {prefix: member_name} for audio members present
    watermarked_preview: bool      # manifest.preview.watermarked (False for legacy)
    _zip: zipfile.ZipFile          # open handle; router extracts via extract_member()

    def member_ext(self, prefix: str) -> str:
        name = self.members.get(prefix)
        return _safe_member_ext(name) if name else ".wav"

    def extract_member(self, prefix: str, dest_path: str) -> bool:
        """Stream the audio member named by ``prefix`` to ``dest_path`` (a path
        the CALLER derived from a server-generated id — never from the member
        name). Returns False when the member is absent. Last-wins on dup (B9)."""
        name = self.members.get(prefix)
        if not name:
            return False
        try:
            copy_member(self._zip, name, dest_path)
        except ArchiveError as e:
            raise BundleError(e.status, e.detail)
        return True


def _read_json_member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        return read_member(zf, name)
    except ArchiveError as e:
        raise BundleError(e.status, e.detail)


def parse_persona_bundle(content: bytes) -> ParsedPersona:
    """Validate the ZIP and read manifest/consent WITHOUT touching the DB or
    writing files. Raises :class:`BundleError` (400|413) for B1-B11. The caller
    must use the returned ``ParsedPersona`` while the process holds ``content``
    (the open ZIP reads from the in-memory bytes)."""
    if len(content) > MAX_BUNDLE_BYTES:
        raise BundleError(413, f"Bundle too large. Max is {MAX_BUNDLE_BYTES} bytes.")
    try:
        zf = open_bounded_zip(content)
    except ArchiveError as e:
        raise BundleError(e.status, e.detail)

    names = [n for n in zf.namelist() if not n.endswith("/")]

    # Manifest selection: prefer manifest.json, fall back to legacy metadata.json.
    manifest: dict = {}
    is_legacy = False
    if "manifest.json" in names:
        try:
            manifest = json.loads(_read_json_member(zf, "manifest.json"))
        except (ValueError, UnicodeDecodeError):
            raise BundleError(400, "manifest is not valid JSON")
        if not isinstance(manifest, dict):
            raise BundleError(400, "manifest is not valid JSON")
        # A bundle whose format is neither ovsvoice nor absent → still read
        # leniently (B6); we only branch on schema_version below.
    elif "metadata.json" in names:
        is_legacy = True
        try:
            legacy = json.loads(_read_json_member(zf, "metadata.json"))
        except (ValueError, UnicodeDecodeError):
            raise BundleError(400, "manifest is not valid JSON")
        if not isinstance(legacy, dict):
            raise BundleError(400, "manifest is not valid JSON")
        manifest = {
            "format": "omnivoice-legacy",
            "schema_version": OVSVOICE_SCHEMA_VERSION,
            "persona": {
                "name": legacy.get("profile_name") or legacy.get("name") or "Imported Voice",
                "kind": legacy.get("kind") or "clone",
                "language": legacy.get("language") or "Auto",
                "personality": legacy.get("personality") or "",
                "instruct": legacy.get("instruct") or "",
                "ref_text": legacy.get("ref_text") or "",
                "seed": legacy.get("seed"),
                "is_locked": bool(legacy.get("is_locked")),
                "vd_states": legacy.get("vd_states"),
            },
            "license": {"spdx": DEFAULT_LICENSE, "custom_text": None},
            "tags": [],
            "preview": None,
            "members": {},
        }
    else:
        raise BundleError(400, "bundle is missing a manifest")

    # Audio members by prefix (last-wins on duplicates, B9). The member NAME is
    # retained only to read bytes + pick an extension — never to build a path.
    members: dict = {}
    for name in names:
        for prefix in _AUDIO_MEMBER_PREFIXES:
            if os.path.basename(name).startswith(prefix):
                members[prefix] = name
    has_audio = any(p in members for p in ("ref_audio", "locked_audio", "preview"))
    if not has_audio:
        raise BundleError(400, "bundle has no audio member")

    consent = None
    if "consent.json" in names:
        try:
            parsed = json.loads(_read_json_member(zf, "consent.json"))
            if isinstance(parsed, dict):
                consent = parsed
        except (ValueError, UnicodeDecodeError, BundleError):
            consent = None  # advisory only — a bad consent.json never 400s

    schema_version = manifest.get("schema_version", OVSVOICE_SCHEMA_VERSION)
    try:
        ahead = int(schema_version) > OVSVOICE_SCHEMA_VERSION
    except (TypeError, ValueError):
        ahead = False

    preview_block = manifest.get("preview") or {}
    watermarked_preview = bool(preview_block.get("watermarked")) if isinstance(preview_block, dict) else False
    license_spdx = normalize_spdx((manifest.get("license") or {}).get("spdx"))
    preview_only = ("preview" in members
                    and "ref_audio" not in members and "locked_audio" not in members)

    return ParsedPersona(
        manifest=manifest, consent=consent, is_legacy=is_legacy,
        schema_version_ahead=ahead, license_spdx=license_spdx,
        preview_only=preview_only, members=members,
        watermarked_preview=watermarked_preview, _zip=zf,
    )


__all__ = [
    "OVSVOICE_FORMAT", "OVSVOICE_SCHEMA_VERSION", "MAX_BUNDLE_BYTES",
    "PREVIEW_MAX_SECONDS", "PREVIEW_SAMPLE_RATE", "DEFAULT_LICENSE",
    "BundleError", "NoPreviewSource", "ParsedPersona",
    "normalize_spdx", "build_manifest", "build_consent_json",
    "build_persona_bundle", "parse_persona_bundle",
]
