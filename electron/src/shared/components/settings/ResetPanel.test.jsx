import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import toast from 'react-hot-toast';

const { clearLongformProjects, flushLongformPendingWrites } = vi.hoisted(() => ({
  clearLongformProjects: vi.fn(async () => {}),
  flushLongformPendingWrites: vi.fn(async () => {}),
}));
vi.mock('../../utils/longformPersistence', async (importOriginal) => ({
  ...(await importOriginal()),
  clearLongformProjects: (...args) => clearLongformProjects(...args),
  flushLongformPendingWrites: (...args) => flushLongformPendingWrites(...args),
}));
vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

import ResetPanel, {
  PRESETS,
  plan,
  selectedBytes,
  needsTypedConfirm,
  matchingPreset,
} from './ResetPanel';

const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a) => invoke(...a) }));

const clearHistory = vi.fn(async () => ({ ok: true }));
const clearDubHistory = vi.fn(async () => ({ ok: true }));
vi.mock('../../api/generate', () => ({ clearHistory: (...a) => clearHistory(...a) }));
vi.mock('../../api/dub', () => ({ clearDubHistory: (...a) => clearDubHistory(...a) }));

/** What `reset_scan` returns from the shell: every scope, with its real size. */
const SCOPES = [
  { key: 'ui_prefs', paths: [], size_bytes: 0, exists: true, shared: false, needs_restart: false },
  { key: 'history', paths: [], size_bytes: 0, exists: true, shared: false, needs_restart: false },
  {
    key: 'settings',
    paths: ['/d/prefs.json'],
    size_bytes: 4_096,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'content',
    paths: ['/d/voices'],
    size_bytes: 5 * 1024 ** 3,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'engines',
    paths: ['/d/engines'],
    size_bytes: 2 * 1024 ** 3,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'tools',
    paths: ['/d/media_tools'],
    size_bytes: 100 * 1024 ** 2,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'models',
    paths: ['/hf'],
    size_bytes: 14 * 1024 ** 3,
    exists: true,
    shared: true,
    needs_restart: true,
  },
  {
    key: 'caches',
    paths: ['/d/gallery_cache'],
    size_bytes: 10 * 1024 ** 2,
    exists: true,
    shared: false,
    needs_restart: true,
  },
  {
    key: 'logs',
    paths: ['/d/omnivoice.log'],
    size_bytes: 1024,
    exists: true,
    shared: false,
    needs_restart: true,
  },
];

const byKey = Object.fromEntries(SCOPES.map((s) => [s.key, s]));

describe('reset planning', () => {
  it('never sends a frontend-only scope to the shell', () => {
    // ui_prefs is localStorage and history is a DB endpoint — the Rust purge has
    // no idea what either means, and passing them would be a silent no-op at best.
    const steps = plan(['ui_prefs', 'history', 'models']);
    expect(steps.disk).toEqual(['models']);
    expect(steps.prefs).toBe(true);
    expect(steps.history).toBe(true);
  });

  it('skips the history endpoints when the whole database is going anyway', () => {
    // `content` deletes omnivoice.db outright, so clearing history rows first is a
    // pointless round-trip against records that are about to cease to exist.
    const steps = plan(['history', 'content']);
    expect(steps.history).toBe(false);
    expect(steps.longformProjects).toBe(true);
    expect(steps.disk).toEqual(['content']);
  });

  it('only needs a backend restart when something on disk is being deleted', () => {
    expect(plan(['ui_prefs']).restart).toBe(false);
    expect(plan(['history']).restart).toBe(false);
    expect(plan(['ui_prefs', 'settings']).restart).toBe(true);
  });

  it('demands the typed word for anything unrecoverable, and not otherwise', () => {
    expect(needsTypedConfirm(['content'])).toBe(true);
    expect(needsTypedConfirm(PRESETS.everything)).toBe(true);
    // Models are a big download, but they are only a download — they come back.
    expect(needsTypedConfirm(PRESETS.assets)).toBe(false);
    expect(needsTypedConfirm(PRESETS.settings)).toBe(false);
  });

  it('counts exactly what is ticked, so the button number is the truth', () => {
    expect(selectedBytes(SCOPES, ['settings'])).toBe(4_096);
    expect(selectedBytes(SCOPES, PRESETS.assets)).toBe(
      byKey.models.size_bytes +
        byKey.engines.size_bytes +
        byKey.tools.size_bytes +
        byKey.caches.size_bytes,
    );
    // Scopes that own no files contribute nothing.
    expect(selectedBytes(SCOPES, ['ui_prefs', 'history'])).toBe(0);
    expect(selectedBytes(SCOPES, [])).toBe(0);
    expect(selectedBytes(undefined, ['models'])).toBe(0);
  });

  it('leaves the user something that still runs: no preset removes the Python env', () => {
    // The owner's call — "Everything" means a fresh install, not a reinstall. The
    // interpreter the backend runs on is the uninstaller's business, not reset's.
    for (const scopes of Object.values(PRESETS)) {
      expect(scopes).not.toContain('env');
    }
    expect(PRESETS.assets).not.toContain('content');
  });

  it('recognises the tier a selection corresponds to', () => {
    expect(matchingPreset(['ui_prefs'])).toBe('ui');
    expect(matchingPreset([...PRESETS.everything].reverse())).toBe('everything');
    expect(matchingPreset(['models'])).toBeNull();
  });
});

describe('ResetPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearLongformProjects.mockResolvedValue(undefined);
    flushLongformPendingWrites.mockResolvedValue(undefined);
    localStorage.clear();
    window.__TAURI_INTERNALS__ = {};
    invoke.mockImplementation(async (cmd) => {
      if (cmd === 'reset_scan') return SCOPES;
      if (cmd === 'reset_purge')
        return { removed: [], failed: [], refused: [], freed_bytes: 0, restarted: true };
      return null;
    });
  });

  const openDialog = async () => {
    render(<ResetPanel />);
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('reset_scan'));
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    await screen.findByTestId('factory-reset-confirm');
  };

  it('defaults to the least destructive tier', async () => {
    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-ui')).toBeChecked());
    expect(screen.getByTestId('reset-tier-everything')).not.toBeChecked();
  });

  it('will not delete voices and projects on a single click', async () => {
    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-everything')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reset-tier-everything'));
    fireEvent.click(screen.getByTestId('factory-reset-open'));

    const confirm = await screen.findByTestId('factory-reset-confirm');
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId('reset-type-confirm'), { target: { value: 'delete' } });
    await waitFor(() => expect(confirm).toBeEnabled());
  });

  it('sends only disk scopes to the shell and clears preferences here', async () => {
    localStorage.setItem('omnivoice.app', '{"state":{}}');
    localStorage.setItem('omni_transcriptions', '[{"text":"note"}]');

    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-settings')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reset-tier-settings')); // ui_prefs + settings
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    fireEvent.click(await screen.findByTestId('factory-reset-confirm'));

    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('reset_purge', { scopes: ['settings'] }),
    );
    // The zustand blob goes; dictation history — user data, not a preference — stays.
    expect(localStorage.getItem('omnivoice.app')).toBeNull();
    expect(localStorage.getItem('omni_transcriptions')).toBe('[{"text":"note"}]');
  });

  it('waits for a pending long-form fallback before clearing preferences', async () => {
    const compactA = JSON.stringify({
      version: 9,
      state: { theme: 'dark', currentProjectId: 'p_a' },
    });
    localStorage.setItem('omnivoice.app', compactA);
    let finishFlush = () => {};
    flushLongformPendingWrites.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishFlush = () => {
            // The pending B write lost IndexedDB, so its only full copy lands
            // in localStorage when the awaited flush completes.
            localStorage.setItem(
              'omnivoice.app',
              JSON.stringify({
                version: 9,
                longformFallbackRevision: 2,
                state: {
                  theme: 'light',
                  currentProjectId: 'p_b',
                  script: 'pending manuscript B',
                  storyProjects: [{ id: 'p_b', name: 'Book B' }],
                },
              }),
            );
            resolve();
          };
        }),
    );

    await openDialog();
    fireEvent.click(screen.getByTestId('factory-reset-confirm'));

    await waitFor(() => expect(flushLongformPendingWrites).toHaveBeenCalledOnce());
    expect(localStorage.getItem('omnivoice.app')).toBe(compactA);
    finishFlush();

    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('omnivoice.app'))).toEqual({
        version: 9,
        longformFallbackRevision: 2,
        state: {
          script: 'pending manuscript B',
          storyProjects: [{ id: 'p_b', name: 'Book B' }],
        },
      }),
    );
  });

  it('clears history through the API without bouncing the backend', async () => {
    await openDialog();
    fireEvent.click(screen.getByTestId('reset-advanced-toggle'));
    expect(screen.getByTestId('reset-advanced')).toHaveClass('overflow-y-auto');
    fireEvent.click(await screen.findByTestId('reset-scope-history'));
    fireEvent.click(screen.getByTestId('reset-scope-ui_prefs')); // untick, leaving history alone
    fireEvent.click(screen.getByTestId('factory-reset-confirm'));

    await waitFor(() => expect(clearHistory).toHaveBeenCalled());
    expect(clearDubHistory).toHaveBeenCalled();
    // Nothing on disk was touched, so there is nothing to restart for.
    expect(invoke).not.toHaveBeenCalledWith('reset_purge', expect.anything());
  });

  it('warns before sweeping up a model cache other tools share', async () => {
    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-assets')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reset-tier-assets'));
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    expect(await screen.findByTestId('reset-shared-warning')).toBeInTheDocument();
  });

  it('maps a local fallback clear failure to dedicated localized recovery text', async () => {
    clearLongformProjects.mockRejectedValueOnce(
      new DOMException('', 'LongformLocalFallbackClearError'),
    );
    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-everything')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reset-tier-everything'));
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    fireEvent.change(await screen.findByTestId('reset-type-confirm'), {
      target: { value: 'DELETE' },
    });
    fireEvent.click(screen.getByTestId('factory-reset-confirm'));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'VoiceStudio couldn’t safely clear saved projects. Your project data was left intact; free some storage or restart the app, then try again.',
      ),
    );
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining('LongformLocalFallback'));
  });

  it('does not redundantly flush long-form writes after clearing projects', async () => {
    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-everything')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reset-tier-everything'));
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    fireEvent.change(await screen.findByTestId('reset-type-confirm'), {
      target: { value: 'DELETE' },
    });
    fireEvent.click(screen.getByTestId('factory-reset-confirm'));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(clearLongformProjects).toHaveBeenCalledOnce();
    expect(flushLongformPendingWrites).not.toHaveBeenCalled();
  });

  it('stays silent about sharing when the cache is app-private (Windows, portable)', async () => {
    invoke.mockImplementation(async (cmd) =>
      cmd === 'reset_scan'
        ? SCOPES.map((s) => (s.key === 'models' ? { ...s, shared: false } : s))
        : { removed: [], failed: [], refused: [], freed_bytes: 0, restarted: true },
    );
    render(<ResetPanel />);
    await waitFor(() => expect(screen.getByTestId('reset-tier-assets')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reset-tier-assets'));
    fireEvent.click(screen.getByTestId('factory-reset-open'));
    await screen.findByTestId('factory-reset-confirm');
    expect(screen.queryByTestId('reset-shared-warning')).not.toBeInTheDocument();
  });

  it('outside the desktop shell, offers preferences only', async () => {
    delete window.__TAURI_INTERNALS__;
    render(<ResetPanel />);
    expect(screen.queryByTestId('reset-tier-everything')).not.toBeInTheDocument();
    expect(screen.getByTestId('factory-reset-open')).toBeInTheDocument();
    expect(invoke).not.toHaveBeenCalled();
  });
});
