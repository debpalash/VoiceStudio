import { describe, it, expect } from 'vitest';
import { parseSsmlLite, spellOut, SLOW_SPEED, FAST_SPEED } from '../utils/ssmlLite';

describe('parseSsmlLite (client port — parity with ssml_lite.py)', () => {
  it('plain text → one default segment', () => {
    expect(parseSsmlLite('hello world')).toEqual([
      { text: 'hello world', speed: null, spell: false, emphasis: false },
    ]);
  });

  it('empty/None → []', () => {
    expect(parseSsmlLite('')).toEqual([]);
    expect(parseSsmlLite(null)).toEqual([]);
  });

  it('[slow]/[fast] set speed', () => {
    expect(parseSsmlLite('[slow]a[/slow]')[0].speed).toBe(SLOW_SPEED);
    expect(parseSsmlLite('[fast]a[/fast]')[0].speed).toBe(FAST_SPEED);
  });

  it('[spell] flags spell; emphasis flags + mild slow', () => {
    const sp = parseSsmlLite('[spell]USA[/spell]')[0];
    expect(sp.spell).toBe(true);
    expect(parseSsmlLite('[emphasis]x[/emphasis]')[0].emphasis).toBe(true);
  });

  it('innermost wins; spell-in-slow keeps both', () => {
    const seg = parseSsmlLite('[slow][spell]K[/spell][/slow]')[0];
    expect(seg.speed).toBe(SLOW_SPEED);
    expect(seg.spell).toBe(true);
  });

  it('unclosed tag runs to end of line', () => {
    const segs = parseSsmlLite('plain [fast]rest');
    expect(segs[0]).toMatchObject({ text: 'plain ', speed: null });
    expect(segs[1]).toMatchObject({ text: 'rest', speed: FAST_SPEED });
  });

  it('stray close ignored; only-markers → []', () => {
    expect(parseSsmlLite('hi[/slow]')[0].text).toBe('hi');
    expect(parseSsmlLite('[slow][/slow]')).toEqual([]);
  });

  it('mixed line splits + adjacent identical merge', () => {
    const segs = parseSsmlLite('a [slow]b[/slow] c');
    expect(segs.map((s) => s.text)).toEqual(['a ', 'b', ' c']);
  });

  it('spellOut spaces letters, drops whitespace', () => {
    expect(spellOut('USA')).toBe('U S A');
    expect(spellOut('go USA')).toBe('g o U S A');
  });

  it('is linear-time on pathological input (no ReDoS)', () => {
    const big = '[slow]'.repeat(5000);
    const t0 = Date.now();
    parseSsmlLite(big);
    expect(Date.now() - t0).toBeLessThan(1000);
  });
});
