"""Speaker refresh must preserve the replay path's source-language contract."""
import pytest
import test_dub_speaker_reanalysis as speaker_analysis

pytest_plugins = ['test_dub_speaker_reanalysis']
# Transcription mechanics only: the no-ASR-installed preflight has its own suite.
pytestmark = pytest.mark.usefixtures('asr_model_installed')


@pytest.mark.parametrize('source_lang', [None, '', 'nl', 'haw'])
@pytest.mark.parametrize('override', [None, 'fr'])
def test_speaker_refresh_preserves_source_language(reanalysis, source_lang, override):
    client, job, calls, saved, _, _ = reanalysis
    job['source_lang'] = source_lang
    job['source_lang_detection_missing'] = not bool(source_lang)
    if override:
        job['source_lang_override'] = override

    replay = speaker_analysis.events(client.get('/dub/transcribe-stream/job'))
    refreshed = speaker_analysis.events(client.get('/dub/transcribe-stream/job?reanalyze_speakers=true'))
    replay_final = next(data for event, data in replay if event == 'final')
    refreshed_final = next(data for event, data in refreshed if event == 'final')

    assert refreshed_final['source_lang'] == replay_final['source_lang'] == (override or source_lang)
    assert job['source_lang'] == source_lang
    assert saved[-1]['source_lang_detection_missing'] is (not bool(source_lang))
    assert len(calls) == 1
    assert [row['text'] for row in refreshed_final['segments']] == ['Edited first', 'Edited second']
