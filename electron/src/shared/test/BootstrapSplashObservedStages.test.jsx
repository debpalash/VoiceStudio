/**
 * Regression tests for #1894 — the first-run INSTALLING journey was shown on
 * every launch, with green ticks for work that never ran.
 *
 * `BootstrapSplash` used to derive "done" purely from `STEPS.indexOf(stage)`
 * (BootstrapSplash.jsx:453/774 pre-fix): on a warm start Rust jumps straight
 * from `checking` to `starting_backend` (bootstrap.rs finds the venv healthy
 * and returns early), so `downloading_uv`, `creating_venv` and
 * `installing_deps` — including the "first run, 5–10 min." label — all
 * rendered with a green DONE tick for work that never happened. The same
 * fabrication hit a repair sync (venv exists, only `installing_deps` runs,
 * but `downloading_uv`/`creating_venv` still rendered done), and `JourneyRail`
 * hardcoded Setup=done/Installing=active regardless of `stage`.
 *
 * The fix tracks which stages were actually observed and derives doneness +
 * journey-chrome visibility from that instead of list position.
 *
 * Two follow-up findings from bot review on PR #1896 are covered at the end:
 *   - the ~1s `bootstrap_status` poll can miss a stage that starts and
 *     finishes between samples, so stage-tagged `bootstrap-log` lines are
 *     unioned in as independent proof a stage ran;
 *   - a Retry restarts the bootstrap, so stages observed during the previous
 *     attempt must not carry over and render as done in the new one.
 *
 * #1900 replaced the attempt-boundary GUESS with a fact. The splash used to
 * infer a restart from the ~1 s stage poll (arriving at `checking`, or leaving
 * `failed`), which cannot see a restart that begins and passes those stages
 * inside one sample window — and cannot see a Rust-side restart that never
 * passes them at all. Rust now stamps an attempt id on the status reply and on
 * every log line, and the splash scopes evidence by equality on it. The last
 * three tests here pin that contract.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { BootstrapSplash } from '../components/BootstrapSplash';

vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(async () => null),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async () => () => {}),
}));
vi.mock('@tauri-apps/plugin-opener', () => ({
  revealItemInDir: vi.fn(),
}));

beforeEach(() => {
  // Not in a Tauri context: the log/progress subscription effects no-op.
  delete window.__TAURI_INTERNALS__;
});

describe('BootstrapSplash — observed-stage tracking (#1894)', () => {
  it('warm start (checking -> starting_backend): no fabricated done ticks, no "first run" chrome', () => {
    const { rerender } = render(<BootstrapSplash stage="checking" message={null} />);

    // The journey rail and "Installing" heading must not appear before any
    // real install stage has ever been observed.
    expect(screen.queryByText('Installing')).toBeNull();
    expect(screen.queryByText('Setup')).toBeNull();
    expect(screen.queryByText('Models & engines')).toBeNull();

    rerender(<BootstrapSplash stage="starting_backend" message={null} />);

    // Live stage label still shows — this is not a blank screen.
    expect(screen.getByText('Starting backend…')).toBeInTheDocument();
    // The install journey never appeared: bootstrap.rs never entered any of
    // downloading_uv/creating_venv/installing_deps on this run.
    expect(screen.queryByText('Installing')).toBeNull();
    expect(screen.queryByText('Downloading uv (Python package manager)…')).toBeNull();
    expect(screen.queryByText('Creating Python virtual environment…')).toBeNull();
    expect(screen.queryByText(/first run, 5.10 min/)).toBeNull();
  });

  it('repair sync (checking -> installing_deps): installing_deps active, earlier steps not fabricated done', () => {
    const { rerender } = render(<BootstrapSplash stage="checking" message={null} />);
    rerender(<BootstrapSplash stage="installing_deps" message={null} />);

    // Real install work observed: the journey chrome comes back (both the
    // JourneyRail item and the section heading render the same "Installing"
    // string, so there are two matches).
    expect(screen.getAllByText('Installing').length).toBeGreaterThan(0);
    // The live stage label (masthead) and the step-list item both render the
    // "first run, 5-10 min" text; scope to the step-list one inside the <ol>.
    const activeLabel = screen.getAllByText(/first run, 5.10 min/).find((el) => el.closest('ol'));
    expect(activeLabel).toBeInTheDocument();
    // The active step renders semibold, not the muted "done" styling.
    expect(activeLabel.className).toMatch(/font-semibold/);
    expect(activeLabel.className).not.toMatch(/text-fg-muted/);

    // downloading_uv/creating_venv were never entered by Rust on a repair
    // sync — they must render as pending, not done.
    const uvStep = screen.getByText('Downloading uv (Python package manager)…');
    const venvStep = screen.getByText('Creating Python virtual environment…');
    expect(uvStep.className).not.toMatch(/text-fg-muted/);
    expect(venvStep.className).not.toMatch(/text-fg-muted/);
  });

  it('genuine first run walks all five stages: every step is marked done as it passes (no regression)', () => {
    const stages = [
      'checking',
      'downloading_uv',
      'creating_venv',
      'installing_deps',
      'starting_backend',
    ];
    const { rerender } = render(<BootstrapSplash stage={stages[0]} message={null} />);

    for (let i = 1; i < stages.length; i += 1) {
      rerender(<BootstrapSplash stage={stages[i]} message={null} />);
      // Every stage strictly before the current one must show as done
      // (muted styling), since this run genuinely walked through each one.
      for (let j = 0; j < i; j += 1) {
        const stepLabel = screen.getByText(
          {
            checking: 'Checking environment…',
            downloading_uv: 'Downloading uv (Python package manager)…',
            creating_venv: 'Creating Python virtual environment…',
            installing_deps: /first run, 5.10 min/,
            starting_backend: 'Starting backend…',
          }[stages[j]],
        );
        expect(stepLabel.className).toMatch(/text-fg-muted/);
      }
    }
  });

  it('a stage the 1s poll never sampled still counts as done when its logs prove it ran', async () => {
    // Greptile finding on #1896: `bootstrap_status` is sampled ~1/s, so on a
    // fast disk `creating_venv` can start and finish between two samples and
    // never be polled. Stage-tagged bootstrap logs are emitted as the work
    // happens, so they are independent proof the stage ran.
    window.__TAURI_INTERNALS__ = {};
    const { invoke } = await import('@tauri-apps/api/core');
    invoke.mockImplementation(async (cmd) =>
      cmd === 'get_bootstrap_logs'
        ? [{ stage: 'creating_venv', line: 'Creating virtualenv at .venv' }]
        : null,
    );

    // Poll sequence skips creating_venv entirely.
    const { rerender } = render(<BootstrapSplash stage="downloading_uv" message={null} />);
    rerender(<BootstrapSplash stage="installing_deps" message={null} />);

    await waitFor(() => {
      const venvStep = screen.getByText('Creating Python virtual environment…');
      expect(venvStep.className).toMatch(/text-fg-muted/);
    });
  });

  it('a retry drops stages observed during the previous attempt', () => {
    // Greptile finding on #1896: the observed set was add-only and the splash
    // stays mounted across a Retry, so a stage the FAILED attempt reached
    // would still render done in the new attempt even if that attempt skips
    // it. The restart is Rust's attempt id changing (#1900).
    const { rerender } = render(<BootstrapSplash stage="checking" message={null} attempt={1} />);
    rerender(<BootstrapSplash stage="downloading_uv" message={null} attempt={1} />);
    rerender(<BootstrapSplash stage="installing_deps" message={null} attempt={1} />);
    rerender(<BootstrapSplash stage="failed" message="uv sync failed" attempt={1} />);

    // Retry: Rust opens attempt 2, then this attempt finds the venv healthy
    // and jumps straight to starting_backend.
    rerender(<BootstrapSplash stage="checking" message={null} attempt={2} />);
    rerender(<BootstrapSplash stage="starting_backend" message={null} attempt={2} />);

    // Nothing from the previous attempt may be presented as this attempt's
    // completed work — so the install chrome is gone entirely again.
    expect(screen.queryByText('Downloading uv (Python package manager)…')).toBeNull();
    expect(screen.queryByText(/first run, 5.10 min/)).toBeNull();
    expect(screen.getByText('Starting backend…')).toBeInTheDocument();
  });

  it('logs arriving after Retry but before the next poll are not discarded', async () => {
    // Greptile finding on ece08bd7: the attempt boundary was stamped when the
    // ~1s poll first reported `checking`, which lands AFTER the Rust side has
    // already emitted the new attempt's first log lines — so that evidence was
    // filtered out as "previous attempt" and a fast stage missed by polling
    // stayed pending. The boundary must open when the retry is initiated.
    window.__TAURI_INTERNALS__ = {};
    const { invoke } = await import('@tauri-apps/api/core');
    const { listen } = await import('@tauri-apps/api/event');
    const handlers = {};
    listen.mockImplementation(async (name, cb) => {
      handlers[name] = cb;
      return () => {};
    });
    invoke.mockImplementation(async () => null);

    const { rerender } = render(
      <BootstrapSplash stage="failed" message="uv sync failed" attempt={1} />,
    );
    await waitFor(() => expect(handlers['bootstrap-log']).toBeTypeOf('function'));

    // User clicks Retry. Rust bumps to attempt 2 inside respawn_backend.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Retry$/ }));
    });

    // Rust immediately emits the new attempt's logs, still before the poll
    // has reported anything — but they are already stamped attempt 2.
    await act(async () => {
      handlers['bootstrap-log']({
        payload: { attempt: 2, stage: 'creating_venv', line: 'Creating virtualenv at .venv' },
      });
    });

    // Only now does the poll catch up, and it never samples creating_venv.
    rerender(<BootstrapSplash stage="checking" message={null} attempt={2} />);
    rerender(<BootstrapSplash stage="installing_deps" message={null} attempt={2} />);

    // The log line proved creating_venv ran in THIS attempt; it must not have
    // been discarded by a boundary stamped after it arrived.
    await waitFor(() => {
      const venvStep = screen.getByText('Creating Python virtual environment…');
      expect(venvStep.className).toMatch(/text-fg-muted/);
    });
  });

  it('a retry whose restart stage the poll never sampled still drops the old evidence', () => {
    // CodeRabbit finding on 5e9538a0: a retry can go failed -> checking ->
    // starting_backend inside one ~1s sample window, so the poll observes
    // only failed -> starting_backend. The attempt id is stamped by the
    // producer, so it survives a sample window that swallows the stage.
    const { rerender } = render(<BootstrapSplash stage="checking" message={null} attempt={1} />);
    rerender(<BootstrapSplash stage="downloading_uv" message={null} attempt={1} />);
    rerender(<BootstrapSplash stage="installing_deps" message={null} attempt={1} />);
    rerender(<BootstrapSplash stage="failed" message="uv sync failed" attempt={1} />);

    // Retry — and the poll misses `checking` entirely.
    rerender(<BootstrapSplash stage="starting_backend" message={null} attempt={2} />);

    expect(screen.getByText('Starting backend…')).toBeInTheDocument();
    // Nothing from the failed attempt may be shown as this attempt's work.
    expect(screen.queryByText('Downloading uv (Python package manager)…')).toBeNull();
    expect(screen.queryByText(/first run, 5.10 min/)).toBeNull();
    expect(screen.queryByText('Installing')).toBeNull();
  });

  it('a Rust-side restart the poll cannot see at all drops the old evidence', () => {
    // #1900, the case no amount of stage inference reaches. The supervisor
    // rebuilds a broken venv on its own: it re-enters `checking` and runs the
    // whole bootstrap again, with no `failed` stage and no UI-initiated retry.
    // If the poll samples `installing_deps` on either side of that window, the
    // stage sequence is `installing_deps` -> `installing_deps` — literally no
    // signal that anything restarted. The old heuristics kept the previous
    // attempt's `downloading_uv` and rendered it done for work this attempt
    // never did. The attempt id changing is the whole signal.
    const { rerender } = render(
      <BootstrapSplash stage="downloading_uv" message={null} attempt={1} />,
    );
    rerender(<BootstrapSplash stage="installing_deps" message={null} attempt={1} />);

    // Attempt 1 really did download uv, so its step reads as done.
    expect(screen.getByText('Downloading uv (Python package manager)…').className).toMatch(
      /text-fg-muted/,
    );

    // Venv turns out to be broken; Rust quarantines it and starts over. The
    // poll happens to sample the new attempt at the same stage name, so the
    // stage sequence carries no evidence of the restart at all.
    rerender(<BootstrapSplash stage="installing_deps" message={null} attempt={2} />);

    // This attempt has not downloaded uv. Claiming otherwise is the #1894
    // fabrication, arriving by a route stage inference cannot close.
    expect(screen.getByText('Downloading uv (Python package manager)…').className).not.toMatch(
      /text-fg-muted/,
    );
  });

  it('keeps a repeated line that belongs to a different attempt', async () => {
    // CodeRabbit: the backfill→live seam is deduplicated on stage + text, and
    // installer output repeats itself constantly. Across a restart the same
    // stage and the same text is not a replayed line — it is the new attempt's
    // own evidence, and dropping it can remove the only proof for a stage the
    // poll never samples. The attempt is part of the identity.
    window.__TAURI_INTERNALS__ = {};
    const { invoke } = await import('@tauri-apps/api/core');
    const { listen } = await import('@tauri-apps/api/event');
    const handlers = {};
    listen.mockImplementation(async (name, cb) => {
      handlers[name] = cb;
      return () => {};
    });
    // Attempt 1 already logged this exact line; it is in the backfill buffer.
    invoke.mockImplementation(async (cmd) =>
      cmd === 'get_bootstrap_logs'
        ? [{ attempt: 1, stage: 'creating_venv', line: 'Creating virtualenv at .venv' }]
        : null,
    );

    render(<BootstrapSplash stage="installing_deps" message={null} attempt={2} />);
    await waitFor(() => expect(handlers['bootstrap-log']).toBeTypeOf('function'));

    // Attempt 2 does the same work and says the same thing.
    await act(async () => {
      handlers['bootstrap-log']({
        payload: { attempt: 2, stage: 'creating_venv', line: 'Creating virtualenv at .venv' },
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Creating Python virtual environment…').className).toMatch(
        /text-fg-muted/,
      );
    });
  });

  it('a log line from the previous attempt is not counted as this attempt evidence', async () => {
    // Log lines are the second evidence source, and the one that closes the
    // poll's blind spot — so they carry the attempt too. A `creating_venv`
    // line emitted during attempt 1 must not make attempt 2's step list claim
    // a venv was created, no matter how recently it arrived.
    window.__TAURI_INTERNALS__ = {};
    const { listen } = await import('@tauri-apps/api/event');
    const handlers = {};
    listen.mockImplementation(async (name, cb) => {
      handlers[name] = cb;
      return () => {};
    });

    const { rerender } = render(
      <BootstrapSplash stage="installing_deps" message={null} attempt={1} />,
    );
    await waitFor(() => expect(handlers['bootstrap-log']).toBeTypeOf('function'));

    await act(async () => {
      handlers['bootstrap-log']({
        payload: { attempt: 1, stage: 'creating_venv', line: 'Creating virtualenv at .venv' },
      });
    });

    // Proof for attempt 1: the step reads as done.
    await waitFor(() => {
      expect(screen.getByText('Creating Python virtual environment…').className).toMatch(
        /text-fg-muted/,
      );
    });

    // Attempt 2 starts, and skips straight past venv creation.
    rerender(<BootstrapSplash stage="installing_deps" message={null} attempt={2} />);

    await waitFor(() => {
      expect(screen.getByText('Creating Python virtual environment…').className).not.toMatch(
        /text-fg-muted/,
      );
    });
  });
});
