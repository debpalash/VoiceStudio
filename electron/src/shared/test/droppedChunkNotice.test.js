/**
 * A take that came back missing text must SAY so (#1330).
 *
 * The backend collects the sentences that rendered to nothing and reports them
 * two ways — response headers on the classic path (the body is a WAV) and a
 * `warning` frame before `done` on the streaming one. Both have to reach a
 * toast, or the user is back to noticing by reading along, which is how this
 * was reported in the first place.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const read = (rel) => fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8');

describe('the streaming client forwards a warning frame', () => {
  it('routes `warning` to onWarning without ending the stream', async () => {
    const src = read('utils/streamingTts.js');
    const branch = src.slice(src.indexOf("ev.type === 'warning'"));
    const body = branch.slice(0, branch.indexOf("ev.type === 'done'"));
    expect(body).toMatch(/onWarning\?\.\(ev\)/);
    // It must NOT throw: `error` tears the stream down and triggers a full
    // classic re-render, which would make a complete take out of an
    // incomplete one only by spending the whole render again — and the user
    // would still not be told.
    expect(body).not.toMatch(/throw /);
  });

  it('accepts onWarning as a stream option', () => {
    const src = read('utils/streamingTts.js');
    expect(src).toMatch(/onHeaders, onProgress, onWarning/);
  });
});

describe('the generate hook announces the loss on both paths', () => {
  const src = read('hooks/useTTS.js');

  it('reads the dropped-chunk count from the classic response headers', () => {
    expect(src).toMatch(/X-OmniVoice-Dropped-Chunks/);
    expect(src).toMatch(/X-OmniVoice-Dropped-Text/);
  });

  it('passes onWarning to the streaming call', () => {
    const call = src.slice(src.indexOf('streamGenerateSpeech('));
    expect(call.slice(0, 600)).toMatch(/onWarning:/);
  });

  it('announces through one shared helper, so the two paths cannot drift', () => {
    // Two copies of this message is how one path ends up silent after a
    // refactor — the bug being fixed, one delivery path over. One definition,
    // and a call from each of the two delivery paths.
    expect(src.match(/const announceDroppedText/g) || []).toHaveLength(1);
    expect((src.match(/announceDroppedText\(/g) || []).length).toBeGreaterThanOrEqual(2);
  });

  it('uses i18n keys rather than an English literal', () => {
    expect(src).toMatch(/tts\.droppedChunksWithText/);
    expect(src).toMatch(/tts\.droppedChunks/);
  });

  it('never treats a dropped chunk as a failed generation', () => {
    // The audio is real and playable; only the notice is new.
    const helper = src.slice(src.indexOf('const announceDroppedText'));
    expect(helper.slice(0, 400)).not.toMatch(/throw |setError/);
  });
});

describe('the copy exists in every locale', () => {
  const dir = path.resolve(__dirname, '..', 'i18n', 'locales');
  const locales = fs.readdirSync(dir).filter((f) => f.endsWith('.json'));

  it('has both keys in all locale files, with their placeholders intact', () => {
    expect(locales.length).toBeGreaterThanOrEqual(21);
    for (const f of locales) {
      const j = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
      // i18next plural forms: the message counts parts, so a bare
      // "{{count}} part" reads wrong the moment two are dropped (CodeRabbit).
      for (const suffix of ['_one', '_other']) {
        expect(j.tts?.[`droppedChunks${suffix}`], `${f} ${suffix}`).toBeTruthy();
        expect(j.tts?.[`droppedChunksWithText${suffix}`], `${f} ${suffix}`).toBeTruthy();
        expect(j.tts[`droppedChunks${suffix}`], `${f} ${suffix}`).toContain('{{count}}');
        expect(j.tts[`droppedChunksWithText${suffix}`], `${f} ${suffix}`).toContain('{{count}}');
        expect(j.tts[`droppedChunksWithText${suffix}`], `${f} ${suffix}`).toContain('{{text}}');
      }
      // The unsuffixed keys must be gone, or i18next resolves those instead
      // and the plural forms are dead weight.
      expect(j.tts?.droppedChunks, f).toBeUndefined();
      expect(j.tts?.droppedChunksWithText, f).toBeUndefined();
    }
  });
});

describe('the toast itself', () => {
  it('quotes the lost text when there is text, and still fires when there is not', async () => {
    // Exercised through the helper's shape rather than a full hook render:
    // the hook needs a store, a player and Web Audio, none of which this
    // behaviour depends on.
    const src = read('hooks/useTTS.js');
    const helper = src.slice(src.indexOf('const announceDroppedText'));
    const body = helper.slice(0, helper.indexOf('};'));
    expect(body).toMatch(/preview\s*\n?\s*\?\s*t\('tts\.droppedChunksWithText'/);
    expect(body).toMatch(/:\s*t\('tts\.droppedChunks'/);
    // Truncated, so a long lost paragraph cannot become an unreadable toast.
    expect(body).toMatch(/slice\(0, \d+\)/);
  });

  it('is only raised when something was actually dropped', () => {
    const src = read('hooks/useTTS.js');
    expect(src).toMatch(/droppedCount\) && droppedCount > 0/);
    expect(src).toMatch(/ev\.count > 0/);
  });
});
