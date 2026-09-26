/** Resolve the effective export track against tracks that actually exist. */
export function resolveDubDefaultTrack(defaultTrack, dubLangCode, dubTracks = []) {
  if (defaultTrack === 'original') return 'original';
  if (dubTracks.includes(defaultTrack)) return defaultTrack;
  if (dubTracks.includes(dubLangCode)) return dubLangCode;
  return dubTracks[0] || 'original';
}
