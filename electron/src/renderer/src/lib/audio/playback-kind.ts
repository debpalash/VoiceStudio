/**
 * What produced a clip, so every player names it the same way. Callers pass a
 * kind, never a free-text label: a binary "design or else clone" check is how
 * converted takes ended up labelled "Voice cloning" in the bottom player.
 */
export type PlaybackKind = 'clone' | 'design' | 'convert' | 'dub' | 'audiobook' | 'story' | 'audio';

const LABEL_KEYS: Record<PlaybackKind, string> = {
  clone: 'nav.clone',
  design: 'nav.design',
  convert: 'player.kind_convert',
  dub: 'nav.dub',
  audiobook: 'projects.audiobook',
  story: 'projects.story',
  audio: 'player.generated_audio',
};

/** i18n key of the label shown under a clip's title. */
export function playbackKindLabelKey(kind: PlaybackKind): string {
  return LABEL_KEYS[kind] ?? LABEL_KEYS.audio;
}

/**
 * Kind of a generation-history take from its `mode` column. Rows written
 * before `mode` existed are clone takes; anything unrecognised stays neutral
 * rather than claiming a workspace it did not come from.
 */
export function takePlaybackKind(mode: string | null | undefined): PlaybackKind {
  if (!mode || mode === 'clone') return 'clone';
  if (mode === 'design' || mode === 'convert') return mode;
  return 'audio';
}

/**
 * Shorten a clip title to at most `max` characters on a word boundary, adding
 * an ellipsis. A single word longer than `max` is cut at `max`.
 */
export function clipTitle(text: string, max = 56): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  const head = clean.slice(0, max);
  const space = head.lastIndexOf(' ');
  const cut = space > max / 2 ? head.slice(0, space) : head;
  return cut.replace(/[\s,.;:—–-]+$/u, '') + '…';
}
