/**
 * Render regression test for PR #1159 — the "Setup failed" card crashed with
 * a TDZ ReferenceError instead of rendering.
 *
 * fail-before/pass-after: BootstrapSplash computed
 * `isUnrecoverable = isFailed && isUnrecoverableFailure(message, logs)`
 * BEFORE `const [logs, setLogs] = useState([])` — reading `logs` in its
 * temporal dead zone. Because of `&&` short-circuiting, the read only
 * happened when `stage === 'failed'`, i.e. the crash replaced the one screen
 * that exists to explain a failure. The sibling test file
 * (BootstrapSplashFailedRecovery.test.jsx) only exercises the
 * useBootstrapStage hook, which is why CI never rendered the failed branch
 * and missed the crash — these tests actually mount <BootstrapSplash
 * stage="failed">.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
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

describe('BootstrapSplash — stage="failed" renders (#1159 TDZ regression)', () => {
  it('renders the failure card with the error message and retry actions', () => {
    // Pre-#1159 this render threw `ReferenceError: Cannot access 'logs'
    // before initialization` — the assertions below never ran.
    render(<BootstrapSplash stage="failed" message="uv sync failed: exit code 1" />);

    expect(screen.getByText('uv sync failed: exit code 1')).toBeInTheDocument();
    // A recoverable failure must offer both retry paths.
    expect(screen.getByRole('button', { name: /^Retry/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Clean & Retry/ })).toBeInTheDocument();
  });

  it('unrecoverable failure (Intel Mac, #1112) renders without retry buttons', () => {
    // This is the exact expression that read `logs` in its TDZ:
    // isUnrecoverableFailure(message, logs) with isFailed === true.
    render(
      <BootstrapSplash
        stage="failed"
        message="Intel Macs can't run the local AI backend (PyTorch ships no macOS x86_64 wheels)."
      />,
    );

    expect(screen.getByText(/Retrying cannot fix this/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Retry/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Clean & Retry/ })).toBeNull();
  });
});
