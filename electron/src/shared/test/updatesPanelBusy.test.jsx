/**
 * The Install button in Settings → Updates must be greyed out while work is
 * running — and, just as importantly, must NOT be greyed out otherwise.
 *
 * Installing relaunches the process, so a click during a synth or a dub throws
 * that work away. The click handler is the safety check (work can start
 * between the last render and the click), but the disabled state is what tells
 * the user why the button won't do anything.
 *
 * The failure mode this pins is the expensive one: a busy predicate that is
 * accidentally always true makes the app permanently un-updatable, which is
 * far worse than the bug it was added to fix.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const { storeState, installUpdate } = vi.hoisted(() => ({
  storeState: {},
  installUpdate: vi.fn(),
}));

const BASE = {
  updateStatus: 'available',
  updateVersion: '0.4.2',
  updateNotes: null,
  updateError: null,
  updateProgress: 0,
  appVersion: '0.4.1',
  updateChannel: 'stable',
  releases: [],
  releasesStatus: 'idle',
  loadReleases: vi.fn(),
  dismissUpdate: vi.fn(),
  setWhatsNewSeenVersion: vi.fn(),
  dubStep: 'idle',
  stage: 'idle',
  ttsInflight: 0,
};

vi.mock('../store', () => ({
  useAppStore: Object.assign((sel) => sel(storeState), { getState: () => storeState }),
}));
vi.mock('../utils/updater', () => ({
  installUpdate: (...a) => installUpdate(...a),
  checkForUpdate: vi.fn(),
}));
vi.mock('../utils/updatesApi', () => ({
  fetchChangelog: () => Promise.resolve([]),
  fetchBackupState: () => Promise.resolve(null),
}));
vi.mock('../utils/channelControl', () => ({ setChannel: vi.fn() }));
vi.mock('../utils/updatePresentation', () => ({ prepareReleases: () => [] }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k) => k }) }));
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn() }) }));

import UpdatesPanel from '../components/UpdatesPanel';

const renderWith = (over) => {
  Object.keys(storeState).forEach((k) => delete storeState[k]);
  Object.assign(storeState, BASE, over);
  return render(<UpdatesPanel />);
};

beforeEach(() => installUpdate.mockClear());

describe('UpdatesPanel install button', () => {
  it('is enabled when nothing is running', () => {
    renderWith({});
    expect(screen.getByRole('button', { name: /update\.install/ })).not.toBeDisabled();
  });

  it.each([
    ['a dub upload', { dubStep: 'uploading' }],
    ['a translation', { stage: 'translating' }],
    ['a synth', { ttsInflight: 1 }],
    ['overlapping synths', { ttsInflight: 2 }],
  ])('is disabled during %s', (_label, over) => {
    renderWith(over);
    expect(screen.getByRole('button', { name: /update\.install/ })).toBeDisabled();
  });

  it('disables the restart button too, not just install', () => {
    renderWith({ updateStatus: 'ready', ttsInflight: 1 });
    expect(screen.getByRole('button', { name: /update\.restart/ })).toBeDisabled();
  });

  it('disables retry after an update error while work is running', () => {
    renderWith({ updateStatus: 'error', updateError: 'failed', ttsInflight: 1 });
    expect(screen.getByRole('button', { name: /update\.retry/ })).toBeDisabled();
  });

  it('an open transcript does not block updating', () => {
    // 'editing' waits on the user; treating it as busy would make the app
    // un-updatable for as long as a dub tab stays open.
    renderWith({ dubStep: 'editing' });
    expect(screen.getByRole('button', { name: /update\.install/ })).not.toBeDisabled();
  });
});
