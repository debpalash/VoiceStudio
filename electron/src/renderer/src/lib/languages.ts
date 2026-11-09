import languagesJson from './languages.json';

/** Display names the backend accepts as `language`; index 0 is 'Auto' (omitted on the wire). */
export const LANGUAGES: string[] = languagesJson;

export const POPULAR_LANGUAGES: string[] = [
  'English',
  'Spanish',
  'French',
  'German',
  'Italian',
  'Portuguese (Brazil)',
  'Portuguese (Portugal)',
  'Russian',
  'Chinese',
  'Japanese',
  'Korean',
  'Arabic',
  'Hindi',
];

/** Expression tokens the engine renders as non-verbal sounds when inlined in the script. */
export const TAGS: string[] = [
  '[laughter]',
  '[sigh]',
  '[confirmation-en]',
  '[question-en]',
  '[question-ah]',
  '[question-oh]',
  '[question-ei]',
  '[question-yi]',
  '[surprise-ah]',
  '[surprise-oh]',
  '[surprise-wa]',
  '[surprise-yo]',
  '[dissatisfaction-hnn]',
];
