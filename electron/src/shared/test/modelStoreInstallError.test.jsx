import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import i18n from '../i18n';
import {
  reduceModelDownloadEvent,
  isAutoPurgeTerminal,
  isTerminalPhase,
  downloadKey,
} from '../components/settings/models/downloadReducer';
import { makeModelColumns } from '../components/settings/models/columns';

const t = i18n.t.bind(i18n);
const REPO = 'org/model';
const KEY = downloadKey('local', REPO);

// ── P1-A: async install errors must be visible, not silently purged ─────────

describe('reduceModelDownloadEvent — install_error persistence (P1-A)', () => {
  it('stores install_error with its mirror-aware message (#890 core/failure.py)', () => {
    const s = reduceModelDownloadEvent(
      {},
      { repo_id: REPO, phase: 'install_error', error: 'boom — mirror hf-mirror.com unreachable' },
    );
    expect(s[KEY].phase).toBe('install_error');
    expect(s[KEY].error).toBe('boom — mirror hf-mirror.com unreachable');
  });

  it('the error survives later unrelated events (never gets wiped)', () => {
    let s = reduceModelDownloadEvent({}, { repo_id: REPO, phase: 'install_start' });
    s = reduceModelDownloadEvent(s, { repo_id: REPO, phase: 'install_error', error: 'disk full' });
    // A stray aggregate for ANOTHER repo must not touch this errored row.
    s = reduceModelDownloadEvent(s, {
      repo_id: 'other/x',
      phase: 'aggregate',
      bytes_done: 1,
      total_bytes: 2,
    });
    expect(s[KEY].phase).toBe('install_error');
    expect(s[KEY].error).toBe('disk full');
  });

  it('ignores keepalive events without a repo_id', () => {
    const prev = { [REPO]: { phase: 'install_error', error: 'x' } };
    expect(reduceModelDownloadEvent(prev, {})).toBe(prev);
  });
});

it('keeps simultaneous local and remote downloads of one repo separate', () => {
  let s = reduceModelDownloadEvent(
    {},
    {
      target: 'local',
      repo_id: REPO,
      phase: 'aggregate',
      bytes_done: 1,
      total_bytes: 10,
    },
  );
  s = reduceModelDownloadEvent(s, {
    target: 'gpu2',
    repo_id: REPO,
    phase: 'aggregate',
    bytes_done: 7,
    total_bytes: 10,
  });
  expect(s[downloadKey('local', REPO)].agg.bytes_done).toBe(1);
  expect(s[downloadKey('gpu2', REPO)].agg.bytes_done).toBe(7);
});

describe('isAutoPurgeTerminal — only success terminals auto-purge (P1-A)', () => {
  it('purges success terminals but NOT install_error', () => {
    expect(isAutoPurgeTerminal('install_done')).toBe(true);
    expect(isAutoPurgeTerminal('delete_done')).toBe(true);
    expect(isAutoPurgeTerminal('install_cancelled')).toBe(true);
    expect(isAutoPurgeTerminal('install_error')).toBe(false);
    expect(isAutoPurgeTerminal('active')).toBe(false);
  });

  it('isTerminalPhase counts install_error (so the list still refetches)', () => {
    expect(isTerminalPhase('install_error')).toBe(true);
    expect(isTerminalPhase('install_done')).toBe(true);
    expect(isTerminalPhase('active')).toBe(false);
  });
});

function renderCell(colId, rt, handlers, mOver = {}) {
  const cols = makeModelColumns({
    t,
    getRowRuntime: () => rt,
    speedRef: { current: {} },
    MODEL_ROLE_LABEL: {},
    onInstall: vi.fn(),
    onDelete: vi.fn(),
    onReinstall: vi.fn(),
    onCancel: vi.fn(),
    onDismissError: vi.fn(),
    ...handlers,
  });
  const col = cols.find((c) => c.id === colId);
  const m = { repo_id: REPO, label: 'My Model', role: 'tts', installed: false, ...mOver };
  return render(col.cell({ row: { original: m } }));
}

describe('Model Store row — install_error renders inline with Retry + Dismiss (P1-A)', () => {
  const rt = {
    phase: 'install_error',
    rs: { error: 'Not enough disk space to install' },
    showBar: false,
    isDeleting: false,
    isInstalling: false,
    rowBusy: false,
    unsupported: false,
  };

  it('renders the persisted error message on the row', () => {
    renderCell('name', rt);
    expect(screen.getByText(/Not enough disk space to install/)).toBeInTheDocument();
  });

  it('Retry fires onInstall(repo_id)', () => {
    const onInstall = vi.fn();
    renderCell('name', rt, { onInstall });
    fireEvent.click(screen.getByRole('button', { name: t('models.retry_btn') }));
    expect(onInstall).toHaveBeenCalledWith(REPO);
  });

  it('Dismiss fires onDismissError(repo_id)', () => {
    const onDismissError = vi.fn();
    renderCell('name', rt, { onDismissError });
    fireEvent.click(screen.getByRole('button', { name: t('common.dismiss') }));
    expect(onDismissError).toHaveBeenCalledWith(REPO);
  });
});

// ── P2-A: wire the orphaned cancel endpoint ─────────────────────────────────

describe('Model Store row — Cancel wires the in-flight install (P2-A)', () => {
  it('shows Cancel while installing and fires onCancel(repo_id)', () => {
    const onCancel = vi.fn();
    renderCell(
      'actions',
      { showBar: true, isDeleting: false, isInstalling: true, rowBusy: false, unsupported: false },
      { onCancel },
    );
    fireEvent.click(screen.getByRole('button', { name: t('models.cancel_btn') }));
    expect(onCancel).toHaveBeenCalledWith(REPO);
  });

  it('no Cancel button when the row is idle', () => {
    renderCell('actions', {
      showBar: false,
      isDeleting: false,
      isInstalling: false,
      rowBusy: false,
      unsupported: false,
    });
    expect(screen.queryByRole('button', { name: t('models.cancel_btn') })).not.toBeInTheDocument();
  });
});
