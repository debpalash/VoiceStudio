/**
 * pasteTranslations — map externally-produced translated text onto the dub
 * timeline's EXISTING segments.
 *
 * The workflow this serves: the user transcribes once, then translates
 * elsewhere (ChatGPT, DeepL, a human translator) and pastes the result back.
 * Nothing about the timeline may change — no re-transcription, no re-timing,
 * no touching `text_original` (which `handleTranslateAll` reads as its
 * translate source; overwriting it would poison every later re-translate).
 *
 * Three input shapes cover essentially everything people paste. They are
 * auto-detected, and every mapping is previewed before it is applied:
 *
 *   timestamped — a full .srt/.vtt. Cues are parsed by the BACKEND
 *     (`/dub/parse-subtitle-text`, the same lenient parser the .srt import
 *     uses) and matched to segments by TIME OVERLAP, so a translation whose
 *     cue boundaries drift from ours still lands on the right rows.
 *   numbered — `1. …` / `2) …` / `[3] …`, what an LLM emits when asked to
 *     keep lines aligned. Mapped by the number, tolerant of renumbering.
 *   plain — one translation per line, mapped positionally.
 *
 * Everything here is pure so the preview dialog and the applying hook derive
 * the SAME plan from the same inputs.
 */

// A timing line, e.g. `00:00:01,000 --> 00:00:04,500` (`.` ms separator,
// missing leading zeros and WebVTT's hourless `00:01.000` allowed — mirrors
// backend/services/srt_parser.py).
const TIMING_RE =
  /^[^\S\n]*(?:\d{1,2}:)?[0-5]?\d:[0-5]?\d[,.]\d{1,3}[^\S\n]*-->[^\S\n]*(?:\d{1,2}:)?[0-5]?\d:[0-5]?\d[,.]\d{1,3}/m;

// `1. text` / `2) text` / `[3] text` / `4 - text` / `5: text`.
const NUMBERED_RE = /^\s*(?:\[\s*(\d{1,5})\s*\]|\(\s*(\d{1,5})\s*\)|(\d{1,5}))\s*[.):\-—]?\s+(.*)$/;

/** Lines that carry content. Blank lines are separators, never translations. */
const nonBlank = (text) =>
  String(text || '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);

/**
 * Which of the three shapes is this paste?
 *
 * @returns {'timestamped'|'numbered'|'plain'}
 */
export function detectPasteMode(text) {
  const raw = String(text || '');
  if (TIMING_RE.test(raw)) return 'timestamped';
  const lines = nonBlank(raw);
  if (lines.length >= 2) {
    const numbered = lines.filter((l) => NUMBERED_RE.test(l)).length;
    // A majority rules: LLM output often carries a stray preamble line
    // ("Here are the translations:") that must not veto the detection.
    if (numbered >= 2 && numbered / lines.length >= 0.6) return 'numbered';
  }
  return 'plain';
}

/**
 * Numbered paste → entries in encounter order, prefix stripped.
 * Continuation lines (no number) append to the entry above them.
 *
 * @returns {Array<{num: number|null, text: string}>}
 */
export function parseNumberedLines(text) {
  const entries = [];
  for (const line of nonBlank(text)) {
    const m = NUMBERED_RE.exec(line);
    if (m) {
      const num = parseInt(m[1] ?? m[2] ?? m[3], 10);
      entries.push({ num: Number.isFinite(num) ? num : null, text: (m[4] || '').trim() });
    } else if (entries.length) {
      entries[entries.length - 1].text = `${entries[entries.length - 1].text} ${line}`.trim();
    }
    // A leading line with no number and no entry yet is a preamble — dropped.
  }
  return entries.filter((e) => e.text);
}

/**
 * Time-overlap matching, one-to-one and greedy by best overlap.
 *
 * Greedy-unique rather than per-segment-argmax on purpose: when one long cue
 * overlaps several short segments, argmax would copy the same sentence into
 * every one of them. Claiming the strongest pair first and then excluding
 * both sides leaves the weaker rows honestly unmatched, which the preview
 * flags instead of silently duplicating text.
 *
 * Candidate pairs come from a sweep over both lists sorted by start time, so
 * a feature-length transcript (thousands of segments × thousands of cues)
 * costs "pairs that actually overlap" rather than the full cross product.
 */
export function matchByOverlap(cues, segments) {
  const byStart = (a, b) => a.start - b.start || a.i - b.i;
  const segIv = segments.map((s, i) => ({ i, start: s.start, end: s.end })).sort(byStart);
  const cueIv = cues.map((c, i) => ({ i, start: c.start, end: c.end })).sort(byStart);

  const pairs = [];
  let lo = 0;
  for (const s of segIv) {
    // Segments are visited in start order, so a cue ending at or before this
    // segment's start cannot reach any later segment either — retire it.
    while (lo < cueIv.length && cueIv[lo].end <= s.start) lo++;
    for (let k = lo; k < cueIv.length && cueIv[k].start < s.end; k++) {
      const ov = Math.min(s.end, cueIv[k].end) - Math.max(s.start, cueIv[k].start);
      if (ov > 0) pairs.push({ si: s.i, ci: cueIv[k].i, ov });
    }
  }
  // Ties resolve by document order so the plan is deterministic.
  pairs.sort((a, b) => b.ov - a.ov || a.si - b.si || a.ci - b.ci);
  const bySeg = new Map();
  const usedCues = new Set();
  for (const p of pairs) {
    if (bySeg.has(p.si) || usedCues.has(p.ci)) continue;
    bySeg.set(p.si, p.ci);
    usedCues.add(p.ci);
  }
  return { bySeg, usedCues };
}

/**
 * Build the full before→after plan for a paste. Pure — the dialog renders it
 * and the hook applies exactly the same thing.
 *
 * @param {string} text        raw pasted text
 * @param {Array}  segments    current dub segments (unchanged)
 * @param {object} opts
 * @param {string} [opts.mode] force a mode; defaults to auto-detection
 * @param {Array}  [opts.cues] parsed cues (required for 'timestamped')
 * @returns {{mode, rows, matchedCount, unmatchedCount, sourceCount, unusedCount}}
 *   `rows` has one entry per segment: {id, index, before, after, matched}.
 */
export function buildPastePlan(text, segments, opts = {}) {
  const segs = Array.isArray(segments) ? segments : [];
  const mode = opts.mode || detectPasteMode(text);
  const assigned = new Map(); // segment index -> new text
  let sourceCount = 0;
  let usedCount = 0;

  if (mode === 'timestamped') {
    const cues = (opts.cues || []).filter(
      (c) => c && Number.isFinite(c.start) && Number.isFinite(c.end),
    );
    sourceCount = cues.length;
    const { bySeg } = matchByOverlap(cues, segs);
    for (const [si, ci] of bySeg) {
      const t = String(cues[ci].text || '').trim();
      if (t) assigned.set(si, t);
    }
    usedCount = assigned.size;
  } else if (mode === 'numbered') {
    const entries = parseNumberedLines(text);
    sourceCount = entries.length;
    // Trust the numbers only when they read as a clean 1-based index into
    // the segment list. LLMs happily restart at 1 mid-answer or emit
    // "1)…2)…2)…" — in that case the ORDER is still right, so fall back to
    // positional rather than scattering lines onto wrong rows.
    const nums = entries.map((e) => e.num);
    const usable =
      nums.every((n) => Number.isFinite(n) && n >= 1 && n <= segs.length) &&
      nums.every((n, i) => i === 0 || n > nums[i - 1]);
    entries.forEach((e, i) => {
      const si = usable ? e.num - 1 : i;
      if (si < segs.length && !assigned.has(si)) assigned.set(si, e.text);
    });
    usedCount = assigned.size;
  } else {
    const lines = nonBlank(text);
    sourceCount = lines.length;
    lines.forEach((line, i) => {
      if (i < segs.length) assigned.set(i, line);
    });
    usedCount = assigned.size;
  }

  const rows = segs.map((s, i) => ({
    id: s.id,
    index: i,
    start: s.start,
    end: s.end,
    before: s.text || '',
    after: assigned.has(i) ? assigned.get(i) : null,
    matched: assigned.has(i),
  }));

  return {
    mode,
    rows,
    matchedCount: assigned.size,
    unmatchedCount: segs.length - assigned.size,
    sourceCount,
    // Pasted lines/cues that found no home — an over-long paste, or cues
    // outside the timeline. Surfaced so the user can tell "nothing was
    // dropped" from "12 lines went nowhere".
    unusedCount: Math.max(0, sourceCount - usedCount),
  };
}
