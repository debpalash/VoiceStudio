import { beforeEach, expect, it } from 'vitest';
import { appendTranslationLog, finishTranslationRun, startTranslationRun, translationActivity, updateTranslationRun } from './translation-activity';

beforeEach(() => translationActivity.setState(() => ({ runs: [], expanded: false, tab: 'output' })));
const request = { jobId: 'job', agent: 'codex', target: 'Bengali', purpose: 'translate' as const, rows: [{ id: 'a', source: 'Hello' }] };
it('opens logs without inventing completed segments, then retains validated output', () => {
  const id = startTranslationRun(request);
  appendTranslationLog(id, 'Working…');
  expect(translationActivity.state.expanded).toBe(true);
  expect(translationActivity.state.tab).toBe('logs');
  expect(translationActivity.state.runs[0].rows[0].text).toBeUndefined();
  updateTranslationRun(id, { rows: [{ id: 'a', source: 'Hello', text: 'হ্যালো' }] });
  finishTranslationRun(id, 'complete');
  expect(translationActivity.state.runs[0].rows[0].text).toBe('হ্যালো');
  expect(translationActivity.state.runs[0].endedAt).toBeDefined();
});
it('isolates run logs and ignores late output after cancellation', () => {
  const first = startTranslationRun(request);
  finishTranslationRun(first, 'cancelled');
  const second = startTranslationRun({ ...request, target: 'Spanish' });
  appendTranslationLog(first, 'late output');
  appendTranslationLog(second, 'current output');
  expect(translationActivity.state.runs.map((r) => r.logs)).toEqual(['', 'current output']);
});
it('bounds log memory while preserving separate batch language results', () => {
  const first = startTranslationRun(request);
  appendTranslationLog(first, 'x'.repeat(300_000));
  finishTranslationRun(first, 'complete');
  startTranslationRun({ ...request, target: 'Spanish' });
  expect(translationActivity.state.runs).toHaveLength(2);
  expect(translationActivity.state.runs[0].logs).toHaveLength(250_000);
});
