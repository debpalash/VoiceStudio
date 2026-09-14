import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { useAppStore } from '../store';
import {
  detectPasteMode,
  parseNumberedLines,
  buildPastePlan,
  matchByOverlap,
} from '../utils/pasteTranslations';
import { ENCODED, SAMPLE } from './encodedText';

// "Paste translation from an external source": the user transcribes once,
// translates elsewhere (ChatGPT / DeepL / a human), and pastes the result
// back onto the segments that already exist. The two invariants this
// feature must never break:
//   - `text_original` stays the SOURCE text — `handleTranslateAll` reads it
//     as the translate input, so overwriting it poisons every later
//     re-translate (the same guard the backend keeps at dub_generate.py:96);
//   - only the ACTIVE `dubLangCode` entry of `translations` is written.

const dubApi = vi.hoisted(() => ({ dubParseSubtitleText: vi.fn() }));
vi.mock('../api/dub', () => dubApi);

import useSegmentEditing from '../hooks/useSegmentEditing';
import DubPasteTranslationDialog from '../components/dub/DubPasteTranslationDialog';

const baseState = useAppStore.getState();

const seg = (id, start, end, text) => ({
  id,
  start,
  end,
  text,
  text_original: text,
});

const SEGMENTS = [
  seg('1', 0, 2, 'Hello there'),
  seg('2', 2, 4, 'How are you'),
  seg('3', 4, 6, 'Goodbye'),
];

beforeEach(() => {
  useAppStore.setState(baseState, true);
  dubApi.dubParseSubtitleText.mockReset();
  useAppStore.setState({
    dubJobId: 'job1',
    dubStep: 'editing',
    dubLangCode: 'es',
    dubSegments: SEGMENTS.map((s) => ({ ...s })),
  });
});

// ── Mode auto-detection ───────────────────────────────────────────────────
describe('detectPasteMode', () => {
  it('detects a timestamped SRT paste', () => {
    expect(detectPasteMode('1\n00:00:01,000 --> 00:00:04,500\nHola.\n')).toBe('timestamped');
  });

  it('detects a VTT paste (dot ms separator, no cue indices)', () => {
    expect(detectPasteMode('WEBVTT\n\n00:00:01.000 --> 00:00:04.500\nHola.\n')).toBe('timestamped');
  });

  it('detects numbered lines in every common prefix style', () => {
    expect(detectPasteMode('1. Hola\n2. Que tal\n3. Adios')).toBe('numbered');
    expect(detectPasteMode('1) Hola\n2) Que tal')).toBe('numbered');
    expect(detectPasteMode('[1] Hola\n[2] Que tal')).toBe('numbered');
  });

  it('ignores an LLM preamble line when deciding numbered', () => {
    expect(detectPasteMode('Here are the translations:\n1. Hola\n2. Que tal\n3. Adios')).toBe(
      'numbered',
    );
  });

  it('falls back to plain for ordinary lines', () => {
    expect(detectPasteMode('Hola\nQue tal\nAdios')).toBe('plain');
  });

  it('does not mistake prose that happens to start with a number for numbered', () => {
    expect(detectPasteMode('2024 was a good year\nWe shipped a lot\nThanks for watching')).toBe(
      'plain',
    );
  });

  it('prefers timestamped even when cue indices look numbered', () => {
    const srt =
      '1\n00:00:01,000 --> 00:00:02,000\nHola.\n\n2\n00:00:03,000 --> 00:00:04,000\nAdios.\n';
    expect(detectPasteMode(srt)).toBe('timestamped');
  });
});

// ── Mapping ───────────────────────────────────────────────────────────────
describe('buildPastePlan — plain (positional)', () => {
  it('maps one line per segment in order', () => {
    const plan = buildPastePlan('Hola\nQue tal\nAdios', SEGMENTS);
    expect(plan.mode).toBe('plain');
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
    expect(plan.matchedCount).toBe(3);
    expect(plan.unmatchedCount).toBe(0);
  });

  it('treats blank lines as separators, never as empty translations', () => {
    const plan = buildPastePlan('Hola\n\n\nQue tal\n\nAdios\n', SEGMENTS);
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('reports a short paste as unmatched rows instead of blanking them', () => {
    const plan = buildPastePlan('Hola\nQue tal', SEGMENTS);
    expect(plan.matchedCount).toBe(2);
    expect(plan.unmatchedCount).toBe(1);
    expect(plan.rows[2].matched).toBe(false);
    expect(plan.rows[2].after).toBeNull();
  });

  it('reports leftover lines from an over-long paste', () => {
    const plan = buildPastePlan('a\nb\nc\nd\ne', SEGMENTS);
    expect(plan.matchedCount).toBe(3);
    expect(plan.sourceCount).toBe(5);
    expect(plan.unusedCount).toBe(2);
  });
});

describe('buildPastePlan — numbered', () => {
  it('strips the prefix and maps by number', () => {
    const plan = buildPastePlan('1. Hola\n2) Que tal\n[3] Adios', SEGMENTS);
    expect(plan.mode).toBe('numbered');
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('maps by the number, not position, when the LLM skips one', () => {
    const plan = buildPastePlan('1. Hola\n3. Adios', SEGMENTS);
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', null, 'Adios']);
    expect(plan.unmatchedCount).toBe(1);
  });

  it('falls back to positional when the LLM renumbers from 1 mid-answer', () => {
    // "1. … 2. … 1. …" — the numbers are junk but the ORDER is right.
    const plan = buildPastePlan('1. Hola\n2. Que tal\n1. Adios', SEGMENTS);
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('joins wrapped continuation lines into the entry above', () => {
    const plan = buildPastePlan('1. Hola\nmundo entero\n2. Que tal\n3. Adios', SEGMENTS);
    expect(plan.rows[0].after).toBe('Hola mundo entero');
  });

  it('drops an LLM preamble instead of shifting every row by one', () => {
    const plan = buildPastePlan('Here you go:\n1. Hola\n2. Que tal\n3. Adios', SEGMENTS);
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('parseNumberedLines returns entries in encounter order', () => {
    expect(parseNumberedLines('2) dos\n1) uno')).toEqual([
      { num: 2, text: 'dos' },
      { num: 1, text: 'uno' },
    ]);
  });
});

describe('buildPastePlan — timestamped (time overlap)', () => {
  const cues = [
    { start: 0.1, end: 1.9, text: 'Hola' },
    { start: 2.1, end: 3.9, text: 'Que tal' },
    { start: 4.1, end: 5.9, text: 'Adios' },
  ];

  it('matches cues to segments by overlap, not by index', () => {
    const plan = buildPastePlan('x', SEGMENTS, { mode: 'timestamped', cues });
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('still matches when cue boundaries drift from ours', () => {
    const drifted = [
      { start: 0.0, end: 2.4, text: 'Hola' },
      { start: 2.4, end: 4.4, text: 'Que tal' },
    ];
    const plan = buildPastePlan('x', SEGMENTS, { mode: 'timestamped', cues: drifted });
    expect(plan.rows[0].after).toBe('Hola');
    expect(plan.rows[1].after).toBe('Que tal');
    expect(plan.rows[2].matched).toBe(false);
  });

  it('flags a segment no cue overlaps', () => {
    const plan = buildPastePlan('x', SEGMENTS, {
      mode: 'timestamped',
      cues: [{ start: 0, end: 2, text: 'Hola' }],
    });
    expect(plan.matchedCount).toBe(1);
    expect(plan.rows[1].matched).toBe(false);
    expect(plan.rows[2].matched).toBe(false);
  });

  it('never copies one cue onto several segments (greedy one-to-one)', () => {
    // One long cue spanning all three rows: argmax matching would stamp the
    // same sentence three times; only the strongest overlap may claim it.
    const plan = buildPastePlan('x', SEGMENTS, {
      mode: 'timestamped',
      cues: [{ start: 0, end: 6, text: 'Una frase larga' }],
    });
    expect(plan.matchedCount).toBe(1);
    expect(plan.rows.filter((r) => r.after === 'Una frase larga')).toHaveLength(1);
  });

  it('matches correctly when cues arrive out of chronological order', () => {
    // The matcher sweeps both lists by start time; it must sort first rather
    // than trust the caller's ordering.
    const plan = buildPastePlan('x', SEGMENTS, {
      mode: 'timestamped',
      cues: [
        { start: 4.1, end: 5.9, text: 'Adios' },
        { start: 0.1, end: 1.9, text: 'Hola' },
        { start: 2.1, end: 3.9, text: 'Que tal' },
      ],
    });
    expect(plan.rows.map((r) => r.after)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('matchByOverlap resolves ties deterministically by document order', () => {
    const { bySeg } = matchByOverlap(
      [
        { start: 0, end: 2, text: 'a' },
        { start: 0, end: 2, text: 'b' },
      ],
      [seg('1', 0, 2, 'x')],
    );
    expect(bySeg.get(0)).toBe(0);
  });
});

// ── Applying through the hook ─────────────────────────────────────────────
describe('pasteTranslations (useSegmentEditing)', () => {
  const paste = (result, text, opts) => {
    let plan;
    act(() => {
      plan = result.current.pasteTranslations(text, opts);
    });
    return plan;
  };

  it('writes text and translations[activeLang] in lock-step', () => {
    const { result } = renderHook(() => useSegmentEditing());
    paste(result, 'Hola\nQue tal\nAdios');
    const segs = useAppStore.getState().dubSegments;
    expect(segs.map((s) => s.text)).toEqual(['Hola', 'Que tal', 'Adios']);
    expect(segs.map((s) => s.translations.es)).toEqual(['Hola', 'Que tal', 'Adios']);
  });

  it('NEVER overwrites text_original (would poison a later re-translate)', () => {
    const { result } = renderHook(() => useSegmentEditing());
    paste(result, 'Hola\nQue tal\nAdios');
    expect(useAppStore.getState().dubSegments.map((s) => s.text_original)).toEqual([
      'Hello there',
      'How are you',
      'Goodbye',
    ]);
  });

  it('touches only the ACTIVE language entry, leaving other languages intact', () => {
    useAppStore.setState({
      dubSegments: [{ ...SEGMENTS[0], translations: { bn: 'ওহে', es: 'viejo' } }],
    });
    const { result } = renderHook(() => useSegmentEditing());
    paste(result, 'Hola');
    const s = useAppStore.getState().dubSegments[0];
    expect(s.translations).toEqual({ bn: 'ওহে', es: 'Hola' });
  });

  it('clears stale machine-translation badges on the rows it rewrites', () => {
    useAppStore.setState({
      dubSegments: [
        { ...SEGMENTS[0], translate_error: 'engine down', translate_degraded: true },
        { ...SEGMENTS[1], translate_error: 'engine down' },
      ],
    });
    const { result } = renderHook(() => useSegmentEditing());
    paste(result, 'Hola'); // only row 1 is matched
    const segs = useAppStore.getState().dubSegments;
    expect(segs[0].translate_error).toBeUndefined();
    expect(segs[0].translate_degraded).toBeUndefined();
    // Untouched row keeps its badge — its text really is still the bad one.
    expect(segs[1].translate_error).toBe('engine down');
  });

  it('leaves unmatched rows byte-identical rather than blanking them', () => {
    const { result } = renderHook(() => useSegmentEditing());
    const plan = paste(result, 'Hola');
    expect(plan.unmatchedCount).toBe(2);
    const segs = useAppStore.getState().dubSegments;
    expect(segs[1].text).toBe('How are you');
    expect(segs[2].text).toBe('Goodbye');
    expect(segs[1].translations).toBeUndefined();
  });

  it('reports the mismatch counts instead of applying silently', () => {
    const { result } = renderHook(() => useSegmentEditing());
    const plan = paste(result, 'a\nb\nc\nd');
    expect(plan).toMatchObject({ mode: 'plain', matchedCount: 3, sourceCount: 4, unusedCount: 1 });
  });

  it('applies nothing (and pushes no undo) when zero rows match', () => {
    const { result } = renderHook(() => useSegmentEditing());
    const plan = paste(result, 'x', { mode: 'timestamped', cues: [] });
    expect(plan.matchedCount).toBe(0);
    expect(useAppStore.getState().dubSegments.map((s) => s.text)).toEqual([
      'Hello there',
      'How are you',
      'Goodbye',
    ]);
    act(() => result.current.undo());
    // Undo had nothing to pop, so the timeline is still untouched.
    expect(useAppStore.getState().dubSegments[0].text).toBe('Hello there');
  });

  it('is one undo step for the whole paste', () => {
    const { result } = renderHook(() => useSegmentEditing());
    paste(result, 'Hola\nQue tal\nAdios');
    act(() => result.current.undo());
    const segs = useAppStore.getState().dubSegments;
    expect(segs.map((s) => s.text)).toEqual(['Hello there', 'How are you', 'Goodbye']);
    expect(segs[0].translations).toBeUndefined();
  });

  it('maps timestamped cues onto existing rows without changing their timings', () => {
    const { result } = renderHook(() => useSegmentEditing());
    paste(result, 'x', {
      mode: 'timestamped',
      cues: [
        { start: 0.5, end: 3.0, text: 'Hola' },
        { start: 3.0, end: 5.5, text: 'Adios' },
      ],
    });
    const segs = useAppStore.getState().dubSegments;
    expect(segs.map((s) => [s.start, s.end])).toEqual([
      [0, 2],
      [2, 4],
      [4, 6],
    ]);
    expect(segs[0].text).toBe('Hola');
  });
});

// ── Preview dialog ────────────────────────────────────────────────────────
describe('DubPasteTranslationDialog', () => {
  it('previews before→after rows, flags unmatched, and applies on confirm', async () => {
    const onApply = vi.fn();
    render(
      <DubPasteTranslationDialog open segments={SEGMENTS} onApply={onApply} onClose={vi.fn()} />,
    );

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hola\nQue tal' } });

    const rows = await screen.findAllByTestId('paste-translation-row');
    expect(rows).toHaveLength(3);
    expect(rows[2].getAttribute('data-matched')).toBe('false');
    expect(screen.getByTestId('paste-translation-summary').textContent).toContain('3');

    const apply = screen.getByRole('button', { name: /Apply/i });
    expect(apply).not.toBeDisabled();
    fireEvent.click(apply);
    expect(onApply).toHaveBeenCalledWith(
      'Hola\nQue tal',
      expect.objectContaining({ mode: 'plain' }),
    );
  });

  it('disables Apply only when nothing matched', async () => {
    render(<DubPasteTranslationDialog open segments={[]} onApply={vi.fn()} onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hola' } });
    expect(screen.getByRole('button', { name: /Apply/i })).toBeDisabled();
  });

  it('keeps the file picker reachable by keyboard (not `hidden`)', () => {
    // A bare <label> is not focusable and `hidden` removes the input from the
    // tab order AND the accessibility tree — together they leave keyboard-only
    // users with no path to the file picker at all.
    render(
      <DubPasteTranslationDialog open segments={SEGMENTS} onApply={vi.fn()} onClose={vi.fn()} />,
    );
    // The dialog renders through a portal, so query the document, not the
    // render container.
    const input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    expect(input.hidden).toBe(false);
    expect(input.className).toContain('sr-only');
  });

  it('sends a timestamped paste to the backend parser and previews the cues', async () => {
    dubApi.dubParseSubtitleText.mockResolvedValue({
      segments: [
        { start: 0, end: 2, text: 'Hola' },
        { start: 2, end: 4, text: 'Que tal' },
        { start: 4, end: 6, text: 'Adios' },
      ],
      skipped_cues: 0,
      dropped_overlaps: 0,
    });
    render(
      <DubPasteTranslationDialog open segments={SEGMENTS} onApply={vi.fn()} onClose={vi.fn()} />,
    );
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '1\n00:00:01,000 --> 00:00:02,000\nHola.\n' },
    });

    await waitFor(() => expect(dubApi.dubParseSubtitleText).toHaveBeenCalled());
    const rows = await screen.findAllByTestId('paste-translation-row');
    expect(rows.every((r) => r.getAttribute('data-matched') === 'true')).toBe(true);
  });

  // Windows tools save subtitle files as UTF-16 (Notepad's "Unicode") or in
  // the Windows-1252 code page; a loaded file must read as the text it holds.
  it.each(Object.keys(ENCODED))('loads a %s file with its text intact', async (encoding) => {
    render(
      <DubPasteTranslationDialog open segments={SEGMENTS} onApply={vi.fn()} onClose={vi.fn()} />,
    );
    const input = document.querySelector('input[type="file"]');
    fireEvent.change(input, {
      target: { files: [new File([ENCODED[encoding](SAMPLE)], 'translation.txt')] },
    });

    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(SAMPLE));
  });

  it('surfaces a backend parse failure instead of applying a silent no-op', async () => {
    dubApi.dubParseSubtitleText.mockRejectedValue(new Error('No timed cues found'));
    render(
      <DubPasteTranslationDialog open segments={SEGMENTS} onApply={vi.fn()} onClose={vi.fn()} />,
    );
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '1\n00:00:01,000 --> 00:00:02,000\nHola.\n' },
    });

    expect(await screen.findByTestId('paste-translation-error')).toHaveTextContent(
      'No timed cues found',
    );
    expect(screen.getByRole('button', { name: /Apply/i })).toBeDisabled();
  });
});
