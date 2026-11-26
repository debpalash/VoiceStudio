import torch


def SpeechCheckpoints(*args, **kwargs):
    # Resolved at run time: a test that swaps the module in sys.modules must
    # not leave this file holding a stale class.
    from services.dub_speech_checkpoints import SpeechCheckpoints as cls
    return cls(*args, **kwargs)


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


def test_checkpoints_stay_within_their_disk_bound(tmp_path):
    import os
    cache = SpeechCheckpoints(str(tmp_path), 'engine', 24000, {})
    speech = torch.ones(1, 2400) * .1
    for index in range(4):
        cache.write({'text': f'take {index}'}, speech)
        path = cache._path({'text': f'take {index}'})
        os.utime(path, ns=(index * 10**9, index * 10**9))
    size = cache._path({'text': 'take 0'}).stat().st_size
    assert cache.prune(max_bytes=2 * size) == 2
    # The oldest takes go first, with their checksums; recent ones stay usable.
    assert cache.read({'text': 'take 0'}) is None and cache.read({'text': 'take 1'}) is None
    assert not cache._path({'text': 'take 0'}).with_suffix('.sha256').exists()
    assert cache.read({'text': 'take 3'}) is not None
    assert cache.prune() == 0


def test_request_ids_cannot_escape_the_dub_folder(tmp_path, monkeypatch):
    import os
    import pytest
    import api.routers.dub_generate as dg
    import core.config as config
    from services.dub_speech_preview import preview_path
    monkeypatch.setattr(dg, 'DUB_DIR', str(tmp_path / 'dub'))
    monkeypatch.setattr(config, 'DUB_DIR', str(tmp_path / 'dub'))
    assert dg._track_path('job', 'en') == str(tmp_path / 'dub' / 'job' / 'dubbed_en.wav')
    for job_id in ('..', '../..', '/etc'):
        with pytest.raises(ValueError):
            dg._track_path(job_id, 'en')
    with pytest.raises(ValueError):
        config.dub_seg_path('..', 'x')
    job_dir = str(tmp_path / 'dub' / 'job')
    assert preview_path(job_dir, 'a' * 32, 'b' * 64).startswith(os.path.join(job_dir, '.speech-previews') + os.sep)
    for render_id, token in (('..', 'b' * 64), ('a' * 32, '../' + 'b' * 61), ('A' * 32, 'b' * 64)):
        with pytest.raises(ValueError):
            preview_path(job_dir, render_id, token)


def test_render_ids_are_rebuilt_from_an_exact_allowlist():
    import asyncio
    import pytest
    from fastapi import HTTPException
    import api.routers.dub_generate as dg
    from schemas.requests import DubRequest
    token = dg._request_token('3f2b-Job_1', limit=64, what='job id')
    assert token == '3f2b-Job_1'
    for bad in ('', '..', 'a/b', 'a\\b', 'a.b', '-' * 65, 'job\n', None):
        with pytest.raises(HTTPException) as error:
            dg._request_token(bad, limit=64, what='job id')
        assert error.value.status_code == 400
    body = dict(segments=[dict(start=0, end=1, text='hello')], segment_ids=['a'])
    for job_id, lang in (('../x', 'en'), ('job', '../en'), ('job', 'e.n')):
        with pytest.raises(HTTPException) as error:
            asyncio.run(dg.dub_generation_plan(job_id, DubRequest(**body, language_code=lang)))
        assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        dg.dub_speech_preview('..', 'a' * 32, 'b' * 64)
    assert error.value.status_code == 400


def test_prune_counts_only_takes_it_actually_deleted(tmp_path, monkeypatch):
    import os
    import services.dub_speech_checkpoints as module
    cache = SpeechCheckpoints(str(tmp_path), 'engine', 24000, {})
    for index in range(3):
        cache.write({'text': f'take {index}'}, torch.ones(1, 2400) * .1)
        os.utime(cache._path({'text': f'take {index}'}), ns=(index * 10**9, index * 10**9))
    stuck = cache._path({'text': 'take 0'})
    size = stuck.stat().st_size
    real_discard = module._discard
    monkeypatch.setattr(module, '_discard', lambda path: False if path == stuck else real_discard(path))
    # The oldest cannot be deleted: the next one goes instead, and the
    # undeletable take is not counted as freed.
    assert cache.prune(max_bytes=2 * size) == 1
    assert stuck.exists() and not cache._path({'text': 'take 1'}).exists()


def test_has_does_not_decode(tmp_path, monkeypatch):
    import services.dub_speech_checkpoints as module
    cache = SpeechCheckpoints(str(tmp_path), 'engine', 24000, {})
    cache.write({'text': 'hello', 'duration': None}, torch.ones(1, 2400) * .1)
    monkeypatch.setattr(module, 'load_audio', lambda *a: (_ for _ in ()).throw(AssertionError('decoded')))
    assert cache.has({'text': 'hello', 'duration': 2.0})
    assert not cache.has({'text': 'other', 'duration': None})


def test_symlinked_preview_folder_cannot_lead_outside(tmp_path):
    import os
    import pytest
    from services.dub_speech_preview import preview_path
    job_dir = tmp_path / 'job'
    (job_dir / '.speech-previews').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        os.symlink(outside, job_dir / '.speech-previews' / ('a' * 32))
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(ValueError):
        preview_path(str(job_dir), 'a' * 32, 'b' * 64)
