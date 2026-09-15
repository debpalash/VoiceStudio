import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { TranslationAgentDock } from './translation-agent-dock';
import { translationActivity, startTranslationRun, finishTranslationRun, updateTranslationRun } from '@/features/dub/translation-activity';
import { cancelDub } from '@/features/dub/dub-session';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/features/dub/dub-session', () => ({
  cancelDub: vi.fn(),
  useDubSession: () => ({ jobId: 'job', phase: 'editing', recovery: null }),
}));
beforeEach(() => { translationActivity.setState(() => ({ runs: [], expanded: true, tab: 'output' })); vi.clearAllMocks(); });
afterEach(cleanup);
const request = { jobId: 'job', agent: 'codex', target: 'Bengali', purpose: 'translate' as const, rows: [{ id: 'a', source: 'Hello' }] };
it('collapses running work without hiding cancel or allowing dismissal', () => {
  startTranslationRun(request);
  render(<TranslationAgentDock />);
  fireEvent.click(screen.getByRole('button', { name: 'common.details' }));
  expect(screen.queryByRole('tabpanel')).toBeNull();
  expect(screen.queryByRole('button', { name: 'common.close' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
  expect(cancelDub).toHaveBeenCalledOnce();
});
it('shows original and validated translation after completion until dismissed', () => {
  const id = startTranslationRun(request);
  updateTranslationRun(id, { rows: [{ id: 'a', source: 'Hello', text: 'হ্যালো' }] });
  finishTranslationRun(id, 'complete');
  render(<TranslationAgentDock />);
  fireEvent.click(screen.getByRole('tab', { name: 'dubActivity.output' }));
  expect(screen.getByText('Hello')).toBeVisible();
  expect(screen.getByText('হ্যালো')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'common.close' }));
  expect(translationActivity.state.runs).toHaveLength(0);
});
it('retries failed work only in its original project', () => {
  const retry = vi.fn().mockResolvedValue(true);
  const id = startTranslationRun({ ...request, retry });
  finishTranslationRun(id, 'failed', 'Agent failed');
  render(<TranslationAgentDock />);
  fireEvent.click(screen.getByRole('button', { name: /common.retry/ }));
  expect(retry).toHaveBeenCalledOnce();
});
