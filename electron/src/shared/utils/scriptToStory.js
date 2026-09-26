/**
 * Audiobook script → Stories project (#24). The reciprocal of storyToScript.
 * Parses a chapter-delimited script into the persisted Stories shape (cast +
 * tracks). One physical line = one track; a leading `[voice:ID]` becomes the
 * track's profile override + a cast member; inline mid-line markup (`[voice:]`,
 * `[pause]`, SSML-lite, emotion) stays in the text verbatim so the render keys
 * on the same tokens the backend already understands.
 *
 * Pure. No new regex over user input (leading-voice detection is string ops) —
 * CodeQL-clean. Body text preserved byte-for-byte (minus the stripped leading
 * tag), so `[pause 500ms]` / bare `[pause]` survive for the backend dialect.
 *
 * @param {string} text
 * @param {{id: string, name: string}[]} [profiles]  map `[voice:id]` → named cast
 * @returns {{tracks: Array, cast: Array}}  cast ALWAYS includes a narrator clone
 */
import { isChapterLine, chapterTitle } from './storyExport';
import { nextCastColor } from './storyCast';
import { DEFAULT_CAST } from '../store/longformSlice';

const slug = (s) =>
  String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/(^-|-$)/g, '') || 'char';

/** Leading `[voice:ID]` via string ops. Returns {id, rest} or null. */
function splitLeadingVoice(line) {
  const lead = line.match(/^\s*/)[0]; // count leading ws (linear)
  const t = line.slice(lead.length);
  if (!/^\[voice:/i.test(t)) return null; // fixed anchored literal
  const close = t.indexOf(']');
  if (close === -1) return null;
  const id = t.slice('[voice:'.length, close).trim();
  let rest = t.slice(close + 1);
  if (rest.startsWith(' ')) rest = rest.slice(1); // drop one separating space
  return { id, rest };
}

export function scriptToStory(text, profiles = []) {
  const cast = DEFAULT_CAST.map((c) => ({ ...c })); // never the shared ref
  const tracks = [];
  if (!text || !String(text).trim()) return { tracks, cast };

  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  const usedIds = new Set(cast.map((c) => c.id)); // 'narrator' reserved
  const byProfile = new Map(); // profileId → castMember
  let n = 0;

  for (const line of lines) {
    if (isChapterLine(line)) {
      n += 1;
      tracks.push({
        id: n,
        character: 'narrator',
        text: `# ${chapterTitle(line)}`,
        profileId: null,
        emotion: null,
        speed: null,
      });
      continue;
    }
    if (!line.trim()) continue; // blank dropped

    let profileId = null;
    let body = line;
    const lead = splitLeadingVoice(line);
    if (lead) {
      body = lead.rest;
      // [voice:default] / empty → narrator (profileId null); else real override.
      profileId = lead.id === 'default' || lead.id === '' ? null : lead.id;
    }

    let character = 'narrator';
    if (profileId != null) {
      let cm = byProfile.get(profileId);
      if (!cm) {
        let cid = slug(profileId);
        if (usedIds.has(cid)) {
          // slug-collision / narrator dedupe
          let k = 2;
          while (usedIds.has(`${cid}-${k}`)) k += 1;
          cid = `${cid}-${k}`;
        }
        usedIds.add(cid);
        const known = (profiles || []).find((p) => p.id === profileId);
        cm = {
          id: cid,
          name: (known && known.name) || profileId,
          color: nextCastColor(cast),
          profileId,
        };
        cast.push(cm);
        byProfile.set(profileId, cm);
      }
      character = cm.id;
    }

    n += 1;
    tracks.push({ id: n, character, text: body, profileId, emotion: null, speed: null });
  }

  return { tracks, cast };
}
