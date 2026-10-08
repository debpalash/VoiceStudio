"""Removed FFmpeg filter-file options must not break background/export graphs."""
import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def fu():
    import importlib
    return importlib.import_module("services.ffmpeg_utils")


@pytest.mark.parametrize('modern', [False, True])
def test_filter_file_works_with_legacy_and_modern_ffmpeg(fu, monkeypatch, tmp_path, modern):
    calls = []
    script = tmp_path / 'graph.txt'
    script.write_text('[0:a]anull[out]')
    command = ['ffmpeg', '-i', 'input.wav', '-filter_complex_script', str(script), '-map', '[out]', 'out.wav']

    class Process:
        def __init__(self, cmd):
            self.returncode = 1 if modern and '-filter_complex_script' in cmd else 0
        async def communicate(self):
            return b'', b"Unrecognized option 'filter_complex_script'.\nError splitting the argument list: Option not found" if self.returncode else b''

    async def spawn(cmd, **kwargs):
        calls.append(list(cmd))
        return Process(cmd)

    monkeypatch.setattr(fu, '_spawn_with_retry', spawn)
    monkeypatch.setattr(fu, '_get_semaphore', lambda: asyncio.Semaphore(1))
    rc, _, _ = asyncio.run(fu.run_ffmpeg(command))
    assert rc == 0
    assert len(calls) == (2 if modern else 1)
    if modern:
        expected = ['-/filter_complex' if arg == '-filter_complex_script' else arg
                    for arg in fu.local_inputs_only(command)]
        assert calls[1] == expected
    assert script.exists()  # caller-owned input, never removed
    assert command[3] == '-filter_complex_script'  # caller's argv unchanged


def test_filter_file_does_not_retry_a_real_processing_error(fu, monkeypatch, tmp_path):
    calls = []
    class Process:
        returncode = 1
        async def communicate(self):
            return b'', b'No such filter: missing_filter'
    async def spawn(cmd, **kwargs):
        calls.append(cmd)
        return Process()
    monkeypatch.setattr(fu, '_spawn_with_retry', spawn)
    monkeypatch.setattr(fu, '_get_semaphore', lambda: asyncio.Semaphore(1))
    rc, _, error = asyncio.run(fu.run_ffmpeg(['ffmpeg', '-filter_complex_script', 'graph.txt']))
    assert rc == 1 and b'No such filter' in error
    assert len(calls) == 1


def test_externalized_graph_survives_retry_then_is_cleaned(fu, monkeypatch, tmp_path):
    original_externalize = fu.externalize_long_filter_complex
    monkeypatch.setattr(fu.sys, 'platform', 'win32')
    monkeypatch.setattr(fu, 'externalize_long_filter_complex', lambda cmd: original_externalize(cmd, limit=1, tmp_dir=tmp_path))
    paths = []
    class Process:
        def __init__(self, cmd):
            self.returncode = int('-filter_complex_script' in cmd)
        async def communicate(self):
            return b'', b"Unrecognized option 'filter_complex_script'." if self.returncode else b''
    async def spawn(cmd, **kwargs):
        flag = '-filter_complex_script' if '-filter_complex_script' in cmd else '-/filter_complex'
        path = Path(cmd[cmd.index(flag) + 1])
        assert path.read_text() == '[0:a]anull[out]'
        paths.append(path)
        return Process(cmd)
    monkeypatch.setattr(fu, '_spawn_with_retry', spawn)
    monkeypatch.setattr(fu, '_get_semaphore', lambda: asyncio.Semaphore(1))
    rc, _, _ = asyncio.run(fu.run_ffmpeg(['ffmpeg', '-filter_complex', '[0:a]anull[out]']))
    assert rc == 0 and len(paths) == 2 and paths[0] == paths[1]
    assert not paths[0].exists()


def test_modern_file_syntax_produces_real_audio(fu, monkeypatch, tmp_path):
    import shutil
    import subprocess
    import wave
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('system ffmpeg unavailable')
    # This host-level integration verifies the replacement spelling itself;
    # compatibility with removed/retained legacy options is tested above.
    try:
        probe = subprocess.run([ffmpeg, '-version'], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail('ffmpeg version probe timed out after 10 seconds')
    import re
    version = re.search(r'ffmpeg version (\d+)', probe.stdout)
    if version and int(version.group(1)) < 7:
        pytest.skip('modern filter-file syntax requires a newer FFmpeg')
    script = tmp_path / 'graph.txt'
    script.write_text('[0:a]anull[out]')
    output = tmp_path / 'out.wav'
    rc, _, error = asyncio.run(fu.run_ffmpeg([
        ffmpeg, '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.05',
        '-/filter_complex', str(script), '-map', '[out]', '-c:a', 'pcm_s16le', str(output),
    ]))
    assert rc == 0, error
    with wave.open(str(output)) as audio:
        assert audio.getnframes() > 0
