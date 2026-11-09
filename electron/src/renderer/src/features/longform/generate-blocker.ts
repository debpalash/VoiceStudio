const REASONS = [
  'busy',
  'importing',
  'engine_loading',
  'engine',
  'no_lines',
  'no_script',
  'voice',
  'lexicon',
] as const;

/** The single reason Generate is unavailable; see `generateBlocker` for precedence. */
export type GenerateBlocker = (typeof REASONS)[number];

export type GenerateBlockerInput = {
  mode: 'stories' | 'audiobook';
  busyElsewhere: boolean;
  importing: boolean;
  tts: 'engine' | 'loading' | null;
  usable: boolean;
  voicesReady: boolean;
  duplicateLexicon: boolean;
};

type Rule = readonly [GenerateBlocker, (input: GenerateBlockerInput) => boolean];

// Earlier rules shadow later ones, so the list is ordered from the cause that
// makes every other message moot down to the most local one.
const RULES: readonly Rule[] = [
  ['busy', (i) => i.busyElsewhere],
  ['importing', (i) => i.importing],
  ['engine_loading', (i) => i.tts === 'loading'],
  ['engine', (i) => i.tts === 'engine'],
  ['no_lines', (i) => !i.usable && i.mode === 'stories'],
  ['no_script', (i) => !i.usable && i.mode === 'audiobook'],
  ['voice', (i) => !i.voicesReady],
  // Stories never reads the pronunciation lexicon, so a repeat there is harmless.
  ['lexicon', (i) => i.mode === 'audiobook' && i.duplicateLexicon],
];

export function generateBlocker(input: GenerateBlockerInput): GenerateBlocker | null {
  return RULES.find(([, applies]) => applies(input))?.[0] ?? null;
}
