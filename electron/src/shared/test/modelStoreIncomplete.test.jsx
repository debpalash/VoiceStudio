import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import i18n from '../i18n';
import { makeModelColumns } from '../components/settings/models/columns';

const t = i18n.t.bind(i18n);
const REPO = 'org/model';

// ── Enhancement: surface an incomplete/partial cache ───────────────────────
// The backend already flags a truncated download (config landed, weight shard
// didn't) with `incomplete: true` — but the row used to read as a plain "not
// installed" even though partial bytes sit on disk. These tests lock in the
// dedicated "incomplete — N MB · Repair" surfacing so the state can't silently
// regress to "not installed".

// At-rest runtime (no active download / delete / busy) — the state in which a
// row's authoritative installed/incomplete flags drive the badge + actions.
const IDLE_RT = {
  showBar: false,
  isDeleting: false,
  isInstalling: false,
  rowBusy: false,
  unsupported: false,
  aggPct: null,
  totals: { downloaded: 0, total: 0 },
  hasFiles: false,
};

function renderCell(colId, handlers, mOver = {}) {
  const cols = makeModelColumns({
    t,
    getRowRuntime: () => IDLE_RT,
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
  const m = {
    repo_id: REPO,
    label: 'My Model',
    role: 'tts',
    size_gb: 1.2,
    installed: false,
    ...mOver,
  };
  return render(col.cell({ row: { original: m } }));
}

describe('Model Store row — incomplete cache surfacing', () => {
  const incomplete = { incomplete: true, size_on_disk_bytes: 12 * 1024 * 1024 };

  it('shows an "incomplete" status badge with the partial size (not "not installed")', () => {
    renderCell('status', {}, incomplete);
    // Single badge node: "incomplete · 12.0 MB" — the partial bytes are named.
    const badge = screen.getByText(/incomplete/i);
    expect(badge.textContent).toMatch(/12(\.0)? MB/);
    expect(screen.queryByText(/not installed/i)).not.toBeInTheDocument();
  });

  it('a plain not-installed row still shows "not installed" (no false positive)', () => {
    renderCell('status', {}, { installed: false });
    expect(screen.getByText(/not installed/i)).toBeInTheDocument();
    expect(screen.queryByText(/incomplete/i)).not.toBeInTheDocument();
  });

  it('an installed row is unaffected — shows "installed"', () => {
    renderCell('status', {}, { installed: true, size_on_disk_bytes: 5 });
    expect(screen.getByText(/^installed$/i)).toBeInTheDocument();
    expect(screen.queryByText(/incomplete/i)).not.toBeInTheDocument();
  });

  it('the primary action is "Repair" (not "Install") and fires onInstall(repo_id)', () => {
    const onInstall = vi.fn();
    renderCell('actions', { onInstall }, incomplete);
    const repair = screen.getByRole('button', { name: t('models.repair_btn') });
    expect(repair).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: t('models.install_btn') })).not.toBeInTheDocument();
    fireEvent.click(repair);
    expect(onInstall).toHaveBeenCalledWith(REPO);
  });

  it('offers a Delete affordance to clear the partial bytes', () => {
    const onDelete = vi.fn();
    renderCell('actions', { onDelete }, incomplete);
    fireEvent.click(screen.getByRole('button', { name: t('models.delete_btn') }));
    expect(onDelete).toHaveBeenCalledWith(REPO);
  });

  it('a normal not-installed row shows "Install" (repair path is incomplete-only)', () => {
    renderCell('actions', {}, { installed: false });
    expect(screen.getByRole('button', { name: t('models.install_btn') })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: t('models.repair_btn') })).not.toBeInTheDocument();
  });
});
