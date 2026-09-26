import { describe, it, expect, beforeEach } from 'vitest';
import { useAppStore } from '../store';

// The footer version badge deep-links to Settings → Updates via openSettingsTab.
describe('openSettingsTab — footer version badge → Settings tab deep-link', () => {
  beforeEach(() => {
    useAppStore.getState().setMode('launchpad');
    useAppStore.getState().setPendingSettingsTab(null);
  });

  it('sets the pending tab AND navigates to settings in one call', () => {
    useAppStore.getState().openSettingsTab('updates');
    expect(useAppStore.getState().mode).toBe('settings');
    expect(useAppStore.getState().pendingSettingsTab).toBe('updates');
  });

  it('setPendingSettingsTab can clear the one-shot value after Settings consumes it', () => {
    useAppStore.getState().openSettingsTab('updates');
    useAppStore.getState().setPendingSettingsTab(null);
    expect(useAppStore.getState().pendingSettingsTab).toBeNull();
    // mode stays on settings — only the one-shot tab hint is cleared
    expect(useAppStore.getState().mode).toBe('settings');
  });
});
