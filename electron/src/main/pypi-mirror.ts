/**
 * Regional PyPI file mirrors for the locked dependency install (#3444, #3411).
 *
 * `uv sync --frozen` downloads the exact artifact URLs pinned in uv.lock, so an
 * index setting (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`) never redirects them. The
 * mirrors below serve PyPI's file store byte for byte under the same
 * `/packages/<hash path>/<file>` layout, so swapping only the host keeps every
 * pinned sha256 valid: uv still verifies each file against the lock.
 *
 * Only hosts the region picker already sanctions are listed. PyTorch's own
 * index (`download-r2.pytorch.org`, `download.pytorch.org`) has no sanctioned
 * mirror and is left untouched.
 */

export const PYPI_FILES_ORIGIN = 'https://files.pythonhosted.org/packages/';

/** Region to the mirror prefix that replaces {@link PYPI_FILES_ORIGIN}. */
const PYPI_FILES_MIRRORS: Readonly<Record<string, string>> = {
  china: 'https://mirrors.aliyun.com/pypi/packages/',
};

/** Every prefix a staged lock may carry instead of the origin. */
const KNOWN_MIRRORS = [...new Set(Object.values(PYPI_FILES_MIRRORS))];

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

/** The PyPI file mirror prefix for a resolved download region, or null. */
export function pypiFilesMirror(region: string): string | null {
  return PYPI_FILES_MIRRORS[region] ?? null;
}

/** Host that serves the PyPI-hosted wheels for a resolved download region. */
export function pypiFilesHost(region: string): string {
  return new URL(pypiFilesMirror(region) ?? PYPI_FILES_ORIGIN).host;
}

/** Bytes each side of the auto speed check downloads (one ranged GET per host). */
export const PYPI_SPEED_SAMPLE_BYTES = 256 * 1024;
const PYPI_SPEED_TIMEOUT_MS = 6_000;
/** The mirror that "Auto" may pick when it is clearly faster than PyPI. */
export const AUTO_PYPI_MIRROR = PYPI_FILES_MIRRORS.china!;

/**
 * A locked wheel on files.pythonhosted.org big enough to time a
 * {@link PYPI_SPEED_SAMPLE_BYTES} read, or null. The same lock always gives
 * the same wheel, so both hosts serve the same file.
 */
export function speedSampleUrl(lock: string): string | null {
  for (const match of lock.matchAll(/\burl = "([^"]+\.whl)"[^}\n]*?\bsize = (\d+)/g)) {
    if (match[1]!.startsWith(PYPI_FILES_ORIGIN) && Number(match[2]) >= PYPI_SPEED_SAMPLE_BYTES)
      return match[1]!;
  }
  return null;
}

/** Milliseconds to read the first sample bytes of `url`, or null on any failure. */
export type SampleTimer = (url: string, signal: AbortSignal) => Promise<number | null>;

/** One ranged GET, no retries; a timeout, error or short body is a failure. */
export const timeSample: SampleTimer = async (url, signal) => {
  const started = performance.now();
  const timeout = AbortSignal.any([signal, AbortSignal.timeout(PYPI_SPEED_TIMEOUT_MS)]);
  try {
    const response = await fetch(url, {
      redirect: 'follow',
      signal: timeout,
      // mirrors.aliyun.com refuses requests without a User-Agent.
      headers: {
        Range: `bytes=0-${PYPI_SPEED_SAMPLE_BYTES - 1}`,
        'User-Agent': 'VoiceStudio-setup',
      },
    });
    if ((response.status !== 206 && response.status !== 200) || !response.body) return null;
    const reader = response.body.getReader();
    let received = 0;
    try {
      while (received < PYPI_SPEED_SAMPLE_BYTES) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.byteLength;
      }
    } finally {
      await reader.cancel().catch(() => undefined);
    }
    return received >= PYPI_SPEED_SAMPLE_BYTES ? performance.now() - started : null;
  } catch {
    return null;
  }
};

export interface MirrorRace {
  /** The mirror prefix to use, or null for files.pythonhosted.org. */
  mirror: string | null;
  originMs: number | null;
  mirrorMs: number | null;
}

/**
 * The "Auto" download region's PyPI choice (#3444): time the same small read
 * of one locked wheel from files.pythonhosted.org and the Aliyun mirror at
 * once and use the mirror only when it answers and is clearly faster (the
 * same 20 % margin the GitHub proxy check uses). Anything uncertain (no
 * sample wheel, both hosts failing, a close result) keeps PyPI.
 */
export async function raceAutoPypiMirror(
  lock: string,
  signal: AbortSignal,
  time: SampleTimer = timeSample,
): Promise<MirrorRace> {
  const sample = speedSampleUrl(lock);
  if (!sample) return { mirror: null, originMs: null, mirrorMs: null };
  const [originMs, mirrorMs] = await Promise.all([
    time(sample, signal),
    time(AUTO_PYPI_MIRROR + sample.slice(PYPI_FILES_ORIGIN.length), signal),
  ]);
  const faster = mirrorMs !== null && (originMs === null || mirrorMs * 5 < originMs * 4);
  return { mirror: faster ? AUTO_PYPI_MIRROR : null, originMs, mirrorMs };
}

/**
 * Point every `url = "https://files.pythonhosted.org/packages/..."` artifact in
 * a lock at `mirror`. Only the host prefix of artifact URLs changes; hashes,
 * sizes, `source` registries and every other host stay byte-identical. A null
 * mirror returns the lock unchanged.
 */
export function mirrorLockUrls(lock: string, mirror: string | null): string {
  if (!mirror) return lock;
  return lock.replace(new RegExp(`(\\burl = ")${escape(PYPI_FILES_ORIGIN)}`, 'g'), `$1${mirror}`);
}

/** Inverse of {@link mirrorLockUrls} for any known mirror: compare locks by content, not host. */
export function normalizeLockHosts(lock: string): string {
  let result = lock;
  for (const mirror of KNOWN_MIRRORS) {
    result = result.replace(
      new RegExp(`(\\burl = ")${escape(mirror)}`, 'g'),
      `$1${PYPI_FILES_ORIGIN}`,
    );
  }
  return result;
}
