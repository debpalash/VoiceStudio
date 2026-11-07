import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  acknowledged: false,
  acknowledge: vi.fn().mockResolvedValue(undefined),
  lastCrash: {
    timestamp: 1_700_000_000_000,
    version: '0.5.2',
    exitCode: 1,
    signal: null,
    uptimeMs: 5000,
    logTail: ['RuntimeError: stopped'],
  },
}));

vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({
    lastCrash: { ...mocks.lastCrash, acknowledged: mocks.acknowledged },
  }),
}));
vi.mock('./bridge', () => ({
  getBridge: () => ({ backend: { acknowledgeCrash: mocks.acknowledge } }),
}));
vi.mock('./report-bug', () => ({ ReportBug: () => null }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { count?: number }) =>
      values?.count === undefined ? key : `${key}:${values.count}`,
  }),
}));

import { CrashDetails } from './crash-details';

beforeEach(() => {
  mocks.acknowledged = false;
  mocks.acknowledge.mockClear();
  mocks.lastCrash = {
    timestamp: 1_700_000_000_000,
    version: '0.5.2',
    exitCode: 1,
    signal: null,
    uptimeMs: 5000,
    logTail: ['RuntimeError: stopped'],
  };
});
afterEach(cleanup);

it('acknowledges retained native crash evidence when the user views it', async () => {
  render(<CrashDetails />);
  fireEvent.click(screen.getByText('crash.view'));
  await waitFor(() => expect(mocks.acknowledge).toHaveBeenCalledOnce());
});

it('does not rewrite an already acknowledged crash record', async () => {
  mocks.acknowledged = true;
  render(<CrashDetails />);
  fireEvent.click(screen.getByText('crash.view'));
  await Promise.resolve();
  expect(mocks.acknowledge).not.toHaveBeenCalled();
});

it('explains an unattributed 0xFFFFFFFF exit instead of naming a cause', () => {
  mocks.lastCrash = {
    ...mocks.lastCrash,
    exitCode: 4294967295, // unsigned 0xFFFFFFFF
    signal: null,
    logTail: ['INFO generating audio'],
  };
  render(<CrashDetails />);
  expect(screen.getByText('crash.exit_unattributed')).toBeInTheDocument();
});

it('does not offer the unattributed hint when the tail carries a traceback', () => {
  mocks.lastCrash = {
    ...mocks.lastCrash,
    exitCode: -1, // signed 0xFFFFFFFF
    signal: null,
    logTail: ['Traceback (most recent call last):', 'RuntimeError: boom'],
  };
  render(<CrashDetails />);
  expect(screen.queryByText('crash.exit_unattributed')).not.toBeInTheDocument();
});
