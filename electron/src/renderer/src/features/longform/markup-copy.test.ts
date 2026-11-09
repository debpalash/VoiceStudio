import { describe, expect, it } from 'vitest';
import { SAMPLE_AUDIOBOOK_SCRIPT } from '@shared/data/sampleAudiobook';
import { SAMPLE_STORY_LINES } from '@shared/data/sampleStory';

// Script markup is parsed, not read: a translated "[Pause 0,5 s]" or "[voz:…]"
// in a placeholder teaches users a token the parser reads aloud. Every locale
// shows the one canonical spelling, and the samples use the same pause unit.
const locales = import.meta.glob<Record<string, Record<string, string>>>(
  '../../i18n/locales/*.json',
  {
    eager: true,
    import: 'default',
  },
);
const MARKUP_KEYS = [
  ['audiobook', 'markup_hint'],
  ['audiobook', 'script_placeholder'],
  ['stories', 'linePlaceholder'],
  ['stories', 'insertPause'],
] as const;
const CANONICAL =
  /^\[(?:pause \d+(?:\.\d+)?s|voice:[^\]]*|\/?(?:slow|fast|emphasis|spell)|laughter|sigh|confirmation-en|question-(?:en|ah|oh|ei|yi)|surprise-(?:ah|oh|wa|yo)|dissatisfaction-hnn)\]$/;
const tokens = (text: string) => text.match(/\[[^\]]*\]/g) ?? [];

describe.each(Object.entries(locales))('%s markup copy', (_file, catalog) => {
  it.each(MARKUP_KEYS)('%s.%s keeps literal, canonical markup', (namespace, key) => {
    const text = catalog[namespace][key];
    for (const token of tokens(text)) expect(token).toMatch(CANONICAL);
    if (key === 'insertPause' || key === 'linePlaceholder') expect(text).toContain('[pause 0.5s]');
  });

  it('starts placeholder chapters with "# " so they parse as headings', () => {
    for (const line of catalog.audiobook.script_placeholder.split('\n'))
      if (line.startsWith('#')) expect(line).toMatch(/^# \S/);
  });
});

it('writes every sample pause in seconds, the unit the placeholders show', () => {
  const samples = [SAMPLE_AUDIOBOOK_SCRIPT, ...SAMPLE_STORY_LINES.map((line) => line.text)];
  for (const text of samples)
    for (const token of tokens(text).filter((value) => value.startsWith('[pause')))
      expect(token).toMatch(/^\[pause \d+(?:\.\d+)?s\]$/);
});
