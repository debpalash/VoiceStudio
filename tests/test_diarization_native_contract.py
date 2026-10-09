from pathlib import Path
from types import SimpleNamespace

import pytest

from services.diarization_native import (
    MAX_V1_AUDIO_SECONDS,
    SORTFORMER_FRAME_SAMPLES,
    NativeSortformer,
    _sortformer_command,
    _validated_turn,
)


def test_native_sortformer_uses_bounded_growing_graph():
    command = _sortformer_command(
        Path("audiocpp_cli"),
        Path("sortformer.gguf"),
        SimpleNamespace(backend="vulkan", index=1),
        Path("input.wav"),
        Path("turns.json"),
    )

    assert command[-2:] == ["--session-option", "graph_capacity_mode=grow"]
    assert command[command.index("--backend") + 1] == "vulkan"
    assert command[command.index("--device") + 1] == "1"
    assert MAX_V1_AUDIO_SECONDS == 120.0


def test_native_sortformer_rejects_unbounded_v1_recording(monkeypatch, tmp_path):
    import soundfile

    adapter = object.__new__(NativeSortformer)
    adapter.model = tmp_path / "sortformer.gguf"
    adapter.binary = tmp_path / "audiocpp_cli"
    monkeypatch.setattr(
        soundfile,
        "info",
        lambda _path: SimpleNamespace(duration=MAX_V1_AUDIO_SECONDS + 0.01),
    )

    with pytest.raises(ValueError, match="select pyannote for longer recordings"):
        adapter(tmp_path / "long.wav")


def test_native_sortformer_normalises_formats_libsndfile_cannot_read(monkeypatch, tmp_path):
    import soundfile

    import core.contained_subprocess as contained
    import engines.audiocpp.bootstrap as bootstrap
    import services.ffmpeg_utils as ffmpeg_utils

    adapter = object.__new__(NativeSortformer)
    adapter.model = tmp_path / "sortformer.gguf"
    adapter.binary = tmp_path / "audiocpp_cli"
    spawned = []

    def unreadable(_path):
        raise soundfile.LibsndfileError(1)

    def fake_spawn(command, **_kwargs):
        spawned.append(command)
        raise RuntimeError("stop after ffmpeg")

    monkeypatch.setattr(soundfile, "info", unreadable)
    monkeypatch.setattr(bootstrap, "resolve_compute_selection", lambda: SimpleNamespace(device=SimpleNamespace(backend="cpu", index=0)))
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(contained, "spawn_owned", fake_spawn)

    with pytest.raises(RuntimeError, match="stop after ffmpeg"):
        adapter(tmp_path / "clip.m4a")

    assert spawned and "ffmpeg" in str(spawned[0][0])


def test_native_sortformer_clamps_one_frame_of_decoder_padding():
    frames = 560_000

    assert _validated_turn(
        {
            "start_sample": 373_760,
            "end_sample": frames + 480,
            "speaker_id": "SPEAKER_03",
        },
        frames,
    ) == (373_760, frames, "SPEAKER_03")


def test_native_sortformer_rejects_large_boundary_overshoot():
    frames = 560_000

    with pytest.raises(ValueError, match="Invalid native speaker-turn boundaries"):
        _validated_turn(
            {
                "start_sample": 373_760,
                "end_sample": frames + SORTFORMER_FRAME_SAMPLES + 1,
                "speaker_id": "SPEAKER_03",
            },
            frames,
        )
