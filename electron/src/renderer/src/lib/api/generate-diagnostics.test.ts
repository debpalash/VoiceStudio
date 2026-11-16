import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { generateAbortMs } from '@shared/utils/generateBudget';
import type { CloneGenerateInput } from './types';

const mock = vi.hoisted(() => ({ bridge: null as any }));
vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));

const input: CloneGenerateInput = {
  text: 'Private generation text',
  language: 'Auto',
  steps: 16,
  cfg: 2,
  speed: 1,
  tShift: 0.1,
  posTemp: 5,
  classTemp: 0,
  layerPenalty: 5,
  denoise: true,
  postprocess: true,
  duration: '',
  profileId: 'private-profile',
};
const on = {
  enabled: true,
  opted_in: true,
  available: true,
  prompted: true,
  grant_epoch: 'a'.repeat(32),
};

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  mock.bridge = null;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it.each(['desktop', 'browser'] as const)(
  '%s reports an actual generation deadline while keeping caller cancellation local',
  async (transport) => {
    const capture = vi.fn(async (..._args: unknown[]) => {});
    if (transport === 'desktop') {
      mock.bridge = { diagnostics: { getStatus: async () => on, capture } };
    }
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      if (url.endsWith('/settings/diagnostics/event')) {
        void capture(JSON.parse(String(init?.body)));
        return Promise.resolve(new Response('{}'));
      }
      if (url.endsWith('/settings/diagnostics')) {
        return Promise.resolve(new Response(JSON.stringify(on)));
      }
      return new Promise<Response>((_resolve, reject) => {
        const abort = () => reject(new DOMException('Private transport details', 'AbortError'));
        if (init?.signal?.aborted) abort();
        else init?.signal?.addEventListener('abort', abort, { once: true });
      });
    });
    const diagnostics = await import('../diagnostic-reports');
    const { generateClone } = await import('./generate');
    await diagnostics.refreshDiagnosticState();
    const consent = diagnostics.observeDiagnosticConsent();
    const controller = new AbortController();
    const cancelled = generateClone(input, { signal: controller.signal }).catch((error) => {
      diagnostics.captureDiagnosticException(error, 'generation_failed', 'generation', consent);
      return error;
    });
    controller.abort();
    expect(await cancelled).toMatchObject({ name: 'AbortError' });
    expect(capture).not.toHaveBeenCalled();

    const deadline = generateClone(input).catch((error) => {
      diagnostics.captureDiagnosticException(error, 'generation_failed', 'generation', consent);
      return error;
    });
    await vi.advanceTimersByTimeAsync(generateAbortMs(input.text.length));
    expect(await deadline).toMatchObject({ name: 'TimeoutError' });
    expect(capture).toHaveBeenCalledOnce();
    expect(capture.mock.calls[0][0]).toMatchObject({
      category: 'generation_failed',
      stage: 'generation',
      error_type: 'TimeoutError',
    });
    expect(JSON.stringify(capture.mock.calls)).not.toMatch(/Private|private-profile/);
  },
);
