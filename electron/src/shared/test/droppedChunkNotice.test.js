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

describe('the copy exists in every locale', () => {
  const dir = path.resolve(__dirname, '..', '..', 'renderer', 'src', 'i18n', 'locales');
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
