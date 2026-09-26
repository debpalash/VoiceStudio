import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import UninstallPanel from './UninstallPanel';

const invoke = vi.fn();
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a) => invoke(...a) }));

const TARGETS = [
  {
    key: 'data',
    path: '/u/Library/Application Support/OmniVoice',
    size_bytes: 720 * 1024,
    exists: true,
    shared: false,
  },
  {
    key: 'env',
    path: '/u/Library/Application Support/com.debpalash.omnivoice-studio',
    size_bytes: 391,
    exists: true,
    shared: false,
  },
  { key: 'logs', path: '/u/Library/Logs/OmniVoice', size_bytes: 4096, exists: true, shared: false },
  {
    key: 'models',
    path: '/u/.cache/huggingface',
    size_bytes: 7.5 * 1024 ** 3,
    exists: true,
    shared: true,
  },
];

describe('UninstallPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.__TAURI_INTERNALS__ = {};
    invoke.mockImplementation(async (cmd) => (cmd === 'uninstall_scan' ? TARGETS : null));
  });

  const ready = async () => {
    render(<UninstallPanel />);
    await waitFor(() => expect(screen.getByTestId('uninstall-target-data')).toBeInTheDocument());
  };

  it('renders a 391-byte folder as bytes, not as "0 KB"', async () => {
    // The old formatter floored at KB, so the config folder read as empty.
    await ready();
    expect(screen.getByText('391 B')).toBeInTheDocument();
    expect(screen.getByText('720 KB')).toBeInTheDocument();
  });

  it('leaves the shared model cache out of the total until it is ticked', async () => {
    await ready();
    // 720 KB + 391 B + 4 KB — the 7.5 GB cache is opt-in and must not be counted.
    expect(screen.getByText(/3 locations/)).toBeInTheDocument();
    expect(screen.getByText(/724 KB will be freed/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('uninstall-include-models'));

    // Ticking it moves the total *in the panel* — the number is live, not a
    // surprise sprung on the user in the confirm dialog.
    await waitFor(() => expect(screen.getByText(/4 locations/)).toBeInTheDocument());
    expect(screen.getByText(/7\.5 GB will be freed/)).toBeInTheDocument();
  });

  it('gives each folder a bar sized to its share of what will be freed', async () => {
    await ready();
    const bar = (key) => screen.getByTestId(`uninstall-target-${key}-bar`).firstChild;
    // data is ~99% of the 724 KB being freed; env (391 B) is a sliver.
    expect(parseInt(bar('data').style.width, 10)).toBeGreaterThan(90);
    expect(parseInt(bar('env').style.width, 10)).toBeLessThan(5);
  });

  it('the confirm dialog lists exactly what is going, and warns only when the shared cache is in', async () => {
    await ready();
    fireEvent.click(screen.getByTestId('uninstall-open'));

    const summary = await screen.findByTestId('uninstall-summary');
    expect(summary.children).toHaveLength(3); // the cache is not ticked
    expect(screen.queryByTestId('uninstall-models-warning')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('uninstall-confirm')); // still gated on the typed word
    expect(invoke).not.toHaveBeenCalledWith('uninstall_purge', expect.anything());
  });

  it('warns in the dialog once the shared cache is included', async () => {
    await ready();
    fireEvent.click(screen.getByTestId('uninstall-include-models'));
    fireEvent.click(screen.getByTestId('uninstall-open'));

    expect(await screen.findByTestId('uninstall-models-warning')).toBeInTheDocument();
    expect(screen.getByTestId('uninstall-summary').children).toHaveLength(4);
  });

  it('will not purge until DELETE is typed', async () => {
    await ready();
    fireEvent.click(screen.getByTestId('uninstall-open'));
    const confirm = await screen.findByTestId('uninstall-confirm');
    expect(confirm).toBeDisabled();

    fireEvent.change(screen.getByTestId('uninstall-type-confirm'), { target: { value: 'DELETE' } });
    await waitFor(() => expect(confirm).toBeEnabled());

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith('uninstall_purge', { includeModels: false }),
    );
  });
});
