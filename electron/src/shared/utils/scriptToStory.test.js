import { describe, it, expect } from 'vitest';
import { scriptToStory } from './scriptToStory';
import { storyToScript } from './storyToScript';

describe('scriptToStory', () => {
  it('empty → no tracks, cast is a narrator clone (never [])', () => {
    const r = scriptToStory('');
    expect(r.tracks).toEqual([]);
    expect(r.cast).toHaveLength(1);
    expect(r.cast[0]).toMatchObject({ id: 'narrator', profileId: null });
    expect(scriptToStory(null).cast[0].id).toBe('narrator');
  });

  it('# lines → chapter tracks; body → spoken tracks; sequential ids from 1', () => {
    const r = scriptToStory('# Chapter 1\n\nHello there.\n\nMore.');
    expect(r.tracks.map((t) => t.text)).toEqual(['# Chapter 1', 'Hello there.', 'More.']);
    expect(r.tracks.map((t) => t.id)).toEqual([1, 2, 3]);
    for (const t of r.tracks) expect(t).toMatchObject({ emotion: null, speed: null });
  });

  it('leading [voice:id] → profileId set, tag stripped, cast named from profiles', () => {
    const r = scriptToStory('[voice:p_fox] Hi there', [{ id: 'p_fox', name: 'Fox' }]);
    expect(r.tracks[0].text).toBe('Hi there');
    expect(r.tracks[0].profileId).toBe('p_fox');
    const fox = r.cast.find((c) => c.profileId === 'p_fox');
    expect(fox.name).toBe('Fox');
    expect(r.tracks[0].character).toBe(fox.id);
  });

  it('leading [voice:default] / [voice:] → narrator, tag stripped', () => {
    expect(scriptToStory('[voice:default] hi').tracks[0]).toMatchObject({
      text: 'hi',
      profileId: null,
      character: 'narrator',
    });
    expect(scriptToStory('[voice:] hi').tracks[0]).toMatchObject({ text: 'hi', profileId: null });
  });

  it('mid-line [voice:] stays in text (not split into a new track)', () => {
    const r = scriptToStory('A then [voice:p_x] more on one line');
    expect(r.tracks).toHaveLength(1);
    expect(r.tracks[0].text).toBe('A then [voice:p_x] more on one line');
  });

  it('unknown voice id → cast name = raw id, profileId = raw id (round-trips)', () => {
    const r = scriptToStory('[voice:p_ghost] hi', []);
    const cm = r.cast.find((c) => c.profileId === 'p_ghost');
    expect(cm.name).toBe('p_ghost');
    expect(r.tracks[0].profileId).toBe('p_ghost');
  });

  it('two ids slugging to the same value get unique cast ids', () => {
    const r = scriptToStory('[voice:p.fox] a\n[voice:p-fox] b', []);
    const ids = r.cast.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length); // all unique
  });

  it('normalizes CRLF and drops blank lines', () => {
    const r = scriptToStory('A\r\n\r\nB\r\n');
    expect(r.tracks.map((t) => t.text)).toEqual(['A', 'B']);
  });
});

describe('round-trip render-equivalence', () => {
  it('script → story → script reproduces the script', () => {
    const script = '# Chapter 1\n\nNarrator speaks.\n\n[voice:p_fox] Fox replies.';
    const { tracks, cast } = scriptToStory(script, [{ id: 'p_fox', name: 'Fox' }]);
    const back = storyToScript(tracks, cast).script;
    expect(back).toBe(script);
  });

  it('story → script → story preserves spoken text + voice mapping', () => {
    const cast = [
      { id: 'narrator', name: 'Narrator', color: '#fabd2f', profileId: null },
      { id: 'c_fox', name: 'Fox', color: '#d3869b', profileId: 'p_fox' },
    ];
    const tracks = [
      {
        id: 1,
        character: 'narrator',
        text: 'Once upon a time.',
        profileId: null,
        emotion: null,
        speed: null,
      },
      { id: 2, character: 'c_fox', text: 'Hello!', profileId: null, emotion: null, speed: null },
    ];
    const { script, defaultVoice } = storyToScript(tracks, cast);
    expect(defaultVoice).toBeNull(); // 1 narrator vs 1 fox tie → earliest = narrator(null)
    const round = scriptToStory(script, [{ id: 'p_fox', name: 'Fox' }]);
    expect(round.tracks.map((t) => t.text)).toEqual(['Once upon a time.', 'Hello!']);
    expect(round.tracks[1].profileId).toBe('p_fox');
  });
});
