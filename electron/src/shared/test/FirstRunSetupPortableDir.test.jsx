/**
 * Portable mode must let the user choose the folder.
 *
 * Portable used to be pinned to `OmniVoiceStudio-Data` beside the app, with no
 * picker — the one storage row on the setup screen you could look at but not
 * change. Installed mode had a picker for all three of its directories.
 *
 * The pin existed for a real reason: `portable_base()` is computed from the
 * executable's location and never read from config, which is what makes a
 * portable install self-discovering (plug the drive into another machine and
 * the app finds its data). A user-chosen folder breaks that unless the
 * location is recorded somewhere findable — a pointer file beside the app,
 * falling back to the per-user config when the app folder is read-only.
 *
 * These tests pin the FRONTEND half of that contract: the row is pickable, the
 * chosen path reaches `complete_setup` under the key Rust deserializes
 * (`portableDir` → `InstallPlan::portable_dir`), and the UI tells the truth
 * about which mechanism a relocation will get.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

const invokeMock = vi.fn();
const openMock = vi.fn();

vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a) => invokeMock(...a) }));
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: (...a) => openMock(...a) }));

import FirstRunSetup from '../components/FirstRunSetup';

const ANCHOR = '/Applications';
const ANCHOR_DEFAULT = `${ANCHOR}/OmniVoiceStudio-Data`;

const setupState = ({ anchorWritable = true, baseDir = ANCHOR_DEFAULT } = {}) => ({
  firstRun: true,
  os: 'macos',
  defaults: {
    installMode: 'portable',
    envDir: '/env',
    dataDir: '/data',
    modelsDir: '/models',
    region: 'auto',
    updateChannel: 'stable',
    torchVariant: 'auto',
  },
  portable: {
    available: true,
    baseDir,
    reason: null,
    defaultDir: ANCHOR_DEFAULT,
    anchorDir: ANCHOR,
    anchorWritable,
  },
  requirements: { envBytes: 1e9, modelsBytes: 1e9, dataBytes: 1e9 },
  hardware: { kind: 'apple', name: 'M2' },
});

const wireInvoke = (state) => {
  invokeMock.mockImplementation(async (cmd) => {
    if (cmd === 'get_setup_state') return state;
    if (cmd === 'check_install_target') return { writable: true, freeBytes: 500e9 };
    if (cmd === 'complete_setup') return undefined;
    return undefined;
  });
};

const renderSetup = () =>
  render(
    <I18nextProvider i18n={i18n}>
      <FirstRunSetup />
    </I18nextProvider>,
  );

beforeEach(async () => {
  await i18n.changeLanguage('en');
  invokeMock.mockReset();
  openMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('FirstRunSetup — portable folder is choosable', () => {
  it('sends the chosen folder to complete_setup as portableDir', async () => {
    wireInvoke(setupState());
    openMock.mockResolvedValue('/Volumes/SSD/VoiceStudio');
    renderSetup();

    await waitFor(() => expect(screen.getByText(ANCHOR_DEFAULT)).toBeTruthy());

    // The portable row must offer a picker at all — this is the regression:
    // it previously rendered read-only while installed mode had three pickers.
    const pickers = screen.getAllByRole('button', { name: /change|browse|choose|pick|folder/i });
    expect(pickers.length).toBeGreaterThan(0);
    await act(async () => {
      pickers[0].click();
    });

    await waitFor(() => expect(screen.getByText('/Volumes/SSD/VoiceStudio')).toBeTruthy());

    // The target check is debounced 250 ms, and until it lands the plan counts
    // as `loading` and the button stays disabled — so wait for it to enable
    // rather than clicking into a no-op.
    const startBtn = () =>
      screen.getAllByRole('button').find((b) => /start installation/i.test(b.textContent || ''));
    await waitFor(
      () => {
        expect(startBtn()).toBeTruthy();
        expect(startBtn().disabled).toBe(false);
      },
      { timeout: 4000 },
    );
    await act(async () => {
      startBtn().click();
    });

    await waitFor(() => {
      const call = invokeMock.mock.calls.find(([cmd]) => cmd === 'complete_setup');
      expect(call).toBeTruthy();
      // The key name is the wire contract with InstallPlan::portable_dir —
      // rename either side and the folder is silently ignored.
      expect(call[1].plan.portableDir).toBe('/Volumes/SSD/VoiceStudio');
      expect(call[1].plan.installMode).toBe('portable');
    });
  });

  it('localizes a lifecycle stop failure instead of exposing the Rust error code', async () => {
    const state = setupState();
    invokeMock.mockImplementation(async (cmd) => {
      if (cmd === 'get_setup_state') return state;
      if (cmd === 'check_install_target') return { writable: true, freeBytes: 500e9 };
      if (cmd === 'complete_setup') throw new Error('backend_stop_failed');
      return undefined;
    });
    renderSetup();

    const startBtn = () =>
      screen
        .getAllByRole('button')
        .find((button) => /start installation/i.test(button.textContent || ''));
    await waitFor(
      () => {
        expect(startBtn()).toBeTruthy();
        expect(startBtn().disabled).toBe(false);
      },
      { timeout: 4000 },
    );
    await act(async () => {
      startBtn().click();
    });

    expect(await screen.findByText(/couldn't safely stop the previous backend/i)).toBeTruthy();
    expect(screen.queryByText('backend_stop_failed')).toBeNull();
  });

  it('promises portability only for a folder INSIDE the app directory', async () => {
    // Only that case can be stored as a relative path, which is what survives
    // the mount path changing (/Volumes/Stick on one machine, E:\\ on the next).
    wireInvoke(setupState({ anchorWritable: true, baseDir: `${ANCHOR}/MyVoiceData` }));
    renderSetup();
    await waitFor(() => expect(screen.getByText(`${ANCHOR}/MyVoiceData`)).toBeTruthy());
    expect(screen.getByText(/relative path/i).textContent).toMatch(
      /another machine or drive letter/i,
    );
  });

  it('does NOT promise portability for a folder outside the app directory', async () => {
    // The overclaim CodeRabbit caught (#1404): an absolute pointer cannot
    // survive a different mount path, so the cross-machine wording must not
    // appear here even though the anchor is writable.
    wireInvoke(setupState({ anchorWritable: true, baseDir: '/Volumes/SSD/VoiceStudio' }));
    renderSetup();
    await waitFor(() => expect(screen.getByText('/Volumes/SSD/VoiceStudio')).toBeTruthy());
    expect(screen.queryByText(/relative path/i)).toBeNull();
    expect(screen.getByText(/remembered on this machine/i)).toBeTruthy();
  });

  it('admits a relocation is machine-bound when the app folder is read-only', async () => {
    wireInvoke(setupState({ anchorWritable: false, baseDir: `${ANCHOR}/MyVoiceData` }));
    renderSetup();
    await waitFor(() => expect(screen.getByText(`${ANCHOR}/MyVoiceData`)).toBeTruthy());
    expect(screen.queryByText(/relative path/i)).toBeNull();
    expect(screen.getByText(/remembered on this machine/i)).toBeTruthy();
  });

  it('shows no relocation notice when the default folder is used', async () => {
    wireInvoke(setupState({ baseDir: ANCHOR_DEFAULT }));
    renderSetup();
    await waitFor(() => expect(screen.getByText(ANCHOR_DEFAULT)).toBeTruthy());
    expect(screen.queryByText(/remembered on this machine/i)).toBeNull();
    expect(screen.queryByText(/relative path/i)).toBeNull();
  });
});
