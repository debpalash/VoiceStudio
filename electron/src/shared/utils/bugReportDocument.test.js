/**
 * The shared bug-report document builder.
 *
 * `composeBugReportUrl` is what the Electron app files reports with
 * (`electron/src/renderer/src/components/report-bug.tsx`), while the Tauri app
 * uses `bugReport.js::openBugReport`. The two build their own `## Error`
 * sections, and only the Tauri one carried the backend error class — so #1800's
 * fix was lost in the app that ships as of v0.5.4.
 *
 * #1800: an unclassified backend failure renders one fixed floor message, so a
 * dozen unrelated faults arrive as byte-identical reports. The exception class
 * name is the only thing separating them. Issue #2177 is what that looks like
 * WITH the line present (v0.5.3, Tauri); an Electron report of the same failure
 * carries the message and a stack of minified bundle frames and nothing else.
 */
import { describe, expect, it } from 'vitest';
import { composeBugReportUrl } from './bugReportDocument';

const bodyOf = (options) => decodeURIComponent(composeBugReportUrl(options));

describe('composeBugReportUrl', () => {
  it('records the backend error class so identical messages differ (#1800)', () => {
    const error = new Error('Generation failed. Check the selected engine and try again.');
    error.errorClass = 'MemoryError';
    expect(bodyOf({ error })).toContain('Backend error class: MemoryError');
  });

  it('reads the class from an ApiError payload', () => {
    // The Electron client keeps the parsed 500 body on `payload` rather than
    // lifting `error_class` onto the error itself (lib/api/client.ts).
    const error = new Error('VoiceStudio hit an internal error');
    error.payload = { detail: 'VoiceStudio hit an internal error', error_class: 'RuntimeError' };
    expect(bodyOf({ error })).toContain('Backend error class: RuntimeError');
  });

  it('prefers an explicit errorClass over the payload', () => {
    const error = new Error('boom');
    error.errorClass = 'ValueError';
    error.payload = { error_class: 'RuntimeError' };
    expect(bodyOf({ error })).toContain('Backend error class: ValueError');
    expect(bodyOf({ error })).not.toContain('RuntimeError');
  });

  it('omits the class line when the failure carries none', () => {
    expect(bodyOf({ error: new Error('plain failure') })).not.toContain('Backend error class');
  });

  it('ignores a non-string class instead of stringifying an object', () => {
    const error = new Error('boom');
    error.payload = { error_class: { name: 'RuntimeError' } };
    expect(bodyOf({ error })).not.toContain('Backend error class');
  });

  it('scrubs the class the way every other reported field is scrubbed', () => {
    const error = new Error('load failed');
    error.errorClass = 'Err/Users/alice/secret';
    const body = bodyOf({ error });
    expect(body).not.toContain('/Users/alice');
  });

  // ── the surrounding contract must not move ────────────────────────────────

  it('still renders the error section and scrubs the message', () => {
    const body = bodyOf({ error: new Error('cannot open /Users/alice/voice.wav') });
    expect(body).toContain('## Error');
    expect(body).toContain('cannot open ~/voice.wav');
    expect(body).not.toContain('/Users/alice');
  });

  it('still seeds the title with the error message', () => {
    expect(bodyOf({ error: new Error('synthesis exploded') })).toContain(
      '[Bug] synthesis exploded',
    );
  });

  it('builds a report with no error at all', () => {
    const body = bodyOf({ ctx: '**Version:** `0.5.4`' });
    expect(body).not.toContain('## Error');
    expect(body).toContain('## Environment');
  });
});
