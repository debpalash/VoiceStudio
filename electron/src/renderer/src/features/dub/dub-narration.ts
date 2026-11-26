import { segmentGenInputs } from '@shared/utils/segments';
import type { DubSegment } from './dub-session';

export interface DubNarration {
  enabled: boolean;
  language: string;
  captions: DubSegment[];
  blocks: DubSegment[];
  sourceKey: string;
  previousTiming?: string;
}
const MAX_SECONDS = 25;
const MAX_CHARS = 500;
const LONG_PAUSE = 1.2;
const sentenceEnd = (text: string) =>
  /[.!?。！？]["'”’»)]*$/u.test(text.trim()) &&
  !/\b(?:Mr|Mrs|Ms|Dr|Prof|vs|e\.g|i\.e)\.$/i.test(text.trim());
const signature = (cue: DubSegment) =>
  JSON.stringify([cue.speaker_id, segmentGenInputs({ ...cue, text: '' }), cue.gain]);
const join = (texts: string[]) =>
  texts
    .filter((text) => text.trim())
    .map((text) => text.replace(/\s+/g, ' ').trim())
    .join(' ');
function blockId(ids: string[], language: string) {
  let hash = 2166136261;
  for (const char of JSON.stringify([language, ids]))
    hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return (
    'block_' + (hash >>> 0).toString(16) + '_' + ids[0].replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 48)
  );
}
export const narrationSourceKey = (captions: DubSegment[], language: string) =>
  JSON.stringify([
    language,
    captions.map((c) => [
      c.id,
      c.start,
      c.end,
      c.text,
      c.text_original,
      c.translations,
      signature(c),
    ]),
  ]);
/** Preserve words; join subtitle fragments using phrase ends, speaker boundaries and silence. */
export function consolidateDubCaptions(captions: DubSegment[], language: string): DubSegment[] {
  const blocks: DubSegment[] = [];
  let pending: DubSegment[] = [];
  const flush = () => {
    if (!pending.length) return;
    const first = pending[0],
      last = pending[pending.length - 1],
      ids = pending.map((c) => c.id);
    const langs = [...new Set(pending.flatMap((c) => Object.keys(c.translations || {})))];
    const translations = Object.fromEntries(
      langs
        .filter((code) => pending.every((c) => c.translations?.[code]?.trim()))
        .map((code) => [code, join(pending.map((c) => c.translations![code]))]),
    );
    blocks.push({
      id: blockId(ids, language),
      source_ids: ids,
      start: first.start,
      end: last.end,
      text: join(pending.map((c) => c.text)),
      text_original: join(pending.map((c) => c.text_original)),
      speaker_id: first.speaker_id,
      profile_id: first.profile_id,
      instruct: first.instruct,
      target_lang: first.target_lang,
      direction: first.direction,
      effect_preset: first.effect_preset,
      gain: first.gain,
      speed: first.speed,
      translations: { ...translations, [language]: join(pending.map((c) => c.text)) },
    });
    pending = [];
  };
  for (const cue of captions) {
    if (!cue.text.trim()) {
      flush();
      continue;
    }
    const first = pending[0],
      last = pending[pending.length - 1];
    if (
      last &&
      (signature(cue) !== signature(first) ||
        cue.start - last.end >= LONG_PAUSE ||
        cue.start < last.start ||
        cue.end - first.start > MAX_SECONDS ||
        join([...pending.map((c) => c.text), cue.text]).length > MAX_CHARS)
    )
      flush();
    pending.push(cue);
    const duration = cue.end - pending[0].start;
    if (
      (sentenceEnd(cue.text) && (duration >= 4 || join(pending.map((c) => c.text)).length >= 60)) ||
      duration >= MAX_SECONDS
    )
      flush();
  }
  flush();
  return blocks;
}
export function narrationFile({
  language,
  captions,
  blocks,
}: Pick<DubNarration, 'language' | 'captions' | 'blocks'>) {
  return (
    JSON.stringify(
      {
        type: 'voicestudio.dubbing-script',
        version: 2,
        language,
        timing: { strategy: 'narration', windows: 'srt', contract_version: 2 },
        subtitles: captions.map((c) => ({ id: c.id, start: c.start, end: c.end, text: c.text })),
        blocks: blocks.map((b) => ({
          id: b.id,
          source_ids: b.source_ids || [],
          start: b.start,
          end: b.end,
          text: b.text,
          speaker_id: b.speaker_id,
          audio_start: b.fit_status?.placed_start,
          audio_end: b.fit_status?.placed_end,
          profile_id: b.profile_id,
          instruct: b.instruct,
        })),
      },
      null,
      2,
    ) + '\n'
  );
}

/** Compare intended speech windows with the exact cues the block consolidates. */
export function compareNarration(captions: DubSegment[], blocks: DubSegment[]) {
  const byId = new Map(captions.map((cue) => [cue.id, cue]));
  return blocks.map((block) => {
    const sources = (block.source_ids || [])
      .map((id) => byId.get(id))
      .filter((cue): cue is DubSegment => Boolean(cue));
    const complete = sources.length > 0 && sources.length === block.source_ids?.length;
    const start = complete ? Math.min(...sources.map((cue) => cue.start)) : undefined;
    const end = complete ? Math.max(...sources.map((cue) => cue.end)) : undefined;
    const matches =
      complete && Math.abs(block.start - start!) < 0.001 && Math.abs(block.end - end!) < 0.001;
    return { block, sources, start, end, matches };
  });
}
