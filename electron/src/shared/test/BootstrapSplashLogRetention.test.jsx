/**
 * The bootstrap log window is a DOM budget, not a retention policy (#1847).
 *
 * `MAX_LOG_LINES = 200` capped the `logs` state itself, so every line past the
 * 200th silently destroyed the earlier ones. Four things read that array, and
 * all four degraded once a real cold install went past the cap:
 *
 *   - the Activity heading rendered `logs.length`, so the counter sat pinned at
 *     exactly 200 for the rest of a multi-minute bootstrap while lines were
 *     still streaming underneath. Live progress read as stalled when it was not.
 *   - `handleCopyLogs` serialized the same array, so Copy could only ever hand
 *     back the newest 200 lines — and this splash is the only place in the app
 *     with a Copy-log affordance at all.
 *   - `detectHints(message, logs)` scans for actionable failure markers. A
 *     failure that happened early in a long install lost its marker, so the
 *     card fell back to `hint_default` and the user was told nothing useful.
 *   - `isUnrecoverableFailure(message, logs)` runs off the same scan.
 *
 * The fix keeps the whole run in state and slices only where the DOM is
 * written. The array is bounded by one bootstrap: both retry paths clear it,
 * and App.jsx unmounts the splash the moment the stage flips to 'ready'.
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

const copied = [];
vi.mock('../utils/copyText', () => ({
  copyText: (text) => {
    copied.push(text);
    return Promise.resolve();
  },
}));

/**
 * Stream `n` lines through the live `bootstrap-log` listener, the first
 * carrying a marker that has to survive.
 *
 * Deliberately NOT the `get_bootstrap_logs` backfill: that path replaced the
 * array wholesale and never sliced, so it was already unaffected by the cap.
 * Only the live append trimmed, which is why the bug needed a long *running*
 * install to show up rather than a long buffered one.
 */
const stream = async (n, firstLine) => {
  await act(async () => {
    for (let i = 0; i < n; i += 1) {
      logHandler({
        payload: {
          stage: 'installing_deps',
          line: i === 0 ? firstLine : `dependency step ${i}`,
        },
      });
    }
  });
};

beforeEach(() => {
  logHandler = null;
  copied.length = 0;
  invoke.mockReset();
  invoke.mockResolvedValue([]);
  window.__TAURI_INTERNALS__ = {};
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
});

describe('retention holds up under a real installer stream', () => {
  it('keeps a repeated line instead of mistaking it for a replay', async () => {
    // Installer output repeats constantly ("Downloading…", an identical pip
    // line per wheel). The dedup window used to run for the whole bootstrap,
    // so any line matching one of the last five was dropped — the counter
    // undercounted and Copy lost real output. It now guards only the
    // backfill→live seam, which is the only place the ambiguity exists.
    render(<BootstrapSplash stage="installing_deps" />);
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));
    await act(async () => {
      for (const line of ['Downloading…', 'Resolving…', 'Downloading…']) {
        logHandler({ payload: { stage: 'installing_deps', line } });
      }
    });

    await screen.findByText('3 lines');
    screen.getByRole('button', { name: /Copy/ }).click();
    await waitFor(() => expect(copied).toHaveLength(1));
    expect(copied[0].split('\n')).toHaveLength(3);
  });

  it('still drops the backfill overlap at the seam', async () => {
    // The narrowing must not give up what the dedup was for: a line already
    // delivered by get_bootstrap_logs and then re-emitted live is one line.
    invoke.mockImplementation(async (cmd) =>
      cmd === 'get_bootstrap_logs'
        ? [
            { stage: 'creating_venv', line: 'venv created' },
            { stage: 'installing_deps', line: 'resolving dependencies' },
          ]
        : null,
    );
    render(<BootstrapSplash stage="installing_deps" />);
    await screen.findByText('2 lines');
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));

    await act(async () => {
      logHandler({ payload: { stage: 'installing_deps', line: 'resolving dependencies' } });
      logHandler({ payload: { stage: 'installing_deps', line: 'building wheels' } });
    });

    // The replay is swallowed, the genuinely new line is not.
    await screen.findByText('3 lines');
  });

  it('bounds the per-event copy no matter how long the run gets', async () => {
    // The retained run is a ref that gets pushed to, so appending is O(1);
    // the only array copied per event is `logs`, and this pins the property
    // that keeps it cheap — it never grows past the window, even at 5000
    // lines. Without that bound the append is O(n²) element copies and the
    // splash stutters on exactly the verbose installs that need it most.
    const { container } = render(<BootstrapSplash stage="installing_deps" />);
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));
    await stream(5000, 'starting install');

    await screen.findByText('5000 lines');
    expect(container.querySelector('pre').textContent.split('\n')).toHaveLength(200);
  });
});

describe('a bootstrap longer than the visible window', () => {
  it('counts every line instead of freezing at the window size', async () => {
    render(<BootstrapSplash stage="installing_deps" />);
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));
    await stream(640, 'starting install');

    // Pre-fix this read "200 lines" and stayed there for the rest of the run,
    // which is the whole complaint: the heading is the only live signal that
    // a multi-minute install is still moving.
    await screen.findByText('640 lines');
  });

  it('still writes only the window to the DOM', async () => {
    const { container } = render(<BootstrapSplash stage="installing_deps" />);
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));
    await stream(640, 'starting install');
    await screen.findByText('640 lines');

    const pre = container.querySelector('pre');
    const rendered = pre.textContent.split('\n');
    // Retention grew; the DOM budget did not.
    expect(rendered).toHaveLength(200);
    expect(rendered.at(-1)).toContain('dependency step 639');
    expect(pre.textContent).not.toContain('starting install');
  });

  it('copies the whole run, including what scrolled out of the window', async () => {
    render(<BootstrapSplash stage="installing_deps" />);
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));
    await stream(640, 'starting install');
    await screen.findByText('640 lines');

    screen.getByRole('button', { name: /Copy/ }).click();
    await waitFor(() => expect(copied).toHaveLength(1));
    const text = copied[0];
    // The first line is the one a bug report needs and the one the cap ate.
    expect(text).toContain('starting install');
    expect(text).toContain('dependency step 639');
    expect(text.split('\n')).toHaveLength(640);
  });

  it('keeps the actionable hint for a failure that happened early', async () => {
    // The severe case: `uv sync failed` scrolls out, `detectHints` finds
    // nothing, and the failure card offers the generic hint instead of the
    // one that names the actual problem.
    render(<BootstrapSplash stage="failed" message="Setup failed" />);
    await waitFor(() => expect(logHandler).toBeTypeOf('function'));
    await stream(640, 'uv sync failed: exit code 1');
    await screen.findByText('640 lines');

    // en.json bootstrap.hint_uv_sync. Asserted together with the ABSENCE of
    // hint_default, because falling back to the generic advice is exactly the
    // failure mode: the card still looks helpful while saying nothing.
    await waitFor(() => {
      expect(document.body.textContent).toContain('Dependency install failed');
    });
    expect(document.body.textContent).not.toContain('Try "Retry" first');
  });
});
