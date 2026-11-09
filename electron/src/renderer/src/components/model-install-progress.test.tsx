import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import '@/i18n';
import { InstallProgress } from './model-install-progress';
import { describeInstall, terminalInstallView } from '@/lib/model-install-state';

const navigate = vi.hoisted(() => vi.fn());
vi.mock('@/router', () => ({ router: { navigate } }));
afterEach(cleanup);

const MB = 1024 * 1024;

it('names bytes, speed and remaining time while downloading', () => {
  const onCancel = vi.fn();
  render(
    <InstallProgress
      name="Whisper"
      view={describeInstall({
        repo_id: 'a/b',
        state: 'downloading',
        bytes_done: 512 * MB,
        total_bytes: 1024 * MB,
        rate: 8 * MB,
        eta_seconds: 64,
      })}
      onCancel={onCancel}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Whisper · Downloading · 50%');
  expect(screen.getByText(/512\.0 MB of 1\.00 GB · 8\.0 MB\/s · 2 min left/)).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(onCancel).toHaveBeenCalledOnce();
});

it('reads the stages before and after the bytes without a fake percentage', () => {
  const { rerender } = render(
    <InstallProgress view={describeInstall({ repo_id: 'a/b', state: 'downloading' })} />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Waiting to start…');
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
  rerender(
    <InstallProgress
      view={describeInstall({
        repo_id: 'a/b',
        state: 'downloading',
        bytes_done: 9,
        total_bytes: 9,
      })}
      onCancel={() => undefined}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Verifying and installing…');
  rerender(
    <InstallProgress
      view={describeInstall({ repo_id: 'a/b', state: 'cancelling', bytes_done: 3, total_bytes: 9 })}
      onCancel={() => undefined}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Cancelling…');
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
});

it('offers the exact next step, Retry and the fix location for a full disk', () => {
  const onRetry = vi.fn();
  render(
    <InstallProgress
      view={describeInstall({
        repo_id: 'a/b',
        state: 'failed',
        docs_topic: 'DISK_SPACE_LOW',
        error: 'OSError: [Errno 28] No space left on device',
      })}
      onRetry={onRetry}
    />,
  );
  expect(screen.getByRole('alert')).toHaveTextContent('Download failed');
  expect(
    screen.getByText(/Free some up or move the models folder in Settings → Storage/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(onRetry).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Storage' }));
  return vi.waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/settings/storage' }));
});

it('points a gated repository at credentials and waits out the retry cooldown', () => {
  render(
    <InstallProgress
      view={describeInstall({
        repo_id: 'a/b',
        state: 'failed',
        docs_topic: 'HF_AUTH_FAILED',
        retry_after_seconds: 12,
      })}
      onRetry={() => undefined}
    />,
  );
  expect(screen.getByText(/needs Hugging Face access/)).toHaveTextContent('Settings → Credentials');
  expect(screen.getByRole('button', { name: 'Retry in 12s' })).toBeDisabled();
});

it('keeps an unexplained failure to one line and a cancelled download calm', () => {
  const { rerender } = render(
    <InstallProgress
      view={terminalInstallView('failed', 'Connection reset by peer\n  at frame 1\n  at frame 2')}
      onRetry={() => undefined}
    />,
  );
  expect(screen.getByRole('alert')).toHaveTextContent('Download failed — Connection reset by peer');
  expect(screen.getByRole('alert')).not.toHaveTextContent('frame');
  const onRetry = vi.fn();
  rerender(<InstallProgress view={terminalInstallView('cancelled')} onRetry={onRetry} />);
  expect(screen.getByRole('status')).toHaveTextContent('Download cancelled');
  fireEvent.click(screen.getByRole('button', { name: 'Download again' }));
  expect(onRetry).toHaveBeenCalledOnce();
});

it('counts the retry cooldown down locally and then enables Retry', () => {
  vi.useFakeTimers();
  try {
    render(
      <InstallProgress
        view={describeInstall({ repo_id: 'a/b', state: 'failed', retry_after_seconds: 2 })}
        onRetry={() => undefined}
      />,
    );
    expect(screen.getByRole('button', { name: 'Retry in 2s' })).toBeDisabled();
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('button', { name: 'Retry in 1s' })).toBeDisabled();
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  } finally {
    vi.useRealTimers();
  }
});
