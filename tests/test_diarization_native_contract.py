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


def _read_turns_output(path):
    # Resolved at call time so a replaced module in sys.modules is not stale.
    from services import diarization_native

    return diarization_native._read_turns_output(path)


def test_validated_turn_rejects_non_object_turn():
    with pytest.raises(ValueError, match="Invalid native speaker-turn"):
        _validated_turn(7, 16000)


def test_read_turns_output_handles_missing_file(tmp_path):
    # A zero-exit subprocess whose output file was never created (deleted by
    # the OS, lost to a container restart, etc.) must not bubble up a raw
    # FileNotFoundError. _read_turns_output wraps the read+parse in a clear
    # RuntimeError so the caller can log a diagnostic.
    missing = tmp_path / "turns.json"
    with pytest.raises(RuntimeError, match="could not be read as UTF-8"):
        _read_turns_output(missing)


def test_read_turns_output_handles_non_utf8_bytes(tmp_path):
    # The sidecar wrote some bytes that the file system kept but a
    # UTF-8 read cannot decode. This typically happens when the
    # subprocess is killed mid-write or when a binary blob is
    # concatenated with the JSON payload.
    bad = tmp_path / "turns.json"
    bad.write_bytes(b"\xff\xfe" + b'{"start_sample": 0')
    with pytest.raises(RuntimeError, match="could not be read as UTF-8"):
        _read_turns_output(bad)


def test_read_turns_output_handles_truncated_json(tmp_path):
    # The file is text and decodes, but the JSON itself stops mid-record.
    # Older versions of the call site let json.JSONDecodeError propagate; the
    # wrapper must turn it into a clear RuntimeError naming the file.
    truncated = tmp_path / "turns.json"
    truncated.write_text('[{"start_sample": 0, "end_sample": 1280', encoding="utf-8")
    with pytest.raises(RuntimeError, match="is not valid JSON"):
        _read_turns_output(truncated)


def test_read_turns_output_handles_empty_file(tmp_path):
    # An empty file is not valid JSON, so it trips the JSON-decode guard
    # before reaching the ``isinstance(turns, list)`` check. The wrapped
    # error is a ``RuntimeError`` with a clear "is not valid JSON" message.
    empty = tmp_path / "turns.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="is not valid JSON"):
        _read_turns_output(empty)


def test_read_turns_output_handles_non_list_payload(tmp_path):
    # A dict (or any other non-list) at the top level is also rejected
    # by the type guard, not by JSON syntax.
    obj = tmp_path / "turns.json"
    obj.write_text('{"turns": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid native speaker-turn output"):
        _read_turns_output(obj)


def test_read_turns_output_parses_valid_list(tmp_path):
    # The happy path: a well-formed list of turn records round-trips.
    ok = tmp_path / "turns.json"
    ok.write_text(
        '[{"start_sample": 0, "end_sample": 1280, "speaker_id": "SPEAKER_00"}]',
        encoding="utf-8",
    )
    assert _read_turns_output(ok) == [
        {"start_sample": 0, "end_sample": 1280, "speaker_id": "SPEAKER_00"},
    ]
