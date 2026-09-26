import { beforeEach, describe, expect, it, vi } from 'vitest';

import { applyUiScale, responsiveShellWidth } from './uiScaleEngine';

describe('responsiveShellWidth', () => {
  it('uses visible CSS pixels for native Tauri zoom', () => {
    expect(responsiveShellWidth(1412, 1.75, 'native')).toBeCloseTo(806.86, 2);
  });

  it('does not normalize the already-shrunk CSS zoom layout twice', () => {
    expect(responsiveShellWidth(807, 1.75, 'css')).toBe(807);
  });

  it('treats an invalid scale as 1', () => {
    expect(responsiveShellWidth(900, 0, 'native')).toBe(900);
  });
});

describe('applyUiScale', () => {
  beforeEach(() => {
    delete window.__TAURI_INTERNALS__;
    delete document.documentElement.dataset.uiScaleEngine;
  });

  it('uses native webview zoom in Tauri and only then disables CSS zoom', async () => {
    window.__TAURI_INTERNALS__ = {};
    const setZoom = vi.fn().mockResolvedValue(undefined);

    await expect(
      applyUiScale(0.85, async () => ({ getCurrentWebview: () => ({ setZoom }) })),
    ).resolves.toBe('native');

    expect(setZoom).toHaveBeenCalledWith(0.85);
    expect(document.documentElement.dataset.uiScaleEngine).toBe('native');
  });

  it('keeps the viewport-filling CSS path in a browser', async () => {
    const loadWebview = vi.fn();

    await expect(applyUiScale(1.15, loadWebview)).resolves.toBe('css');

    expect(loadWebview).not.toHaveBeenCalled();
    expect(document.documentElement.dataset.uiScaleEngine).toBe('css');
  });

  it('keeps CSS zoom active when native zoom is unavailable', async () => {
    window.__TAURI_INTERNALS__ = {};

    await expect(
      applyUiScale(0.9, async () => {
        throw new Error('webview zoom unavailable');
      }),
    ).resolves.toBe('css');

    expect(document.documentElement.dataset.uiScaleEngine).toBe('css');
  });

  it('serializes overlapping native zoom changes so the latest scale wins', async () => {
    window.__TAURI_INTERNALS__ = {};
    /** @type {(() => void) | undefined} */
    let finishFirst;
    const setZoom = vi.fn((/** @type {number} */ scale) => {
      if (scale === 0.85) {
        return new Promise((resolve) => {
          finishFirst = () => resolve(undefined);
        });
      }
      return Promise.resolve();
    });
    const loadWebview = async () => ({ getCurrentWebview: () => ({ setZoom }) });

    const first = applyUiScale(0.85, loadWebview);
    const latest = applyUiScale(1.2, loadWebview);
    await vi.waitFor(() => expect(setZoom).toHaveBeenCalledTimes(1));
    expect(setZoom).toHaveBeenLastCalledWith(0.85);

    expect(finishFirst).toBeTypeOf('function');
    finishFirst?.();
    await Promise.all([first, latest]);

    expect(setZoom.mock.calls.map(([scale]) => scale)).toEqual([0.85, 1.2]);
    expect(document.documentElement.dataset.uiScaleEngine).toBe('native');
  });
});
