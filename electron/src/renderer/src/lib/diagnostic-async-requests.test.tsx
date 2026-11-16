import { act, cleanup, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const fixture = vi.hoisted(() => ({
  enabled: false,
  epoch: 'a'.repeat(32),
  grants: 0,
  capture: vi.fn(async (_input: unknown, _epoch: string) => {}),
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    diagnostics: {
      getStatus: async () => ({
        enabled: fixture.enabled,
        opted_in: fixture.enabled,
        available: true,
        prompted: true,
        grant_epoch: fixture.enabled ? fixture.epoch : null,
      }),
      setConsent: async (enabled: boolean) => {
        fixture.enabled = enabled;
        if (enabled) fixture.epoch = (++fixture.grants).toString(16).padStart(32, '0');
        return {
          enabled,
          opted_in: enabled,
          available: true,
          prompted: true,
          grant_epoch: enabled ? fixture.epoch : null,
        };
      },
      capture: fixture.capture,
    },
  }),
}));
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock('@/hooks/use-clone-readiness', () => ({ useCloneInputsReadiness: () => null }));
vi.mock('@/hooks/use-tts-readiness', async (load) => ({
  ...(await load<typeof import('@/hooks/use-tts-readiness')>()),
  useTtsReadiness: () => null,
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({ activeTts: { id: 'omnivoice', supports_cloning: true } }),
}));
import { GenerationProvider, useGenerateClone, type UseGenerateClone } from '@/hooks/use-generate';
import { DEFAULT_CLONE_SETTINGS, patchCloneSettings } from '@/lib/store/clone-settings';
import { startTranscription } from '@/features/transcriptions/transcription-job';
import { dubSession, importDubSubtitles, translateDub } from '@/features/dub/dub-session';
import { queryClient } from '@/lib/query';
import { setDiagnosticConsent } from './diagnostic-reports';
let clone: UseGenerateClone;
function CaptureClone() {
  clone = useGenerateClone();
  return null;
}
const failure = () =>
  Response.json({ detail: 'Renderer diagnostic fixture failure' }, { status: 500 });
function ordinary(path: string) {
  if (path.includes('/glossary/')) return Response.json([]);
  if (path.endsWith('/dictation-refinement')) return Response.json({ auto: false });
  return Response.json({ ready: true, status: 'loaded' });
}
beforeEach(async () => {
  localStorage.clear();
  queryClient.clear();
  await setDiagnosticConsent(false);
  fixture.capture.mockClear();
  patchCloneSettings({
    ...DEFAULT_CLONE_SETTINGS,
    text: 'Hello',
    selectedProfileId: 'voice-1',
    autoPlay: false,
  });
  dubSession.setState((state) => ({
    ...state,
    phase: 'editing',
    recovery: null,
    jobId: 'media',
    segments: [{ id: 'a', start: 0, end: 1, text: 'hello', text_original: 'hello' }],
    tracks: [],
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each(['clone', 'fast', 'accurate', 'dub-import'] as const)(
  'keeps %s failure with its request grant through held headers and error-body reads',
  async (owner) => {
    for (const held of ['headers', 'body'] as const)
      for (const transition of ['off-on', 'regrant', 'same-grant'] as const) {
        await setDiagnosticConsent(false);
        fixture.capture.mockClear();
        if (transition !== 'off-on') await setDiagnosticConsent(true);
        let release!: () => void,
          entered = false;
        const target =
          owner === 'clone'
            ? '/generate'
            : owner === 'dub-import'
              ? '/dub/import-srt/media'
              : '/transcribe';
        let first = true;
        vi.stubGlobal(
          'fetch',
          vi.fn(async (input: RequestInfo | URL) => {
            const path = new URL(String(input), 'http://fixture.invalid').pathname;
            if (!path.endsWith(target)) return ordinary(path);
            if (!first) return failure();
            first = false;
            if (held === 'headers') {
              entered = true;
              return new Promise<Response>((resolve) => {
                release = () => resolve(failure());
              });
            }
            const stream = new ReadableStream<Uint8Array>({
              start(controller) {
                release = () => {
                  controller.enqueue(
                    new TextEncoder().encode(
                      JSON.stringify({ detail: 'Renderer diagnostic fixture failure' }),
                    ),
                  );
                  controller.close();
                };
              },
            });
            const response = new Response(stream, {
              status: 500,
              headers: { 'Content-Type': 'application/json' },
            });
            const text = response.text.bind(response);
            response.text = () => {
              entered = true;
              return text();
            };
            return response;
          }),
        );
        if (owner === 'clone')
          render(
            <QueryClientProvider client={new QueryClient()}>
              <GenerationProvider>
                <CaptureClone />
              </GenerationProvider>
            </QueryClientProvider>,
          );
        let pending!: Promise<unknown>;
        act(() => {
          pending =
            owner === 'clone'
              ? clone.generate()
              : owner === 'dub-import'
                ? importDubSubtitles(new File(['captions'], 'captions.srt'))
                : startTranscription(new File(['audio'], 'talk.wav'), owner);
        });
        await waitFor(() => expect(entered).toBe(true));
        if (transition !== 'same-grant') {
          await setDiagnosticConsent(false);
          await setDiagnosticConsent(true);
        }
        release();
        await act(async () => {
          await pending;
        });
        expect(fixture.capture).toHaveBeenCalledTimes(transition === 'same-grant' ? 1 : 0);
        // A new semantic request under the replacement grant must still report.
        fixture.capture.mockClear();
        await act(async () => {
          if (owner === 'clone') await clone.generate();
          else if (owner === 'dub-import')
            await importDubSubtitles(new File(['captions'], 'captions.srt'));
          else await startTranscription(new File(['audio'], 'talk.wav'), owner);
        });
        expect(fixture.capture).toHaveBeenCalledOnce();
        expect(JSON.stringify(fixture.capture.mock.calls)).not.toContain('fixture failure');
        cleanup();
      }
  },
);

it('lets a later Dubbing request observe fresh consent after an earlier successful request', async () => {
  let release!: () => void,
    entered = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://fixture.invalid').pathname;
      if (path.includes('/glossary/')) {
        entered = true;
        return new Promise<Response>((resolve) => {
          release = () => resolve(Response.json([]));
        });
      }
      if (path.endsWith('/dub/translate')) return failure();
      return ordinary(path);
    }),
  );
  const pending = translateDub('fr', 'google');
  await waitFor(() => expect(entered).toBe(true));
  await setDiagnosticConsent(true);
  release();
  await pending;
  expect(fixture.capture).toHaveBeenCalledOnce();
});

it.each(['fast', 'accurate'] as const)(
  'a fresh %s transcription request observes consent after successful readiness work',
  async (mode) => {
    let release!: () => void,
      entered = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), 'http://fixture.invalid').pathname;
        if (path.endsWith('/dictation/readiness')) {
          entered = true;
          return new Promise<Response>((resolve) => {
            release = () => resolve(Response.json({ ready: true }));
          });
        }
        if (path.endsWith('/transcribe')) return failure();
        return ordinary(path);
      }),
    );
    const pending = startTranscription(new File(['audio'], 'talk.wav'), mode);
    await waitFor(() => expect(entered).toBe(true));
    await setDiagnosticConsent(true);
    release();
    await pending;
    expect(fixture.capture).toHaveBeenCalledOnce();
  },
);
