import { afterEach, expect, it, vi } from 'vitest';

const observed = vi.hoisted(() => ({ randomUUIDAtApplicationLoad: vi.fn() }));

vi.mock('react-dom/client', () => ({ createRoot: vi.fn(() => ({ render: vi.fn() })) }));
vi.mock('./features/transcriptions/capture-widget', () => ({ CaptureWidget: () => null }));
vi.mock('./features/browser/site-browser-host', () => ({ SiteBrowserHost: () => null }));
vi.mock('@shared/utils/consoleBuffer', () => ({ installConsoleCapture: vi.fn() }));
vi.mock('./i18n', () => ({}));
vi.mock('./hooks/use-appearance', () => ({}));
vi.mock('./app', () => {
  observed.randomUUIDAtApplicationLoad(crypto.randomUUID);
  return { App: () => null };
});
vi.mock('./lib/preload-recovery', () => ({ installPreloadRecovery: vi.fn() }));
vi.mock('./lib/global-error-recovery', () => ({ installGlobalErrorRecovery: vi.fn() }));
vi.mock('./lib/foreign-dom-guard', () => ({ installForeignDomGuard: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

it('installs the UUID fallback before application modules load', async () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => {
    bytes.fill(0);
    return bytes;
  });
  vi.stubGlobal('crypto', { getRandomValues });
  document.body.innerHTML = '<div id="root"></div>';

  await import('./main');

  expect(observed.randomUUIDAtApplicationLoad).toHaveBeenCalledWith(expect.any(Function));
  expect(crypto.randomUUID()).toBe('00000000-0000-4000-8000-000000000000');
});
