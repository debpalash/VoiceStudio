// @vitest-environment node
import { mkdir, mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  DOWNLOAD_SUMMARY_MS,
  SetupDownloadReporter,
  inFlightDownloadBytes,
  lockArtifactHosts,
  pruneInterruptedUvDownloads,
  setupDownloadHosts,
} from './setup-downloads';

const LOCK = `
[[package]]
name = "torch"
version = "2.8.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/aa/torch-2.8.0-cp311-none-macosx_11_0_arm64.whl", size = 1 },
]

[[package]]
name = "torch"
version = "2.8.0+cu128"
source = { registry = "https://download.pytorch.org/whl/cu128" }
wheels = [
    { url = "https://download-r2.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl" },
]

[[package]]
name = "nvidia-cudnn-cu12"
version = "9.10.2.21"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/bb/nvidia_cudnn_cu12-9.10.2.21-py3-none-manylinux_2_27_x86_64.whl" },
]

[[package]]
name = "en-core-web-sm"
version = "3.8.0"
source = { url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" }
`;

const roots: string[] = [];
async function cacheDir(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'vs-uv-cache-'));
  roots.push(root);
  return root;
}
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe('download hosts from uv.lock', () => {
  it('collects every host a package is locked to', () => {
    const hosts = lockArtifactHosts(LOCK);
    expect(hosts.get('torch')).toEqual(['files.pythonhosted.org', 'download-r2.pytorch.org']);
    expect(hosts.get('nvidia-cudnn-cu12')).toEqual(['files.pythonhosted.org']);
    expect(hosts.get('en-core-web-sm')).toEqual(['github.com']);
  });

  it('picks PyTorch’s index for torch only where the lock uses it', () => {
    const cuda = setupDownloadHosts(LOCK, { pytorchIndex: true });
    expect(cuda.get('torch')).toBe('download-r2.pytorch.org');
    expect(cuda.get('nvidia-cudnn-cu12')).toBe('files.pythonhosted.org');
    expect(setupDownloadHosts(LOCK, { pytorchIndex: false }).get('torch')).toBe(
      'files.pythonhosted.org',
    );
    expect(
      setupDownloadHosts(LOCK, {
        pytorchIndex: true,
        overrides: { torch: 'download.pytorch.org' },
      }).get('torch'),
    ).toBe('download.pytorch.org');
  });
});

describe('uv in-flight downloads', () => {
  it('counts only unfinished downloads, nested files included', async () => {
    const cache = await cacheDir();
    await mkdir(join(cache, '.tmpAbC', 'torch', 'lib'), { recursive: true });
    await writeFile(join(cache, '.tmpAbC', 'torch', 'lib', 'a.so'), Buffer.alloc(3000));
    await writeFile(join(cache, '.tmpAbC', 'top.py'), Buffer.alloc(500));
    await mkdir(join(cache, 'archive-v0', 'done'), { recursive: true });
    await writeFile(join(cache, 'archive-v0', 'done', 'b.so'), Buffer.alloc(9000));
    expect(await inFlightDownloadBytes(cache)).toBe(3500);
    expect(await inFlightDownloadBytes(join(cache, 'missing'))).toBe(0);
  });

  it('removes partial downloads a cancelled uv left behind and keeps finished wheels', async () => {
    const cache = await cacheDir();
    await mkdir(join(cache, '.tmpHZ1msW', 'torch'), { recursive: true });
    await writeFile(join(cache, '.tmpHZ1msW', 'torch', 'x.so'), 'partial');
    await mkdir(join(cache, 'archive-v0', 'kept'), { recursive: true });
    await writeFile(join(cache, '.lock'), '');
    expect(await pruneInterruptedUvDownloads(cache)).toBe(1);
    expect((await readdir(cache)).sort()).toEqual(['.lock', 'archive-v0']);
    expect(await pruneInterruptedUvDownloads(join(cache, 'missing'))).toBe(0);
  });
});

describe('SetupDownloadReporter', () => {
  it('logs each source once, a periodic summary, and stall transitions', () => {
    const reporter = new SetupDownloadReporter();
    const base = {
      downloadsComplete: false,
      totalBytes: 4 * 1024 ** 3,
      downloadedBytes: 1024 ** 3,
      bytesPerSecond: 1024 ** 2,
      downloadHost: 'files.pythonhosted.org',
    };
    expect(reporter.lines(base, 0)).toEqual(['Download source: files.pythonhosted.org']);
    expect(reporter.lines(base, 1_000)).toEqual([]);
    expect(reporter.lines(base, DOWNLOAD_SUMMARY_MS)).toEqual([
      'Download progress: 1.0 GB of ~4.0 GB at 1.0 MB/s from files.pythonhosted.org',
    ]);
    const stalled = { ...base, stalled: true, stalledSeconds: 45 };
    expect(reporter.lines(stalled, DOWNLOAD_SUMMARY_MS + 1)).toEqual([
      'Download stalled: no data from files.pythonhosted.org for 45 s',
    ]);
    expect(reporter.lines(stalled, DOWNLOAD_SUMMARY_MS + 2)).toEqual([]);
    expect(reporter.lines(base, DOWNLOAD_SUMMARY_MS + 3)).toEqual([
      'Download resumed from files.pythonhosted.org',
    ]);
  });

  it('never writes a line the uv progress parser would read as a package', () => {
    const reporter = new SetupDownloadReporter();
    const lines = [
      ...reporter.lines({ downloadsComplete: false, totalBytes: 10, downloadHost: 'a.org' }, 0),
      ...reporter.lines(
        { downloadsComplete: false, totalBytes: 10, stalled: true, downloadHost: 'a.org' },
        DOWNLOAD_SUMMARY_MS,
      ),
    ];
    expect(lines.length).toBeGreaterThan(1);
    for (const line of lines) expect(line).not.toMatch(/^Download(?:ing|ed)\s/);
  });
});
