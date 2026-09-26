import { parseScript } from './parseScript';
import { nextCastColor } from './storyCast';
/** Shared local screenplay/prose casting. Existing voices win; new speakers cycle profiles. */
export function buildAutoCast(text, cast = [], profiles = []) {
  const parsed = parseScript(text);
  const next = cast.map((character) => ({ ...character }));
  const speakers = [...new Set(parsed.map((line) => line.speaker))];
  const ids = new Map();
  let voiceIndex = 0;
  const voice = () => (profiles.length ? profiles[voiceIndex++ % profiles.length].id : null);
  for (const name of speakers) {
    let character = next.find(
      (c) =>
        (name.toLowerCase() === 'narrator' && c.id === 'narrator') ||
        c.name.toLowerCase() === name.toLowerCase(),
    );
    if (!character) {
      const base =
        name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-|-$/g, '') || 'char';
      let id = base;
      let suffix = 2;
      while (next.some((c) => c.id === id)) id = base + '-' + suffix++;
      character = { id, name, color: nextCastColor(next), profileId: voice() };
      next.push(character);
    } else if (!character.profileId) character.profileId = voice();
    ids.set(name, character.id);
  }
  return {
    cast: next,
    tracks: parsed.map((line) => ({ character: ids.get(line.speaker), text: line.text })),
    speakers,
  };
}
