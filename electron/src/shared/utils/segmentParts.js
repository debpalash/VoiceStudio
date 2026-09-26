/**
 * segmentParts — speaker attribution that survives merge → split round-trips.
 *
 * Moving a few stray words across a speaker boundary has only one path in the
 * dub table: merge the two lines, then split them again at the right word.
 * That round-trip used to LOSE the second speaker (#1612). `segmentMerge`
 * built the merged row from `...a` and kept only `b.end`, dropping b's
 * `speaker_id` / `profile_id` / `direction` / `gain` / `target_lang`; a later
 * `segmentSplit` then spread the merged row's fields onto BOTH halves. The
 * result was the second character's words dubbed in the first character's
 * voice — exactly the failure the reporter predicted.
 *
 * The fix records, on the merged row, which attribution covered which stretch
 * of TEXT (`merge_parts`). A split reads the part covering the split offset
 * and restores that attribution, so words moved across the boundary keep the
 * voice of whoever actually speaks them. Segments that were never merged carry
 * no `merge_parts` and behave exactly as before — both halves inherit the
 * parent.
 *
 * Offsets, not timestamps. Timestamps look like the natural key and were the
 * first thing tried, but they are wrong: nothing stops a user retiming a line
 * so it overlaps its neighbour, and after merging, two parts then cover the
 * same instant — a lookup by time picks whichever it visits first and hands
 * the words to the wrong speaker. Driving the real app caught exactly that.
 * The split point the user chooses IS a text position, so text offsets are the
 * honest coordinate space, and they cannot overlap by construction.
 *
 * `merge_parts` is client-side bookkeeping: `DubSegment` (backend/schemas/
 * requests.py) ignores unknown fields, and `segmentGenInputs` does not hash it,
 * so carrying it changes neither the request contract nor regen fingerprints.
 */

/**
 * Per-row fields that say WHO speaks a line and HOW, as opposed to WHAT is
 * said. This is the complete set of user-editable row fields that a merge
 * would otherwise destroy — fixing only `speaker_id` would leave the same bug
 * wearing a different field's name.
 */
export const ATTRIBUTION_FIELDS = ['speaker_id', 'profile_id', 'direction', 'gain', 'target_lang'];

/** The attribution carried by a segment, omitting fields it doesn't set. */
export function attributionOf(seg) {
  const attr = {};
  for (const f of ATTRIBUTION_FIELDS) {
    if (seg?.[f] !== undefined && seg?.[f] !== null) attr[f] = seg[f];
  }
  return attr;
}

/**
 * Apply an attribution wholesale: fields absent from `attr` are DELETED rather
 * than left in place. A half whose speaker set no profile must not silently
 * inherit the other speaker's voice — that is the bug.
 */
export function applyAttribution(seg, attr) {
  const next = { ...seg };
  for (const f of ATTRIBUTION_FIELDS) {
    if (attr && attr[f] !== undefined) next[f] = attr[f];
    else delete next[f];
  }
  return next;
}

/**
 * The attribution parts of a segment in text-offset space: its recorded
 * `merge_parts` when it is the product of a merge, otherwise a single part
 * spanning the whole line.
 */
export function partsFor(seg) {
  const recorded = Array.isArray(seg?.merge_parts) ? seg.merge_parts.filter(Boolean) : null;
  if (recorded && recorded.length) return recorded;
  return [{ textStart: 0, textEnd: (seg?.text || '').length, ...attributionOf(seg) }];
}

/** Attribution parts in the coordinate space of text_original. */
export function originalPartsFor(seg) {
  const recorded = Array.isArray(seg?.merge_parts_original)
    ? seg.merge_parts_original.filter(Boolean)
    : null;
  if (recorded && recorded.length) return recorded;
  return [
    {
      textStart: 0,
      textEnd: (seg?.text_original || seg?.text || '').length,
      ...attributionOf(seg),
    },
  ];
}

/**
 * Where b's words begin inside the merged line. `segmentMerge` builds it as
 * `${a.text} ${b.text}`.trim(), so b follows a plus one joining space — unless
 * a contributed nothing, in which case the trim removes the space too.
 */
export function mergeTextOffset(a) {
  const at = a?.text || '';
  return at ? at.length + 1 : 0;
}

/**
 * Parts for `a` followed by `b`, with b's offsets rebased into the merged
 * line's coordinates. Flattened so repeated merges accumulate instead of
 * nesting.
 */
export function mergedParts(a, b) {
  const shift = mergeTextOffset(a);
  return [
    ...partsFor(a),
    ...partsFor(b).map((pt) => ({
      ...pt,
      textStart: pt.textStart + shift,
      textEnd: pt.textEnd + shift,
    })),
  ];
}

/** Merge attribution using the immutable original-text coordinate space. */
export function mergedOriginalParts(a, b) {
  const aText = a?.text_original || a?.text || '';
  const shift = aText ? aText.length + 1 : 0;
  return [
    ...originalPartsFor(a),
    ...originalPartsFor(b).map((pt) => ({
      ...pt,
      textStart: pt.textStart + shift,
      textEnd: pt.textEnd + shift,
    })),
  ];
}

/**
 * Parts overlapping [from, to), clipped and rebased so offsets are relative to
 * the extracted slice. Keeps a split half's own record usable for a further
 * split.
 */
export function clipParts(parts, from, to) {
  const out = [];
  for (const pt of parts) {
    const s = Math.max(pt.textStart, from);
    const e = Math.min(pt.textEnd, to);
    if (e > s) out.push({ ...pt, textStart: s - from, textEnd: e - from });
  }
  return out;
}

/**
 * Drop a parts record that no longer says anything: a single part carries the
 * same information the segment's own fields already do, so storing it would
 * put a redundant `merge_parts` on every ordinary split.
 */
export function keepParts(parts) {
  return parts && parts.length > 1 ? parts : undefined;
}

/**
 * Attribution covering text offset `at`. Offsets past the recorded span clamp
 * to the first/last part rather than returning nothing — a half must always
 * get an answer, and its nearest neighbour is the only sensible one.
 */
export function attributionAt(parts, at) {
  if (!parts || !parts.length) return {};
  for (const pt of parts) {
    if (at >= pt.textStart && at < pt.textEnd) return attributionOf(pt);
  }
  if (at < parts[0].textStart) return attributionOf(parts[0]);
  return attributionOf(parts[parts.length - 1]);
}

/**
 * A segment id not already in `existing`. Derived from `base` so the
 * relationship stays readable in logs, and deterministic so tests don't need
 * to stub a clock or RNG.
 */
export function nextSegmentId(existing, base) {
  const taken = new Set(Array.from(existing || [], String));
  const root = `${base ?? 'seg'}_new`;
  if (!taken.has(root)) return root;
  for (let i = 2; ; i++) {
    const candidate = `${root}${i}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** Shortest slot worth inserting into before falling back to `defaultDur`. */
export const MIN_INSERT_GAP_S = 0.3;

/** Only expose merge actions whose source-order neighbor is currently visible. */
export function visibleMergeAvailability(segments, visibleSegments, segment) {
  const index = segments.indexOf(segment);
  const visibleIndex = visibleSegments.indexOf(segment);
  return {
    canMerge:
      index >= 0 &&
      index < segments.length - 1 &&
      visibleIndex >= 0 &&
      visibleSegments[visibleIndex + 1] === segments[index + 1],
    canMergePrev:
      index > 0 && visibleIndex > 0 && visibleSegments[visibleIndex - 1] === segments[index - 1],
  };
}

/**
 * The time slot a newly inserted line should occupy after `prev`.
 *
 * Prefers the silent gap before `next` — that is where a missing line actually
 * belongs. When there is no usable gap the slot is placed immediately after
 * `prev` at `defaultDur`; it may then overlap `next`, which the timeline lane's
 * overlap detection flags. NOTE: the segment TABLE has no equivalent warning
 * today, so an overlap created here is invisible unless the user looks at the
 * waveform.
 */
export function insertionSlot(prev, next, { defaultDur = 2, minGap = MIN_INSERT_GAP_S } = {}) {
  const start = +(prev?.end ?? 0).toFixed(3);
  if (next) {
    const gap = next.start - start;
    if (gap >= minGap) return { start, end: +next.start.toFixed(3) };
  }
  return { start, end: +(start + defaultDur).toFixed(3) };
}
