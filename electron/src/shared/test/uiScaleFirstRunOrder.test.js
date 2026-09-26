import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * #1849 — the legibility control must come before the long first run.
 *
 * `UiScaleSetup` is a client-side zoom: it makes no backend calls at all. It
 * was nonetheless gated on `backendReady`, so on a clean install the user
 * watched the whole bootstrap — and answered the macOS Accessibility prompt —
 * at whatever size the app had guessed, and was offered the size control only
 * once all of that had finished. Someone who cannot comfortably read the UI
 * had to get through the least readable part of the product first.
 *
 * Checked at the source level because the ordering lives in App.jsx's early
 * returns, and rendering App in jsdom to observe it would need the whole
 * backend, store and Tauri surface mocked — a test far more fragile than the
 * two facts it would be pinning.
 */
const app = readFileSync(resolve(process.cwd(), 'src/App.jsx'), 'utf8');

const gateIndex = app.indexOf('if (!uiScaleConfigured');
const hydrationIndex = app.indexOf('if (!setupChecked || !storeHydrated)');
// The brace matters: `if (!backendReady) return;` appears earlier inside an
// effect, and matching that instead would compare against the wrong line.
const backendGateIndex = app.indexOf('if (!backendReady) {');
const wizardIndex = app.indexOf('if (setupNeeded && backendReady)');

describe('first-run ordering: legibility before the backend gates', () => {
  it('still has the gate at all', () => {
    expect(gateIndex).toBeGreaterThan(-1);
  });

  it('does not wait for the backend', () => {
    const gateLine = app.slice(gateIndex, app.indexOf('\n', gateIndex));
    expect(gateLine).not.toContain('backendReady');
  });

  it('comes before the bootstrap progress screen and the setup wizard', () => {
    expect(backendGateIndex).toBeGreaterThan(-1);
    expect(wizardIndex).toBeGreaterThan(-1);
    expect(gateIndex).toBeLessThan(backendGateIndex);
    expect(gateIndex).toBeLessThan(wizardIndex);
  });

  it('still comes after store hydration', () => {
    // Reading `uiScaleConfigured` before the store hydrates would flash this
    // screen at someone who had already chosen a scale.
    expect(hydrationIndex).toBeGreaterThan(-1);
    expect(hydrationIndex).toBeLessThan(gateIndex);
  });
});
