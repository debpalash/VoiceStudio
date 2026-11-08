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

const CUE_FIELD_BREAK = /[\t\n\r\v\f\u0085\u2028\u2029]+/g;

function cueTitle(value) {
  return String(value ?? '')
    .replace(CUE_FIELD_BREAK, ' ')
    .trim();
}

function numericDuration(value) {
  if (value == null || (typeof value === 'string' && !value.trim())) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** Convert ordered cues to a tab-delimited, newline-separated text document. */
export function buildCueSheet(cues) {
  const rows = [];
  for (const cue of cues ?? []) rows.push(`${formatTimecode(cue.time)}\t${cueTitle(cue.title)}`);
  return rows.join('\n');
}

export function cuesFromChapters(chapters, fallbackTitle = (position) => `Chapter ${position}`) {
  const result = [];
  let cursorMs = 0;
  for (const [index, chapter] of (chapters ?? []).entries()) {
    const suppliedTitle = cueTitle(chapter?.title);
    result.push({
      title: suppliedTitle || cueTitle(fallbackTitle(index + 1)),
      time: cursorMs / 1000,
    });
    const milliseconds = numericDuration(chapter?.duration_ms);
    const seconds = numericDuration(chapter?.duration_s);
    const contribution = milliseconds === undefined ? (seconds ?? 0) * 1000 : milliseconds;
    cursorMs += Math.max(0, Math.round(contribution));
  }
  return result;
}

export function cueSheetFilename(output) {
  const segments = String(output ?? '').split(/[\\/]/);
  const leaf = segments.at(-1) ?? '';
  const audioSuffix = /\.(?:m4b|mp3)$/i;
  return audioSuffix.test(leaf) ? leaf.replace(audioSuffix, '.txt') : 'cuesheet.txt';
}

/**
 * Render an ordered track list to one WAV blob + chapter cues.
 * Lines whose text starts with "# " are chapter markers — not spoken; they
 * record a cue at the current timeline position.
 * @param tracks          [{ text, character, profileId, speed }]
 * @param resolveOpts     (track) => { profileId, speed }   // applies cast fallback
 * @param fetchChunkBlob  (text, profileId, speed, emotion?) => Promise<Blob>   // /generate WAV;
 *                        emotion is passed only for a line's [emotion:] span
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
            emotion: span.emotion,
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
      const blob = await fetchChunkBlob(
        item.text,
        item.profileId,
        item.speed,
        ...(item.emotion ? [item.emotion] : []),
      );
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
