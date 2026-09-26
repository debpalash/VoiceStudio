/**
 * Turn a finished dub's segments into a Stories cast + lines.
 *
 * A dub already knows who speaks which line: diarisation grouped the segments
 * by `speaker_id`, and the Cast strip assigned each speaker a voice. That is
 * the same information a Story needs, so rebuilding it by hand — adding a
 * character per speaker and retyping every line — is work the app can do.
 *
 * Pure and framework-free, like `buildAutoCast` next door: no React, no i18n,
 * no store. The caller resolves the label for unattributed speech and mints
 * the line ids, which is what lets both desktop shells share this.
 *
 * A merged row is not one speaker. `segmentParts` records, on a row built by
 * merging, which attribution covered which stretch of TEXT — the fix for
 * #1612, where a merge → split round-trip dubbed the second character's words
 * in the first character's voice. Reading only the row's top-level
 * `speaker_id` here would reintroduce exactly that bug on a new surface, so a
 * row is expanded into its attributed spans and each becomes its own line.
 * Stories has one character per line, so there is no other way to say it.
 * Adjacent spans with the same voice are re-joined, which is why the ordinary
 * case — a row merged to repair one speaker's broken sentence — stays one
 * line, and an unmerged row is untouched.
 *
 * Nothing here trusts the input. A `DubSegment` is typed
 * `Record<string, unknown> & { id, text }`, so every other field arrives
 * unknown — segments can also come from an imported SRT rather than from
 * diarisation. Every read is coerced and the function never throws.
 */

import { partsFor } from './segmentParts.js';
import { nextCastColor } from './storyCast.js';

/**
 * @typedef {Object} DubSpeechSegment
 * @property {unknown} [text]        spoken line; only string|number is usable
 * @property {unknown} [start]       ordering only; non-finite sorts as 0
 * @property {unknown} [speaker_id]  grouping key; blank means unattributed
 * @property {unknown} [profile_id]  '' | '<id>' | 'auto:<slug>' | 'preset:<id>'
 *
 * @typedef {Object} CastMember
 * @property {string} id
 * @property {string} name
 * @property {string} color
 * @property {string|null} profileId
 *
 * @typedef {Object} StoryTrack
 * @property {string} character  cast member id
 * @property {string} text
 *
 * @typedef {Object} DubStoryStats
 * @property {number} lines
 * @property {number} speakers
 * @property {number} skippedEmpty  segments carrying no usable text
 */

/**
 * Key for every segment that names no speaker. They share one character rather
 * than becoming one character each. A leading NUL cannot collide with a real
 * `speaker_id`, which keeps the sentinel out of the caller's namespace.
 */
const UNATTRIBUTED = '\u0000unattributed';

/** A fresh empty result. Built per call so no caller can mutate the next one's. */
function emptyStory() {
  return { cast: [], tracks: [], stats: { lines: 0, speakers: 0, skippedEmpty: 0 } };
}

/**
 * The spoken text of a segment, or '' when there is nothing usable.
 *
 * Deliberately NOT trimmed: span offsets index this exact string, and trimming
 * first would shift every one of them by the leading whitespace. Each span is
 * trimmed after it is cut.
 */
function speechOf(segment) {
  const text = segment?.text;
  return typeof text === 'string' || typeof text === 'number' ? String(text) : '';
}

/** A speaker key: the trimmed id, or the shared unattributed one. */
function speakerKeyOf(source) {
  const raw = source?.speaker_id;
  const name = (raw == null ? '' : String(raw)).trim();
  return name === '' ? UNATTRIBUTED : name;
}

/** A text offset clamped into the string, or `null` when it is not one. */
function offsetOf(value, length) {
  const offset = Number(value);
  if (!Number.isFinite(offset)) return null;
  return Math.min(Math.max(Math.trunc(offset), 0), length);
}

/**
 * One segment's text cut into attributed spans, in reading order.
 *
 * An unmerged row yields exactly one span holding the whole line, because
 * `partsFor` synthesises a single part from the row's own attribution — so the
 * common path is unchanged by all of this.
 *
 * The recorded offsets are treated as hints, not as truth. They are written at
 * merge time and the user can edit the merged text afterwards, which leaves
 * them stale; honouring a stale record literally would silently drop the words
 * outside it. So the spans are clamped, de-overlapped, and stretched to cover
 * the whole line: a gap goes to the span before it, a leading gap to the first
 * span and a trailing gap to the last. Attribution can be approximate at a
 * boundary the user has since moved. Losing their words cannot.
 *
 * @param {DubSpeechSegment} segment
 * @returns {{source: SegmentAttribution, text: string}[]}
 */
function attributedSpans(segment) {
  const text = speechOf(segment);
  const length = text.length;
  if (length === 0) return [];

  // A part counts only if it says where it applies. One that does not is
  // corrupt bookkeeping, and dropping it hands the text back to the row — an
  // answer the row can always give. A part that names no SPEAKER is a
  // different thing entirely: that is a real unattributed stretch, and it must
  // not inherit the row's speaker, or merging an unattributed line into a
  // named one would put the wrong voice on it.
  const clamped = [];
  for (const part of partsFor(segment)) {
    const start = offsetOf(part?.textStart, length);
    const end = offsetOf(part?.textEnd, length);
    if (start !== null && end !== null && end > start) clamped.push({ source: part, start, end });
  }
  clamped.sort((a, b) => a.start - b.start || a.end - b.end);

  const covering = [];
  let cursor = 0;
  for (const span of clamped) {
    const start = Math.max(span.start, cursor);
    if (span.end <= start) continue; // wholly swallowed by an earlier span
    if (covering.length === 0) covering.push({ source: span.source, start: 0, end: span.end });
    else {
      if (start > cursor) covering[covering.length - 1].end = start;
      covering.push({ source: span.source, start, end: span.end });
    }
    cursor = span.end;
  }
  // No usable record at all — the row speaks for itself rather than vanishing.
  if (covering.length === 0) covering.push({ source: segment, start: 0, end: length });
  covering[covering.length - 1].end = length;

  // Re-join neighbouring spans of the same speaker, on the speaker alone: a
  // Story line has no per-line voice, so two spans that differ only by
  // `profile_id` — one segment given its own voice before the merge — would
  // become two identical lines under one character. Honouring the user's merge
  // is the better answer, and the cast still takes the first span's voice.
  const joined = [];
  for (const span of covering) {
    const previous = joined[joined.length - 1];
    if (previous && speakerKeyOf(previous.source) === speakerKeyOf(span.source))
      previous.end = span.end;
    else joined.push({ ...span });
  }

  return joined
    .map((span) => ({ source: span.source, text: text.slice(span.start, span.end).trim() }))
    .filter((span) => span.text !== '');
}

/**
 * A cast id for `name`, unique within `taken`.
 *
 * Slugged by the same rule the Stories editor uses for a hand-added character,
 * so a dub-derived cast is indistinguishable from one built by hand. The
 * character class is bounded and non-overlapping, so a hostile `speaker_id`
 * cannot make it backtrack.
 *
 * "Narrator" claims the bare `narrator` id, matching `buildAutoCast`, because
 * that id is what the editor treats as the default narrator.
 *
 * @param {string} name
 * @param {Set<string>} taken  ids already minted; the chosen id is added to it
 * @returns {string}
 */
function castIdFor(name, taken) {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'char';
  let id = base;
  let suffix = 2;
  while (taken.has(id)) id = `${base}-${suffix++}`;
  taken.add(id);
  return id;
}

/**
 * The saved voice a speaker's segments point at, or `null` for the cast
 * default.
 *
 * A dub can hold three kinds of `profile_id`, and only one is a saved profile:
 *
 * - `auto:<slug>` — an auto-clone that lives only for that dub job. The slug is
 *   lossy, so it is not reversed; the speaker's own name is matched against the
 *   saved profiles instead, which is what binds an auto-clone the user later
 *   saved under that name. No match means the cast default rather than a
 *   reference that would dangle.
 * - `preset:<id>` — a designed-voice preset, which the Stories voice picker
 *   does not list. Mapping it to the default avoids a permanently blank
 *   selection.
 * - anything else non-empty — a saved id, validated against current profiles.
 *   Deleted profiles fall back to the cast default rather than blocking Stories.
 *
 * Matching is by equality and `startsWith`, never a regex, because
 * `profile_id` is user-influenced.
 *
 * @param {{profile_id?: unknown}} source  a segment, or one attributed span of one
 * @param {string} speakerName
 * @param {{id?: unknown, name?: unknown}[]} profiles
 * @returns {string|null}
 */
function resolveVoice(source, speakerName, profiles) {
  const raw = source?.profile_id;
  const id = raw == null ? '' : String(raw);
  if (id === '') return null;
  if (id.startsWith('preset:')) return null;
  if (id.startsWith('auto:')) {
    const wanted = speakerName.trim().toLowerCase();
    if (!wanted) return null;
    const match = (profiles || []).find(
      (profile) =>
        String(profile?.name ?? '')
          .trim()
          .toLowerCase() === wanted,
    );
    return match?.id == null ? null : String(match.id);
  }
  return profiles.some((profile) => profile?.id != null && String(profile.id) === id) ? id : null;
}

/**
 * Build a Stories cast and line list from a dub's segments.
 *
 * Speakers become characters in the order they first speak, and every
 * attributed span with usable text becomes one line under its speaker — one
 * line per row, except where a merged row genuinely changes speaker mid-line.
 * Lines carry no per-line voice override: the voice comes from the cast
 * member, so changing a character's voice afterwards re-voices all of their
 * lines at once.
 *
 * @param {DubSpeechSegment[]|null|undefined} segments
 * @param {{profiles?: {id?: unknown, name?: unknown}[], unknownSpeakerLabel?: string}} [options]
 * @returns {{cast: CastMember[], tracks: StoryTrack[], stats: DubStoryStats}}
 */
export function dubToStory(segments, options = {}) {
  if (!Array.isArray(segments)) return emptyStory();
  const profiles = Array.isArray(options.profiles) ? options.profiles : [];
  const unknownSpeakerLabel =
    typeof options.unknownSpeakerLabel === 'string' && options.unknownSpeakerLabel.trim()
      ? options.unknownSpeakerLabel.trim()
      : 'Speaker';

  // Order first. The dub table is already ordered, but an imported or merged
  // segment list need not be, and a cast built from the wrong order would name
  // its characters in the wrong order too.
  const ordered = segments
    .map((segment, index) => {
      const start = Number(segment?.start);
      return { segment, index, start: Number.isFinite(start) ? start : 0 };
    })
    // Array.prototype.sort is stable, and the index tiebreak states the intent
    // outright: segments sharing a start keep the order they arrived in.
    .sort((a, b) => a.start - b.start || a.index - b.index);

  const cast = [];
  const castIdBySpeaker = new Map();
  const taken = new Set();
  const tracks = [];
  let skippedEmpty = 0;
  for (const { segment } of ordered) {
    const spans = attributedSpans(segment);
    if (spans.length === 0) {
      skippedEmpty += 1;
      continue;
    }
    for (const { source, text } of spans) {
      const key = speakerKeyOf(source);
      if (!castIdBySpeaker.has(key)) {
        const name = key === UNATTRIBUTED ? unknownSpeakerLabel : key;
        // Resolved from this speaker's FIRST span. A clean dub gives every
        // segment of a speaker the same voice; an imported one might not, and
        // first-wins keeps the cast deterministic instead of last-write-wins.
        const member = {
          id: castIdFor(name, taken),
          name,
          color: nextCastColor(cast),
          profileId: resolveVoice(source, name, profiles),
        };
        cast.push(member);
        castIdBySpeaker.set(key, member.id);
      }
      // Every track's character exists: this loop walks the same spans that
      // built the cast, so a line can never reference a missing member.
      tracks.push({ character: castIdBySpeaker.get(key), text });
    }
  }

  return {
    cast,
    tracks,
    stats: { lines: tracks.length, speakers: cast.length, skippedEmpty },
  };
}
