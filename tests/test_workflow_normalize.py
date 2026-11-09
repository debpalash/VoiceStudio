"""Workflow normalization stays local, bounded, and synthetic-marked."""
import io
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import tools


def wav(samples=None):
    output = io.BytesIO()
    sf.write(output, np.full(2400, 0.1) if samples is None else samples, 24000, format="WAV", subtype="FLOAT")
    return output.getvalue()


@pytest.fixture
def client(monkeypatch):
    from services import watermark

    marker = AsyncMock(side_effect=lambda audio, rate, **kwargs: audio)
    monkeypatch.setattr(watermark, "mark_synthetic_async", marker)
    app = FastAPI()
    app.include_router(tools.router)
    with TestClient(app) as client:
        yield client, marker


def test_normalizes_and_marks_synthetic_output(client):
    client, marker = client
    response = client.post('/tools/normalize-speech', files={'audio': ('test.wav', wav(), 'audio/wav')})
    assert response.status_code == 200
    audio, rate = sf.read(io.BytesIO(response.content))
    assert rate == 24000
    assert np.max(np.abs(audio)) == pytest.approx(10 ** (-2 / 20), abs=0.0001)
    marker.assert_awaited_once()
    assert marker.call_args.kwargs['context'] == 'workflow.normalize'


def test_silence_is_not_amplified(client):
    client, _ = client
    response = client.post('/tools/normalize-speech', files={'audio': ('silence.wav', wav(np.zeros(2400)))})
    assert response.status_code == 200
    audio, _ = sf.read(io.BytesIO(response.content))
    assert not np.any(audio)


# Explicit ids: a float WAV embeds a PEAK timestamp, so the generated id differs
# per xdist worker and fails collection ("Different tests were collected").
@pytest.mark.parametrize(
    'data',
    [pytest.param(b'not audio', id='not-audio'), pytest.param(wav(np.array([np.nan, np.inf])), id='nan-inf')],
)
def test_rejects_invalid_samples_before_marking(client, data):
    client, marker = client
    response = client.post('/tools/normalize-speech', files={'audio': ('bad.wav', data)})
    assert response.status_code == 422
    marker.assert_not_called()


def test_rejects_oversized_upload(client):
    client, marker = client
    response = client.post('/tools/normalize-speech', files={'audio': ('big.wav', b'x' * (64 * 1024 * 1024 + 1))})
    assert response.status_code == 413
    marker.assert_not_called()


def test_custom_peak_target(client):
    client, _ = client
    response = client.post('/tools/normalize-speech', data={'target_dbfs': '-12'},
                           files={'audio': ('test.wav', wav())})
    assert response.status_code == 200
    audio, _ = sf.read(io.BytesIO(response.content))
    assert np.abs(audio).max() == pytest.approx(10 ** (-12 / 20), abs=0.0001)


@pytest.mark.parametrize('target', ['0', '-60', 'nan', 'inf'])
def test_invalid_peak_rejected(client, target):
    client, marker = client
    response = client.post('/tools/normalize-speech', data={'target_dbfs': target},
                           files={'audio': ('test.wav', wav())})
    assert response.status_code == 422
    marker.assert_not_called()
