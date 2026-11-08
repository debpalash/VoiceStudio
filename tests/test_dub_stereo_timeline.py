"""Timeline energy must not disappear when stereo channels cancel in mono."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def export_module(monkeypatch):
    # Job-store boundary only: importing dub_core also loads unrelated engines.
    core = types.ModuleType("api.routers.dub_core")
    core._get_job = lambda job_id: None
    core._safe_lang_or_400 = lambda lang: lang
    monkeypatch.setitem(sys.modules, "api.routers.dub_core", core)
    path = Path(__file__).parents[1] / "backend/api/routers/dub_export.py"
    spec = importlib.util.spec_from_file_location("stems_export_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("right_gain", [-1.0, 0.0, 1.0])
def test_stereo_waveform_and_onsets_match_the_audible_channel(export_module, tmp_path, right_gain):
    import numpy as np
    import soundfile as sf
    from services.onset_align import detect_speech_onsets
    sr = 16000
    mono = np.zeros(sr * 2, dtype=np.float32)
    times = np.arange(sr // 2) / sr
    mono[sr // 2:sr] = 0.5 * np.sin(2 * np.pi * 220 * times)
    stereo = np.column_stack([mono, mono * right_gain])
    reference = detect_speech_onsets(mono, sr)
    assert reference and abs(reference[0] - 0.5) < 0.03
    assert detect_speech_onsets(stereo, sr) == reference
    path = tmp_path / "stereo.wav"
    sf.write(path, stereo, sr, subtype="FLOAT")
    onsets, peaks = export_module._compute_timeline_sync(str(path))
    assert onsets == reference
    assert max(peaks) == pytest.approx(0.5, abs=1e-5)


def test_legacy_phase_cancelled_cache_is_recomputed(export_module, monkeypatch, tmp_path):
    import json
    import numpy as np
    import soundfile as sf
    module = export_module
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    path = job_dir / "audio.wav"
    tone = np.sin(np.arange(16000) * 2 * np.pi * 220 / 16000).astype(np.float32) * 0.5
    sf.write(path, np.column_stack([tone, -tone]), 16000, subtype="FLOAT")
    (job_dir / "onsets.json").write_text(json.dumps({"onsets": [], "peaks": [0], "source": "mix"}))
    monkeypatch.setattr(module, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(module, "_get_job", lambda _: {"audio_path": str(path)})
    result = asyncio.run(module.dub_get_onsets("job"))
    assert max(result["peaks"]) == pytest.approx(0.5, abs=1e-5)
    assert result["onsets"]
