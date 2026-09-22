/**
 * Story audiobook export.
 *
 * Renders each track (chunks + [pause] gaps) to audio via the job-less
 * `/generate` endpoint, decodes with the Web Audio API, stitches into one
 * mono buffer with timed silences, and encodes a single 16-bit PCM WAV.
 *
 * The pure helpers (silenceBuffer/concatBuffers/encodeWav) take and return
 * plain {sampleRate, numberOfChannels, length, getChannelData} shapes so they
 * are testable without a real AudioContext.
 */
import { parseChapterBody } from './longformParser';

/** Mono buffer of `seconds` of silence at `sampleRate`. */
export function silenceBuffer(seconds, sampleRate) {
  const length = Math.max(0, Math.round(seconds * sampleRate));
  const data = new Float32Array(length);
  return { sampleRate, numberOfChannels: 1, length, getChannelData: () => data };
}

/** Concatenate mono buffers (channel 0) in order. */
export function concatBuffers(buffers, sampleRate) {
  const total = buffers.reduce((n, b) => n + b.length, 0);
  const out = new Float32Array(total);
  let offset = 0;
  for (const b of buffers) {
    out.set(b.getChannelData(0).subarray(0, b.length), offset);
    offset += b.length;
  }
  return { sampleRate, numberOfChannels: 1, length: total, getChannelData: () => out };
}

/** Encode a mono buffer to a 16-bit PCM WAV ArrayBuffer. */
export function encodeWav(buffer, sampleRate) {
  const samples = buffer.getChannelData(0);
  const n = buffer.length;
  const ab = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(ab);
  const writeStr = (o, s) => {
    for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  dv.setUint32(4, 36 + n * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  dv.setUint32(16, 16, true); // PCM chunk size
  dv.setUint16(20, 1, true); // PCM format
  dv.setUint16(22, 1, true); // mono
  dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, sampleRate * 2, true); // byte rate (mono * 2 bytes)
  dv.setUint16(32, 2, true); // block align
  dv.setUint16(34, 16, true); // bits per sample
  writeStr(36, 'data');
  dv.setUint32(40, n * 2, true);
  let o = 44;
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    o += 2;
  }
  return ab;
}

/** A line is a chapter heading iff it's an H1 with a non-space title (#27:
 *  narrowed from H1–H6 to match the server's _HEADING_RE — `##`… and `# ` with
 *  no non-space title narrate as body, not chapter breaks). */
export function isChapterLine(text) {
  return /^#[ \t]+\S/.test(String(text || '').trim());
}

/** The chapter title — strip only the single leading "# ", remainder verbatim
 *  (matching the server's raw-title behavior, so `# [voice:x] T` → `[voice:x] T`). */
export function chapterTitle(text) {
  return String(text || '')
    .trim()
    .replace(/^#[ \t]+/, '')
    .trim();
}

/** Format seconds as HH:MM:SS for a chapter cue sheet. */
export function formatTimecode(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const hh = String(Math.floor(s / 3600)).padStart(2, '0');
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

/** Group spoken lines (skip chapter headings) by character, preserving order. */
export function tracksByCharacter(tracks) {
  const groups = [];
  const idx = Object.create(null);
  for (const tk of tracks || []) {
    if (isChapterLine(tk.text)) continue;
    const key = tk.character || 'narrator';
    if (!(key in idx)) {
      idx[key] = groups.length;
      groups.push({ character: key, tracks: [] });
    }
    groups[idx[key]].tracks.push(tk);
  }
  return groups;
}

/**
 * Build a chapter cue sheet string from {time,title} cues.
 *
 * `HH:MM:SS<TAB>Title`, one line per chapter, no trailing newline — the shape
 * spreadsheets and chapter-aware editors split on without guessing where the
 * timestamp ends (spec 33).
 */
export function buildCueSheet(chapters) {
  return (chapters || []).map((c) => `${formatTimecode(c.time)}\t${c.title}`).join('\n');
}

/**
 * Accumulate `{time, title}` cues from the ordered SUCCESSFUL chapters of a
 * render. Cue k starts at the summed duration of chapters 0..k-1, so cue 0 is
 * always 00:00:00.
 *
 * Callers must pass only chapters that actually rendered. The backend appends
 * to `chapters_meta` — the source of the m4b's embedded FFMETADATA chapters —
 * only on success, so including a failed chapter here would shift every
 * following cue out of step with the audio it describes.
 *
 * Total by construction: a missing, NaN, or negative `duration_s` contributes
 * zero rather than poisoning every later start time, and a blank title falls
 * back to its position.
 *
 * @param {{title?: string, duration_s?: number|string}[]|null|undefined} chapters
 * @returns {{time: number, title: string}[]}
 */
export function cuesFromChapters(chapters) {
  const cues = [];
  let elapsed = 0;
  for (const chapter of chapters || []) {
    const title = String(chapter?.title ?? '').trim();
    cues.push({ time: elapsed, title: title || `Chapter ${cues.length + 1}` });
    elapsed += Math.max(0, Number(chapter?.duration_s) || 0);
  }
  return cues;
}

/**
 * Cue-sheet filename for a render output: `audiobook_ab12.m4b` ->
 * `audiobook_ab12.txt`.
 *
 * Any path prefix is dropped, then a trailing `.m4b`/`.mp3` becomes `.txt`.
 * Only those two are swapped — the formats the renderer actually produces — so
 * an unexpected name falls back to `cuesheet.txt` instead of turning
 * `report.tar.gz` into `report.tar.txt`.
 *
 * Both patterns are anchored with fixed alternations and no unbounded
 * repetition, so a hostile `output` cannot make them backtrack.
 *
 * @param {string|null|undefined} output
 * @returns {string}
 */
export function cueSheetFilename(output) {
  const base = String(output || '')
    .split('/')
    .pop();
  const named = base.replace(/\.(m4b|mp3)$/i, '.txt');
  return /\.txt$/i.test(named) ? named : 'cuesheet.txt';
}

/**
 * Render an ordered track list to one WAV blob + chapter cues.
 * Lines whose text starts with "# " are chapter markers — not spoken; they
 * record a cue at the current timeline position.
 * @param tracks          [{ text, character, profileId, speed }]
 * @param resolveOpts     (track) => { profileId, speed }   // applies cast fallback
 * @param fetchChunkBlob  (text, profileId, speed) => Promise<Blob>   // /generate WAV
 * @param onProgress      (done, total) => void
 * @returns { blob: Blob, chapters: [{time,title}], durationSec: number }
 */
export async function exportStoryAudio(tracks, resolveOpts, fetchChunkBlob, onProgress) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const sr = ctx.sampleRate;
  try {
    const plan = [];
    for (const tk of tracks) {
      if (isChapterLine(tk.text)) {
        plan.push({ chapter: chapterTitle(tk.text) });
        continue;
      }
      const opts = resolveOpts(tk) || {};
      for (const span of parseChapterBody(tk.text || '', {
        defaultVoice: opts.profileId,
        defaultSpeed: opts.speed,
      })) {
        if (span.text)
          plan.push({
            type: 'chunk',
            text: span.text,
            profileId: span.voice_id,
            speed: span.speed,
          });
        if (span.pause_ms_after > 0)
          plan.push({ type: 'pause', seconds: span.pause_ms_after / 1000 });
      }
    }
    const chunkCount = plan.filter((s) => s.type === 'chunk').length;
    let done = 0;
    let sampleCursor = 0;
    const buffers = [];
    const chapters = [];
    for (const item of plan) {
      if (item.chapter !== undefined) {
        chapters.push({ time: sampleCursor / sr, title: item.chapter });
        continue;
      }
      if (item.type === 'pause') {
        const b = silenceBuffer(item.seconds, sr);
        buffers.push(b);
        sampleCursor += b.length;
        continue;
      }
      const blob = await fetchChunkBlob(item.text, item.profileId, item.speed);
      const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
      buffers.push(decoded);
      sampleCursor += decoded.length; // resamples to ctx.sampleRate → safe to concat
      onProgress?.(++done, chunkCount);
    }
    const combined = concatBuffers(buffers, sr);
    return {
      blob: new Blob([encodeWav(combined, sr)], { type: 'audio/wav' }),
      chapters,
      durationSec: combined.length / sr,
    };
  } finally {
    ctx.close?.();
  }
}

/**
 * Export one WAV per character (stems). Returns [{ character, blob }] in the
 * order characters first appear. Reuses exportStoryAudio per character group.
 */
export async function exportStems(tracks, resolveOpts, fetchChunkBlob, onProgress) {
  const groups = tracksByCharacter(tracks);
  const out = [];
  for (let i = 0; i < groups.length; i++) {
    const { blob } = await exportStoryAudio(groups[i].tracks, resolveOpts, fetchChunkBlob, null);
    out.push({ character: groups[i].character, blob });
    onProgress?.(i + 1, groups.length);
  }
  return out;
}
