import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UpdateState } from '../../../../preload/index.d';

const mocks = vi.hoisted(() => ({
  state: null as UpdateState | null,
  listener: undefined as ((state: UpdateState) => void) | undefined,
  check: vi.fn(),
  download: vi.fn(),
  dismiss: vi.fn(),
  later: vi.fn(),
  install: vi.fn(),
  openExternal: vi.fn(),
  flushDubDraft: vi.fn(),
  api: vi.fn(),
}));

vi.mock('@/components/bridge', () => {
  const bridge = {
    updates: {
      getState: async () => mocks.state,
      check: mocks.check,
      download: mocks.download,
      dismiss: mocks.dismiss,
      later: mocks.later,
      install: mocks.install,
      onState: (listener: (state: UpdateState) => void) => {
        mocks.listener = listener;
        return () => {
          mocks.listener = undefined;
        };
      },
    },
    files: { openExternal: mocks.openExternal },
  };
  return { getBridge: () => bridge };
});
vi.mock('@/features/dub/dub-session', () => ({
  dubSession: { state: { phase: 'idle', recovery: null, batchProgress: null } },
  flushDubDraft: mocks.flushDubDraft,
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  ApiError: class ApiError extends Error {},
}));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), info: vi.fn() } }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values && 'pct' in values ? `${key} ${values.pct}` : key,
  }),
}));

import { capNotes, UpdatePill, updatePillVisible } from './update-pill';
import { UpdateRestartConfirm } from '@/components/update-restart-confirm';
import { beginAppActivity } from '@/lib/app-activity';

const base: UpdateState = {
  status: 'idle',
  currentVersion: '0.5.2',
  progress: 0,
  autoDownload: true,
  installOnQuit: true,
};

function renderPill(state: Partial<UpdateState>) {
  mocks.state = { ...base, ...state };
  return render(
    <>
      <UpdatePill />
      <UpdateRestartConfirm />
    </>,
  );
}

const pill = () => document.querySelector<HTMLElement>('[data-slot="update-pill"]');

describe('UpdatePill', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listener = undefined;
    mocks.check.mockResolvedValue({ ...base, status: 'available', availableVersion: '0.5.3' });
    mocks.download.mockResolvedValue(base);
    mocks.dismiss.mockResolvedValue(base);
    mocks.later.mockResolvedValue(base);
    mocks.install.mockResolvedValue(undefined);
    mocks.openExternal.mockResolvedValue(undefined);
    mocks.api.mockImplementation(async (path: string) =>
      path.includes('/batch/') ? [] : { jobs: [] },
    );
  });
  afterEach(cleanup);

  test('stays out of the title bar while idle, checking or after "Later"', () => {
    expect(updatePillVisible({ ...base })).toBe(false);
    expect(updatePillVisible({ ...base, status: 'checking' })).toBe(false);
    expect(updatePillVisible({ ...base, status: 'unsupported' })).toBe(false);
    expect(
      updatePillVisible({
        ...base,
        status: 'downloaded',
        availableVersion: '0.5.3',
        dismissedVersion: '0.5.3',
      }),
    ).toBe(false);
    // A newer version than the dismissed one shows again.
    expect(
      updatePillVisible({
        ...base,
        status: 'downloaded',
        availableVersion: '0.5.4',
        dismissedVersion: '0.5.3',
      }),
    ).toBe(true);
  });

  test('shows a progress ring and percent while downloading', async () => {
    renderPill({ status: 'downloading', availableVersion: '0.5.3', progress: 42.4 });
    await waitFor(() => expect(pill()).not.toBeNull());
    expect(pill()).toHaveTextContent('42%');
    expect(pill()!.querySelector('svg circle')).not.toBeNull();
  });

  test('ready: Restart restarts at once when nothing is running', async () => {
    renderPill({
      status: 'downloaded',
      availableVersion: '0.5.3',
      progress: 100,
      notes: '### Fixed\n- Faster dubbing',
    });
    await waitFor(() => expect(pill()).toHaveTextContent('update.pill_restart'));
    fireEvent.click(pill()!);
    expect(await screen.findByText('v0.5.2 → v0.5.3')).toBeInTheDocument();
    expect(screen.getByText('update.ready_hint_quit')).toBeInTheDocument();
    expect(screen.getByText('Faster dubbing')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /update\.pill_restart/ }));
    await waitFor(() => expect(mocks.install).toHaveBeenCalledOnce());
    expect(mocks.flushDubDraft).toHaveBeenCalledOnce();
  });

  test('ready: Restart asks first while work is running', async () => {
    const release = beginAppActivity('batch');
    try {
      renderPill({ status: 'downloaded', availableVersion: '0.5.3', progress: 100 });
      await waitFor(() => expect(pill()).not.toBeNull());
      fireEvent.click(pill()!);
      fireEvent.click(await screen.findByRole('button', { name: /update\.pill_restart/ }));
      expect(await screen.findByText('update.restart_confirm')).toBeInTheDocument();
      expect(mocks.install).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'update.restart_anyway' }));
      await waitFor(() => expect(mocks.install).toHaveBeenCalledOnce());
    } finally {
      release();
    }
  });

  test('ready: Later dismisses this version', async () => {
    renderPill({ status: 'downloaded', availableVersion: '0.5.3', progress: 100 });
    await waitFor(() => expect(pill()).not.toBeNull());
    fireEvent.click(pill()!);
    fireEvent.click(await screen.findByRole('button', { name: 'update.toast_later' }));
    await waitFor(() => expect(mocks.later).toHaveBeenCalledOnce());
    act(() =>
      mocks.listener?.({
        ...base,
        status: 'downloaded',
        availableVersion: '0.5.3',
        dismissedVersion: '0.5.3',
      }),
    );
    await waitFor(() => expect(pill()).toBeNull());
  });

  test('failed: Retry re-checks and Download manually opens the release page', async () => {
    renderPill({
      status: 'error',
      availableVersion: '0.5.3',
      errorKind: 'verify',
      error: 'sha512 checksum mismatch',
    });
    await waitFor(() => expect(pill()).toHaveTextContent('update.retry'));
    fireEvent.click(pill()!);
    expect(await screen.findByText('update.error_verify')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'update.download_manually' }));
    await waitFor(() =>
      expect(mocks.openExternal).toHaveBeenCalledWith(
        'https://github.com/debpalash/VoiceStudio/releases/tag/v0.5.3',
      ),
    );
    fireEvent.click(pill()!);
    fireEvent.click(await screen.findByRole('button', { name: 'update.retry' }));
    await waitFor(() => expect(mocks.check).toHaveBeenCalledOnce());
    expect(mocks.download).toHaveBeenCalledOnce();
  });

  test('failed: Later clears the error', async () => {
    renderPill({ status: 'error', errorKind: 'check', error: 'offline' });
    await waitFor(() => expect(pill()).not.toBeNull());
    fireEvent.click(pill()!);
    fireEvent.click(await screen.findByRole('button', { name: 'update.toast_later' }));
    await waitFor(() => expect(mocks.dismiss).toHaveBeenCalledOnce());
    expect(mocks.later).not.toHaveBeenCalled();
  });

  test('an update that did not apply says so', async () => {
    renderPill({ status: 'error', errorKind: 'not-applied', availableVersion: '0.5.3' });
    await waitFor(() => expect(pill()).not.toBeNull());
    fireEvent.click(pill()!);
    expect(await screen.findByText('update.error_not_applied')).toBeInTheDocument();
  });

  test('available (automatic downloads off): Download starts it', async () => {
    renderPill({ status: 'available', availableVersion: '0.5.3', autoDownload: false });
    await waitFor(() => expect(pill()).toHaveTextContent('update.pill_update'));
    fireEvent.click(pill()!);
    fireEvent.click(await screen.findByRole('button', { name: /update\.download$/ }));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledOnce());
  });

  test('caps long release notes at a line break', () => {
    const notes = Array.from({ length: 200 }, (_, index) => `- change ${index}`).join('\n');
    const capped = capNotes(notes, 100);
    expect(capped.length).toBeLessThan(110);
    expect(capped.endsWith('…')).toBe(true);
    expect(capNotes('short')).toBe('short');
  });
});
