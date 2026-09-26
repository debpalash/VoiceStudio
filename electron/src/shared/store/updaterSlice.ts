import type { StateCreator } from 'zustand';
import { normalizeChannel } from '../utils/updateChannel';

/**
 * Auto-update state machine (Tauri updater). Transient — never persisted.
 * idle → checking → available → downloading(progress) → ready → (relaunch)
 *                 ↘ idle (up to date)        ↘ error → idle (retry/dismiss)
 */
type UpdateStatus = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error';

export interface UpdaterSlice {
  updateStatus: UpdateStatus;
  updateVersion: string | null;
  updateNotes: string | null;
  updateProgress: number; // 0–100
  updateError: string | null;
  appVersion: string | null;
  updateChannel: 'stable' | 'preview';
  setAppVersion: (v: string | null) => void;
  setUpdateChannelValue: (ch: string) => void;
  setUpdateChecking: () => void;
  setUpdateAvailable: (version: string, notes: string | null) => void;
  setUpdateIdle: () => void;
  setUpdateProgress: (pct: number) => void;
  setUpdateReady: () => void;
  setUpdateError: (msg: string) => void;
  /** Clear a failed/finished update surface (the badge's dismiss ×). */
  dismissUpdate: () => void;
}

export const createUpdaterSlice: StateCreator<UpdaterSlice, [], [], UpdaterSlice> = (set) => ({
  updateStatus: 'idle',
  updateVersion: null,
  updateNotes: null,
  updateProgress: 0,
  updateError: null,
  setUpdateChecking: () => set({ updateStatus: 'checking', updateError: null }),
  setUpdateAvailable: (version, notes) =>
    set({
      updateStatus: 'available',
      updateVersion: version,
      updateNotes: notes,
      updateError: null,
    }),
  setUpdateIdle: () => set({ updateStatus: 'idle', updateProgress: 0 }),
  setUpdateProgress: (pct) =>
    set({
      updateStatus: 'downloading',
      updateProgress: Math.max(0, Math.min(100, Math.round(pct))),
    }),
  setUpdateReady: () => set({ updateStatus: 'ready', updateProgress: 100 }),
  setUpdateError: (msg) => set({ updateStatus: 'error', updateError: msg }),
  dismissUpdate: () => set({ updateStatus: 'idle', updateError: null, updateProgress: 0 }),
  appVersion: null,
  updateChannel: 'stable',
  setAppVersion: (v) => set({ appVersion: v }),
  setUpdateChannelValue: (ch) => set({ updateChannel: normalizeChannel(ch) }),
});
