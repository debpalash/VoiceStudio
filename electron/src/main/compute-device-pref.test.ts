// @vitest-environment node
import { mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { COMPUTE_DEVICE_PREF, pinComputeDeviceToCpu } from './compute-device-pref';

const dir = () => mkdtempSync(join(tmpdir(), 'voice-prefs-'));
const read = (data: string) => JSON.parse(readFileSync(join(data, 'prefs.json'), 'utf8'));

describe('pinComputeDeviceToCpu', () => {
  it('stores CPU and keeps every other setting', async () => {
    const data = dir();
    writeFileSync(
      join(data, 'prefs.json'),
      JSON.stringify({ compute_device: 'cuda', active_engine: 'omnivoice', hf_token: 'x' }),
    );
    await pinComputeDeviceToCpu(data);
    expect(read(data)).toEqual({
      [COMPUTE_DEVICE_PREF]: 'cpu',
      active_engine: 'omnivoice',
      hf_token: 'x',
    });
    if (process.platform !== 'win32')
      expect(statSync(join(data, 'prefs.json')).mode & 0o777).toBe(0o600);
  });

  it('drops a stored OMNIVOICE_DEVICE override that would beat the CPU choice', async () => {
    const data = dir();
    writeFileSync(
      join(data, 'prefs.json'),
      JSON.stringify({
        compute_device: 'auto',
        'env.OMNIVOICE_DEVICE': 'cuda',
        'env.HF_ENDPOINT': 'https://hf-mirror.com',
      }),
    );
    await pinComputeDeviceToCpu(data);
    expect(read(data)).toEqual({
      compute_device: 'cpu',
      'env.HF_ENDPOINT': 'https://hf-mirror.com',
    });
  });

  it('creates the file and folder on a fresh data folder', async () => {
    const data = join(dir(), 'OmniVoice');
    await pinComputeDeviceToCpu(data);
    expect(read(data)).toEqual({ compute_device: 'cpu' });
  });

  it('never replaces a file it cannot read as settings', async () => {
    for (const content of ['{not json', '[1, 2]']) {
      const data = dir();
      writeFileSync(join(data, 'prefs.json'), content);
      await expect(pinComputeDeviceToCpu(data)).rejects.toThrow();
      expect(readFileSync(join(data, 'prefs.json'), 'utf8')).toBe(content);
    }
  });
});
