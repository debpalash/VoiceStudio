export function segTimeRange(seg) {
  const known = (v) => typeof v === 'number' && Number.isFinite(v);
  const start = known(seg?.start) ? `${seg.start.toFixed(1)}s` : null;
  const end = known(seg?.end) ? `${seg.end.toFixed(1)}s` : null;
  if (start && end) return `${start} – ${end}`;
  return start || end || '';
}

/** Cleaned dictation text is the user-facing result; raw ASR stays recoverable. */
export function preferredTranscript(entry) {
  return entry?.refined_text?.trim() || entry?.text || '';
}

/**
 * One "Speaker: text" line per run of same-speaker segments, or null when the
 * transcript has no speaker labels.
 */
export function speakerTranscript(entry) {
  const segments = Array.isArray(entry?.segments) ? entry.segments : [];
  if (!segments.some((segment) => segment?.speaker)) return null;
  const lines = [];
  for (const segment of segments) {
    const text = (segment?.text || '').trim();
    if (!text) continue;
    const last = lines[lines.length - 1];
    if (last && last.speaker === segment.speaker) last.text += ` ${text}`;
    else lines.push({ speaker: segment.speaker, text });
  }
  return lines
    .map((line) => (line.speaker ? `${line.speaker}: ${line.text}` : line.text))
    .join('\n');
}

function timedCues(entry) {
  if (entry?.timestamps_available !== true) return null;
  if (!Array.isArray(entry?.segments)) return null;
  const cues = [];
  for (const segment of entry.segments) {
    if (typeof segment?.text !== 'string') return null;
    const text = segment.text
      .trim()
      .replace(/\r\n?/g, '\n')
      .replace(/\n\s*\n/g, '\n');
    if (!text) continue;
    const { start, end } = segment;
    if (
      typeof start !== 'number' ||
      typeof end !== 'number' ||
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 0 ||
      end <= start
    )
      return null;
    const from = Math.round(start * 1000),
      to = Math.round(end * 1000);
    if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || to <= from) return null;
    cues.push({ from, to, text });
  }
  return cues.length ? cues : null;
}

export function canExportTranscriptSubtitles(entry) {
  return timedCues(entry) !== null;
}

function subtitleTime(milliseconds) {
  const pad = (value, width = 2) => String(value).padStart(width, '0');
  return `${pad(Math.floor(milliseconds / 3600000))}:${pad(Math.floor(milliseconds / 60000) % 60)}:${pad(Math.floor(milliseconds / 1000) % 60)},${pad(milliseconds % 1000, 3)}`;
}

/** History retains metadata; plain text and SubRip are explicit export choices. */
export function formatTranscriptExport(entries, { format = 'history' } = {}) {
  if (format === 'text') return entries.map(preferredTranscript).join('\n\n') + '\n';
  if (format === 'srt') {
    const cues = entries.length === 1 ? timedCues(entries[0]) : null;
    if (!cues) throw new Error('transcript_timings_unavailable');
    return cues
      .map(
        (cue, index) =>
          `${index + 1}\r\n${subtitleTime(cue.from)} --> ${subtitleTime(cue.to)}\r\n${cue.text.replaceAll('\n', '\r\n')}\r\n\r\n`,
      )
      .join('');
  }
  return entries
    .map((entry) => {
      const date = new Date(entry.timestamp);
      const stamp = Number.isFinite(date.getTime()) ? date.toLocaleString() : '';
      const speakers = speakerTranscript(entry);
      // Segments keep raw recognition, so a cleaned transcript stays the export
      // and the speaker-labelled version rides along beneath it.
      const text = !speakers
        ? preferredTranscript(entry)
        : entry.refined_text?.trim()
          ? `${preferredTranscript(entry)}\n\n${speakers}`
          : speakers;
      return `[${stamp}] (${entry.language || ''})\n${text}\n`;
    })
    .join('\n---\n\n');
}
