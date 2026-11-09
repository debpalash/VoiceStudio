/**
 * `m:ss` for a playback position. Kept out of the player modules so lists and
 * sidebars can show clip lengths without loading Vidstack and WaveSurfer.
 */
export function formatClock(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  return Math.floor(safe / 60) + ':' + String(Math.floor(safe % 60)).padStart(2, '0');
}
