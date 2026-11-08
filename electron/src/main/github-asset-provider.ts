import type { OutgoingHttpHeaders } from 'node:http';
import type {
  AppUpdater,
  CancellationToken,
  ResolvedUpdateFileInfo,
  UpdateInfo,
} from 'electron-updater';
import type { ProviderRuntimeOptions } from 'electron-updater/out/providers/Provider.js';
// electron-updater's package root does not export GenericProvider. It is
// CommonJS, so take the class off the default export (see updater.ts).
import genericProviderModule from 'electron-updater/out/providers/GenericProvider.js';

const { GenericProvider } = genericProviderModule;

/**
 * GitHub's API route for one release asset. With `Accept: application/octet-stream`
 * it answers 302 to the same object storage the release-download host uses, and
 * it kept working while `github.com/.../releases/download/...` returned 502/504.
 */
export const RELEASE_ASSET_API =
  'https://api.github.com/repos/debpalash/VoiceStudio/releases/assets';

/** Release asset name -> GitHub asset id. */
export type ReleaseAssets = Record<string, number>;

const OCTET_STREAM = { accept: 'application/octet-stream' } as const;

export function releaseAssets(value: unknown): ReleaseAssets {
  const assets: ReleaseAssets = {};
  if (!Array.isArray(value)) return assets;
  for (const asset of value) {
    if (!asset || typeof asset !== 'object') continue;
    const { name, id } = asset as Record<string, unknown>;
    if (typeof name === 'string' && Number.isSafeInteger(id) && (id as number) > 0)
      assets[name] = id as number;
  }
  return assets;
}

/**
 * Reads one tagged release through the API asset route instead of the
 * release-download host. The channel manifest, checksums and file choice are
 * still electron-updater's: only the transport of each request changes.
 */
export class GitHubAssetProvider extends GenericProvider {
  private readonly releaseBase: string;
  private readonly assetIds: ReleaseAssets;
  private readonly payloadExtension: string | undefined;

  // `options` is electron-updater's CustomPublishOptions; it carries the feed.
  constructor(
    options: Record<string, any>,
    updater: AppUpdater,
    runtimeOptions: ProviderRuntimeOptions,
  ) {
    const url = String(options.url);
    super({ provider: 'generic', url, channel: options.channel }, updater, {
      ...runtimeOptions,
      // Every request costs one of the 60 unauthenticated API calls an hour.
      isUseMultipleRangeRequest: false,
    });
    this.releaseBase = url.endsWith('/') ? url : `${url}/`;
    this.assetIds = { ...(options.assets as ReleaseAssets) };
    this.payloadExtension =
      typeof options.payloadExtension === 'string' ? options.payloadExtension : undefined;
  }

  /** Maps a release-download URL of this release to its asset route. */
  assetUrl(url: URL): URL {
    const name = decodeURIComponent(url.pathname.slice(url.pathname.lastIndexOf('/') + 1));
    const id = Object.hasOwn(this.assetIds, name) ? this.assetIds[name] : undefined;
    if (!url.href.startsWith(this.releaseBase) || !id)
      throw new Error(`The update release does not publish ${name}`);
    return new URL(`${RELEASE_ASSET_API}/${id}`);
  }

  // electron-updater sends these with every installer download.
  override get fileExtraDownloadHeaders(): OutgoingHttpHeaders {
    return { ...OCTET_STREAM };
  }

  // The channel manifest request; GenericProvider still parses and validates it.
  protected override httpRequest(
    url: URL,
    headers?: OutgoingHttpHeaders | null,
    cancellationToken?: CancellationToken,
  ): Promise<string | null> {
    return super.httpRequest(
      this.assetUrl(url),
      { ...headers, ...OCTET_STREAM },
      cancellationToken,
    );
  }

  override resolveFiles(updateInfo: UpdateInfo): ResolvedUpdateFileInfo[] {
    // GenericProvider rejects files without a checksum; the manifest's sha512
    // is verified after the download exactly as on the normal feed.
    const files = super
      .resolveFiles(updateInfo)
      .map((file) => ({ ...file, url: this.assetUrl(file.url) }));
    // electron-updater picks its installer by URL extension, which an asset id
    // lacks, and then takes the first file: list the one this install uses first.
    const extension = this.payloadExtension?.toLowerCase();
    if (!extension) return files;
    const payload = (file: ResolvedUpdateFileInfo) =>
      file.info.url.toLowerCase().endsWith(extension);
    return [...files.filter(payload), ...files.filter((file) => !payload(file))];
  }
}
