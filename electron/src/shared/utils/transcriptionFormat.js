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
  return lines.map((line) => (line.speaker ? `${line.speaker}: ${line.text}` : line.text)).join('\n');
}

/** Keep language and recording date attached when exporting history. */
export function formatTranscriptExport(entries) {
  return entries
    .map((entry) => {
      const date = new Date(entry.timestamp);
      const stamp = Number.isFinite(date.getTime()) ? date.toLocaleString() : '';
      const text = speakerTranscript(entry) ?? preferredTranscript(entry);
      return `[${stamp}] (${entry.language || ''})\n${text}\n`;
    })
    .join('\n---\n\n');
}
