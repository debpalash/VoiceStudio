/** @vitest-environment node */
import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { runtimeDownloadHosts } from './runtime-project';

const mocks = vi.hoisted(() => ({ nvidia: vi.fn(() => false) }));
vi.mock('../../scripts/torch-variant.mjs', async (original) => ({
  ...(await original<typeof import('../../scripts/torch-variant.mjs')>()),
  nvidiaDriverPresent: mocks.nvidia,
}));
vi.mock('node:fs/promises', async (original) => ({
  ...(await original<typeof import('node:fs/promises')>()),
  readFile: vi.fn(),
}));

type Recipe = {
  torch: { find_links: string; packages: string[]; wheel_files: string[] };
  runtime: { packages: string[]; wheel_files: string[] };
};
const PINNED_RECIPE = JSON.parse(
  readFileSync(new URL('../../../scripts/windows-rocm-recipe.json', import.meta.url), 'utf8'),
) as Recipe;
const BUNDLE = join('fixture', 'host-map-bundle');
const LOCK = [
  ...['torch', 'torchaudio', 'torchvision'].flatMap((name) => [
    '[[package]]',
    `name = "${name}"`,
    'wheels = [{ url = "https://files.pythonhosted.org/packages/mac.whl" }]',
    '[[package]]',
    `name = "${name}"`,
    'wheels = [{ url = "https://download-r2.pytorch.org/whl/cu128/windows.whl" }]',
  ]),
  '[[package]]',
  'name = "ctranslate2"',
  'wheels = [{ url = "https://files.pythonhosted.org/packages/ct2.whl" }]',
  '[[package]]',
  'name = "numpy"',
  'wheels = [{ url = "https://files.pythonhosted.org/packages/numpy.whl" }]',
].join('\n');
let recipe: Recipe;

beforeEach(() => {
  recipe = structuredClone(PINNED_RECIPE);
  mocks.nvidia.mockReturnValue(false);
  vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'auto');
  vi.stubEnv('OMNIVOICE_TORCH_INDEX', '');
  vi.mocked(readFile).mockImplementation(async (path) => {
    if (String(path) === join(BUNDLE, 'uv.lock')) return LOCK;
    if (String(path) === join(BUNDLE, 'scripts', 'windows-rocm-recipe.json'))
      return JSON.stringify(recipe);
    throw new Error('Unexpected host-map fixture read');
  });
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe('Windows HIP artifact hosts', () => {
  it('keeps AMD hosts while showing the selected PyPI mirror', async () => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    const hosts = await runtimeDownloadHosts(
      BUNDLE,
      'win32',
      'x64',
      { variant: 'rocm', explicit: true },
      'mirrors.aliyun.com',
    );
    expect(hosts.get('torch')).toBe('repo.radeon.com');
    expect(hosts.get('rocm-sdk-devel')).toBe('repo.radeon.com');
    expect(hosts.get('numpy')).toBe('mirrors.aliyun.com');
    expect(hosts.has('ctranslate2')).toBe(false);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
  });

  it('uses an explicit HIP choice instead of an ambient CPU choice', async () => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    const hosts = await runtimeDownloadHosts(BUNDLE, 'win32', 'x64', {
      variant: 'rocm',
      explicit: true,
    });
    expect(hosts.get('torch')).toBe('repo.radeon.com');
    expect(hosts.get('rocm-sdk-devel')).toBe('repo.radeon.com');
    expect(hosts.has('ctranslate2')).toBe(false);
    expect(process.env.OMNIVOICE_TORCH_VARIANT).toBe('cpu');
  });

  it.each(['cpu', 'default'] as const)(
    'uses an explicit %s choice instead of ambient HIP',
    async (variant) => {
      vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
      const hosts = await runtimeDownloadHosts(BUNDLE, 'win32', 'x64', { variant, explicit: true });
      expect(hosts.get('torch')).toBe('download-r2.pytorch.org');
      expect(hosts.get('ctranslate2')).toBe('files.pythonhosted.org');
      expect(hosts.has('rocm-sdk-devel')).toBe(false);
      expect(vi.mocked(readFile).mock.calls).toHaveLength(1);
    },
  );

  it('preserves an explicit Linux HIP index despite ambient CPU', async () => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'cpu');
    vi.stubEnv('OMNIVOICE_TORCH_INDEX', 'https://linux.example.invalid/rocm');
    const hosts = await runtimeDownloadHosts(BUNDLE, 'linux', 'x64', {
      variant: 'rocm',
      explicit: true,
    });
    expect(hosts.get('torch')).toBe('linux.example.invalid');
    expect(hosts.get('ctranslate2')).toBe('files.pythonhosted.org');
    expect(vi.mocked(readFile).mock.calls).toHaveLength(1);
  });

  it.each(['', 'https://linux-only.example.invalid/whl/rocm6.4', 'not a URL'])(
    'uses pinned Torch and SDK artifact hosts, ignoring Linux index %j',
    async (index) => {
      vi.stubEnv('OMNIVOICE_TORCH_VARIANT', ' ROCm ');
      vi.stubEnv('OMNIVOICE_TORCH_INDEX', index);
      const hosts = await runtimeDownloadHosts(BUNDLE, 'win32', 'x64');
      for (const section of [recipe.torch, recipe.runtime]) {
        for (const pin of section.packages)
          expect(hosts.get(pin.split('==')[0]!)).toBe('repo.radeon.com');
      }
      expect(hosts.has('ctranslate2')).toBe(false);
      expect(hosts.get('numpy')).toBe('files.pythonhosted.org');
      expect([...hosts.values()]).not.toContain('github.com');
    },
  );

  it('reads the recipe location and package inventory instead of assuming public AMD hosts', async () => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    recipe.torch.find_links = 'https://pinned-amd.example.invalid:8443/artifacts/';
    recipe.runtime.packages.push('rocm-sdk-fixture==7.2.1');
    recipe.runtime.wheel_files.push('rocm_sdk_fixture-7.2.1-py3-none-win_amd64.whl');
    recipe.runtime.wheel_files.reverse();
    const hosts = await runtimeDownloadHosts(BUNDLE, 'win32', 'x64');
    expect(hosts.get('torch')).toBe('pinned-amd.example.invalid:8443');
    expect(hosts.get('rocm-sdk-devel')).toBe('pinned-amd.example.invalid:8443');
    expect(hosts.get('rocm-sdk-fixture')).toBe('pinned-amd.example.invalid:8443');
  });
});

describe('unchanged host selection', () => {
  it.each([
    ['', 'download.pytorch.org'],
    [
      'https://user:secret@linux-mirror.example.invalid:8443/whl/rocm6.4',
      'linux-mirror.example.invalid:8443',
    ],
  ])('keeps the Linux ROCm index %j', async (index, host) => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    vi.stubEnv('OMNIVOICE_TORCH_INDEX', index);
    const hosts = await runtimeDownloadHosts(BUNDLE, 'linux', 'x64');
    expect(hosts.get('torch')).toBe(host);
    expect(hosts.get('torchaudio')).toBe(host);
    expect(hosts.get('torchvision')).toBe(host);
    expect(hosts.get('ctranslate2')).toBe('files.pythonhosted.org');
    expect(hosts.has('rocm-sdk-devel')).toBe(false);
    expect(vi.mocked(readFile).mock.calls).toHaveLength(1);
  });

  it.each([
    ['win32', 'x64', 'cpu', false, 'download-r2.pytorch.org'],
    ['win32', 'x64', 'cuda', true, 'download-r2.pytorch.org'],
    ['win32', 'x64', 'auto', false, 'download-r2.pytorch.org'],
    ['win32', 'x64', 'auto', true, 'download-r2.pytorch.org'],
    ['win32', 'arm64', 'auto', false, 'download-r2.pytorch.org'],
    ['linux', 'x64', 'cpu', false, 'download-r2.pytorch.org'],
    ['linux', 'x64', 'cuda', true, 'download-r2.pytorch.org'],
    ['linux', 'arm64', 'auto', false, 'files.pythonhosted.org'],
    ['darwin', 'arm64', 'rocm', false, 'files.pythonhosted.org'],
    ['darwin', 'x64', 'auto', false, 'files.pythonhosted.org'],
  ] as const)(
    'preserves %s/%s %s with NVIDIA=%s',
    async (platform, arch, variant, nvidia, host) => {
      vi.stubEnv('OMNIVOICE_TORCH_VARIANT', variant);
      vi.stubEnv('OMNIVOICE_TORCH_INDEX', 'https://unrelated-linux.example.invalid/rocm');
      mocks.nvidia.mockReturnValue(nvidia);
      const hosts = await runtimeDownloadHosts(BUNDLE, platform, arch);
      expect(hosts.get('torch')).toBe(host);
      expect(hosts.get('ctranslate2')).toBe('files.pythonhosted.org');
      expect(hosts.has('rocm-sdk-devel')).toBe(false);
      expect(vi.mocked(readFile).mock.calls).toHaveLength(1);
    },
  );

  it('keeps the unsupported Windows ARM64 HIP rejection', async () => {
    vi.stubEnv('OMNIVOICE_TORCH_VARIANT', 'rocm');
    await expect(runtimeDownloadHosts(BUNDLE, 'win32', 'arm64')).rejects.toThrow('cp312');
  });
});
