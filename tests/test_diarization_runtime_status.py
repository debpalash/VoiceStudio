import os

from services import diarization_runtime


def _gguf(path):
    path.write_bytes(b"GGUF" + b"\0" * 16)
    return path


def test_sortformer_status_distinguishes_installed_model_from_missing_runtime(
    monkeypatch, tmp_path
):
    model = _gguf(tmp_path / "sortformer.gguf")
    monkeypatch.setattr(diarization_runtime, "sortformer_model_path", lambda: model)

    from engines.audiocpp import bootstrap

    def missing_runtime():
        raise RuntimeError("private path and setup diagnostic")

    monkeypatch.setattr(bootstrap, "resolve_server_binary", missing_runtime)

    status = diarization_runtime.sortformer_status()

    assert status == {
        "model": diarization_runtime.SORTFORMER_MODEL_ID,
        "model_installed": True,
        "runtime_installed": False,
        "installed": False,
        "reason": "The Sortformer model is installed. Install the audio.cpp runtime to use it",
    }


def test_sortformer_status_requires_matching_cli(monkeypatch, tmp_path):
    model = _gguf(tmp_path / "sortformer.gguf")
    server = tmp_path / ("audiocpp_server.exe" if os.name == "nt" else "audiocpp_server")
    server.write_bytes(b"server")
    monkeypatch.setattr(diarization_runtime, "sortformer_model_path", lambda: model)

    from engines.audiocpp import bootstrap

    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: server)

    status = diarization_runtime.sortformer_status()

    assert status["model_installed"] is True
    assert status["runtime_installed"] is False
    assert status["installed"] is False
    assert status["reason"] == (
        "The installed audio.cpp runtime does not include speaker diarisation"
    )


def test_sortformer_status_reports_complete_runtime(monkeypatch, tmp_path):
    model = _gguf(tmp_path / "sortformer.gguf")
    server = tmp_path / ("audiocpp_server.exe" if os.name == "nt" else "audiocpp_server")
    cli = tmp_path / ("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
    server.write_bytes(b"server")
    cli.write_bytes(b"cli")
    cli.chmod(0o755)
    monkeypatch.setattr(diarization_runtime, "sortformer_model_path", lambda: model)

    from engines.audiocpp import bootstrap

    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: server)

    assert diarization_runtime.sortformer_status() == {
        "model": diarization_runtime.SORTFORMER_MODEL_ID,
        "model_installed": True,
        "runtime_installed": True,
        "installed": True,
        "reason": None,
    }


def test_sortformer_status_rejects_corrupt_model_without_exposing_path(
    monkeypatch, tmp_path
):
    model = tmp_path / "private-user-path.gguf"
    model.write_bytes(b"nope")
    monkeypatch.setattr(diarization_runtime, "sortformer_model_path", lambda: model)

    status = diarization_runtime.sortformer_status()

    assert status["model_installed"] is False
    assert status["installed"] is False
    assert status["reason"] == "Repair the installed Sortformer model bundle"
    assert str(model) not in repr(status)
