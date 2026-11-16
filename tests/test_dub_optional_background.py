"""Voice-only ingest must never launch Demucs or reuse separated background audio."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from services import dub_pipeline as dp


@pytest.mark.parametrize('separate,failure', [(False, False), (True, False), (True, True)])
def test_ingest_respects_background_choice(monkeypatch, tmp_path, separate, failure):
    commands, cache_keys, separations = [], [], []
    source = tmp_path / 'input.wav'
    sf.write(source, np.ones(16000, dtype=np.float32) * .05, 16000)
    job_dir = tmp_path / 'job'
    job_dir.mkdir()
    async def run(cmd, **kwargs):
        commands.append(cmd)
        rate = int(cmd[cmd.index('-ar') + 1])
        channels = int(cmd[cmd.index('-ac') + 1])
        output = cmd[-2]
        sf.write(output, np.zeros((rate, channels), dtype=np.float32), rate)
        return SimpleNamespace(returncode=0), b'', b''
    async def separate_audio(jid, audio, directory, **kwargs):
        separations.append(audio)
        if failure:
            raise RuntimeError('separation fixture failure')
        stems = Path(directory) / 'htdemucs' / Path(audio).stem
        stems.mkdir(parents=True)
        for name in ['vocals.wav', 'no_vocals.wav']:
            sf.write(stems / name, np.zeros(16000), 16000)
        yield ('progress', 100)
    monkeypatch.setattr(dp, 'run_proc_factory', lambda _: run)
    monkeypatch.setattr(dp, 'find_cached_job', lambda h, _: cache_keys.append(h))
    monkeypatch.setattr(dp, 'save_job', lambda *a, **kw: None)
    monkeypatch.setattr(dp, '_separate_background', separate_audio)
    async def collect():
        return [json.loads(e.removeprefix('data: ').strip()) async for e in dp.ingest_pipeline(
            'roadmap', str(job_dir), {'kind': 'file', 'path': str(source), 'input_type': 'audio', 'separate_background': separate})]
    try:
        events = asyncio.run(collect())
        assert events[-1]['type'] == 'ready', events
        assert bool(separations) is separate
        assert any(e['type'] == 'demucs_start' for e in events) is separate
        assert any('44100' in cmd for cmd in commands) is separate
        job = dp.get_job('roadmap')
        assert job['background_separation'] == (('failed' if failure else 'ok') if separate else 'skipped')
        assert bool(job['no_vocals_path']) is (separate and not failure)
        assert cache_keys[0].endswith(':voice-only') is (not separate)
        done = next(e for e in events if e['type'] == 'demucs_done')
        assert done['skipped'] is (not separate)
        if failure:
            assert any(e['type'] == 'warning' for e in events)
    finally:
        dp._dub_jobs.pop('roadmap', None)
