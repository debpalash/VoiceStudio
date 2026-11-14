import { normalizeChannel } from './updateChannel';

export function isTauri() {
  return typeof window !== 'undefined' && !!window.voicestudio;
}

/** Retained release-list adapter for the shared releases store. */
export async function listReleases(channel) {
  if (!isTauri()) return [];
  const data = await window.voicestudio.updates.listReleases(normalizeChannel(channel));
  return Array.isArray(data) ? data : [];
}
