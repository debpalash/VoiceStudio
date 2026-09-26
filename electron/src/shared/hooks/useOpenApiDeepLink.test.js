import { renderHook, act } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useOpenApiDeepLink } from './useOpenApiDeepLink';
import { useAppStore } from '../store';

it('opens API from another Settings category and removes its listener on unmount', () => {
  window.location.hash = '';
  useAppStore.setState({ mode: 'settings', pendingSettingsTab: 'sharing' });
  const open = vi.fn((tab) => useAppStore.getState().openSettingsTab(tab));
  const { unmount } = renderHook(() => useOpenApiDeepLink(open));
  act(() => {
    window.location.hash = '#tag/speech-platform';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(useAppStore.getState().pendingSettingsTab).toBe('openapi');
  expect(open).toHaveBeenCalledTimes(1);
  unmount();
  window.dispatchEvent(new HashChangeEvent('hashchange'));
  expect(open).toHaveBeenCalledTimes(1);
  window.location.hash = '';
});
