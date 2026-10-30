// @vitest-environment node
import { expect, it } from 'vitest';
import { runtimeChildEnv } from './runtime-environment';
import type { RuntimeEnvironmentContext } from './runtime-environment';

const inherited = Object.freeze({
  HIP_PATH: 'system-hip',
  hip_path: 'alternate-hip',
  Rocm_Home: 'system-home',
  ROCM_PATH: 'system-rocm',
  PATH: 'keep-search-path',
  OMNIVOICE_TORCH_VARIANT: 'rocm',
  HIP_VISIBLE_DEVICES: '0',
  CUDA_VISIBLE_DEVICES: '0',
  ROCR_VISIBLE_DEVICES: '0',
  HSA_OVERRIDE_GFX_VERSION: 'unqualified-architecture',
  MIOPEN_DISABLE_CACHE: '1',
  MIOPEN_CUSTOM_CACHE_DIR: 'unqualified-cache',
  MIOPEN_FIND_MODE: 'unqualified-search',
  TEMP: 'keep-temp',
});

it('selects the bundled SDK for an owned Windows ROCm child without mutating its parent', () => {
  const child = runtimeChildEnv(inherited, {
    managed: true,
    platform: 'win32',
    variant: 'rocm',
  });
  expect(child).toEqual({
    PATH: inherited.PATH,
    OMNIVOICE_TORCH_VARIANT: 'rocm',
    HIP_VISIBLE_DEVICES: '0',
    CUDA_VISIBLE_DEVICES: '0',
    ROCR_VISIBLE_DEVICES: '0',
    HSA_OVERRIDE_GFX_VERSION: 'unqualified-architecture',
    MIOPEN_DISABLE_CACHE: '1',
    MIOPEN_CUSTOM_CACHE_DIR: 'unqualified-cache',
    MIOPEN_FIND_MODE: 'unqualified-search',
    TEMP: 'keep-temp',
  });
  expect(inherited.HIP_PATH).toBe('system-hip');
  expect(inherited.hip_path).toBe('alternate-hip');
  expect(inherited.Rocm_Home).toBe('system-home');
  expect(inherited.ROCM_PATH).toBe('system-rocm');
});

it.each<RuntimeEnvironmentContext>([
  { managed: false, platform: 'win32', variant: 'rocm' },
  { managed: true, platform: 'win32', variant: 'cpu' },
  { managed: true, platform: 'win32', variant: 'default' },
  { managed: true, platform: 'linux', variant: 'rocm' },
  { managed: true, platform: 'darwin', variant: 'rocm' },
])('preserves inherited SDK selection for $managed/$platform/$variant', (context) => {
  const child = runtimeChildEnv(inherited, context);
  expect(child).toEqual(inherited);
  expect(child).not.toBe(inherited);
});
