import { describe, it, expect, vi } from 'vitest';
import { sanitizeOmniUi, OMNI_UI_SCHEMA } from '../utils/omniUiSchema';

describe('sanitizeOmniUi', () => {
  it('drops malformed fields without discarding the rest (the mid-restore-abort class)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = sanitizeOmniUi({
      uiScale: 1.2,
      dubSegments: 'oops-a-string', // poisoned — used to throw at .map and abort the restore
      dubStep: 'editing',
      speed: 1.0,
    });
    expect(out.uiScale).toBe(1.2);
    expect(out.dubStep).toBe('editing');
    expect(out.speed).toBe(1.0);
    expect('dubSegments' in out).toBe(false);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it('drops unknown keys (future fields must be added to the schema)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const out = sanitizeOmniUi({ someFutureField: { deeply: 'nested' }, text: 'hi' });
    expect(out).toEqual({ text: 'hi' });
    warn.mockRestore();
  });

  it('never throws on garbage input', () => {
    for (const garbage of [null, undefined, 'str', 42, [], () => {}]) {
      expect(sanitizeOmniUi(garbage)).toEqual({});
    }
  });

  it('passes a fully well-formed blob through unchanged', () => {
    const good = {
      uiScale: 1,
      text: 't',
      mode: 'studio',
      language: 'en',
      isSidebarCollapsed: false,
      dubSegments: [{ id: '1', text: 'x' }],
      dubTracks: ['es', 'fr'],
      dubStep: 'editing',
      exportTracks: { original: true, es: false },
      preserveBg: true,
      speed: 1,
      steps: 16,
      cfg: 2,
    };
    expect(sanitizeOmniUi({ ...good })).toEqual(good);
  });

  it('rejects swapped persisted track shapes without dropping valid track state', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(
      sanitizeOmniUi({
        dubTracks: ['es'],
        exportTracks: { original: true, es: false },
      }),
    ).toEqual({ dubTracks: ['es'], exportTracks: { original: true, es: false } });
    expect(sanitizeOmniUi({ dubTracks: {}, exportTracks: [] })).toEqual({});
    warn.mockRestore();
  });

  it('schema covers every field the restore path reads', () => {
    // Lockstep guard: if useAppData reads a saved.<field> not in the schema,
    // restore silently drops it — fail here instead of in the field.
    const src = require('fs').readFileSync(
      require('path').resolve(__dirname, '../hooks/useAppData.js'),
      'utf8',
    );
    const reads = [...src.matchAll(/saved\.([A-Za-z0-9_]+)/g)].map((m) => m[1]);
    for (const key of new Set(reads)) {
      expect(OMNI_UI_SCHEMA[key], `omniUiSchema is missing '${key}'`).toBeTypeOf('function');
    }
  });
});
