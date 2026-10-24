// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { VoiceStudioBridge } from '@/components/bridge';
import { openExternalLink } from './open-external-link';

const openExternal = vi.fn<VoiceStudioBridge['files']['openExternal']>();
const feedback = vi.fn();

beforeEach(() => {
  openExternal.mockReset().mockResolvedValue(undefined);
  feedback.mockReset();
  vi.stubGlobal('window', { voicestudio: { files: { openExternal } } });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('awaits the typed preload bridge without reporting a successful open', async () => {
  await expect(openExternalLink('https://voicestudio.sh/docs', feedback)).resolves.toBeUndefined();
  expect(openExternal).toHaveBeenCalledExactlyOnceWith('https://voicestudio.sh/docs');
  expect(feedback).not.toHaveBeenCalled();
  expect(console.warn).not.toHaveBeenCalled();
});

it('handles a refused bridge promise locally and retains its diagnostic cause', async () => {
  openExternal.mockRejectedValueOnce(new Error('Default browser is unavailable'));
  await expect(openExternalLink('https://voicestudio.sh/docs', feedback)).resolves.toBeUndefined();
  expect(feedback).toHaveBeenCalledExactlyOnceWith();
  expect(console.warn).toHaveBeenCalledExactlyOnceWith(
    'Could not open external link:',
    'Default browser is unavailable',
  );
});

it('also handles a synchronous preload invocation failure', async () => {
  openExternal.mockImplementationOnce(() => {
    throw new Error('Preload disconnected');
  });
  await expect(openExternalLink('https://voicestudio.sh/docs', feedback)).resolves.toBeUndefined();
  expect(feedback).toHaveBeenCalledExactlyOnceWith();
  expect(console.warn).toHaveBeenCalledWith(
    'Could not open external link:',
    'Preload disconnected',
  );
});

it('scrubs credentials and home directories before logging a failure', async () => {
  openExternal.mockRejectedValueOnce(
    new Error(
      'Open failed /Users/example/browser https://example.test/?token=private-fixture-token',
    ),
  );
  await openExternalLink('https://voicestudio.sh/docs', feedback);
  expect(console.warn).toHaveBeenCalledWith(
    'Could not open external link:',
    'Open failed ~/browser https://example.test/?token=***REDACTED***',
  );
  expect(feedback).toHaveBeenCalledExactlyOnceWith();
});

it('preserves the noopener fallback when the browser returns no handle', async () => {
  const open = vi.fn().mockReturnValue(null);
  vi.stubGlobal('window', { open });
  await openExternalLink('https://voicestudio.sh/docs', feedback);
  expect(open).toHaveBeenCalledExactlyOnceWith(
    'https://voicestudio.sh/docs',
    '_blank',
    'noopener,noreferrer',
  );
  expect(feedback).not.toHaveBeenCalled();
});

it('reports a thrown browser fallback error without a preload bridge', async () => {
  vi.stubGlobal('window', {
    open: vi.fn(() => {
      throw new Error('Browser operation refused');
    }),
  });
  await expect(openExternalLink('https://voicestudio.sh/docs', feedback)).resolves.toBeUndefined();
  expect(feedback).toHaveBeenCalledExactlyOnceWith();
  expect(console.warn).toHaveBeenCalledWith(
    'Could not open external link:',
    'Browser operation refused',
  );
});
