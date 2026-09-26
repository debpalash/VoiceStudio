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

/** Keep language and recording date attached when exporting history. */
export function formatTranscriptExport(entries) {
  return entries
    .map((entry) => {
      const date = new Date(entry.timestamp);
      const stamp = Number.isFinite(date.getTime()) ? date.toLocaleString() : '';
      const text = preferredTranscript(entry);
      return `[${stamp}] (${entry.language || ''})\n${text}\n`;
    })
    .join('\n---\n\n');
}
