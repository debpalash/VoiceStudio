import { describe, it, expect } from 'vitest';
import { applySpeakerCloneDefaults, autoProfileId, castSourcesFromJob } from '../utils/segments';

// #486: multi-speaker dub must auto-bind each segment to its detected
// speaker's cloned voice instead of leaving every row on "Default".
describe('applySpeakerCloneDefaults (#486)', () => {
  const clones = { 'Speaker 1': { duration: 4.2 }, 'Speaker 2': { duration: 3.1 } };

  it('assigns the auto: clone id to segments whose speaker was cloned', () => {
    const segs = [
      { id: '0', speaker_id: 'Speaker 1', profile_id: '' },
      { id: '1', speaker_id: 'Speaker 2', profile_id: '' },
    ];
    const out = applySpeakerCloneDefaults(segs, clones);
    expect(out[0].profile_id).toBe('auto:speaker_1');
    expect(out[1].profile_id).toBe('auto:speaker_2');
    expect(autoProfileId('Speaker 2')).toBe('auto:speaker_2');
  });

  it('uses the backend-compatible portable slug for raw diarizer labels', () => {
    expect(autoProfileId('SPEAKER_00')).toBe('auto:speaker00');
    expect(autoProfileId('Guest-2!')).toBe('auto:guest_2');
  });

  it('recovers From video choices from legacy per-segment references', () => {
    const sources = castSourcesFromJob({
      segments: [
        { id: 'a', speaker_id: 'Speaker 1' },
        { id: 'b', speaker_id: 'Speaker 2' },
      ],
      speaker_clones: {},
      segment_clones: {
        a: { ref_audio: '/private/a.wav', ref_text: 'hidden', duration: 3.4 },
        b: { ref_audio: '/private/b.wav', ref_text: 'hidden', duration: 4.2 },
      },
    });
    expect(sources).toEqual({
      'Speaker 1': { duration: 3.4, source_count: 1, kind: 'segment' },
      'Speaker 2': { duration: 4.2, source_count: 1, kind: 'segment' },
    });
    expect(JSON.stringify(sources)).not.toContain('/private');
  });

  it('strips paths and transcript text from legacy pooled clone metadata', () => {
    const sources = castSourcesFromJob({
      speaker_clones: {
        'Speaker 1': {
          ref_audio: '/home/person/private.wav',
          ref_text: 'private transcript',
          duration: 7.5,
          source_count: 2,
        },
      },
    });
    expect(sources).toEqual({
      'Speaker 1': { duration: 7.5, source_count: 2, kind: 'speaker' },
    });
    expect(JSON.stringify(sources)).not.toContain('/home/person');
    expect(JSON.stringify(sources)).not.toContain('private transcript');
  });

  it('never clobbers a profile_id the user already chose', () => {
    const segs = [{ id: '0', speaker_id: 'Speaker 1', profile_id: 'preset:narrator' }];
    expect(applySpeakerCloneDefaults(segs, clones)[0].profile_id).toBe('preset:narrator');
  });

  it('leaves a segment on Default when its speaker has no clone', () => {
    const segs = [{ id: '0', speaker_id: 'Speaker 9', profile_id: '' }];
    expect(applySpeakerCloneDefaults(segs, clones)[0].profile_id).toBe('');
  });

  it('is a no-op when there are no clones', () => {
    const segs = [{ id: '0', speaker_id: 'Speaker 1', profile_id: '' }];
    expect(applySpeakerCloneDefaults(segs, {})[0].profile_id).toBe('');
    expect(applySpeakerCloneDefaults(segs, null)).toEqual(segs);
  });
});
