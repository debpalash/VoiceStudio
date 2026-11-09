import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useTtsReadiness } from './use-tts-readiness';

const state = vi.hoisted(() => ({
  backendStage: 'ready',
  localReady: false,
  supportsCloning: true,
}));

vi.mock('./use-backend-status', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./use-backend-status')>();
  return { ...actual, useBackendStatus: () => ({ stage: state.backendStage }) };
});

vi.mock('./use-engines', () => ({
  engineFamilyState: (data: Record<string, unknown> | undefined, family: string) => data?.[family],
  useEngines: () => ({
    data: {
      tts: {
        active: 'omnivoice',
        active_model: 'k2-fsa/OmniVoice',
        backends: [
          {
            id: 'omnivoice',
            available: state.localReady,
            supports_cloning: state.supportsCloning,
          },
        ],
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

describe('local TTS readiness', () => {
  beforeEach(() => {
    Object.assign(state, {
      backendStage: 'ready',
      localReady: false,
      supportsCloning: true,
    });
  });

  it('blocks an unavailable local engine', () => {
    expect(renderHook(() => useTtsReadiness()).result.current).toBe('engine');
  });

  it('allows an installed local engine', () => {
    state.localReady = true;
    expect(renderHook(() => useTtsReadiness()).result.current).toBeNull();
  });

  it('blocks cloning with a model that would ignore the reference', () => {
    state.localReady = true;
    state.supportsCloning = false;
    expect(renderHook(() => useTtsReadiness('clone')).result.current).toBe('cloning');
  });

  it('keeps generation available while a live backend is only busy', () => {
    state.backendStage = 'unresponsive';
    state.localReady = true;
    expect(renderHook(() => useTtsReadiness()).result.current).toBeNull();
  });

  it('still blocks generation when the backend is terminally gone', () => {
    state.backendStage = 'crashed';
    state.localReady = true;
    expect(renderHook(() => useTtsReadiness()).result.current).toBe('loading');
  });
});
