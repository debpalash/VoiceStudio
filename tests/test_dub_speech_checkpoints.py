import torch
from services.dub_speech_checkpoints import SpeechCheckpoints


def test_recipe_and_voice_content_control_reuse(tmp_path):
    reference=tmp_path/'ref.wav';reference.write_bytes(b'voice A')
    args={'text':'hello','duration':None,'ref_audio':str(reference),'num_step':16}
    c=SpeechCheckpoints(str(tmp_path),'engine',24000,{})
    c.write(args,torch.ones(1,2400)*.1)
    # New process/object, timing-only change: natural speech is reusable.
    other=SpeechCheckpoints(str(tmp_path),'engine',24000,{})
    assert other.read(dict(args,duration=1)) is not None
    assert other.read(dict(args,text='different')) is None
    assert other.read(dict(args,num_step=32)) is None
    reference.write_bytes(b'voice B')
    assert other.read(args) is None
    assert SpeechCheckpoints(str(tmp_path),'other-engine',24000,{}).read(args) is None


def test_corrupt_and_silent_audio_never_becomes_recoverable_speech(tmp_path):
    import pytest
    args={'text':'hello'}
    c=SpeechCheckpoints(str(tmp_path),'engine',24000,{})
    with pytest.raises(ValueError):c.write(args,torch.zeros(1,2400))
    c.write(args,torch.ones(1,2400)*.1)
    path=c._path(args)
    data=bytearray(path.read_bytes());data[-10]^=0xff;path.write_bytes(data)
    assert c.read(args) is None


def test_unproven_or_changed_model_never_reuses_speech(tmp_path):
    cache = SpeechCheckpoints(str(tmp_path), 'test', 24000, {})
    proof = ['weights-v1']
    cache.key_proof = lambda prepared: proof[0]
    prepared = {'text': 'same words', 'duration': None}
    speech = torch.ones(1, 2400) * .1
    cache.write(prepared, speech)
    assert cache.read(prepared) is not None
    proof[0] = 'weights-v2'
    assert cache.read(prepared) is None
    proof[0] = None
    assert cache.read(prepared) is None
    before = cache.saved
    assert torch.equal(cache.write(prepared, speech), speech)
    assert cache.saved == before


def test_cache_write_and_identity_failures_keep_the_working_render(tmp_path, monkeypatch, caplog):
    import services.dub_speech_checkpoints as module
    cache = SpeechCheckpoints(str(tmp_path), 'test', 24000, {})
    speech = torch.ones(1, 2400) * .1
    def unavailable(*args, **kwargs):
        raise OSError('/home/private-voice/sample.wav')
    monkeypatch.setattr(module, 'atomic_save_wav', unavailable)
    assert torch.equal(cache.write({'text': 'hello'}, speech), speech)
    assert cache.saved == 0
    assert 'private-voice' not in caplog.text
    cache.key_proof = unavailable
    assert cache.read({'text': 'hello'}) is None
    assert torch.equal(cache.write({'text': 'hello'}, speech), speech)
