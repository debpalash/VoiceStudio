import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

import HotkeyTab from './HotkeyTab';

const shortcutEvents = vi.hoisted(() => ({}));

// Recording is only armed in the desktop shell; pretend we are in it and
// stub the two shortcut IPC commands.
vi.mock('./native', () => ({ isTauri: () => true }));
vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(async (cmd) =>
    cmd === 'get_effective_dictation_shortcut'
      ? {
          accelerator: 'CmdOrCtrl+Shift+Space',
          display: 'Ctrl+Shift+Space',
          backend: 'native',
        }
      : '',
  ),
}));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (name, handler) => {
    shortcutEvents[name] = handler;
    return vi.fn();
  }),
}));

async function startRecording() {
  render(<HotkeyTab />);
  // Wait for the mount-time shortcut load so state updates stay inside act().
  await screen.findByText('Ctrl+Shift+Space');
  fireEvent.click(screen.getByRole('button', { name: 'Record shortcut' }));
  expect(screen.getByText(/listening/)).toBeInTheDocument();
}

describe('HotkeyTab — recording feedback and cancel affordances', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('a modifier-less key press shows "add a modifier" feedback instead of silence', async () => {
    await startRecording();
    fireEvent.keyDown(window, { key: 'a', code: 'KeyA' });
    expect(screen.getByText(/Add a modifier/)).toBeInTheDocument();
    // Still recording — the button stays in its cancel state.
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('a pure modifier press (chord in progress) does NOT trigger the rejection message', async () => {
    await startRecording();
    fireEvent.keyDown(window, { key: 'Control', code: 'ControlLeft', ctrlKey: true });
    expect(screen.queryByText(/Add a modifier/)).toBeNull();
    expect(screen.getByText(/listening/)).toBeInTheDocument();
  });

  it('a modifier+key press captures the accelerator and clears the rejection state', async () => {
    await startRecording();
    fireEvent.keyDown(window, { key: 'a', code: 'KeyA' }); // rejected first
    fireEvent.keyDown(window, { key: 'a', code: 'KeyA', ctrlKey: true });
    expect(screen.getByText('Ctrl+A')).toBeInTheDocument();
    expect(screen.queryByText(/Add a modifier/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Record shortcut' })).toBeInTheDocument();
  });

  it('clicking the record button while recording cancels instead of re-arming', async () => {
    await startRecording();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText(/listening/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Record shortcut' })).toBeInTheDocument();
  });

  it('losing window focus cancels recording (no global key-swallower left armed)', async () => {
    await startRecording();
    fireEvent(window, new Event('blur'));
    expect(screen.queryByText(/listening/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Record shortcut' })).toBeInTheDocument();
  });

  it('Escape cancels recording', async () => {
    await startRecording();
    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByText(/listening/)).toBeNull();
  });

  it('shows the portal effective shortcut when registration finishes', async () => {
    render(<HotkeyTab />);
    await screen.findByText('Ctrl+Shift+Space');
    act(() => {
      shortcutEvents['dictation-shortcut-changed']({
        payload: {
          accelerator: 'CmdOrCtrl+Shift+Space',
          display: 'Meta+Shift+V',
          backend: 'portal',
        },
      });
    });
    expect(await screen.findByText('Meta+Shift+V')).toBeInTheDocument();
  });
});
