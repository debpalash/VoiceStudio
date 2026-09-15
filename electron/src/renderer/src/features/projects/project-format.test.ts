import { expect, it } from 'vitest';
import { projectSession, projectPayload } from './project-format';
import type { DubSession } from '../dub/dub-session';
const defaults: DubSession = {
  jobId: null,
  taskId: null,
  filename: '',
  inputType: 'video',
  phase: 'idle',
  segments: [],
  sourceLang: '',
  tracks: [],
  event: null,
  error: null,
  recovery: null,
  target: 'Spanish',
  quality: 'fast',
};
it('opens legacy projects and preserves unexposed options when saving edits', () => {
  const project = {
    id: 'project',
    name: 'Legacy',
    audio_path: '/audio.wav',
    duration: 3,
    state: {
      dubJobId: 'job',
      dubFilename: 'clip.mp4',
      dubLang: 'French',
      translateQuality: 'cinematic',
      translationInstructions: 'Preserve humor.',
      fitOptions: { allow_video_retime: false, audio_rate_cap: 1.3 },
      dubStep: 'generating',
      dubSegments: [{ id: 7, start: 0, end: 2, text: 'Bonjour', translations: { fr: 'Bonjour' } }],
      preserveBg: false,
      multiLangs: [{ lang: 'French', code: 'fr' }],
      exportTracks: { original: false },
    },
  };
  const session = projectSession(project, defaults);
  expect(session.quality).toBe('cinematic');
  expect(session.translationInstructions).toBe('Preserve humor.');
  expect(session.exportOptions).toMatchObject({ preserveBg: false, excluded: ['original'] });
  expect(session.fitOptions).toEqual({ allow_video_retime: false, audio_rate_cap: 1.3 });
  expect(session.phase).toBe('editing');
  expect(session.taskId).toBeNull();
  expect(session.segments[0]).toMatchObject({ id: '7', text_original: 'Bonjour' });
  const payload = projectPayload({ ...session, target: 'Spanish' }, ' Renamed ');
  expect(payload.state.translationInstructions).toBe('Preserve humor.');
  expect(payload).toMatchObject({
    name: 'Renamed',
    audio_path: '/audio.wav',
    duration: 3,
    state: {
      dubLangCode: 'es',
      translateQuality: 'cinematic',
      fitOptions: { allow_video_retime: false, audio_rate_cap: 1.3 },
      preserveBg: false,
      multiLangs: project.state.multiLangs,
      exportTracks: project.state.exportTracks,
    },
  });
  expect(payload.state.dubSegments[0]).toHaveProperty('translations.fr', 'Bonjour');
});
it('discards malformed segments without inventing active tasks', () => {
  const session = projectSession(
    { id: 'x', name: 'x', state: { dubSegments: [null, { start: -1, end: 3, text: 'bad' }] } },
    defaults,
  );
  expect(session.segments).toEqual([]);
  expect(session.recovery).toBeNull();
});
