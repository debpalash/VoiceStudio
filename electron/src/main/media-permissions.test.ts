import { describe, expect, it, vi } from 'vitest';
import { allowsRendererPermission, installRendererPermissions } from './media-permissions';

describe('renderer permissions', () => {
  it('grants trusted audio and denies camera or foreign origins', () => {
    expect(allowsRendererPermission('media', 'app://voicestudio/#/capture', ['audio'])).toBe(
      true,
    );
    expect(allowsRendererPermission('media', 'app://voicestudio/#/capture', ['video'])).toBe(
      false,
    );
    expect(allowsRendererPermission('media', 'https://example.com', ['audio'])).toBe(false);
    expect(
      allowsRendererPermission('media', 'app://voicestudio/frame.html', ['audio'], undefined, false),
    ).toBe(false);
    expect(
      allowsRendererPermission('media', 'http://localhost:3902/#/capture', ['audio'], 'http://localhost:3902'),
    ).toBe(true);
  });

  it('allows only the trusted browser permissions used by the renderer', () => {
    expect(allowsRendererPermission('clipboard-read', 'app://voicestudio/index.html', undefined)).toBe(
      true,
    );
    expect(allowsRendererPermission('fullscreen', 'app://voicestudio/index.html', undefined)).toBe(
      true,
    );
    expect(allowsRendererPermission('notifications', 'app://voicestudio/index.html', undefined)).toBe(
      false,
    );
  });

  it('installs matching check and request handlers', () => {
    let check: (...args: any[]) => boolean = () => false;
    let request: (...args: any[]) => void = () => {};
    const fakeSession = {
      setPermissionCheckHandler: vi.fn((handler) => {
        check = handler;
      }),
      setPermissionRequestHandler: vi.fn((handler) => {
        request = handler;
      }),
    };
    installRendererPermissions(fakeSession as never);

    expect(
      check(null, 'media', 'app://voicestudio', {
        isMainFrame: true,
        requestingUrl: 'app://voicestudio/#/capture',
        mediaType: 'audio',
      }),
    ).toBe(true);
    const callback = vi.fn();
    request(
      { getURL: () => 'app://voicestudio/#/capture' },
      'media',
      callback,
      { isMainFrame: true, requestingUrl: 'app://voicestudio/#/capture', mediaTypes: ['video'] },
    );
    expect(callback).toHaveBeenCalledWith(false);
  });
});
