import { fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DesktopCaptureShortcutBridge from './DesktopCaptureShortcutBridge';

const { invoke, listen, handlers } = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  handlers: {},
}));
vi.mock('@tauri-apps/api/core', () => ({ invoke }));
vi.mock('@tauri-apps/api/event', () => ({ listen }));

describe('DesktopCaptureShortcutBridge', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
    invoke.mockReset().mockResolvedValue({
      accelerator: 'Ctrl+Alt+K',
      display: 'Ctrl+Alt+K',
      backend: 'native',
    });
    listen.mockReset().mockImplementation(async (name, handler) => {
      handlers[name] = handler;
      return vi.fn();
    });
  });

  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it('forwards the configured shortcut and disarms when a modifier is released first', async () => {
    render(<DesktopCaptureShortcutBridge />);
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('get_effective_dictation_shortcut'));
    fireEvent.keyDown(window, { code: 'KeyK', ctrlKey: true, altKey: true });
    fireEvent.keyUp(window, { code: 'ControlLeft', altKey: true });

    await waitFor(() => {
      expect(invoke).toHaveBeenCalledWith('request_dictation_capture', { action: 'start' });
      expect(invoke).toHaveBeenCalledWith('request_dictation_capture', { action: 'stop' });
    });
  });

  it('ignores auto-repeat and unrelated shortcuts', async () => {
    render(<DesktopCaptureShortcutBridge />);
    await waitFor(() => expect(invoke).toHaveBeenCalled());
    fireEvent.keyDown(window, {
      code: 'KeyK',
      ctrlKey: true,
      altKey: true,
      repeat: true,
    });
    fireEvent.keyDown(window, { code: 'KeyK', ctrlKey: true });
    await Promise.resolve();
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('uses a successfully rebound shortcut without remounting', async () => {
    render(<DesktopCaptureShortcutBridge />);
    await waitFor(() => expect(handlers['dictation-shortcut-changed']).toBeTypeOf('function'));
    handlers['dictation-shortcut-changed']({
      payload: { accelerator: 'Ctrl+Shift+Space', display: 'Ctrl+Shift+Space' },
    });
    fireEvent.keyDown(window, { code: 'Space', ctrlKey: true, shiftKey: true });
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('request_dictation_capture', { action: 'start' }),
    );
  });

  it('removes a subscription that resolves after unmount', async () => {
    let resolveListen;
    const unlisten = vi.fn();
    listen.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveListen = resolve;
      }),
    );
    const view = render(<DesktopCaptureShortcutBridge />);
    view.unmount();
    resolveListen(unlisten);

    await waitFor(() => expect(unlisten).toHaveBeenCalledOnce());
    expect(invoke).not.toHaveBeenCalled();
  });
});
