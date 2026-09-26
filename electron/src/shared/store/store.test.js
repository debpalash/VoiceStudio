import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';

describe('Zustand store', () => {
  it('useAppStore initialises with default mode', async () => {
    const { useAppStore } = await import('../store');
    const { result } = renderHook(() => useAppStore((s) => s.mode));
    // Default mode should be a string (launchpad, design, clone, or dub)
    expect(typeof result.current).toBe('string');
    expect(result.current.length).toBeGreaterThan(0);
  });

  it('setMode updates mode', async () => {
    const { useAppStore } = await import('../store');
    const { result, rerender } = renderHook(() => ({
      mode: useAppStore((s) => s.mode),
      setMode: useAppStore((s) => s.setMode),
    }));
    result.current.setMode('dub');
    rerender();
    expect(result.current.mode).toBe('dub');
  });

  it('setText updates text', async () => {
    const { useAppStore } = await import('../store');
    const { result, rerender } = renderHook(() => ({
      text: useAppStore((s) => s.text),
      setText: useAppStore((s) => s.setText),
    }));
    result.current.setText('hello world');
    rerender();
    expect(result.current.text).toBe('hello world');
  });

  it('dubSlice initialises with idle step', async () => {
    const { useAppStore } = await import('../store');
    const { result } = renderHook(() => useAppStore((s) => s.dubStep));
    expect(result.current).toBe('idle');
  });

  it('pill slice starts at idle', async () => {
    const { useAppStore } = await import('../store');
    const { result } = renderHook(() => useAppStore((s) => s.stage));
    expect(result.current).toBe('idle');
  });
});
