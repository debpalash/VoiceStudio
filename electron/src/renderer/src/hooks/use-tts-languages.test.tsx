import { renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useTtsLanguages } from './use-tts-languages';

const state = vi.hoisted(() => ({ error: false, loading: false }));
vi.mock('./use-engines', () => ({
  useEngines: () => ({
    activeTts: { display_name: 'KittenTTS', supported_language_names: ['english'] },
    isError: state.error,
    isLoading: state.loading,
  }),
}));

it('reports local language metadata readiness', () => {
  const { result, rerender } = renderHook(() => useTtsLanguages());
  expect(result.current).toMatchObject({ names: ['english'], state: 'known' });
  state.loading = true;
  rerender();
  expect(result.current.state).toBe('loading');
  state.loading = false;
  state.error = true;
  rerender();
  expect(result.current).toMatchObject({ names: null, state: 'error' });
});
