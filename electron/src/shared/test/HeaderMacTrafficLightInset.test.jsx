/** macOS native overlay controls need space without shifting browser UI. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';

import Header from '../components/Header';

vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    minimize: vi.fn(async () => {}),
    toggleMaximize: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
  }),
}));

// Capture the original descriptor (inherited getter in jsdom, so this is
// `undefined`) so afterEach can restore the real state instead of leaving
// `navigator.platform` pinned to whichever test ran last — otherwise these
// tests become order-dependent and can leak into any later test in this
// file/environment that reads `navigator.platform`.
const originalPlatformDescriptor = Object.getOwnPropertyDescriptor(navigator, 'platform');

function setPlatform(value) {
  Object.defineProperty(navigator, 'platform', { value, configurable: true });
}

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  if (originalPlatformDescriptor) {
    Object.defineProperty(navigator, 'platform', originalPlatformDescriptor);
  } else {
    delete navigator.platform;
  }
});

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Header mode="dub" setMode={() => {}} modelStatus="idle" />
    </QueryClientProvider>,
  );
}

describe('Header left block — macOS traffic-light inset', () => {
  it('gets the mac inset class on macOS', () => {
    setPlatform('MacIntel');
    window.__TAURI_INTERNALS__ = {};
    const { container } = renderHeader();
    expect(container.querySelector('.header-area__left--mac-inset')).not.toBeNull();
  });

  it.each(['MacIntel', 'iPhone', 'iPad'])(
    'does not reserve native chrome in a %s browser',
    (platform) => {
      setPlatform(platform);
      const { container } = renderHeader();
      expect(container.querySelector('.header-area__left--mac-inset')).toBeNull();
    },
  );

  it('macOS config supplies native overlay controls', () => {
    const config = JSON.parse(
      fs.readFileSync(
        path.resolve(import.meta.dirname, '../../src-tauri/tauri.macos.conf.json'),
        'utf8',
      ),
    );
    // The platform windows array replaces the base; omitted decorations defaults true.
    expect(config.app.windows[0].decorations).not.toBe(false);
    expect(config.app.windows[0].titleBarStyle).toBe('Overlay');
  });

  it('does not get the inset on Windows', () => {
    setPlatform('Win32');
    const { container } = renderHeader();
    expect(container.querySelector('.header-area__left--mac-inset')).toBeNull();
  });

  it('does not get the inset on Linux', () => {
    setPlatform('Linux x86_64');
    const { container } = renderHeader();
    expect(container.querySelector('.header-area__left--mac-inset')).toBeNull();
  });
});
