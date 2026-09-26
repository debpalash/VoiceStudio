import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const invokeMock = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args) => invokeMock(...args),
}));

import {
  inTauri,
  checkMicrophone,
  checkAccessibility,
  openMicrophoneSettings,
  openAccessibilitySettings,
  openInputMonitoringSettings,
} from './permissions';

let warnSpy;

beforeEach(() => {
  invokeMock.mockReset();
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  warnSpy.mockRestore();
});

describe('permissions — browser fallback (no Tauri shell)', () => {
  it('inTauri() is false', () => {
    expect(inTauri()).toBe(false);
  });

  it('checkMicrophone resolves "unknown" without touching invoke', async () => {
    await expect(checkMicrophone()).resolves.toBe('unknown');
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it('checkAccessibility resolves true without touching invoke', async () => {
    await expect(checkAccessibility()).resolves.toBe(true);
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it('every open-settings deep-link resolves false without throwing', async () => {
    await expect(openMicrophoneSettings()).resolves.toBe(false);
    await expect(openAccessibilitySettings()).resolves.toBe(false);
    await expect(openInputMonitoringSettings()).resolves.toBe(false);
    expect(invokeMock).not.toHaveBeenCalled();
  });
});

describe('permissions — inside the Tauri shell', () => {
  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
  });

  it.each(['granted', 'denied', 'prompt', 'unknown'])(
    'checkMicrophone passes "%s" through',
    async (state) => {
      invokeMock.mockResolvedValue(state);
      await expect(checkMicrophone()).resolves.toBe(state);
      expect(invokeMock).toHaveBeenCalledWith('check_microphone');
    },
  );

  it('coerces an unexpected shell value to "unknown"', async () => {
    invokeMock.mockResolvedValue('whatever-new-state');
    await expect(checkMicrophone()).resolves.toBe('unknown');
  });

  it('a failing probe degrades to unknown / true (never blocks)', async () => {
    invokeMock.mockRejectedValue(new Error('command check_microphone not found'));
    await expect(checkMicrophone()).resolves.toBe('unknown');
    await expect(checkAccessibility()).resolves.toBe(true);
  });

  it('checkAccessibility maps the boolean through', async () => {
    invokeMock.mockResolvedValue(false);
    await expect(checkAccessibility()).resolves.toBe(false);
    invokeMock.mockResolvedValue(true);
    await expect(checkAccessibility()).resolves.toBe(true);
  });

  it('openMicrophoneSettings resolves true when the pane opened', async () => {
    invokeMock.mockResolvedValue(undefined);
    await expect(openMicrophoneSettings()).resolves.toBe(true);
    expect(invokeMock).toHaveBeenCalledWith('open_microphone_settings');
  });

  it('openMicrophoneSettings resolves false on the Linux "settings:" rejection', async () => {
    invokeMock.mockRejectedValue('settings: no microphone permission pane on this OS');
    await expect(openMicrophoneSettings()).resolves.toBe(false);
  });

  it('openInputMonitoringSettings resolves false on the non-macOS rejection', async () => {
    invokeMock.mockRejectedValue('settings: input monitoring settings are macOS-only');
    await expect(openInputMonitoringSettings()).resolves.toBe(false);
    expect(invokeMock).toHaveBeenCalledWith('open_input_monitoring_settings');
  });
});
