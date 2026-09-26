import defaults from './firstSound.json';

// Taxonomy tokens also provide the nonempty description VoiceDesign requires.
export function firstSoundRequest(text) {
  const body = new FormData();
  body.append('text', text);
  body.append('instruct', defaults.instruct);
  body.append('num_step', String(defaults.num_step));
  return { method: 'POST', body };
}
