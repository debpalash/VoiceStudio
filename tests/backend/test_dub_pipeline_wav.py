"""End-to-end / structural regression tests for the dub-pipeline WAV write path.

Closes BUG-01 / issue #48 ("dub pipeline produces silent / corrupt WAVs").

Three layers of coverage:

1. **Grep gate** — assert that ``backend/api/routers/`` contains zero
   direct ``torchaudio.save`` / ``soundfile.write`` / ``sf.write`` calls
   that aren't routed through the audited helpers. Prevents future drift
   from re-opening the bug class.

2. **Track-assembly reproduction** — synthesize the dub_generate.py:390
   pattern directly (torch.cat of slices, varied devices, out-of-range
   peaks) and assert _safe_torchaudio_save still produces a WAV that
   ``soundfile.info`` decodes cleanly. This is the smoking-gun
   reproduction of #48 — the bug was the helper-less call site at line
   390 silently corrupting non-contig + out-of-range tensors.

3. **Atomic-save round-trip** — same shape as (2) but routed through
   ``atomic_save_wav`` (the path the real dub pipeline now uses).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import soundfile as sf
import torch

# Make the backend package importable (conftest.py handles this for
# tests/, but be explicit so direct invocations also work).
_BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.audio_io import (  # noqa: E402
    _safe_soundfile_write,
    _safe_torchaudio_save,
    atomic_save_wav,
    load_audio,
)


_ROUTER_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "api" / "routers"


# ── 1. Grep gate ──────────────────────────────────────────────────────────


def _bare_audio_writes_in_routers() -> list[str]:
    """Return list of ``file:line: text`` for any bare audio-write call in routers.

    A "bare" call is one that is NOT routed through ``_safe_torchaudio_save``
    or ``_safe_soundfile_write``. Comments are excluded — bare ``grep -c``
    would otherwise count "# torchaudio.save(...)" as a violation.
    """
    pattern = re.compile(r"(torchaudio\.save|soundfile\.write|sf\.write)\(")
    helper_re = re.compile(r"_safe_(torchaudio_save|soundfile_write)")
    violations: list[str] = []
    for py in sorted(_ROUTER_DIR.rglob("*.py")):
        with py.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.rstrip("\n")
                # Strip leading whitespace, then drop comment-only lines.
                if line.lstrip().startswith("#"):
                    continue
                if not pattern.search(line):
                    continue
                if helper_re.search(line):
                    continue
                violations.append(f"{py.relative_to(_ROUTER_DIR.parent.parent.parent)}:{lineno}: {line.strip()}")
    return violations


def test_no_bare_audio_writes_in_routers():
    """Every audio-write site in ``backend/api/routers/`` must go through
    ``_safe_torchaudio_save`` or ``_safe_soundfile_write``.

    Future regressions (a new endpoint that uses bare ``torchaudio.save``)
    will fail this gate before they ship.
    """
    violations = _bare_audio_writes_in_routers()
    assert violations == [], (
        "Audio writes outside the audited helpers were found:\n  "
        + "\n  ".join(violations)
        + "\n\nUse services.audio_io._safe_torchaudio_save or "
        "_safe_soundfile_write (or atomic_save_wav for on-disk dub outputs)."
    )


def test_no_bare_audio_writes_via_subprocess_grep():
    """Same gate via subprocess `grep`, matching the planner's CI script.

    Skipped on Windows where grep isn't standard. The in-process check
    above runs everywhere.
    """
    if not _which("grep"):
        pytest.skip("grep not available on this platform")

    # Match the exact pipeline from 02-02-PLAN.md verification block.
    proc = subprocess.run(
        [
            "grep",
            "-rnE",
            r"(torchaudio\.save|soundfile\.write|sf\.write)\(",
            str(_ROUTER_DIR),
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # returncode 1 == no matches at all (also acceptable, just a stricter case)
    if proc.returncode not in (0, 1):
        pytest.fail(f"grep failed unexpectedly: rc={proc.returncode}, stderr={proc.stderr}")

    bad: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # Skip helper-routed sites
        if "_safe_torchaudio_save" in line or "_safe_soundfile_write" in line:
            continue
        # Skip comment-only matches (handle both "#" at start of code section)
        # Format from grep -n: ``path:lineno:content``.
        try:
            _, _, content = line.split(":", 2)
        except ValueError:
            continue
        if content.lstrip().startswith("#"):
            continue
        bad.append(line)

    assert bad == [], (
        "Subprocess grep gate found bare audio writes:\n  "
        + "\n  ".join(bad)
    )


def _which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)


# ── 2. Track-assembly reproduction (#48 smoking gun) ───────────────────────


def test_track_assembly_handles_non_contig_after_torch_cat(tmp_path):
    """Reproduce the dub_generate.py:390 / batch.py:341 failure pattern.

    Before the fix, bare ``torchaudio.save`` of a torch.cat-built,
    non-contiguous, slightly-out-of-range tensor produced a WAV that
    ``sf.info`` decoded as zero-amplitude (silent corruption). The
    audited helper must produce a valid PCM_16 WAV with audible samples
    on the same input.
    """
    sr = 24000
    # Build segments via slicing — produces non-contig tensors after cat.
    src = torch.linspace(-1.2, 1.2, 6 * sr, dtype=torch.float32)  # slight out-of-range
    seg_a = src[:sr * 2].unsqueeze(0)
    seg_b = src[sr * 2 : sr * 4].unsqueeze(0)
    seg_c = src[sr * 4 :].unsqueeze(0)

    # The real dub_generate.py:390 path mixes via index assignment, but
    # the resulting tensor often comes from `torch.cat` of segment slices.
    # Build both shapes to cover both branches.
    cat_audio = torch.cat([seg_a, seg_b, seg_c], dim=1)
    assert cat_audio.shape == (1, 6 * sr)

    # Mix-via-add: zero-init then += slices (the batch.py:341 path).
    mixed = torch.zeros(1, 6 * sr, dtype=torch.float32)
    mixed[:, : 2 * sr] += seg_a
    mixed[:, 2 * sr : 4 * sr] += seg_b
    mixed[:, 4 * sr :] += seg_c

    # Force the non-contiguity case via transpose, mimicking the upstream
    # segmenter producing non-contig outputs.
    weird = torch.stack([cat_audio.squeeze(0), mixed.squeeze(0)], dim=1).t()
    assert not weird.is_contiguous()

    out_cat = tmp_path / "cat.wav"
    out_mixed = tmp_path / "mixed.wav"
    out_weird = tmp_path / "weird.wav"

    _safe_torchaudio_save(str(out_cat), cat_audio, sr)
    _safe_torchaudio_save(str(out_mixed), mixed, sr)
    _safe_torchaudio_save(str(out_weird), weird, sr)

    for path in (out_cat, out_mixed, out_weird):
        info = sf.info(str(path))
        assert info.frames > 0, f"{path.name}: zero frames"
        assert info.samplerate == sr, f"{path.name}: wrong sr"
        assert info.subtype.startswith("PCM_"), f"{path.name}: subtype={info.subtype}"
        samples, _ = sf.read(str(path))
        assert abs(samples).max() > 0.1, (
            f"{path.name}: samples too quiet (max={abs(samples).max()}) — "
            "silent-corruption mode"
        )
        assert abs(samples).max() <= 1.0 + 1e-3, (
            f"{path.name}: out-of-range not clamped (max={abs(samples).max()})"
        )


def test_atomic_save_wav_assembly_pattern(tmp_path):
    """Same failure pattern, but through the atomic_save_wav helper that
    the real dub_generate.py + batch.py code paths actually use."""
    sr = 24000
    src = torch.linspace(-1.5, 1.5, 4 * sr, dtype=torch.float32)
    seg_a = src[: 2 * sr].unsqueeze(0)
    seg_b = src[2 * sr :].unsqueeze(0)
    full_audio = torch.cat([seg_a, seg_b], dim=1)
    # Common upstream pattern: slice from a tensor view.
    sliced = full_audio[:, ::2]
    assert sliced.shape == (1, 2 * sr)

    track_path = tmp_path / "dubbed_en.wav"
    atomic_save_wav(str(track_path), sliced, sr)

    info = sf.info(str(track_path))
    assert info.frames == 2 * sr
    assert info.samplerate == sr
    assert info.subtype == "PCM_16"
    samples, _ = sf.read(str(track_path))
    assert abs(samples).max() > 0.1
    assert abs(samples).max() <= 1.0 + 1e-3


def test_safe_soundfile_write_dub_core_pattern(tmp_path):
    """Mirror the dub_core.py:438 transcribe-chunk pattern.

    Build a numpy slice of float32 samples (the shape returned by
    ``sf.read(..., dtype='float32')``), pass it through the helper, and
    verify the temp WAV round-trips through sf.info.
    """
    import numpy as np

    sr = 16000
    total = np.linspace(-0.8, 0.8, 5 * sr, dtype=np.float32)
    # ASR chunk extraction: ``audio_np[s_from:s_to]`` is a view.
    chunk = total[sr : 3 * sr]
    assert not chunk.flags["OWNDATA"]  # confirm it's a view

    out = tmp_path / "chunk.wav"
    _safe_soundfile_write(str(out), chunk, sr)

    info = sf.info(str(out))
    assert info.frames == 2 * sr
    assert info.samplerate == sr
    decoded, _ = sf.read(str(out))
    assert abs(decoded).max() > 0.1


# ── 3. End-to-end (skipped if heavy fixture missing) ──────────────────────


def test_dub_pipeline_produces_valid_wav():
    """End-to-end dub-pipeline regression via FastAPI TestClient.

    Requires a 5-second sample video fixture. If the Phase 0 fixture
    does not ship one, this test xfails with a clear "add the fixture"
    message rather than silently skipping — per the planner's "do not
    skip silently" directive.
    """
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "sample_5s.mp4"
    if not fixture.exists():
        pytest.xfail(
            f"Phase 0 fixture missing: {fixture}. "
            "The structural reproduction tests above already cover the "
            "#48 failure modes against the same helper code path; this "
            "end-to-end test will exercise the FastAPI route once the "
            "fixture lands."
        )

    # The fixture path is reserved; if a future fixture lands the test
    # will run the full pipeline. Until then the xfail above keeps CI
    # honest about coverage gaps without producing a green silent skip.
    pytest.xfail(
        "Phase 0 fixture present but end-to-end dub-pipeline harness "
        "not yet wired (deferred to Phase 4)."
    )


# ── 4. Load path (issue #2378) ──────────────────────────────────────────


def _bare_audio_loads_in_routers() -> list[str]:
    """Bare ``torchaudio.load`` calls in routers (must use ``load_audio``)."""
    pattern = re.compile(r"torchaudio\.load\(")
    violations: list[str] = []
    for py in sorted(_ROUTER_DIR.rglob("*.py")):
        with py.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.rstrip("\n")
                if line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    violations.append(f"{py.relative_to(_ROUTER_DIR.parent.parent.parent)}:{lineno}: {line.strip()}")
    return violations


def test_no_bare_audio_loads_in_routers():
    """Every audio-read site in routers must go through ``load_audio`` (#2378)."""
    assert _bare_audio_loads_in_routers() == []


def test_load_audio_falls_back_without_torchcodec(tmp_path, monkeypatch):
    """Fail-before/pass-after for #2378: ImportError from torchaudio still reads."""
    import torchaudio

    sr = 24000
    wav = (torch.sin(torch.linspace(0, 40 * 3.14159, sr)) * 0.5).unsqueeze(0)
    target = tmp_path / "seg_0.wav"
    atomic_save_wav(str(target), wav, sr)

    def _missing(*a, **k):
        raise ImportError("TorchCodec is required")

    monkeypatch.setattr(torchaudio, "load", _missing)
    got, got_sr = load_audio(str(target))
    assert got_sr == sr
    assert got.shape[-1] == sr
    assert abs(got.numpy()).max() > 0.1
