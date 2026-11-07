import { describe, expect, it } from 'vitest';
import { logSeverity, stripLogAnsi } from './log-format';

describe('log formatting', () => {
  it.each([
    ['2026-09-12 ERROR backend crashed', 'error'],
    ['Traceback (most recent call last):', 'error'],
    ['WARNING model is retrying', 'warning'],
    ['INFO Application startup complete.', 'success'],
    ['DEBUG request headers', 'debug'],
    ['INFO GET /health 200', 'info'],
  ])('classifies %s as %s', (line, severity) => {
    expect(logSeverity(line)).toBe(severity);
  });

  it('removes terminal color escapes before display', () => {
    expect(stripLogAnsi('\u001B[31mERROR\u001B[0m failed')).toBe('ERROR failed');
  });

  // Real lines from omnivoice.log. The render trace reports zero failures on
  // every healthy generate; "'failures': 0" must not read as an error just
  // because "failures" contains the substring "failure".
  describe('render trace lines', () => {
    const healthy =
      "2026-10-18 05:22:11,320 INFO [omnivoice.render] render trace {'id': 'a4bb67ae44ea448f', 'surface': 'generate', 'total_seconds': 2.454943, 'stages': {'synthesis': {'calls': 1, 'seconds': 2.302946, 'failures': 0}, 'effects': {'calls': 1, 'seconds': 0.018976, 'failures': 0}, 'watermark': {'calls': 1, 'seconds': 0.061913, 'failures': 0}}, 'outcome': 'complete'}";
    const failedTrace =
      "2026-10-18 04:19:37,963 INFO [omnivoice.render] render trace {'id': '2800cddce7ff4b45', 'surface': 'generate', 'total_seconds': 154.176929, 'stages': {}, 'outcome': 'failed'}";

    it('classifies a completed trace with zero failures as success, not error', () => {
      expect(logSeverity(healthy)).toBe('success');
    });

    it('still classifies a failed trace as error', () => {
      expect(logSeverity(failedTrace)).toBe('error');
    });

    it('does not read a zero failure count as an error', () => {
      expect(logSeverity("INFO healthcheck {'failures': 0}")).toBe('info');
    });

    it('still flags the standalone word failure', () => {
      expect(logSeverity('ERROR batch aborted after failure')).toBe('error');
    });
  });
});
