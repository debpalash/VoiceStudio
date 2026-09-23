/**
 * Single source of truth for a dub segment's *generation inputs* — the
 * fields that actually change the TTS output: text, voice, instruct,
 * speed, language, direction, effect preset.
 *
 * Both the `/dub/generate` request body and the `/tools/incremental`
 * fingerprint recompute MUST build their payloads through this helper.
 * Before #281 they diverged (the generate body expanded `preset:` voices
 * into instruct text; the recompute sent raw store fields), so the stored
 * fingerprints never matched the recomputed ones and every segment was
 * reported "changed" after every run — a 1-line edit re-dubbed all N lines.
 */
import { PRESETS } from './constants';

export function segmentGenInputs(s) {
  let profileId = s.profile_id || '';
  let instruct = s.instruct || '';
  if (profileId.startsWith('preset:')) {
    const pr = PRESETS.find((p) => p.id === profileId.replace('preset:', ''));
    if (pr) {
      const parts = Object.values(pr.attrs).filter((v) => v !== 'Auto');
      if (instruct.trim()) parts.push(instruct.trim());
      instruct = parts.join(', ');
    }
    profileId = '';
  }
  return {
    text: s.text,
    instruct,
    profile_id: profileId,
    speed: s.speed || undefined,
    target_lang: s.target_lang || undefined,
    direction: s.direction || undefined,
    effect_preset: s.effect_preset || undefined,
  };
}

// A caption import keeps the cue it wrote (`webvtt_source` / `srt_source`) so
// an unchanged export restores its markup, and names it in `cue_source_id`
// while `text` is still that import's words (backend/services/srt_parser.py
// CUE_SOURCE_ID). That id is provenance, not a text match: a paste, edit or
// translation clears it even when the new words equal the imported ones.
const CUE_SOURCE_FIELDS = ['webvtt_source', 'srt_source'];

const importedSource = (segment, text) =>
  CUE_SOURCE_FIELDS.map((key) => segment?.[key]).find(
    (source) => source && source.id && source.text === text,
  );

/** `segment` with its text no longer the import's. Every text write that is
 *  not the import itself (edit, paste, translation) must go through this. */
export function withoutCueSource(segment) {
  if (!segment || !('cue_source_id' in segment)) return segment;
  const next = { ...segment };
  delete next.cue_source_id;
  return next;
}

/** Clear `cue_source_id` wherever the text no longer matches its cue, so no
 *  writer can revive it by landing on equal text later. Returns `segments`
 *  itself when nothing changed. */
export function settleCueSources(segments) {
  if (!Array.isArray(segments)) return segments;
  let changed = false;
  const next = segments.map((segment) => {
    const id = segment?.cue_source_id;
    if (!id || importedSource(segment, segment.text)?.id === id) return segment;
    changed = true;
    return withoutCueSource(segment);
  });
  return changed ? next : segments;
}

/** A row switched back to its untouched original text shows the import's
 *  words again (`text_original` is only ever written by the import). */
export function withOriginalCueSource(segment) {
  const source = importedSource(segment, segment?.text_original);
  if (!source || segment.text !== segment.text_original) return withoutCueSource(segment);
  return segment.cue_source_id === source.id ? segment : { ...segment, cue_source_id: source.id };
}

/** The import id `/dub/generate` needs to keep the segment's imported cue. */
export function cueSourceId(segment) {
  const source = importedSource(segment, segment?.text);
  return source && segment.cue_source_id === source.id ? source.id : undefined;
}

/** The `auto:<safe>` profile id for a diarized speaker. Mirrors the backend's
 *  portable filename/profile slug and is shared by every voice picker. */
export function autoProfileId(speakerId) {
  const cleaned = [];
  for (const char of String(speakerId || '').toLowerCase()) {
    if (/^[\p{L}\p{N}]$/u.test(char)) cleaned.push(char);
    else if (char === ' ' || char === '-') cleaned.push('_');
  }
  return `auto:${cleaned.join('') || 'speaker'}`;
}

/** Active merged parts for cast lookup. An empty `merge_parts` means the
 * editable projection was cleared; the original attribution remains the
 * canonical source for speakers and their assigned voices. */
export function castParts(segment) {
  if (Array.isArray(segment?.merge_parts) && segment.merge_parts.length) {
    return segment.merge_parts;
  }
  return Array.isArray(segment?.merge_parts_original) ? segment.merge_parts_original : [];
}

/**
 * The one per-speaker cast mutation: give every segment (and merged part)
 * spoken by `speakerId` the voice `profileId`. Shared by the CAST dropdowns
 * and the casting board so a drag-and-drop writes exactly the fields the
 * <select> always has — `profile_id` on directly-matching segments, plus
 * `merge_parts` / `merge_parts_original` attribution on merged rows (a merged
 * row keeps its own top-level voice when only a nested part's speaker moves).
 */
export function assignSpeakerProfile(segments, speakerId, profileId) {
  return (segments || []).map((s) => {
    const directMatch = s.speaker_id === speakerId;
    const nestedMatch = castParts(s).some((part) => part.speaker_id === speakerId);
    if (!directMatch && !nestedMatch) return s;
    return {
      ...s,
      ...(directMatch ? { profile_id: profileId } : {}),
      ...(s.merge_parts
        ? {
            merge_parts: s.merge_parts.map((part) =>
              part.speaker_id === speakerId ? { ...part, profile_id: profileId } : part,
            ),
          }
        : {}),
      ...(s.merge_parts_original
        ? {
            merge_parts_original: s.merge_parts_original.map((part) =>
              part.speaker_id === speakerId ? { ...part, profile_id: profileId } : part,
            ),
          }
        : {}),
    };
  });
}

/** Every distinct diarized speaker in cast order — top-level ids first-seen,
 *  including speakers that only survive inside merged rows' `merge_parts`. */
export function castSpeakers(segments) {
  return [
    ...new Set(
      (segments || [])
        .flatMap((s) => [s.speaker_id, ...castParts(s).map((part) => part.speaker_id)])
        .filter(Boolean),
    ),
  ];
}

/** Recover path-free cast metadata from current or legacy job payloads. */
export function castSourcesFromJob(job) {
  if (!job || typeof job !== 'object') return {};
  const sources = {};
  for (const [speaker, info] of Object.entries(job.cast_sources || job.speaker_clones || {})) {
    if (!info || typeof info !== 'object') continue;
    sources[speaker] = {
      duration: Number(info.duration) || 0,
      source_count: Number(info.source_count) || 1,
      kind: info.kind === 'segment' ? 'segment' : 'speaker',
    };
  }
  for (const segment of job.segments || []) {
    const speaker = segment?.speaker_id || 'Speaker 1';
    const current = sources[speaker];
    if (current && current.kind !== 'segment') continue;
    const info = (job.segment_clones || {})[String(segment?.id ?? '')];
    if (!info?.ref_audio) continue;
    const duration = Number(info.duration) || 0;
    if (!current || duration > current.duration) {
      sources[speaker] = { duration, source_count: 1, kind: 'segment' };
    }
  }
  return sources;
}

/**
 * #486: auto-assign each diarized segment to its detected speaker's cloned
 * voice instead of leaving it on "Default". When the backend cloned a speaker
 * from the video (`speakerClones[speaker_id]` present) and the segment has no
 * voice chosen yet, default its `profile_id` to that speaker's `auto:` clone.
 * The user can still override per-speaker or per-segment afterwards — we only
 * fill an *empty* profile_id, never clobber an explicit choice.
 */
export function applySpeakerCloneDefaults(segments, speakerClones) {
  const clones = speakerClones && typeof speakerClones === 'object' ? speakerClones : {};
  if (!Array.isArray(segments) || !Object.keys(clones).length) return segments || [];
  return segments.map((s) => {
    if (!s || s.profile_id || !s.speaker_id || !clones[s.speaker_id]) return s;
    return { ...s, profile_id: autoProfileId(s.speaker_id) };
  });
}
