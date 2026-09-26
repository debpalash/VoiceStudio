/**
 * SetupWizard System Check permission rows: LED/chip per grant state,
 * macOS-only Accessibility row, denied → Open Settings, and the plain-browser
 * no-op (renders nothing without the Tauri shell).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));

import PermissionChecks, { permissionChip } from '../components/PermissionChecks';

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

describe('permissionChip — status → chip mapping', () => {
  it('maps the four states to ok/warn/neutral', () => {
    expect(permissionChip('granted')).toMatchObject({ led: 'ok', tone: 'success' });
    expect(permissionChip('denied')).toMatchObject({ led: 'warn', tone: 'warn' });
    expect(permissionChip('prompt')).toMatchObject({ led: 'neutral', tone: 'neutral' });
    expect(permissionChip('unknown')).toMatchObject({ led: 'neutral', tone: 'neutral' });
  });
});

describe('PermissionChecks — wizard rows', () => {
  it('renders nothing outside the Tauri shell (browser/dev no-op)', () => {
    const { container } = render(<PermissionChecks platform="mac" />);
    expect(container).toBeEmptyDOMElement();
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it('shows mic + Accessibility rows on macOS with live chips', async () => {
    window.__TAURI_INTERNALS__ = {};
    stubInvoke({ mic: 'granted', a11y: false });
    render(<PermissionChecks platform="mac" />);
    expect(screen.getByTestId('permission-checks')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Granted');
      expect(screen.getByTestId('perm-chip-accessibility')).toHaveTextContent('Denied');
    });
    // Denied a11y row exposes the deep-link.
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings' }));
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('open_accessibility_settings');
    });
  });

  it('hides the Accessibility row off-macOS and keeps the mic row honest on Linux', async () => {
    window.__TAURI_INTERNALS__ = {};
    stubInvoke({ mic: 'unknown' });
    render(<PermissionChecks platform="linux" />);
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Unknown');
    });
    expect(screen.queryByTestId('perm-row-accessibility')).not.toBeInTheDocument();
    // Non-granted states surface the per-OS guide line.
    expect(screen.getByText(/audio group/)).toBeInTheDocument();
  });

  it('denied mic shows the Open Settings deep-link and the per-OS fix path', async () => {
    window.__TAURI_INTERNALS__ = {};
    stubInvoke({ mic: 'denied' });
    render(<PermissionChecks platform="windows" />);
    await waitFor(() => {
      expect(screen.getByTestId('perm-chip-microphone')).toHaveTextContent('Denied');
    });
    expect(screen.getByText(/Privacy & security → Microphone/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings' }));
    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('open_microphone_settings');
    });
  });
});
