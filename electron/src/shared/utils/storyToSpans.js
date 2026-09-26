import { isChapterLine, chapterTitle } from './storyExport';
import { effectiveProfile } from './storyCast';
import { parseChapterBody } from './longformParser';

/**
 * Compile the Stories Editor's cast + ordered lines into the chapter/span plan
 * the shared `/longform/render` endpoint consumes — the bridge that lets a
 * multi-voice story render on the same server-side pipeline as an audiobook
 * (resume, loudness, cover, chapter markers).
 *
 * The track→canonical adapter (#27): per-track cast voice + speed are resolved
 * here, then the track's text runs through the ONE canonical voice→pause→SSML
 * layering (`parseChapterBody`) — the same code the Python parser uses. The
 * adapter, not the canonical parser, owns the two track-shaped concerns:
 *  - a `#`-line *inside* a track's text must NOT re-chapter (we call
 *    parseChapterBody, which never chapter-splits, not parseScriptToSpans);
 *  - a track's *leading* pause folds into the previous track's last span
 *    (cross-track fold) — but a mid-track silent span (from `[voice:x][pause]`)
 *    is kept, matching the server's single-blob behavior.
 *
 * ``globalSpeed`` (#415) is one reading speed applied to every track that has no
 * per-track speed of its own — a per-track slider still overrides it. Pass null
 * (or 1.0×, the engine default) to leave every track at the engine default.
 *
 * @returns Array<{ title, spans: [{ voice_id, text, pause_ms_after, speed }] }>
 */
export function storyToSpans(tracks, cast, globalSpeed = null) {
  const chapters = [];
  let cur = { title: '', spans: [] };
  const flush = () => {
    if (cur.spans.length) chapters.push(cur);
  };
  // 1.0× is the engine default → treat it as "no global override" so we don't
  // stamp an explicit speed on every span when the control is at rest.
  const gspeed = globalSpeed && globalSpeed !== 1 ? globalSpeed : null;

  for (const tk of tracks || []) {
    const text = tk.text || '';
    if (isChapterLine(text)) {
      flush();
      cur = { title: chapterTitle(text), spans: [] };
      continue;
    }
    const voiceId = effectiveProfile(tk, cast) || null;
    // Per-track speed wins; otherwise the global speed; else engine default.
    // (falsy 0 → fall through to global/null, per the #27 zero-is-default rule.)
    const speed = tk.speed || gspeed || null;
    const spans = parseChapterBody(text, { defaultVoice: voiceId, defaultSpeed: speed });
    spans.forEach((s, i) => {
      const prev = cur.spans[cur.spans.length - 1];
      // Cross-track fold: a track that *leads* with a pause merges that silence
      // onto the previous span instead of emitting a standalone silent span.
      if (i === 0 && s.text === '' && s.pause_ms_after > 0 && prev) {
        prev.pause_ms_after += s.pause_ms_after;
      } else {
        cur.spans.push(s);
      }
    });
  }
  flush();
  return chapters;
}
