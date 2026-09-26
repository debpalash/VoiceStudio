import { describe, it, expect } from 'vitest';
import { inlineSegments, parseBlocks } from './markdownLite';

describe('inlineSegments (safe markdown-lite inline tokens)', () => {
  it('splits **bold leads** from plain text', () => {
    expect(inlineSegments('**Dictation, rebuilt.** Live waveform. (#123)')).toEqual([
      { type: 'bold', text: 'Dictation, rebuilt.' },
      { type: 'text', text: ' Live waveform. (#123)' },
    ]);
  });

  it('tokenizes `code` spans', () => {
    expect(inlineSegments('set `OMNIVOICE_DATA_DIR` first')).toEqual([
      { type: 'text', text: 'set ' },
      { type: 'code', text: 'OMNIVOICE_DATA_DIR' },
      { type: 'text', text: ' first' },
    ]);
  });

  it('keeps (#NNN) refs as plain text — never links', () => {
    const segs = inlineSegments('Fixed the thing. (#827, #869)');
    expect(segs).toEqual([{ type: 'text', text: 'Fixed the thing. (#827, #869)' }]);
  });

  it('flattens [label](url) links to their label', () => {
    expect(inlineSegments('see [the docs](https://evil.example/x) now')).toEqual([
      { type: 'text', text: 'see the docs now' },
    ]);
  });

  it('treats raw HTML as inert text (no injection path)', () => {
    const segs = inlineSegments('<script>alert(1)</script> **b**');
    expect(segs[0]).toEqual({ type: 'text', text: '<script>alert(1)</script> ' });
    expect(segs[1]).toEqual({ type: 'bold', text: 'b' });
  });

  it('handles empty/nullish input', () => {
    expect(inlineSegments('')).toEqual([]);
    expect(inlineSegments(null)).toEqual([]);
    expect(inlineSegments(undefined)).toEqual([]);
  });
});

describe('parseBlocks (notes → headings/bullets/paragraphs)', () => {
  it('parses the house release-notes shape', () => {
    const md = [
      '### Fixed',
      '',
      '- **One.** Fix one. (#1)',
      '- **Two.** Fix two. (#2)',
      '',
      'A closing paragraph.',
    ].join('\n');
    expect(parseBlocks(md)).toEqual([
      { type: 'heading', level: 3, text: 'Fixed' },
      { type: 'bullet', text: '**One.** Fix one. (#1)' },
      { type: 'bullet', text: '**Two.** Fix two. (#2)' },
      { type: 'para', text: 'A closing paragraph.' },
    ]);
  });

  it('joins hard-wrapped bullet continuation lines (older changelog style)', () => {
    const md = '- **"Autofit" quality.** A new\n  quality alongside Fast. (#838)';
    expect(parseBlocks(md)).toEqual([
      { type: 'bullet', text: '**"Autofit" quality.** A new quality alongside Fast. (#838)' },
    ]);
  });

  it('joins wrapped paragraphs but splits on blank lines', () => {
    const md = 'First line\nwraps here.\n\nSecond paragraph.';
    expect(parseBlocks(md)).toEqual([
      { type: 'para', text: 'First line wraps here.' },
      { type: 'para', text: 'Second paragraph.' },
    ]);
  });

  it('returns [] for empty/nullish input', () => {
    expect(parseBlocks('')).toEqual([]);
    expect(parseBlocks(null)).toEqual([]);
  });
});
