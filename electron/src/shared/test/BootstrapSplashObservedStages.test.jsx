/**
 * The install chrome (journey rail, "Installing" heading, step rows, resume
 * note) may only describe work that was actually observed in the current
 * bootstrap attempt (#1894, #1900). Warm starts and repair syncs skip stages,
 * so list position must never be what marks a step as finished.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { BootstrapSplash } from '../components/BootstrapSplash';

const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a) => invoke(...a) }));

let logHandler = null;
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (name, cb) => {
    if (name === 'bootstrap-log') logHandler = cb;
    return () => {};
  }),
}));
vi.mock('@tauri-apps/plugin-opener', () => ({ revealItemInDir: vi.fn() }));

const STEP_IDS = [
  'checking',
  'downloading_uv',
  'creating_venv',
  'installing_deps',
  'starting_backend',
];

const stepState = (container, id) =>
  container.querySelector(`[data-step="${id}"]`)?.getAttribute('data-state') ?? null;

const hasChrome = (container) => container.querySelector('[data-step]') !== null;

/** Render in the Tauri context and wait until the log listener is registered. */
async function renderInTauri(props, buffered = []) {
  window.__TAURI_INTERNALS__ = {};
  invoke.mockImplementation(async (cmd) => (cmd === 'get_bootstrap_logs' ? buffered : null));
  const view = render(<BootstrapSplash {...props} />);
  await waitFor(() => expect(logHandler).toBeTypeOf('function'));
  return view;
}

const emit = async (payload) => {
  await act(async () => {
    logHandler({ payload });
  });
};

beforeEach(() => {
  logHandler = null;
  invoke.mockReset();
  invoke.mockResolvedValue([]);
  delete window.__TAURI_INTERNALS__;
  localStorage.setItem('dismissed_lang_suggestion', 'true');
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
});

describe('BootstrapSplash observed stages', () => {
  it('shows no install chrome on a warm start but keeps the live stage label', () => {
    const { container, rerender } = render(<BootstrapSplash stage="checking" attempt={1} />);
    rerender(<BootstrapSplash stage="starting_backend" attempt={1} />);

    expect(hasChrome(container)).toBe(false);
    expect(screen.queryByText('Installing')).not.toBeInTheDocument();
    expect(screen.queryByText(/Interrupted downloads/)).not.toBeInTheDocument();
    expect(screen.getByText(/Starting backend/)).toBeInTheDocument();
  });

  it('shows the chrome on a repair sync with only the deps row active', () => {
    const { container, rerender } = render(<BootstrapSplash stage="checking" attempt={1} />);
    rerender(<BootstrapSplash stage="installing_deps" attempt={1} />);

    expect(hasChrome(container)).toBe(true);
    expect(screen.getAllByText('Installing')).toHaveLength(2); // rail + heading
    expect(stepState(container, 'installing_deps')).toBe('active');
    expect(stepState(container, 'downloading_uv')).toBe('pending');
    expect(stepState(container, 'creating_venv')).toBe('pending');
  });

  it('marks every earlier row done as a genuine first run advances', () => {
    const { container, rerender } = render(<BootstrapSplash stage="checking" attempt={1} />);
    for (let i = 1; i < STEP_IDS.length; i += 1) {
      rerender(<BootstrapSplash stage={STEP_IDS[i]} attempt={1} />);
      for (let j = 0; j < i; j += 1) expect(stepState(container, STEP_IDS[j])).toBe('done');
      expect(stepState(container, STEP_IDS[i])).toBe('active');
    }
  });

  it('counts a stage the poll skipped when a tagged buffered log line proves it ran', async () => {
    const { container, rerender } = await renderInTauri({ stage: 'checking', attempt: 0 }, [
      { stage: 'downloading_uv', line: 'fetching uv' },
    ]);
    rerender(<BootstrapSplash stage="installing_deps" attempt={0} />);

    await waitFor(() => expect(stepState(container, 'downloading_uv')).toBe('done'));
    expect(stepState(container, 'creating_venv')).toBe('pending');
  });

  it('forgets a failed attempt when the next attempt takes the warm path', () => {
    const { container, rerender } = render(<BootstrapSplash stage="checking" attempt={1} />);
    rerender(<BootstrapSplash stage="downloading_uv" attempt={1} />);
    rerender(<BootstrapSplash stage="installing_deps" attempt={1} />);
    rerender(<BootstrapSplash stage="failed" message="boom" attempt={1} />);
    rerender(<BootstrapSplash stage="checking" attempt={2} />);
    rerender(<BootstrapSplash stage="starting_backend" attempt={2} />);

    expect(hasChrome(container)).toBe(false);
    expect(screen.queryByText('Installing')).not.toBeInTheDocument();
  });

  it('keeps log lines that arrive for the new attempt right after Retry', async () => {
    const { container, rerender } = await renderInTauri({ stage: 'installing_deps', attempt: 1 });
    rerender(<BootstrapSplash stage="failed" message="boom" attempt={1} />);

    await act(async () => {
      screen.getByRole('button', { name: /^Retry$/ }).click();
    });
    // New attempt output lands before the next status poll reports it.
    await emit({ attempt: 2, stage: 'downloading_uv', line: 'fetching uv again' });
    rerender(<BootstrapSplash stage="installing_deps" attempt={2} />);

    expect(stepState(container, 'downloading_uv')).toBe('done');
  });

  it('drops old evidence when a retry never polled checking', () => {
    const { container, rerender } = render(<BootstrapSplash stage="installing_deps" attempt={1} />);
    rerender(<BootstrapSplash stage="failed" message="boom" attempt={1} />);
    rerender(<BootstrapSplash stage="starting_backend" attempt={2} />);

    expect(hasChrome(container)).toBe(false);
  });

  it('resets doneness when Rust restarts with the same stage under a new attempt id', () => {
    const { container, rerender } = render(<BootstrapSplash stage="downloading_uv" attempt={1} />);
    rerender(<BootstrapSplash stage="installing_deps" attempt={1} />);
    expect(stepState(container, 'downloading_uv')).toBe('done');

    rerender(<BootstrapSplash stage="installing_deps" attempt={2} />);
    expect(stepState(container, 'installing_deps')).toBe('active');
    expect(stepState(container, 'downloading_uv')).toBe('pending');
  });

  it('does not dedupe an identical line from another attempt at the backfill seam', async () => {
    const { container, rerender } = await renderInTauri({ stage: 'installing_deps', attempt: 2 }, [
      { attempt: 1, stage: 'creating_venv', line: 'make venv' },
    ]);
    expect(stepState(container, 'creating_venv')).toBe('pending');

    await emit({ attempt: 2, stage: 'creating_venv', line: 'make venv' });
    rerender(<BootstrapSplash stage="installing_deps" attempt={2} />);

    expect(stepState(container, 'creating_venv')).toBe('done');
  });

  it('stops treating an earlier attempt line as proof once the next attempt begins', async () => {
    const { container, rerender } = await renderInTauri({ stage: 'installing_deps', attempt: 1 });
    await emit({ attempt: 1, stage: 'creating_venv', line: 'make venv' });
    expect(stepState(container, 'creating_venv')).toBe('done');

    rerender(<BootstrapSplash stage="installing_deps" attempt={2} />);
    expect(stepState(container, 'creating_venv')).toBe('pending');
  });
});
