import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import UpdatesPanel from './UpdatesPanel';
import { useAppStore } from '../store';

// Local-first backend data (changelog + backup state) — controllable per test.
const mockChangelog = vi.fn();
const mockBackup = vi.fn();
vi.mock('../utils/updatesApi', () => ({
  fetchChangelog: (...a) => mockChangelog(...a),
  fetchBackupState: (...a) => mockBackup(...a),
}));

const CHANGELOG = [
  {
    version: '0.3.9',
    date: '2026-07-02',
    intro: 'Headline.',
    sections: [{ title: 'Fixed', bullets: ['**A fix.** Done. (#1)'] }],
  },
];

beforeEach(() => {
  mockChangelog.mockResolvedValue([]);
  mockBackup.mockResolvedValue({ available: false, latest: null });
  const s = useAppStore.getState();
  s.dismissUpdate();
  s.setAppVersion('0.3.9');
  s.setWhatsNewSeenVersion(null);
});

describe('UpdatesPanel — data-safety line (pre-update DB backups)', () => {
  it('shows the backup promise with the fallback when no backup exists yet', async () => {
    render(<UpdatesPanel />);
    const line = await screen.findByTestId('backup-line');
    expect(line).toHaveTextContent('Your data is backed up before every update.');
    expect(line).toHaveTextContent('The first backup is created automatically');
  });

  it('shows the latest backup timestamp from the backend endpoint', async () => {
    const created = new Date('2026-07-02T10:00:00Z').getTime() / 1000;
    mockBackup.mockResolvedValue({
      available: true,
      latest: { path: '/data/omnivoice.db.backup-0.3.9-1', created_at: created, size_bytes: 42 },
    });
    render(<UpdatesPanel />);
    await waitFor(() =>
      expect(screen.getByTestId('backup-line')).toHaveTextContent('Latest backup:'),
    );
    expect(screen.getByTestId('backup-line')).toHaveTextContent(
      new Date(created * 1000).toLocaleString(),
    );
  });
});

describe("UpdatesPanel — available update's release notes", () => {
  it('renders the updater metadata notes as safe markdown-lite', async () => {
    useAppStore
      .getState()
      .setUpdateAvailable('0.4.0', '### Fixed\n- **Big fix.** No more bug. (#42)');
    render(<UpdatesPanel />);
    const notes = await screen.findByTestId('update-notes');
    expect(notes).toHaveTextContent('Release notes — v0.4.0');
    const strong = notes.querySelector('strong');
    expect(strong).not.toBeNull();
    expect(strong.textContent).toBe('Big fix.');
    expect(notes.textContent).not.toContain('**');
    expect(notes).toHaveTextContent('(#42)');
  });

  it('renders no notes block when up to date', async () => {
    render(<UpdatesPanel />);
    await screen.findByTestId('backup-line');
    expect(screen.queryByTestId('update-notes')).not.toBeInTheDocument();
  });
});

describe('UpdatesPanel — "What\'s new" changelog reader', () => {
  it('renders the shipped changelog via the accordion viewer', async () => {
    mockChangelog.mockResolvedValue(CHANGELOG);
    render(<UpdatesPanel />);
    const viewer = await screen.findByTestId('changelog-viewer');
    expect(viewer).toHaveTextContent('v0.3.9');
    expect(screen.getByTestId('changelog-body-0.3.9')).toHaveTextContent('Headline.');
    expect(mockChangelog).toHaveBeenCalledWith(5);
  });

  it('hides the section when the changelog is unavailable', async () => {
    render(<UpdatesPanel />);
    await screen.findByTestId('backup-line');
    expect(screen.queryByTestId('changelog-viewer')).not.toBeInTheDocument();
  });

  it('marks the running version as seen (retires the footer pill)', async () => {
    render(<UpdatesPanel />);
    await waitFor(() => expect(useAppStore.getState().whatsNewSeenVersion).toBe('0.3.9'));
  });
});

describe('UpdatesPanel — channel switcher stays surfaced', () => {
  it('shows both channels with the current one checked', async () => {
    render(<UpdatesPanel />);
    await screen.findByTestId('backup-line');
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    const checked = radios.filter((r) => r.getAttribute('aria-checked') === 'true');
    expect(checked).toHaveLength(1);
  });
});
