export type GenerateBlocker =
  | 'busy'
  | 'importing'
  | 'engine_loading'
  | 'engine'
  | 'no_lines'
  | 'no_script'
  | 'voice'
  | 'lexicon';

/**
 * The ONE reason Generate is unavailable right now, most fundamental first, or
 * null when it can run. A greyed-out button with no explanation is a dead end:
 * the commonest case (lines with no voice and no default voice) looks exactly
 * like a broken app.
 */
export function generateBlocker(input: {
  mode: 'stories' | 'audiobook';
  /** The OTHER longform mode is rendering; one render runs at a time. */
  busyElsewhere: boolean;
  importing: boolean;
  tts: 'engine' | 'loading' | null;
  usable: boolean;
  voicesReady: boolean;
  duplicateLexicon: boolean;
}): GenerateBlocker | null {
  if (input.busyElsewhere) return 'busy';
  if (input.importing) return 'importing';
  if (input.tts === 'loading') return 'engine_loading';
  if (input.tts === 'engine') return 'engine';
  if (!input.usable) return input.mode === 'stories' ? 'no_lines' : 'no_script';
  if (!input.voicesReady) return 'voice';
  if (input.mode === 'audiobook' && input.duplicateLexicon) return 'lexicon';
  return null;
}
