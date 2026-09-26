import { apiFetch } from './client';

/**
 * Transcode a client-stitched WAV blob to a compressed format via the backend
 * ffmpeg endpoint. Same-origin + PIN-aware (apiFetch). Throws on failure so the
 * caller can fall back to the raw WAV.
 */
export async function encodeAudio(wavBlob: Blob, format = 'mp3', bitrate = '192k'): Promise<Blob> {
  const fd = new FormData();
  fd.append('file', wavBlob, 'story.wav');
  fd.append('format', format);
  fd.append('bitrate', bitrate);
  const res = await apiFetch('/stories/encode', { method: 'POST', body: fd });
  return res.blob();
}
