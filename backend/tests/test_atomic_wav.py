"""Tests for ``services.audio_io.atomic_save_wav`` — closes #48.

The invariant we are protecting: when ``atomic_save_wav`` returns, the
target path either contains a complete, valid WAV or is unchanged. There
is no third state where a partial WAV is visible at the target path and
downstream tools (ffmpeg in the dub mux, NLEs the user imports the WAV
into) read truncated audio without an error.
"""
import os
import sys
from pathlib import Path

import pytest
import soundfile as sf
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.audio_io import atomic_save_wav  # noqa: E402


class TestSuccessPath:
    def test_writes_valid_wav(self, tmp_path: Path):
        target = tmp_path / "out.wav"
        audio = torch.randn(1, 24000)  # 1s mono @ 24kHz
        atomic_save_wav(str(target), audio, 24000)

        assert target.exists()
        loaded, sr = sf.read(target, always_2d=True)
        assert sr == 24000
        assert loaded.T.shape == audio.shape

    def test_no_temp_leaks_on_success(self, tmp_path: Path):
        target = tmp_path / "out.wav"
        atomic_save_wav(str(target), torch.zeros(1, 100), 24000)

        leaked = [p for p in tmp_path.glob(".*") if p.name.startswith(".")]
        assert leaked == [], f"leaked temp files after success: {leaked}"

    def test_overwrites_existing_target(self, tmp_path: Path):
        target = tmp_path / "out.wav"
        # Pre-populate with a different-length WAV
        sf.write(target, torch.zeros(1000, 1).numpy(), 24000)
        old_samples = sf.info(target).frames

        new_audio = torch.randn(1, 5000)
        atomic_save_wav(str(target), new_audio, 24000)

        loaded, _ = sf.read(target, always_2d=True)
        assert loaded.shape[0] == 5000
        assert loaded.shape[0] != old_samples


class TestAtomicity:
    """The core invariant of #48: no partial files at the target path."""

    def test_target_unchanged_when_save_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target = tmp_path / "out.wav"
        original_bytes = b"PREVIOUS-CONTENT-DO-NOT-CORRUPT"
        target.write_bytes(original_bytes)

        def explode(*args, **kwargs):
            raise RuntimeError("simulated kill mid-write")

        # Patch the symbol *inside* the audio_io module, not the global —
        # rebinding torchaudio.save would leak into other tests.
        monkeypatch.setattr(
            "services.audio_io.torchaudio.save", explode
        )

        with pytest.raises(RuntimeError, match="simulated kill"):
            atomic_save_wav(str(target), torch.zeros(1, 100), 24000)

        assert target.read_bytes() == original_bytes, (
            "atomic_save_wav must not modify the target path on failure"
        )

    def test_no_temp_leaks_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target = tmp_path / "out.wav"

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "services.audio_io.torchaudio.save", explode
        )

        with pytest.raises(RuntimeError):
            atomic_save_wav(str(target), torch.zeros(1, 100), 24000)

        leaked = [p for p in tmp_path.glob(".*") if p.name.startswith(".")]
        assert leaked == [], f"leaked temp files after failure: {leaked}"

    def test_target_absent_when_save_raises_on_new_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target = tmp_path / "never-existed.wav"
        assert not target.exists()

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "services.audio_io.torchaudio.save", explode
        )

        with pytest.raises(RuntimeError):
            atomic_save_wav(str(target), torch.zeros(1, 100), 24000)

        assert not target.exists(), (
            "atomic_save_wav must not create the target path on failure"
        )

    def test_temp_file_lives_in_target_dir(self, tmp_path: Path):
        """Cross-fs renames are not atomic on POSIX. The temp file *must*
        live next to the target so os.replace() stays a single rename().
        We assert this by intercepting torchaudio.save to inspect the path
        it was handed.
        """
        target = tmp_path / "out.wav"
        captured: list[str] = []

        # Capture the path torchaudio.save is called with, then call the
        # real implementation so the test still ends in a valid WAV.
        from services import audio_io as _aio
        real_save = _aio.torchaudio.save

        def spy(path, *args, **kwargs):
            captured.append(path)
            return real_save(path, *args, **kwargs)

        import unittest.mock
        with unittest.mock.patch.object(_aio.torchaudio, "save", side_effect=spy):
            atomic_save_wav(str(target), torch.zeros(1, 100), 24000)

        assert len(captured) == 1
        tmp_used = captured[0]
        assert os.path.dirname(tmp_used) == str(tmp_path), (
            f"temp file {tmp_used} not in target dir {tmp_path} — "
            "cross-fs rename would break atomicity"
        )
        assert tmp_used != str(target)
