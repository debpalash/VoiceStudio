import numpy as np
import pytest
import soundfile as sf



def check(tmp_path, samples, sr=8000):
    from services.audio_quality import analyze_audio
    path = tmp_path / 'audio.wav'
    sf.write(path, samples, sr, subtype='FLOAT')
    before = path.read_bytes()
    result = analyze_audio(path)
    assert path.read_bytes() == before
    return result


def tone(seconds, amplitude=0.1):
    return amplitude * np.sin(2 * np.pi * 200 * np.arange(int(seconds * 8000)) / 8000)


def test_empty_and_silent(tmp_path):
    assert check(tmp_path, np.zeros(0)).warnings[0].kind == 'empty'
    assert check(tmp_path, np.zeros(16000)).warnings[0].kind == 'silence'
    assert check(tmp_path, np.zeros(1600)).warnings[0].kind == 'silence'


def test_timestamps_and_normal_pauses(tmp_path):
    result = check(tmp_path, np.concatenate([tone(2), np.zeros(12000), tone(2)]))
    warning = next(w for w in result.warnings if w.kind == 'silence')
    assert (warning.start, warning.end) == pytest.approx((2, 3.5))
    assert not check(tmp_path, np.concatenate([tone(2), np.zeros(2400), tone(2)])).warnings


def test_relative_volume_and_quiet_speech(tmp_path):
    result = check(tmp_path, np.concatenate([tone(3), tone(1, .008), tone(3)]))
    assert any(w.kind == 'quiet' and w.start == 3 for w in result.warnings)
    result = check(tmp_path, np.concatenate([tone(3, .02), tone(1, .5), tone(3, .02)]))
    assert any(w.kind == 'loud' for w in result.warnings)
    assert not check(tmp_path, tone(3, .008)).warnings


def test_channels_do_not_cancel_and_bad_samples_detected(tmp_path):
    signal = tone(2)
    assert not check(tmp_path, np.column_stack([signal, -signal])).warnings
    assert any(w.kind == 'clipping' for w in check(tmp_path, np.ones(8000)).warnings)
    signal[100] = np.nan
    assert any(w.kind == 'invalid' for w in check(tmp_path, signal).warnings)


def test_scan_and_warning_bounds(tmp_path):
    from services.audio_quality import analyze_audio
    result = check(tmp_path, tone(3))
    assert result.duration == 3
    path = tmp_path / 'audio.wav'
    assert analyze_audio(path, max_seconds=1).truncated


def test_warning_cap_and_short_clip(tmp_path):
    signal = np.tile(np.concatenate([np.ones(800), tone(.1)]), 110)
    result = check(tmp_path, signal)
    assert len(result.warnings) == 100
    assert result.truncated
    assert not check(tmp_path, tone(.04)).warnings


def test_warning_cap_keeps_earliest_warnings_across_checks(tmp_path):
    # #2636: 110 late 'invalid' windows must not crowd out the early silence.
    bad = tone(.1)
    bad[0] = np.nan
    signal = np.concatenate([np.zeros(12000), np.tile(np.concatenate([bad, tone(.1)]), 110)])
    result = check(tmp_path, signal)
    assert len(result.warnings) == 100
    assert result.truncated
    assert (result.warnings[0].kind, result.warnings[0].start) == ('silence', 0)
    assert [w.start for w in result.warnings] == sorted(w.start for w in result.warnings)


def test_api_confines_paths_and_returns_analysis(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import generation

    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    monkeypatch.setattr(generation, 'OUTPUTS_DIR', str(outputs))
    sf.write(outputs / '12345678.wav', np.zeros(16000), 8000)
    app = FastAPI()
    app.include_router(generation.router)
    client = TestClient(app)
    response = client.get('/audio/12345678/quality')
    assert response.status_code == 200
    assert response.json()['warnings'][0]['kind'] == 'silence'
    assert client.get('/audio/not-an-id/quality').status_code == 404
    assert client.get('/audio/ffffffff/quality').status_code == 404
    outside = tmp_path / 'private.wav'
    sf.write(outside, np.ones(800), 8000)
    (outputs / 'deadbeef.wav').write_bytes(b'broken')
    assert client.get('/audio/deadbeef/quality').status_code == 422
    # Windows CI may not have the symlink privilege. Still exercise the
    # resolved-path confinement there, without skipping the API checks.
    import os
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, 'realpath', lambda path, **kwargs:
                        str(outside) if str(path).endswith('abcdef12.wav')
                        else realpath(path, **kwargs))
    assert client.get('/audio/abcdef12/quality').status_code == 404
