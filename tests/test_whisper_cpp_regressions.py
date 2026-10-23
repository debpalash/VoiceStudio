import json
import subprocess
from pathlib import Path

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]


@pytest.fixture
def invocation(tmp_path, monkeypatch, m):
    audio = tmp_path / 'input.wav'
    audio.write_bytes(b'audio')
    monkeypatch.setattr(m.wc, 'locate_binary', lambda: tmp_path / 'whisper-cli')
    monkeypatch.setattr(m.wc, 'resolve_model', lambda: tmp_path / 'model.bin')
    monkeypatch.setattr(m.wc, '_ensure_wav_16k', lambda p, _: Path(p))
    return str(audio)

def test_default_language_is_auto_and_cpu_pin_wins(invocation, monkeypatch, m):
    monkeypatch.setenv('OMNIVOICE_DEVICE', 'cpu')
    monkeypatch.setenv('OMNIVOICE_WHISPER_CPP_BACKEND', 'vulkan')
    def run(cmd, **kwargs):
        assert cmd[cmd.index('-l') + 1] == 'auto'
        assert '-ng' in cmd
        assert '-nt' not in cmd
        Path(cmd[cmd.index('-of') + 1] + '.json').write_text('{}')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    m.WhisperCppBackend().transcribe(invocation)

def test_bpe_pieces_form_words(invocation, monkeypatch, m):
    def run(cmd, **kwargs):
        tokens = [{'text': t, 'offsets': {'from': i * 100, 'to': (i+1)*100}, 'p': .9}
                  for i, t in enumerate(['[_BEG_]', ' trans', 'cription', ' works', '.'])]
        data = {'transcription': [{'text': 'transcription works.', 'tokens': tokens}]}
        Path(cmd[cmd.index('-of') + 1] + '.json').write_text(json.dumps(data))
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    words = m.WhisperCppBackend().transcribe(invocation)['segments'][0]['words']
    assert [w['word'] for w in words] == ['transcription', 'works.']
    assert words[0]['start'] == .1 and words[0]['end'] == .3

def test_failure_does_not_retry_or_leak_stderr(invocation, monkeypatch, m):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, '', 'failed /private/customer/audio.wav')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(RuntimeError) as error:
        m.WhisperCppBackend().transcribe(invocation)
    assert len(calls) == 1
    assert '/private' not in str(error.value)

def test_local_model_strict_preflight(tmp_path, monkeypatch, m):
    model = tmp_path / 'ggml-base.bin'
    model.write_bytes(b'weights')
    monkeypatch.setenv('OMNIVOICE_WHISPER_CPP_MODEL', str(model))
    assert m.ab.asr_model_missing_error(backend_id='whisper-cpp', require_installed=True) is None

def test_unsupported_json_retry_has_single_timeout_budget(invocation, monkeypatch, m):
    from engines.whisper_cpp import process
    calls = []
    times = iter([100, 101, 108])
    monkeypatch.setattr(process.time, 'monotonic', lambda: next(times))
    def run(cmd, **kwargs):
        calls.append(kwargs['timeout'])
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, '', 'error: unknown argument: --output-json-full')
        raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(m.ab.ASRTimeoutError):
        process.run_transcription(['whisper-cli', '--output-json-full'], Path(invocation + '.json'), 10)
    assert calls == [9, 2]


def test_successful_legacy_json_retry(invocation, monkeypatch, m):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, '', 'unknown argument: --output-json-full')
        assert '-oj' in cmd and '--output-json-full' not in cmd
        Path(cmd[cmd.index('-of') + 1] + '.json').write_text('{}')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    assert m.WhisperCppBackend().transcribe(invocation)['segments'] == []
    assert len(calls) == 2


def test_path_discovery_rejects_unrelated_commands(tmp_path, monkeypatch, m):
    from engines.whisper_cpp import bootstrap
    monkeypatch.delenv(bootstrap.BIN_ENV, raising=False)
    monkeypatch.delenv(bootstrap.DIR_ENV, raising=False)
    monkeypatch.setattr(bootstrap, '_PKG_BIN_DIR', tmp_path)
    monkeypatch.setattr(bootstrap.shutil, 'which', lambda name: '/wrong/program' if name in ('whisper', 'main', 'server', 'whisper-server') else None)
    assert bootstrap.locate_binary() is None


def test_resampling_filters_above_nyquist(tmp_path, m):
    import numpy as np
    import soundfile as sf
    from engines.whisper_cpp.audio import _ensure_wav_16k
    rate = 48000
    audio = np.sin(2 * np.pi * 12000 * np.arange(rate) / rate).astype('float32')
    source = tmp_path / 'high.wav'
    sf.write(source, audio, rate, subtype='FLOAT')
    result, sr = sf.read(_ensure_wav_16k(str(source), tmp_path))
    assert sr == 16000
    assert np.sqrt(np.mean(result[100:-100] ** 2)) < .02


def test_licence_repository_is_not_a_local_path(tmp_path, monkeypatch, m):
    monkeypatch.setenv('OMNIVOICE_WHISPER_CPP_MODEL', str(tmp_path / 'private.bin'))
    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == []


def test_missing_local_model_has_manual_install_guidance(monkeypatch, m):
    monkeypatch.setattr('engines.whisper_cpp.bootstrap.resolve_model', lambda: None)
    missing = m.ab.asr_model_missing_error(backend_id='whisper-cpp', require_installed=True)
    assert missing['reason'] == 'manual_install'
    assert missing['recommended'] is None
    assert 'OMNIVOICE_WHISPER_CPP_MODEL' in m.ab.asr_model_missing_detail(missing)

@pytest.mark.parametrize('filename, expected', [('ggml-base.en.bin', False), ('ggml-base.bin', True)])
def test_translation_requires_multilingual_model(tmp_path, monkeypatch, m, filename, expected):
    monkeypatch.setattr(m.wc, 'resolve_model', lambda: tmp_path / filename)
    assert m.WhisperCppBackend().supports_translation() is expected


def test_direct_server_binary_is_rejected(tmp_path, monkeypatch, m):
    from engines.whisper_cpp import bootstrap
    server = tmp_path / 'whisper-server'
    server.write_bytes(b'executable')
    server.chmod(0o755)
    monkeypatch.setenv(m.BIN_ENV, str(server))
    monkeypatch.delenv(m.DIR_ENV, raising=False)
    monkeypatch.setattr(bootstrap, '_PKG_BIN_DIR', tmp_path / 'missing')
    monkeypatch.setattr(bootstrap.shutil, 'which', lambda _: None)
    assert m.locate_binary() is None
