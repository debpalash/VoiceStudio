"""Regressions for review findings on local native ASR boundaries."""
import subprocess
import traceback

import pytest

pytest_plugins = ["whisper_cpp_fixtures"]


@pytest.fixture
def fallback(tmp_path, monkeypatch):
    import soundfile
    from services import ffmpeg_utils
    source = tmp_path / 'private-customer.wav'
    source.write_bytes(b'playlist')
    monkeypatch.setattr(soundfile, 'info', lambda _: (_ for _ in ()).throw(ValueError(str(source))))
    monkeypatch.setattr(ffmpeg_utils, 'find_ffmpeg', lambda: '/tools/ffmpeg')
    return source


def test_ffmpeg_fallback_restricts_local_inputs(fallback, monkeypatch, tmp_path):
    from engines.whisper_cpp.audio import _ensure_wav_16k
    def run(cmd, **kwargs):
        i = cmd.index('-i')
        assert cmd[i-2:i] == ['-protocol_whitelist', 'file,pipe']
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, 'run', run)
    _ensure_wav_16k(str(fallback), tmp_path)


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'missing'])
def test_conversion_errors_hide_input_paths(fallback, monkeypatch, tmp_path, m, failure):
    from engines.whisper_cpp.audio import _ensure_wav_16k
    def run(cmd, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(cmd, 60)
        if failure == 'missing':
            raise FileNotFoundError(str(fallback))
        return subprocess.CompletedProcess(cmd, 3, stderr=str(fallback))
    monkeypatch.setattr(subprocess, 'run', run)
    expected = m.ab.ASRTimeoutError if failure == 'timeout' else RuntimeError
    with pytest.raises(expected) as error:
        _ensure_wav_16k(str(fallback), tmp_path)
    assert str(fallback) not in ''.join(traceback.format_exception(error.value))


@pytest.mark.parametrize('filename', ['ggml-large-v3-turbo.bin', 'ggml-distil-large-v3.bin', 'ggml-base.en-q5_0.bin'])
def test_transcription_only_checkpoints_refuse_translation(tmp_path, monkeypatch, m, filename):
    monkeypatch.setattr(m.wc, 'resolve_model', lambda: tmp_path / filename)
    assert m.WhisperCppBackend().supports_translation() is False


def test_full_json_replaces_broken_utf8_token_bytes(tmp_path, monkeypatch):
    from engines.whisper_cpp.process import run_transcription
    output = tmp_path / 'out.json'
    def run(cmd, **kwargs):
        assert kwargs['encoding'] == 'utf-8' and kwargs['errors'] == 'replace'
        output.write_bytes(b'{"transcription":[{"text":"valid","tokens":[{"text":"\xe4"}]}]}')
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, 'run', run)
    result = run_transcription(['whisper-cli'], output, 20)
    assert result['transcription'][0]['text'] == 'valid'
    assert result['transcription'][0]['tokens'][0]['text'] == '\ufffd'
    from engines.whisper_cpp.output import parse_transcript
    transcript = parse_transcript(result, word_timestamps=True)
    assert transcript['text'] == 'valid'
    assert transcript['segments'][0]['words'] == []


def test_custom_local_model_has_no_invented_repository(tmp_path, monkeypatch, m):
    model = tmp_path / 'custom.bin'
    model.write_bytes(b'weights')
    monkeypatch.setenv('OMNIVOICE_WHISPER_CPP_MODEL', str(model))
    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == []


def test_known_cached_model_preserves_repository_licence(tmp_path, monkeypatch, m):
    model = tmp_path / 'models--licensed-owner--custom-whisper' / 'snapshots' / 'rev' / 'ggml-custom.bin'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'weights')
    monkeypatch.setenv('OMNIVOICE_WHISPER_CPP_MODEL', str(model))
    assert m.ab.backend_model_repos(m.WhisperCppBackend()) == ['licensed-owner/custom-whisper']


def test_known_model_licence_is_checked_before_use(tmp_path, monkeypatch, m):
    from services import model_acceptance
    model = tmp_path / 'models--gated-owner--custom-whisper' / 'snapshots' / 'rev' / 'ggml-custom.bin'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'weights')
    monkeypatch.setenv('OMNIVOICE_WHISPER_CPP_MODEL', str(model))
    def refuse(repos):
        assert repos == ['gated-owner/custom-whisper']
        raise RuntimeError('licence refused')
    monkeypatch.setattr(model_acceptance, 'ensure_accepted', refuse)
    with pytest.raises(RuntimeError, match='licence refused'):
        m.ab.ensure_backend_licence(m.WhisperCppBackend())
