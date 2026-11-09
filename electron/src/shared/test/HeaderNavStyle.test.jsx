/**
 * Header ↔ navigation style seam.
 *
 * The titlebar tabs render INSIDE the header row, taking the space the
 * breadcrumb + wordmark normally hold. That swap is the one place the two
 * navigation skins can collide: leave the breadcrumb in and the bar says
 * where you are twice; leave the tabs out in tabs mode and the app has no
 * navigation at all (the rail isn't rendered either).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import Header from '../components/Header';

const windowActions = vi.hoisted(() => ({
  minimize: vi.fn(async () => {}),
  toggleMaximize: vi.fn(async () => {}),
  close: vi.fn(async () => {}),
}));

vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => windowActions }));

const originalPlatformDescriptor = Object.getOwnPropertyDescriptor(navigator, 'platform');

// Pretend to run on a given OS (navigator.platform is a prototype getter, so a
// getter spy is enough) and optionally inside the desktop shell.
let platformSpy;
function pretendToRun(platform, { shell = false } = {}) {
  platformSpy?.mockRestore();
  platformSpy = vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform);
  if (shell) window.__TAURI_INTERNALS__ = {};
}

afterEach(() => {
  delete window.__TAURI_INTERNALS__;
  if (originalPlatformDescriptor) {
    Object.defineProperty(navigator, 'platform', originalPlatformDescriptor);
  } else {
    delete navigator.platform;
  }
  platformSpy?.mockRestore();
  vi.clearAllMocks();
  vi.useRealTimers();
});

function renderHeader(props, engines) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(['sysinfo'], {});
  if (engines) qc.setQueryData(['engines'], engines);
  return render(
    <QueryClientProvider client={qc}>
      <Header mode="dub" setMode={() => {}} modelStatus="idle" {...props} />
    </QueryClientProvider>,
  );
}

describe('Header — rail mode (default)', () => {
  it('cycles all three active engines and pauses while the panel is open', () => {
    vi.useFakeTimers();
    renderHeader(
      { onFlushMemory: vi.fn() },
      {
        tts: {
          active: 'omni',
          backends: [
            { id: 'omni', display_name: 'VoiceStudio (k2-fsa/OmniVoice, 600+ languages)' },
          ],
        },
        asr: { active: 'whisper', backends: [{ id: 'whisper', display_name: 'Whisper' }] },
        llm: { active: 'off', backends: [{ id: 'off', display_name: 'Off (no LLM)' }] },
      },
    );
    const trigger = screen.getByRole('button', { name: 'Engines' });
    expect(trigger).toHaveTextContent('TTS · OmniVoice');
    act(() => vi.advanceTimersByTime(4000));
    expect(trigger).toHaveTextContent('ASR · Whisper');
    act(() => vi.advanceTimersByTime(4000));
    expect(trigger).toHaveTextContent('LLM · Off');
    fireEvent.click(trigger);
    act(() => vi.advanceTimersByTime(4000));
    expect(trigger).toHaveTextContent('LLM · Off');
  });
  it('shows the selected engine name on the title-bar trigger', () => {
    renderHeader(
      { onFlushMemory: vi.fn() },
      {
        tts: {
          active: 'kitten',
          backends: [
            { id: 'kitten', display_name: 'KittenTTS (English, 8 preset voices)', available: true },
          ],
        },
      },
    );
    const trigger = screen.getByRole('button', { name: 'Engines' });
    expect(trigger).toHaveTextContent('KittenTTS');
    expect(trigger).toHaveAttribute('title', 'KittenTTS (English, 8 preset voices)');
  });
  it('opens combined engine and memory controls from the global shortcut', () => {
    renderHeader({ onFlushMemory: vi.fn() });
    fireEvent(window, new Event('engine-quick-switch'));
    expect(screen.getByRole('dialog', { name: 'Engines' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Flush caches/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Speech' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Transcription' }), {
      button: 0,
      ctrlKey: false,
    });
    expect(screen.getByRole('tab', { name: 'Transcription' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByRole('button', { name: /Unload all/ }).closest('details'),
    ).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('Memory management'));
    expect(screen.getByRole('button', { name: /Unload all/ })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Engines' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Engines' })).toHaveFocus();
  });
  it('keeps the breadcrumb and wordmark, and renders no tab strip', () => {
    const { container } = renderHeader({});
    expect(container.querySelector('.tabstrip')).toBeNull();
    expect(container.querySelector('.header-area--tabs')).toBeNull();
    expect(screen.queryByTestId('titletab-dub')).toBeNull();
    // Breadcrumb (current view) + centred wordmark both stay.
    expect(container.textContent).toMatch(/VoiceStudio/);
    expect(screen.getByTestId('voice-studio-logo')).toBeInTheDocument();
    expect(container.textContent).toMatch(/Dub/);
  });

  describe('window chrome per platform', () => {
    const controls = () => screen.queryByTestId('window-controls');

    it('Windows shell: custom controls drive the window API', async () => {
      pretendToRun('Win32', { shell: true });
      renderHeader({});
      const bar = within(controls());
      expect(bar.getAllByRole('button')).toHaveLength(3);

      fireEvent.click(bar.getByRole('button', { name: 'Minimize window' }));
      await waitFor(() => expect(windowActions.minimize).toHaveBeenCalledTimes(1));
      fireEvent.click(bar.getByRole('button', { name: 'Maximize or restore window' }));
      await waitFor(() => expect(windowActions.toggleMaximize).toHaveBeenCalledTimes(1));
      expect(windowActions.close).not.toHaveBeenCalled();
    });

    it('macOS shell: native traffic lights replace the custom controls', () => {
      pretendToRun('MacIntel', { shell: true });
      renderHeader({});
      expect(controls()).toBeNull();
      expect(screen.queryAllByRole('button', { name: /(minimize|close) window/i })).toHaveLength(0);
    });

    it.each([['MacIntel'], ['Win32'], ['Linux x86_64']])(
      'plain browser on %s has no window controls',
      (platform) => {
        pretendToRun(platform);
        renderHeader({});
        expect(controls()).toBeNull();
      },
    );

    it('only macOS gets the traffic-light inset on the breadcrumb block', () => {
      const insetOn = (platform) => {
        pretendToRun(platform, { shell: true });
        const view = renderHeader({});
        const found = view.container.querySelector('.header-area__lead--mac-inset') !== null;
        view.unmount();
        return found;
      };
      expect({
        mac: insetOn('MacIntel'),
        win: insetOn('Win32'),
        linux: insetOn('Linux x86_64'),
      }).toEqual({ mac: true, win: false, linux: false });
    });
  });
});

describe('Header — titlebar tabs mode', () => {
  it('renders the tab strip in the title bar instead of the breadcrumb', () => {
    const { container } = renderHeader({ navStyle: 'tabs' });
    expect(container.querySelector('.header-area--tabs')).not.toBeNull();
    expect(container.querySelector('.tabstrip')).not.toBeNull();
    expect(screen.getByTestId('titletab-dub')).toHaveClass('is-active');
  });

  it('drops the centred wordmark — the tabs need that room', () => {
    const { container } = renderHeader({ navStyle: 'tabs' });
    expect(container.textContent).not.toMatch(/VoiceStudio/);
    expect(screen.queryByTestId('voice-studio-logo')).toBeNull();
  });
});
