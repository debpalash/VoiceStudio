import { describe, expect, test, vi } from 'vitest';
import { GitHubAssetProvider, RELEASE_ASSET_API, releaseAssets } from './github-asset-provider';

// The real electron-updater GenericProvider runs underneath; only the HTTP
// executor is faked, so these requests are the ones electron-updater sends.
const FEED = 'https://github.com/debpalash/VoiceStudio/releases/download/v0.5.9';
const MANIFEST = [
  'version: 0.5.9',
  'files:',
  '  - url: VoiceStudio-0.5.9-mac-arm64.dmg',
  '    sha512: dmg-hash',
  '    size: 281',
  '  - url: VoiceStudio-0.5.9-mac-arm64.zip',
  '    sha512: zip-hash',
  '    size: 280',
  'path: VoiceStudio-0.5.9-mac-arm64.dmg',
  'sha512: dmg-hash',
  "releaseDate: '2026-11-08T00:00:00.000Z'",
].join('\n');
const ASSETS = {
  'electron-stable-darwin-arm64-mac.yml': 11,
  'VoiceStudio-0.5.9-mac-arm64.zip': 12,
  'VoiceStudio-0.5.9-mac-arm64.dmg': 13,
};

function provider(manifest = MANIFEST, assets: Record<string, number> = ASSETS) {
  const executor = { request: vi.fn(async (_options: any) => manifest) };
  const instance = new GitHubAssetProvider(
    {
      provider: 'custom',
      url: FEED,
      channel: 'electron-stable-darwin-arm64',
      assets,
      payloadExtension: '.zip',
    },
    { channel: 'electron-stable-darwin-arm64', isAddNoCacheQuery: true } as any,
    { executor, platform: 'darwin', isUseMultipleRangeRequest: true } as any,
  );
  return { instance, executor };
}

describe('GitHub API asset-route update provider', () => {
  test('reads the channel manifest from its asset id with an octet-stream Accept header', async () => {
    const { instance, executor } = provider();
    const info = await instance.getLatestVersion();
    expect(info.version).toBe('0.5.9');
    expect(executor.request).toHaveBeenCalledOnce();
    const options = executor.request.mock.calls[0][0];
    expect(`${options.protocol}//${options.hostname}${options.path}`).toBe(
      `${RELEASE_ASSET_API}/11`,
    );
    expect(options.headers).toMatchObject({ accept: 'application/octet-stream' });
  });

  test('downloads every file from its asset route, installer first, checksums kept', async () => {
    const { instance } = provider();
    const files = instance.resolveFiles(await instance.getLatestVersion());
    expect(files.map((file) => file.url.href)).toEqual([
      `${RELEASE_ASSET_API}/12`,
      `${RELEASE_ASSET_API}/13`,
    ]);
    expect(files[0].info).toMatchObject({
      url: 'VoiceStudio-0.5.9-mac-arm64.zip',
      sha512: 'zip-hash',
    });
    expect(instance.fileExtraDownloadHeaders).toEqual({ accept: 'application/octet-stream' });
    // Ranged multi-requests would spend the unauthenticated API budget.
    expect(instance.isUseMultipleRangeRequest).toBe(false);
  });

  test('refuses files the release does not publish or the manifest does not checksum', async () => {
    const { instance } = provider(MANIFEST, { 'electron-stable-darwin-arm64-mac.yml': 11 });
    const info = await instance.getLatestVersion();
    expect(() => instance.resolveFiles(info)).toThrow(
      'does not publish VoiceStudio-0.5.9-mac-arm64',
    );
    expect(() =>
      instance.resolveFiles({
        ...info,
        files: [{ url: 'VoiceStudio-0.5.9-mac-arm64.zip' }],
      } as any),
    ).toThrow(/checksum/);
    expect(() =>
      instance.assetUrl(new URL('https://example.com/VoiceStudio-0.5.9-mac-arm64.zip')),
    ).toThrow('does not publish');
    const missingManifest = await provider(MANIFEST, {})
      .instance.getLatestVersion()
      .catch((error: Error) => error);
    expect(String(missingManifest)).toContain(
      'does not publish electron-stable-darwin-arm64-mac.yml',
    );
  });

  test('keeps only well-formed asset ids', () => {
    expect(
      releaseAssets([
        { name: 'a.yml', id: 1 },
        { name: 'b', id: '2' },
        { id: 3 },
        null,
        { name: 'c', id: -1 },
      ]),
    ).toEqual({ 'a.yml': 1 });
    expect(releaseAssets(undefined)).toEqual({});
  });
});
