export type LogSeverity = 'error' | 'warning' | 'success' | 'debug' | 'info';

export function stripLogAnsi(line: string): string {
  let clean = '';
  for (let index = 0; index < line.length; index++) {
    if (line.charCodeAt(index) !== 27 || line[index + 1] !== '[') {
      clean += line[index];
      continue;
    }
    index += 2;
    while (index < line.length) {
      const code = line.charCodeAt(index);
      if (code >= 0x40 && code <= 0x7e) break;
      index++;
    }
  }
  return clean;
}

export function logSeverity(line: string): LogSeverity {
  const value = stripLogAnsi(line);
  // Word-boundary anchored on purpose: render traces print "'failures': 0"
  // on every healthy generate, and a bare substring match on "failure"
  // classified each of those lines as an error — painting the Settings log
  // viewer red on success and polluting repair-agent problem reports.
  if (
    /(?:^|[\s[.:_-])(?:critical|fatal|error|exception|traceback|panic)(?:$|[\s\].:_-])/i.test(
      value,
    ) ||
    /\b(?:failed|failure|crashed|unexpectedly stopped|unhandled rejection)\b/i.test(value)
  ) {
    return 'error';
  }
  if (/(?:^|[\s[.:_-])(?:warn|warning)(?:$|[\s\].:_-])|deprecated|retrying/i.test(value)) {
    return 'warning';
  }
  if (/(?:ready|complete|completed|succeeded|healthy|loaded)\b/i.test(value)) return 'success';
  if (/(?:^|[\s[.:_-])(?:debug|trace)(?:$|[\s\].:_-])/i.test(value)) return 'debug';
  return 'info';
}
