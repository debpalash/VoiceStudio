import { describe, expect, it } from 'vitest';
import type { EnginesResponse } from '@/lib/api/types';
import { engineFamilyState } from '@/hooks/use-engines';
import { isResidentAsrModel, resolveRuntimeHealth, sidebarToolState } from './status-runtime';

describe('plain-language tool status', () => {
  it('never reports cached readiness when the backend is offline', () => {
    for (const state of ['engineRuntime.ready', 'engineRuntime.idle', 'engineRuntime.working']) {
      expect(sidebarToolState(state, false)).toBe('modelMaintenance.offline');
    }
  });

  it('distinguishes loading on use from setup problems without relying on color', () => {
    expect(sidebarToolState('engineRuntime.idle', true)).toBe('modelSettings.available');
    expect(sidebarToolState('modelSettings.unavailable', true)).toBe('sidebarTools.checkSetup');
    expect(sidebarToolState('engineRuntime.working', true)).toBe('engineRuntime.working');
    expect(sidebarToolState('engineSidebar.inactive', true)).toBe('engineSidebar.inactive');
  });
});

const engines = (available = true): EnginesResponse => ({
  tts: {
    active: 'omnivoice',
    active_model: 'k2-fsa/OmniVoice',
    backends: [
      {
        id: 'omnivoice',
        display_name: 'VoiceStudio',
        available,
        reason: available ? null : 'Model missing',
      },
    ],
  },
  asr: { active: null, backends: [] },
  llm: { active: null, backends: [] },
});

describe('sidebar runtime health', () => {
  it('tolerates individual engine families missing from a partial response', () => {
    const partial = { tts: engines().tts } as EnginesResponse;
    expect(engineFamilyState(partial, 'tts')?.active).toBe('omnivoice');
    expect(engineFamilyState(partial, 'asr')).toBeUndefined();
  });

  it('distinguishes engine discovery, unavailable TTS, real loading, and readiness', () => {
    expect(resolveRuntimeHealth(undefined, true, false, undefined)).toBe('checking');
    expect(resolveRuntimeHealth(engines(false), false, false, { status: 'idle' })).toBe(
      'unavailable',
    );
    expect(
      resolveRuntimeHealth(engines(), false, false, {
        status: 'loading',
        sub_stage: 'loading_weights',
      }),
    ).toBe('loading');
    expect(resolveRuntimeHealth(engines(), false, false, { status: 'ready' })).toBe('ready');
  });

  it('does not present a model-load error as ready', () => {
    expect(
      resolveRuntimeHealth(engines(), false, false, {
        status: 'idle',
        sub_stage: 'error',
        error: 'Could not load weights',
      }),
    ).toBe('unavailable');
  });

  it('treats an incomplete engine response as unavailable', () => {
    expect(
      resolveRuntimeHealth(
        { asr: { active: null, backends: [] } } as unknown as EnginesResponse,
        false,
        false,
        undefined,
      ),
    ).toBe('unavailable');
  });
});

describe('resident speech-to-text rows', () => {
  it('counts the warm transcription model alongside dictation', () => {
    expect(isResidentAsrModel('transcription-asr')).toBe(true);
    expect(isResidentAsrModel('capture-asr')).toBe(true);
    expect(isResidentAsrModel('asr')).toBe(true);
    expect(isResidentAsrModel('diarization')).toBe(false);
  });
});
