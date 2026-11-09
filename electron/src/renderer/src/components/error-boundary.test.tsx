import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { ErrorBoundary, ErrorRecovery } from './error-boundary';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./report-bug', () => ({ ReportBug: () => <button>Report</button> }));
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it('catches rendering failures and retries the existing child without resetting stored data', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  let fail = true;
  function Child() {
    if (fail) throw new Error('Failure at /home/private-user/runtime');
    return <div>Recovered draft</div>;
  }
  render(
    <ErrorBoundary>
      <Child />
    </ErrorBoundary>,
  );
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.getByText(/Failure at/).textContent).not.toContain('private-user');
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'errors.tryAgain' }));
  expect(screen.getByText('Recovered draft')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('links to the shared contextual docs while preserving an explicit error class', () => {
  render(
    <ErrorRecovery
      error={Object.assign(new Error('401 Unauthorized'), {
        errorClass: 'PYANNOTE_LICENSE_REQUIRED',
      })}
      reset={() => {}}
    />,
  );
  expect(screen.getByRole('link', { name: 'errors.openDocs' })).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio/blob/main/docs/features/diarization.md#license-acceptance-flow',
  );
});

it('hands a route failure to the repair dock for default-agent recovery', async () => {
  const listener = vi.fn();
  window.addEventListener('voicestudio:repair-agent-open', listener);
  render(<ErrorRecovery error={new Error('Failed to load Gallery')} reset={() => {}} />);
  await waitFor(() => expect(listener).toHaveBeenCalled());
  const event = listener.mock.calls[0]?.[0] as CustomEvent<{
    report: string;
    autoFix: boolean;
  }>;
  expect(event.detail.autoFix).toBe(true);
  expect(event.detail.report).toContain('Failed to load Gallery');
  window.removeEventListener('voicestudio:repair-agent-open', listener);
});

it('reloads a transient dynamic module once in Strict Mode, then asks an agent on repeat', async () => {
  vi.useFakeTimers();
  const listener = vi.fn();
  const reset = vi.fn();
  const reload = vi.fn();
  window.addEventListener('voicestudio:repair-agent-open', listener);
  render(
    <StrictMode>
      <ErrorRecovery
        error={new Error('Failed to fetch dynamically imported module: gallery-page.tsx')}
        reset={reset}
        reload={reload}
      />
    </StrictMode>,
  );
  await vi.advanceTimersByTimeAsync(250);
  expect(reload).toHaveBeenCalledOnce();
  expect(reset).not.toHaveBeenCalled();
  expect(listener).not.toHaveBeenCalled();
  cleanup();
  render(
    <ErrorRecovery
      error={new Error('Failed to fetch dynamically imported module: gallery-page.tsx')}
      reset={reset}
      reload={reload}
    />,
  );
  expect(listener).toHaveBeenCalledOnce();
  window.removeEventListener('voicestudio:repair-agent-open', listener);
});

it('Try Again keeps the renderer when the module recovers, and reloads when it replays', () => {
  const error = new Error('Failed to fetch dynamically imported module: setup-wizard.tsx');
  sessionStorage.setItem('voicestudio.moduleRetry:' + error.message, 'attempted');
  const reset = vi.fn();
  const reload = vi.fn();
  // Recovered: the boundary resets and nothing reloads.
  render(<ErrorRecovery error={error} reset={reset} reload={reload} />);
  fireEvent.click(screen.getByRole('button', { name: 'errors.tryAgain' }));
  expect(reset).toHaveBeenCalledOnce();
  cleanup();
  render(<ErrorRecovery error={new Error('Unrelated failure')} reset={reset} reload={reload} />);
  expect(reload).not.toHaveBeenCalled();
  cleanup();
  // Replayed: React.lazy's cached rejection comes straight back, so reload.
  render(<ErrorRecovery error={error} reset={reset} reload={reload} />);
  fireEvent.click(screen.getByRole('button', { name: 'errors.tryAgain' }));
  cleanup();
  render(<ErrorRecovery error={error} reset={reset} reload={reload} />);
  expect(reload).toHaveBeenCalledOnce();
});
