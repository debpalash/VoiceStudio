"""The exported workflow speaks through the real API and returns a WAV file."""
import importlib
import json
from pathlib import Path

import torch
from fastapi.testclient import TestClient


def test_exported_n8n_request_returns_playable_wav(monkeypatch):
    from main import app

    tts = importlib.import_module('services.tts_backend')
    from services import engine_routing

    class Engine(tts.TTSBackend):
        id = 'n8n-contract-test'
        display_name = 'test'
        gpu_compat = ('cpu',)
        sample_rate = 24000
        supported_languages = ['multi']
        generated = []

        @classmethod
        def is_available(cls):
            return True, 'ready'

        def generate(self, text, **kwargs):
            self.generated.append(text)
            return torch.sin(torch.arange(24000) * 0.05).unsqueeze(0) * 0.1

    engine = Engine()
    monkeypatch.setattr(tts, 'get_active_tts_backend', lambda: engine)
    async def profile(*args, **kwargs):
        return {'routing_status': 'native', 'routing_reason': ''}
    monkeypatch.setattr(engine_routing, 'runtime_compute_profile_async', profile)
    workflow = json.loads((Path(__file__).resolve().parents[1] / 'electron/src/renderer/src/features/integrations/n8n-workflow.json').read_text(encoding="utf-8"))
    request = next(node['parameters'] for node in workflow['nodes'] if node['type'] == 'n8n-nodes-base.httpRequest')
    # Default cold load (20 min) plus CPU generation (10 min), with headroom.
    assert request["options"]["timeout"] >= (1200 + 600) * 1000
    from urllib.parse import urlsplit
    response = TestClient(app, client=('127.0.0.1', 50000)).request(
        request['method'], urlsplit(request['url']).path, json=json.loads(request['jsonBody']))
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'audio/wav'
    assert response.content[:4] == b'RIFF' and response.content[8:12] == b'WAVE'
    assert engine.generated == ['VoiceStudio']
