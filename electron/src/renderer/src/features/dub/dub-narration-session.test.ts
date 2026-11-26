import { afterEach, expect, it } from 'vitest';
import {
  dubSession,
  setDubNarrationEnabled,
  editDubSegment,
  setDubTarget,
  mergeDubSegment,
} from './dub-session';
import { dubGenerationRequest } from './dub-generation-request';
import { restoreDubDraft } from './dub-draft';
import { projectPayload, projectSession } from '../projects/project-format';
const original = dubSession.state;
afterEach(() => dubSession.setState(() => original));
function setup() {
  const segments = [
    {
      id: 'a',
      start: 10,
      end: 12,
      text: 'We configure',
      text_original: 'Configuramos',
      profile_id: 'voice',
      translations: { en: 'We configure' },
    },
    {
      id: 'b',
      start: 12,
      end: 17,
      text: 'the server.',
      text_original: 'el servidor.',
      profile_id: 'voice',
      translations: { en: 'the server.' },
    },
  ];
  dubSession.setState((s) => ({
    ...s,
    jobId: 'job',
    phase: 'editing',
    recovery: null,
    target: 'English',
    sourceLang: 'es',
    segments,
    narration: undefined,
  }));
  return structuredClone(segments);
}
it('generates blocks while keeping the exact caption payload and restoring edited blocks', () => {
  const captions = setup();
  expect(setDubNarrationEnabled(true)).toBe(true);
  expect(dubSession.state.segments).toHaveLength(1);
  editDubSegment(dubSession.state.segments[0].id, { text: 'We configure our server.' });
  const req = dubGenerationRequest(dubSession.state, 'English', 'en');
  expect(req.timing_strategy).toBe('narration');
  expect(req.voice_match).toBe('consistent');
  expect(req.preview).toBe(false);
  expect(req.caption_segment_ids).toEqual(['a', 'b']);
  expect(req.caption_segments?.map((c) => c.text)).toEqual(captions.map((c) => c.text));
  setDubNarrationEnabled(false);
  expect(dubSession.state.segments).toEqual(captions);
  setDubNarrationEnabled(true);
  expect(dubSession.state.segments[0].text).toBe('We configure our server.');
});
it('persists separate scripts through drafts and projects and leaves narration before a language change', () => {
  setup();
  setDubNarrationEnabled(true);
  const s = dubSession.state;
  const restored = restoreDubDraft(JSON.stringify(s), original);
  expect(restored.narration?.captions).toHaveLength(2);
  expect(restored.segments[0].source_ids).toEqual(['a', 'b']);
  const payload = projectPayload(s, 'test');
  const project = { ...payload, id: 'project' };
  expect(projectSession(project, original).narration?.enabled).toBe(true);
  setDubTarget('Spanish', 'es');
  expect(dubSession.state.narration?.enabled).toBe(false);
  expect(dubSession.state.segments).toHaveLength(2);
});
it('preserves source links when blocks are merged', () => {
  setup();
  dubSession.setState((s) => ({
    ...s,
    segments: [
      ...s.segments,
      { ...s.segments[1], id: 'c', start: 20, end: 24, text: 'A new sentence.' },
    ],
  }));
  setDubNarrationEnabled(true);
  expect(dubSession.state.segments).toHaveLength(2);
  mergeDubSegment(dubSession.state.segments[0].id);
  expect(dubSession.state.segments[0].source_ids).toEqual(['a', 'b', 'c']);
  expect(dubSession.state.narration?.captions).toHaveLength(3);
});

it('restores SRT bounds without changing words, voices or original captions', async () => {
  const { restoreDubNarrationWindows } = await import('./dub-session');
  const originalCaptions = setup();
  setDubNarrationEnabled(true);
  const b = dubSession.state.segments[0];
  editDubSegment(b.id, { start: 11, end: 18 });
  expect(restoreDubNarrationWindows()).toBe(true);
  expect(dubSession.state.segments[0]).toMatchObject({
    start: 10,
    end: 17,
    text: b.text,
    profile_id: b.profile_id,
  });
  expect(dubSession.state.narration?.captions).toEqual(originalCaptions);
});
it('turns narration off without a language lookup, and keeps the target when it cannot', () => {
  const captions = setup();
  expect(setDubNarrationEnabled(true)).toBe(true);
  // A restored target label outside the narration list must not trap narration on.
  dubSession.setState((s) => ({ ...s, target: 'Uyghur ' }));
  expect(setDubNarrationEnabled(false)).toBe(true);
  expect(dubSession.state.segments).toEqual(captions);

  dubSession.setState((s) => ({ ...s, target: 'English' }));
  expect(setDubNarrationEnabled(true)).toBe(true);
  dubSession.setState((s) => ({ ...s, phase: 'generating' }));
  setDubTarget('Spanish', 'es');
  expect(dubSession.state.target).toBe('English');
  expect(dubSession.state.narration?.enabled).toBe(true);
});
