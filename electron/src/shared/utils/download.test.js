import { describe, it, expect, vi } from 'vitest';
import { parseFilenameFromContentDisposition, browserDownload } from './download';

describe('parseFilenameFromContentDisposition', () => {
  it('returns null for missing/empty headers', () => {
    expect(parseFilenameFromContentDisposition(null)).toBe(null);
    expect(parseFilenameFromContentDisposition('')).toBe(null);
    expect(parseFilenameFromContentDisposition('inline')).toBe(null);
  });

  it('parses a plain filename', () => {
    expect(parseFilenameFromContentDisposition('attachment; filename="clip.wav"')).toBe('clip.wav');
    expect(parseFilenameFromContentDisposition('attachment; filename=clip.wav')).toBe('clip.wav');
  });

  it('prefers and decodes the RFC 5987 UTF-8 form', () => {
    expect(parseFilenameFromContentDisposition("attachment; filename*=UTF-8''my%20clip.wav")).toBe(
      'my clip.wav',
    );
  });
});

describe('browserDownload', () => {
  function makeDeps({ ok = true, disposition = null } = {}) {
    const anchor = { href: '', download: '', click: vi.fn() };
    const body = { appendChild: vi.fn(), removeChild: vi.fn() };
    const fetch = vi.fn(async () => ({
      ok,
      headers: { get: () => disposition },
      blob: async () => new Blob(['data']),
    }));
    const document = { createElement: vi.fn(() => anchor), body };
    const url = { createObjectURL: vi.fn(() => 'blob:local'), revokeObjectURL: vi.fn() };
    return { deps: { fetch, document, url }, anchor, body, fetch, url };
  }

  it('downloads via a temporary <a> using the fallback name', async () => {
    const { deps, anchor, url } = makeDeps();
    const name = await browserDownload('http://x/audio/foo.wav', 'foo.wav', deps);
    expect(name).toBe('foo.wav');
    expect(anchor.download).toBe('foo.wav');
    expect(anchor.href).toBe('blob:local');
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(url.revokeObjectURL).toHaveBeenCalledWith('blob:local');
  });

  it('prefers the server-provided Content-Disposition filename', async () => {
    const { deps, anchor } = makeDeps({ disposition: 'attachment; filename="server.mp3"' });
    const name = await browserDownload('http://x/audio/foo.wav', 'foo.wav', deps);
    expect(name).toBe('server.mp3');
    expect(anchor.download).toBe('server.mp3');
  });

  it('throws when the response is not ok (so callers can surface an error toast)', async () => {
    const { deps } = makeDeps({ ok: false });
    await expect(browserDownload('http://x/missing', 'foo.wav', deps)).rejects.toThrow(
      'Download failed',
    );
  });

  // Regression for #256: in the Docker/browser build there is no Tauri shell,
  // so the download path must never call the native save dialog (which throws
  // "Cannot read properties of undefined (reading 'invoke')"). This helper is
  // pure HTTP + DOM and works without any Tauri runtime present.
  it('works with no Tauri globals defined', async () => {
    expect(typeof window === 'undefined' || window.__TAURI_INTERNALS__).toBeFalsy();
    const { deps } = makeDeps();
    await expect(browserDownload('http://x/audio/foo.wav', 'foo.wav', deps)).resolves.toBe(
      'foo.wav',
    );
  });
});
