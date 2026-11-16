import { describe, expect, it } from 'vitest';
import { isBenignWindowError } from './foreignErrors';

const MESSAGE = "Cannot read properties of undefined (reading 'M_ID')";
const withStack = (stack) => Object.assign(new TypeError(MESSAGE), { stack });

describe('isBenignWindowError extension origin', () => {
  it('ignores a Chromium extension error with no filename (#2148)', () => {
    const error = withStack(
      `TypeError: ${MESSAGE}\n` +
        '    at Y (chrome-extension://eppiocemhmnlbhjplcgkofciiegomcon/executors/200.js:1:761)\n' +
        '    at E (chrome-extension://eppiocemhmnlbhjplcgkofciiegomcon/executors/200.js:1:1442)',
    );
    expect(isBenignWindowError(MESSAGE, error, '')).toBe(true);
  });

  it.each(['global code', 'eval code', 'module code'])(
    'ignores a WebKit extension error thrown from a "%s" frame',
    (label) => {
      const error = withStack(`${label}@safari-web-extension://abc/executors/200.js:1:761`);
      expect(isBenignWindowError(MESSAGE, error, '')).toBe(true);
    },
  );

  it('ignores a named WebKit extension frame', () => {
    const error = withStack('Y@safari-web-extension://abc/executors/200.js:1:761');
    expect(isBenignWindowError(MESSAGE, error, '')).toBe(true);
  });

  it('keeps an app error whose message quotes an extension URL', () => {
    const message = 'failed user@chrome-extension://abc/x';
    const error = withStack(`Error: ${message}\n    at run (http://localhost/app.js:1:1)`);
    expect(isBenignWindowError(message, error, '')).toBe(false);
  });

  it('keeps an app error thrown from a WebKit top-level frame', () => {
    const error = withStack('global code@http://localhost/app.js:1:1');
    expect(isBenignWindowError(MESSAGE, error, '')).toBe(false);
  });
});
