import { apiJson } from './client';

/**
 * Watermark settings + provenance detection.
 *
 * The backend has exposed these since watermarking shipped; nothing in the
 * frontend ever called them, which is how the app came to promise a control
 * ("Commercial licensees can disable it in Settings → Privacy") that did not
 * exist. `backend/api/routers/watermark.py` is the contract.
 */

export interface WatermarkStatus {
  /** AudioSeal invisible watermark — the provenance mark, on by default. */
  invisible_enabled: boolean;
  visible_audio_enabled: boolean;
  visible_video_enabled: boolean;
  /** False when the AudioSeal package/checkpoint isn't usable on this install. */
  audioseal_available: boolean;
}

export async function getWatermarkStatus(): Promise<WatermarkStatus> {
  return apiJson<WatermarkStatus>('/watermark/status');
}

/** Partial update — the endpoint takes each flag as an optional query param. */
export async function setWatermarkSettings(
  patch: Partial<Pick<WatermarkStatus, 'invisible_enabled'>>,
): Promise<WatermarkStatus> {
  const params = new URLSearchParams();
  if (patch.invisible_enabled !== undefined) {
    params.set('invisible', String(patch.invisible_enabled));
  }
  return apiJson<WatermarkStatus>(`/watermark/settings?${params}`, { method: 'POST' });
}
