export const FACETS = {
  gender: ['male', 'female'],
  age: ['child', 'teenager', 'young adult', 'middle-aged', 'elderly'],
  pitch: ['very low pitch', 'low pitch', 'moderate pitch', 'high pitch', 'very high pitch'],
  accent: [
    'american accent',
    'british accent',
    'australian accent',
    'canadian accent',
    'indian accent',
    'chinese accent',
    'japanese accent',
    'korean accent',
    'portuguese accent',
    'russian accent',
  ],
  // English + Chinese come from the generated catalog; the rest are curated
  // multilingual designed voices. Values must match the archetype `language`
  // field (a languages.json entry) exactly — that drives the backend filter.
  lang: [
    'English',
    'Chinese',
    'Spanish',
    'French',
    'German',
    'Italian',
    'Portuguese',
    'Russian',
    'Hindi',
    'Japanese',
    'Korean',
  ],
};
