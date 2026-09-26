/**
 * Settings → Permissions panel: live status rows for the OS grants the app's
 * default features use (mic everywhere, Accessibility on macOS), deep-link
 * buttons when denied, focus-recheck, and the graceful browser no-op.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const { toastMock } = vi.hoisted(() => {
  const fn = () => {};
  return {
    toastMock: Object.assign(fn, { error: fn, success: fn, dismiss: fn, loading: fn }),
  };
});
vi.mock('react-hot-toast', () => ({ default: toastMock, toast: toastMock }));

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));

import PermissionsPanel from '../components/settings/PermissionsPanel';

/** Route the invoke mock per command. */
function stubInvoke({ mic = 'granted', a11y = true } = {}) {
  invokeMock.mockImplementation(async (cmd) => {
    if (cmd === 'check_microphone') return mic;
    if (cmd === 'check_accessibility') return a11y;
    return undefined;
  });
}

beforeEach(() => {
  invokeMock.mockReset();
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
});

describe('PermissionsPanel — inside Tauri', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
  });

  it('renders the Microphone row with a Granted chip', async () => {
    stubInvoke({ mic: 'granted' });
    render(<PermissionsPanel platform="windows" />);
    expect(screen.getByText('Microphone')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Granted');
    });
    // No Accessibility row off-macOS — nothing to grant there.
    expect(screen.queryByText('Accessibility')).not.toBeInTheDocument();
    // Granted → no Open Settings button.
    expect(screen.queryByRole('button', { name: 'Open Settings' })).not.toBeInTheDocument();
  });

  it('denied mic → Denied chip, per-OS hint, and an Open Settings deep-link', async () => {
    stubInvoke({ mic: 'denied' });
    render(<PermissionsPanel platform="mac" />);
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Denied');
    });
    // The actionable per-OS path replaces the generic "why" line.
    expect(
      screen.getByText(/System Settings → Privacy & Security → Microphone/),
    ).toBeInTheDocument();

    const buttons = screen.getAllByRole('button', { name: 'Open Settings' });
    fireEvent.click(buttons[0]);
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('open_microphone_settings');
    });
  });

  it('shows the Accessibility row on macOS with its own deep-link when denied', async () => {
    stubInvoke({ mic: 'granted', a11y: false });
    render(<PermissionsPanel platform="mac" />);
    expect(screen.getByText('Accessibility')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-accessibility')).toHaveTextContent('Denied');
    });
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings' }));
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('open_accessibility_settings');
    });
  });

  it('re-probes on window focus so a grant flipped in System Settings shows up', async () => {
    stubInvoke({ mic: 'denied' });
    render(<PermissionsPanel platform="windows" />);
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Denied');
    });

    stubInvoke({ mic: 'granted' });
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Granted');
    });
  });

  it('renders the honest neutral chips for prompt/unknown', async () => {
    stubInvoke({ mic: 'prompt' });
    render(<PermissionsPanel platform="mac" />);
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Not asked yet');
    });
  });
});

describe('PermissionsPanel — browser / web UI (no Tauri)', () => {
  it('no-ops gracefully: explains instead of guessing, never invokes', () => {
    render(<PermissionsPanel platform="mac" />);
    expect(screen.getByText(/only readable in the desktop app/)).toBeInTheDocument();
    expect(screen.queryByText('Microphone')).not.toBeInTheDocument();
    expect(invokeMock).not.toHaveBeenCalled();
  });
});
