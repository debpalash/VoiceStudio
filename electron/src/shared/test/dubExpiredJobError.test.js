import { describe, it, expect } from 'vitest';
import { isExpiredDubJobError } from '../hooks/useDubWorkflow.js';
import dubWorkflowSrc from '../hooks/useDubWorkflow.js?raw';

describe('isExpiredDubJobError (#660 — stale dub session)', () => {
  it('matches the backend dub_core preflight message', () => {
    expect(
      isExpiredDubJobError(
        new Error('Job not found. It may have been cleaned up or was never created.'),
      ),
    ).toBe(true);
  });

  it('matches the dub_generate expired-session message', () => {
    expect(
      isExpiredDubJobError(
        new Error(
          'This dub session has expired or was never created. Re-upload the video to start a new one.',
        ),
      ),
    ).toBe(true);
  });

  it('matches a bare 404 "Job not found"', () => {
    expect(isExpiredDubJobError(new Error('Job not found'))).toBe(true);
  });

  it('does NOT match unrelated transcription failures (those stay reportable)', () => {
    expect(
      isExpiredDubJobError(new Error('Transcribe stream dropped before emitting any segments.')),
    ).toBe(false);
    expect(isExpiredDubJobError(new Error('CUDA out of memory'))).toBe(false);
    expect(isExpiredDubJobError(new Error('aborted'))).toBe(false);
  });

  it('is null/shape safe', () => {
    expect(isExpiredDubJobError(null)).toBe(false);
    expect(isExpiredDubJobError(undefined)).toBe(false);
    expect(isExpiredDubJobError({})).toBe(false);
  });
});

describe('#695 — every dub handler resets on a stale job (regression of #660)', () => {
  const src = dubWorkflowSrc;

  it('all four catch handlers route a stale-job error through the predicate, not a bug-report toast', () => {
    // upload, ingest, retry, import — #660 only wired retry+import, leaving the
    // two initial handlers to surface the scary "report a bug" toast (#695).
    const matches = src.match(/isExpiredDubJobError\(err\)/g) || [];
    expect(matches.length).toBeGreaterThanOrEqual(4);
  });

  it('the initial upload + ingest handlers check the predicate BEFORE toastErrorWithReport', () => {
    for (const fn of ['handleDubUpload', 'handleDubIngestUrl']) {
      const start = src.indexOf(`const ${fn} =`);
      expect(start, `${fn} should exist`).toBeGreaterThan(-1);
      // Window sized to span the whole catch chain (grew with the
      // asr_model_missing branch — keep it comfortably ahead of the handlers).
      const body = src.slice(start, start + 4000);
      const guardIdx = body.indexOf('isExpiredDubJobError(err)');
      const reportIdx = body.indexOf('toastErrorWithReport');
      expect(guardIdx, `${fn} must handle a stale job`).toBeGreaterThan(-1);
      expect(reportIdx, `${fn} should still report real failures`).toBeGreaterThan(-1);
      // graceful stale-job reset must come before the reportable fallback
      expect(guardIdx).toBeLessThan(reportIdx);
    }
  });
});
