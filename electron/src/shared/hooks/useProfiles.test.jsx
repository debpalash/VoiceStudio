import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const store = vi.hoisted(() => ({
  setRefText: vi.fn(),
  setInstruct: vi.fn(),
  setLanguage: vi.fn(),
  setVdStates: vi.fn(),
  setDefineMethod: vi.fn(),
  language: 'Auto',
  mode: 'studio',
  steps: 16,
  cfg: 2,
  dubLang: 'English',
  dubSegments: [],
  text: '',
}));

vi.mock('../store', () => ({ useAppStore: (selector) => selector(store) }));

import useProfiles from './useProfiles';

const allAuto = {
  Gender: 'Auto',
  Age: 'Auto',
  Pitch: 'Auto',
  Style: 'Auto',
  EnglishAccent: 'Auto',
  ChineseDialect: 'Auto',
};

describe('useProfiles design-profile selection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('reconstructs missing vd_states from instruct instead of retaining stale sliders', () => {
    const { result } = renderHook(() =>
      useProfiles({ loadHistory: vi.fn(), loadProfiles: vi.fn() }),
    );

    act(() =>
      result.current.handleSelectProfile({
        id: 'legacy-design',
        kind: 'design',
        instruct: 'female, young adult, high pitch',
        vd_states: null,
      }),
    );

    expect(store.setVdStates).toHaveBeenCalledWith({
      ...allAuto,
      Gender: 'female',
      Age: 'young adult',
      Pitch: 'high pitch',
    });
    expect(store.setInstruct).toHaveBeenCalledWith('');
    expect(store.setDefineMethod).toHaveBeenCalledWith('design');
  });

  it('resets to a complete Auto recipe when legacy design metadata is unusable', () => {
    const { result } = renderHook(() =>
      useProfiles({ loadHistory: vi.fn(), loadProfiles: vi.fn() }),
    );

    act(() =>
      result.current.handleSelectProfile({
        id: 'broken-design',
        kind: 'design',
        instruct: 'unsupported prose',
        vd_states: '{broken',
      }),
    );

    expect(store.setVdStates).toHaveBeenCalledWith(allAuto);
    expect(store.setLanguage).toHaveBeenCalledWith('Auto');
  });

  it('fills a partial stored recipe from the validated instruct', () => {
    const { result } = renderHook(() =>
      useProfiles({ loadHistory: vi.fn(), loadProfiles: vi.fn() }),
    );

    act(() =>
      result.current.handleSelectProfile({
        id: 'partial-design',
        kind: 'design',
        instruct: 'female, high pitch, american accent',
        vd_states: JSON.stringify({ Gender: 'male' }),
      }),
    );

    expect(store.setVdStates).toHaveBeenCalledWith({
      ...allAuto,
      Gender: 'male',
      Pitch: 'high pitch',
      EnglishAccent: 'american accent',
    });
  });
});
