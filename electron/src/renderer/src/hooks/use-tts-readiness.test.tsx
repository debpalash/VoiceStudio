import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useTtsReadiness } from './use-tts-readiness';

const state = vi.hoisted(() => ({
  backendStage: 'ready',
  localReady: false,
  supportsCloning: true,
  remoteTarget: undefined as string | undefined,
  remotePending: false,
  remoteError: false,
  remoteDownloaded: true,
  remoteResident: false,
  routeRemotely: true,
  computeTargetCalls: [] as Array<[boolean, string]>,
}));

vi.mock('./use-backend-status', () => ({
  useBackendStatus: () => ({ stage: state.backendStage }),
}));

vi.mock('./use-engines', () => ({
  engineFamilyState: (data: Record<string, unknown> | undefined, family: string) => data?.[family],
  useEngines: () => ({
    data: {
      tts: {
        active: 'omnivoice',
        active_model: 'k2-fsa/OmniVoice',
        backends: [{ id: 'omnivoice', available: state.localReady, supports_cloning: state.supportsCloning }],
      },
    },
    activeTtsReady: state.localReady,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock('@/features/settings/model-catalogue-query', () => ({
  useModelCatalogue: () => ({
    data: {
      models: [
        {
          repo_id: 'k2-fsa/OmniVoice',
          installed: state.localReady,
          incomplete: false,
        },
      ],
    },
    isLoading: false,
  }),
}));

vi.mock('./use-compute-target', () => ({
  useComputeTarget: (enabled: boolean, operation: string) => {
    state.computeTargetCalls.push([enabled, operation]);
    return {
      data: {
        active: {
          remote: Boolean(state.remoteTarget) && state.routeRemotely,
          worker_id: state.remoteTarget,
        },
      },
      isLoading: false,
    };
  },
  useComputeRuntime: () => ({
    data:
      state.remoteTarget && !state.remotePending
        ? {
            target: state.remoteTarget,
            remote: true,
            label: 'Studio GPU',
            reason: 'chosen',
            models: [
              {
                engine: 'omnivoice',
                model_id: 'omnivoice:default',
                supported: true,
                installed: true,
                downloaded: state.remoteDownloaded,
                resident: state.remoteResident,
              },
            ],
          }
        : undefined,
    isPending: state.remotePending,
    isError: state.remoteError,
  }),
}));

describe('target-aware TTS readiness', () => {
  it('blocks cloning with a model that would ignore the reference', () => {
    state.localReady = true;
    state.supportsCloning = false;
    const { result } = renderHook(() => useTtsReadiness('clone'));
    expect(result.current).toBe('cloning');
  });
  beforeEach(() => {
    Object.assign(state, {
      backendStage: 'ready',
      localReady: false,
      supportsCloning: true,
      remoteTarget: undefined,
      remotePending: false,
      remoteError: false,
      remoteDownloaded: true,
      remoteResident: false,
      routeRemotely: true,
      computeTargetCalls: [],
    });
  });

  it('blocks an unavailable local engine', () => {
    expect(renderHook(() => useTtsReadiness()).result.current).toBe('engine');
  });

  it('allows a ready remote engine even when the local model is absent', () => {
    state.remoteTarget = 'worker-1';
    expect(renderHook(() => useTtsReadiness()).result.current).toBeNull();
  });

  it('blocks a remote engine whose weights are absent', () => {
    state.remoteTarget = 'worker-1';
    state.remoteDownloaded = false;
    expect(renderHook(() => useTtsReadiness()).result.current).toBe('engine');
  });

  it('can require the local fallback for a remotely routed operation', () => {
    state.remoteTarget = 'worker-1';

    expect(renderHook(() => useTtsReadiness('dub', true)).result.current).toBe('engine');
  });

  it('keeps the action pending while remote readiness is loading', () => {
    state.remoteTarget = 'worker-1';
    state.remotePending = true;
    expect(renderHook(() => useTtsReadiness()).result.current).toBe('loading');
  });

  it('uses the operation-specific route and honors its local fallback', () => {
    state.remoteTarget = 'worker-1';
    state.routeRemotely = false;
    state.localReady = true;

    expect(renderHook(() => useTtsReadiness('batch')).result.current).toBeNull();
    expect(state.computeTargetCalls).toContainEqual([true, 'batch']);
  });

  it('checks cloning capability for clone actions', () => {
    state.localReady = true;

    expect(renderHook(() => useTtsReadiness('clone')).result.current).toBeNull();
    expect(state.computeTargetCalls).toContainEqual([true, 'clone']);
  });
});
