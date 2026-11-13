import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { SiteBrowserState } from '@shared/site-browser';
import { SiteBrowserPage } from './site-browser-page';

const mocks = vi.hoisted(() => ({
  browser: {
    close: vi.fn(async () => {}),
    state: vi.fn(),
    navigate: vi.fn(async () => {}),
    command: vi.fn(async () => {}),
    installed: vi.fn(),
    openExternal: vi.fn(async () => {}),
    bounds: vi.fn(async () => {}),
    onState: vi.fn(),
  },
  unsubscribe: vi.fn(),
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ browser: mocks.browser }),
  isMac: () => false,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
let onState: (state: SiteBrowserState) => void;
const ready = {
  url: 'https://voicestudio.sh/',
  title: 'VoiceStudio',
  loading: false,
  error: false,
  canGoBack: false,
  canGoForward: false,
};
beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    },
  });
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  mocks.browser.state.mockResolvedValue(ready);
  mocks.browser.installed.mockResolvedValue([{ id: 'firefox', name: 'Firefox' }]);
  mocks.browser.onState.mockImplementation((callback) => {
    onState = callback;
    return mocks.unsubscribe;
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  vi.unstubAllGlobals();
});

it('synchronizes the address and history, navigates typed sites, and stops or reloads', async () => {
  const { unmount } = render(<SiteBrowserPage />);
  const address = await screen.findByDisplayValue(ready.url);
  expect(screen.getByRole('button', { name: 'siteBrowser.back' })).toBeDisabled();
  fireEvent.change(address, { target: { value: 'example.com/docs' } });
  fireEvent.submit(address.closest('form')!);
  await waitFor(() =>
    expect(mocks.browser.navigate).toHaveBeenCalledWith('https://example.com/docs'),
  );
  act(() => onState({ ...ready, url: 'https://example.com/docs', canGoBack: true, loading: true }));
  fireEvent.click(screen.getByRole('button', { name: 'siteBrowser.back' }));
  fireEvent.click(screen.getByRole('button', { name: 'siteBrowser.stop' }));
  expect(mocks.browser.command).toHaveBeenCalledWith('back');
  expect(mocks.browser.command).toHaveBeenCalledWith('stop');
  act(() => onState(ready));
  fireEvent.click(screen.getByRole('button', { name: 'siteBrowser.reload' }));
  expect(mocks.browser.command).toHaveBeenCalledWith('reload');
  unmount();
  expect(mocks.unsubscribe).toHaveBeenCalled();
});

it('lists detected browsers and a system-default option with a working launch action', async () => {
  render(<SiteBrowserPage />);
  await screen.findByDisplayValue(ready.url);
  fireEvent.click(screen.getByRole('button', { name: 'siteBrowser.open_in' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Firefox' }));
  expect(mocks.browser.openExternal).toHaveBeenCalledWith('firefox');
  fireEvent.click(screen.getByRole('button', { name: 'siteBrowser.default_browser' }));
  expect(mocks.browser.openExternal).toHaveBeenCalledWith('default');
});

it('keeps controls available after navigation failure and rejects executable URLs', async () => {
  render(<SiteBrowserPage />);
  const address = await screen.findByDisplayValue(ready.url);
  fireEvent.change(address, { target: { value: 'javascript:alert(1)' } });
  fireEvent.submit(address.closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('siteBrowser.failed');
  expect(mocks.browser.navigate).not.toHaveBeenCalled();
  act(() => onState({ ...ready, error: true }));
  expect(screen.getByRole('button', { name: 'siteBrowser.reload' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'siteBrowser.open_in' })).toBeEnabled();
});

it('returns to Studio through the embedded view close action', async () => {
  render(<SiteBrowserPage />);
  await screen.findByDisplayValue(ready.url);
  fireEvent.click(screen.getByRole('button', { name: 'siteBrowser.return_studio' }));
  expect(mocks.browser.close).toHaveBeenCalled();
});

it('anchors the modal below the clicked item inside the right workspace and reports its body bounds', async () => {
  const workspace = document.createElement('main');
  workspace.dataset.slot = 'workspace-content';
  const anchor = document.createElement('button');
  workspace.append(anchor);
  document.body.append(workspace);
  vi.spyOn(workspace, 'getBoundingClientRect').mockReturnValue({
    left: 250,
    top: 0,
    right: 1024,
    bottom: 768,
  } as DOMRect);
  vi.spyOn(anchor, 'getBoundingClientRect').mockReturnValue({ left: 280, bottom: 140 } as DOMRect);
  const original = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
    function (this: HTMLElement) {
      if (this.className === 'site-browser-viewport')
        return { x: 268, y: 250, width: 738, height: 490 } as DOMRect;
      return original.call(this);
    },
  );
  try {
    render(<SiteBrowserPage anchor={anchor} />);
    const modal = screen.getByRole('dialog');
    expect(modal.style.top).toBe('152px');
    expect(modal.style.left).toBe('266px');
    expect(modal.style.width).toBe('742px');
    await waitFor(() =>
      expect(mocks.browser.bounds).toHaveBeenCalledWith({
        x: 268,
        y: 250,
        width: 738,
        height: 490,
      }),
    );
    fireEvent(modal, new Event('cancel', { cancelable: true }));
    expect(mocks.browser.close).toHaveBeenCalled();
  } finally {
    workspace.remove();
  }
});
