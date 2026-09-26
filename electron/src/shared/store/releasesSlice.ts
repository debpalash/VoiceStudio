import type { StateCreator } from 'zustand';
import { listReleases } from '../utils/updater';

interface ReleaseInfo {
  version: string;
  name: string;
  date: string;
  prerelease: boolean;
  notes: string;
}
type ReleasesStatus = 'idle' | 'loading' | 'loaded' | 'error';

export interface ReleasesSlice {
  releases: ReleaseInfo[];
  releasesStatus: ReleasesStatus;
  /** Load releases for a channel. `loader` is injectable for tests; defaults to the Tauri command. */
  loadReleases: (channel: string, loader?: (ch: string) => Promise<ReleaseInfo[]>) => Promise<void>;
}

export const createReleasesSlice: StateCreator<ReleasesSlice, [], [], ReleasesSlice> = (set) => ({
  releases: [],
  releasesStatus: 'idle',
  loadReleases: async (channel, loader = listReleases) => {
    set({ releasesStatus: 'loading' });
    try {
      const data = await loader(channel);
      set({ releases: Array.isArray(data) ? data : [], releasesStatus: 'loaded' });
    } catch {
      set({ releases: [], releasesStatus: 'error' });
    }
  },
});
