import { EventEmitter } from 'node:events';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  blankFallbackUrl,
  BLANK_CACHE_REPAIR_URL,
  fallbackLocale,
  installBlankWindowGuard,
  localizedFallbackCopy,
} from './blank-window-guard';

afterEach(() => vi.useRealTimers());

describe('blank window fallback', () => {
  it('uses the same locale families as the renderer', () => {
    expect(fallbackLocale('pt-BR')).toBe('pt');
    expect(fallbackLocale('zh-Hant-TW')).toBe('zh-TW');
    expect(fallbackLocale('zh_Hans_CN')).toBe('zh-CN');
    expect(fallbackLocale('unknown')).toBe('en');
  });

  it('builds a self-contained localized recovery page without raw URL injection', () => {
    const target = "http://localhost:3902/?x=</script><script>alert('x')</script>";
    const url = blankFallbackUrl('de-DE');
    const html = decodeURIComponent(url.slice(url.indexOf(',') + 1));
    expect(html).toContain('Etwas ist schief gelaufen');
    expect(html).toContain('Versuchen Sie es erneut');
    expect(html).not.toContain(target);
    expect(html).toContain(BLANK_CACHE_REPAIR_URL);
  });

  it('stays aligned with every renderer locale catalogue', () => {
    const directory = join(process.cwd(), 'src/renderer/src/i18n/locales');
    const locales = readdirSync(directory).filter((file) => file.endsWith('.json'));
    expect(locales).toHaveLength(21);
    for (const file of locales) {
      const locale = file.slice(0, -5);
      const catalogue = JSON.parse(readFileSync(join(directory, file), 'utf8')) as {
        common: { error: string };
        errors: { tryAgain: string };
      };
      expect(localizedFallbackCopy(locale)).toEqual({
        title: catalogue.common.error,
        retry: catalogue.errors.tryAgain,
      });
    }
  });

  it('spends a bounded reload budget before showing the fallback', async () => {
    vi.useFakeTimers();
    const contents = Object.assign(new EventEmitter(), {
      isDestroyed: () => false,
      executeJavaScript: vi.fn().mockResolvedValue(false),
    });
    const windowEvents = new EventEmitter();
    const loadURL = vi.fn(async (url: string) => {
      if (url.startsWith('app://')) contents.emit('did-navigate', {}, url);
    });
    const repair = vi.fn().mockResolvedValue(undefined);
    const win = Object.assign(windowEvents, {
      isDestroyed: () => false,
      webContents: contents,
      loadURL,
    });
    const stop = installBlankWindowGuard(
      win as never,
      'app://voicestudio/index.html',
      'en-US',
      undefined,
      repair,
    );

    await vi.advanceTimersByTimeAsync(12_000 + 6_000 + 12_000 + 18_000);

    expect(loadURL).toHaveBeenCalledTimes(4);
    expect(loadURL.mock.calls.slice(0, 3).every(([url]) => String(url).startsWith('app://'))).toBe(
      true,
    );
    expect(String(loadURL.mock.calls[3]?.[0])).toMatch(/^data:text\/html/);
    const navigation = { preventDefault: vi.fn() };
    contents.emit('will-navigate', navigation, BLANK_CACHE_REPAIR_URL);
    await Promise.resolve();
    expect(navigation.preventDefault).toHaveBeenCalledOnce();
    expect(repair).toHaveBeenCalledOnce();
    contents.emit('will-navigate', navigation, BLANK_CACHE_REPAIR_URL);
    expect(repair).toHaveBeenCalledOnce();
    stop();
  });

  it('tears down safely after Electron destroys the window', () => {
    let destroyed = false;
    const contents = Object.assign(new EventEmitter(), {
      isDestroyed: () => destroyed,
      executeJavaScript: vi.fn().mockResolvedValue(true),
    });
    const windowEvents = new EventEmitter();
    const win = Object.assign(windowEvents, {
      isDestroyed: () => destroyed,
      loadURL: vi.fn().mockResolvedValue(undefined),
    });
    Object.defineProperty(win, 'webContents', {
      get() {
        if (destroyed) throw new TypeError('Object has been destroyed');
        return contents;
      },
    });

    const stop = installBlankWindowGuard(win as never, 'app://voicestudio/index.html', 'en-US');
    destroyed = true;

    expect(() => windowEvents.emit('closed')).not.toThrow();
    expect(() => stop()).not.toThrow();
  });
});

describe('stale blank-window probes', () => {
  const setup = (resolveProbe: () => Promise<boolean>) => {
    let destroyed = false;
    const contents = Object.assign(new EventEmitter(), {
      isDestroyed: () => destroyed,
      executeJavaScript: vi.fn(resolveProbe),
    });
    const loadURL = vi.fn().mockResolvedValue(undefined);
    const win = Object.assign(new EventEmitter(), {
      isDestroyed: () => destroyed,
      webContents: contents,
      loadURL,
    });
    const stop = installBlankWindowGuard(win as never, 'app://voicestudio/index.html', 'en-US');
    return { contents, win, loadURL, stop, destroy: () => (destroyed = true) };
  };
  const probeResult = () => {
    let settle: (value: boolean) => void = () => {};
    const promise = new Promise<boolean>((resolve) => {
      settle = resolve;
    });
    return { promise, settle };
  };

  it('does not reload after a main-frame navigation started during the probe', async () => {
    vi.useFakeTimers();
    const probe = probeResult();
    const { contents, loadURL } = setup(() => probe.promise);
    await vi.advanceTimersByTimeAsync(12_000);
    contents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false });
    probe.settle(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(loadURL).not.toHaveBeenCalled();
  });

  it('still recovers when a child-frame navigation happened during the probe', async () => {
    vi.useFakeTimers();
    const probe = probeResult();
    const { contents, loadURL } = setup(() => probe.promise);
    await vi.advanceTimersByTimeAsync(12_000);
    contents.emit('did-start-navigation', { isMainFrame: false, isSameDocument: false });
    probe.settle(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(loadURL).toHaveBeenCalledTimes(1);
  });

  it('re-arms the guard when the superseding navigation never commits', async () => {
    vi.useFakeTimers();
    const first = probeResult();
    const { contents, loadURL } = setup(() => first.promise);
    await vi.advanceTimersByTimeAsync(12_000);
    contents.emit('did-start-navigation', { isMainFrame: true, isSameDocument: false });
    first.settle(false);
    contents.executeJavaScript.mockResolvedValue(false);
    await vi.advanceTimersByTimeAsync(6_000);
    expect(loadURL).toHaveBeenCalledTimes(1);
  });

  it('ignores a probe that resolves after the guard stopped or the window closed', async () => {
    vi.useFakeTimers();
    for (const end of ['stop', 'destroy'] as const) {
      const probe = probeResult();
      const { loadURL, stop, destroy } = setup(() => probe.promise);
      await vi.advanceTimersByTimeAsync(12_000);
      if (end === 'stop') stop();
      else destroy();
      probe.settle(false);
      await vi.advanceTimersByTimeAsync(0);
      expect(loadURL).not.toHaveBeenCalled();
    }
  });
});
