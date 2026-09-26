import { describe, it, expect } from 'vitest';
import { storyToScript } from './storyToScript';

const spoken = (id, text, character = 'narrator', profileId = null) => ({
  id,
  character,
  text,
  profileId,
  emotion: null,
  speed: null,
});

describe('storyToScript', () => {
  it('empty / degenerate input → empty export', () => {
    const empty = { script: '', metadata: {}, defaultVoice: null };
    expect(storyToScript([], [])).toEqual(empty);
    expect(storyToScript(null, null)).toEqual(empty);
    expect(storyToScript([spoken(1, '# Only chapter')], [])).toEqual(empty);
    expect(storyToScript([spoken(1, '   ')], [])).toEqual(empty);
  });

  it('single narrator, no chapters → flat, no [voice:] tags, no #', () => {
    const r = storyToScript([spoken(1, 'Hello.'), spoken(2, 'World.')], []);
    expect(r.script).toBe('Hello.\n\nWorld.');
    expect(r.defaultVoice).toBeNull();
    expect(r.script).not.toMatch(/\[voice:/);
  });

  it('most-used voice becomes defaultVoice (no tag); others get [voice:id]', () => {
    const cast = [{ id: 'c_fox', name: 'Fox', color: '#x', profileId: 'p_fox' }];
    const tracks = [
      spoken(1, 'A', 'narrator', 'p_main'),
      spoken(2, 'B', 'narrator', 'p_main'),
      spoken(3, 'C', 'c_fox'), // resolves to p_fox via cast
    ];
    const r = storyToScript(tracks, cast);
    expect(r.defaultVoice).toBe('p_main');
    expect(r.script).toBe('A\n\nB\n\n[voice:p_fox] C');
  });

  it('tie-break picks the earliest first occurrence', () => {
    const r = storyToScript(
      [spoken(1, 'A', 'narrator', 'p_a'), spoken(2, 'B', 'narrator', 'p_b')],
      [],
    );
    expect(r.defaultVoice).toBe('p_a'); // 1–1 tie → earliest
    expect(r.script).toBe('A\n\n[voice:p_b] B');
  });

  it('chapters: H1-only (#27) — ## narrates as body, indented # un-indented', () => {
    const r = storyToScript(
      [spoken(1, '## Deep'), spoken(2, 'body1'), spoken(3, '   # Indented'), spoken(4, 'body2')],
      [],
    );
    // #27 convergence: `## Deep` is NOT a heading (H1-only), so it narrates as
    // body text verbatim; only the indented H1 opens a chapter (un-indented).
    expect(r.script).toBe('## Deep\n\nbody1\n\n# Indented\n\nbody2');
  });

  it('does not double-tag a line that already leads with [voice:sameid]', () => {
    const r = storyToScript(
      [spoken(1, 'A', 'narrator', 'p_main'), spoken(2, '[voice:p_fox] hi', 'narrator', 'p_fox')],
      [],
    );
    // p_main is default (1 vs 1 tie, earliest) → line 2 switches to p_fox but
    // already leads with the tag → not doubled.
    expect(r.script).toBe('A\n\n[voice:p_fox] hi');
    expect(r.script).not.toMatch(/\[voice:p_fox\]\s*\[voice:p_fox\]/);
  });

  it('metadata: title from projectName, narrator from default-voice cast member', () => {
    const cast = [{ id: 'c1', name: 'Aria', color: '#x', profileId: 'p_aria' }];
    const r = storyToScript([spoken(1, 'hi', 'c1')], cast, { projectName: 'My Book' });
    expect(r.metadata).toEqual({ title: 'My Book', narrator: 'Aria' });
  });

  it('omits metadata keys with empty values', () => {
    const r = storyToScript([spoken(1, 'hi')], [], { projectName: '' });
    expect(r.metadata).toEqual({});
  });

  it('pause-only line is emitted verbatim (not dropped)', () => {
    const r = storyToScript([spoken(1, '[pause 0.5s]'), spoken(2, 'go')], []);
    expect(r.script).toBe('[pause 0.5s]\n\ngo');
  });
});
