import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubProjectGuide, dubGuideState } from './dub-project-guide';
import { dubGenerationKey } from './dub-generation-request';
import type { DubSession } from './dub-session';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);
const session = (): DubSession =>
  ({
    jobId: 'job',
    phase: 'editing',
    target: 'English',
    sourceLang: 'es',
    tracks: [],
    recovery: null,
    quality: 'fast',
    taskId: null,
    filename: 'test.mp4',
    inputType: 'video',
    event: null,
    error: null,
    segments: [
      {
        id: 'a',
        text: 'Hello',
        text_original: 'Hola',
        start: 0,
        end: 1,
        profile_id: 'voice',
        translations: { en: 'Hello' },
      },
    ],
  }) as DubSession;

it('guides an imported, translated and voiced project directly to audio generation', () => {
  const state = dubGuideState(session(), 'en');
  expect(state.completed).toEqual([true, true, true, false, false]);
  expect(state.nextStep).toBe('audio');
});
it('requires every spoken line to have target text, rather than one translation somewhere', () => {
  const s = session();
  s.segments.push({ ...s.segments[0], id: 'b', translations: undefined });
  expect(dubGuideState(s, 'en').nextStep).toBe('text');
  s.segments[1].translations = { fr: 'Bonjour' };
  expect(dubGuideState(s, 'en').nextStep).toBe('text');
});
it('accepts target-language SRT text and requests a missing voice', () => {
  const s = session();
  s.subtitleTargetLang = 'en';
  s.segments[0].translations = undefined;
  expect(dubGuideState(s, 'en').textReady).toBe(true);
  s.segments[0].profile_id = undefined;
  expect(dubGuideState(s, 'en').nextStep).toBe('voice');
});
it('marks audio complete only for the current text, voice, settings and timings', () => {
  const s = session();
  s.tracks = ['en'];
  s.generatedRequestKeys = { en: dubGenerationKey(s, 'English', 'en') };
  expect(dubGuideState(s, 'en').nextStep).toBe('export');
  for (const edit of [{ text: 'New words' }, { profile_id: 'other' }, { gain: 0.5 }, { end: 2 }]) {
    const changed = { ...s, segments: [{ ...s.segments[0], ...edit }] };
    expect(dubGuideState(changed, 'en').completed[3]).toBe(false);
  }
  expect(dubGuideState({ ...s, guidance: 3 }, 'en').completed[3]).toBe(false);
});
it('does not treat partial takes or an old unknown track as a completed current render', () => {
  const s = session();
  s.speechPreviews = { en: { a: { url: '/take', key: 'old' } } };
  s.tracks = ['en'];
  expect(dubGuideState(s, 'en').completed[3]).toBe(false);
});
it('offers the next action and keeps future actions disabled', () => {
  const select = vi.fn();
  render(<DubProjectGuide session={session()} code="en" disabled={false} onSelect={select} />);
  fireEvent.click(screen.getByRole('button', { name: /dub.guide_continue/ }));
  expect(select).toHaveBeenCalledWith('audio');
  expect(screen.getByRole('button', { name: /dub.guide_export/ }).hasAttribute('disabled')).toBe(
    true,
  );
});

it('completes export only after the matching revision has actually been saved', async () => {
  const { dubExportRevision } = await import('./dub-generation-request');
  const s = session();
  s.tracks = ['en'];
  s.generatedRequestKeys = { en: dubGenerationKey(s, 'English', 'en') };
  expect(dubGuideState(s, 'en').completed[4]).toBe(false);
  s.exportedRequestKeys = { en: dubExportRevision(s, 'en') };
  expect(dubGuideState(s, 'en').allComplete).toBe(true);
  s.exportOptions = { format: 'mp3' };
  expect(dubGuideState(s, 'en').completed[4]).toBe(false);
});

it('prepares target text before voice and waits for an explicitly requested script', () => {
  const s = session();
  s.segments[0].profile_id = undefined;
  s.segments[0].translations = undefined;
  expect(dubGuideState(s, 'en').nextStep).toBe('text');
  s.segments[0].translations = {en: 'Hello'};
  s.segments[0].profile_id = 'voice';
  s.narrationWanted = true;
  expect(dubGuideState(s, 'en').nextStep).toBe('voice');
  expect(dubGuideState(s, 'en').scriptReady).toBe(false);
});

it('treats audio saved before completion records as ready to export', () => {
  const s = { ...session(), phase: 'done' as const, tracks: ['en'] };
  s.fingerprintsByLang = { en: { a: 'hash-a' } };
  const state = dubGuideState(s, 'en');
  expect(state.audioReady).toBe(true);
  expect(state.completed).toEqual([true, true, true, true, false]);
  expect(state.nextStep).toBe('export');
  render(<DubProjectGuide session={s} code="en" disabled={false} onSelect={() => {}} />);
  expect(screen.getByRole('button', { name: /dub.guide_audio.*dub.guide_complete/ })).toBeTruthy();
});
it('keeps legacy audio pending when a line has no saved take or changed since that render', () => {
  const s = { ...session(), phase: 'done' as const, tracks: ['en'] };
  expect(dubGuideState(s, 'en').audioReady).toBe(false);
  s.fingerprintsByLang = { en: { a: 'hash-a' } };
  expect(dubGuideState(s, 'en', ['a']).audioReady).toBe(false);
  s.segments.push({ ...s.segments[0], id: 'b' });
  expect(dubGuideState(s, 'en').audioReady).toBe(false);
  // A guided completion record, once present, is authoritative.
  s.generatedRequestKeys = { en: 'older-request' };
  s.fingerprintsByLang.en.b = 'hash-b';
  expect(dubGuideState(s, 'en').audioReady).toBe(false);
});
