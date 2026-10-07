import { getBridge } from '@/components/bridge';
import { errorFromResponse } from '@/lib/api/client';
import { decodeDownloadFailure, stripIpcErrorPrefix } from '@shared/download-failure';
import type { SaveAudioRequest, SaveAudioResult, SaveDataRequest } from '../../../preload/index.d';

/**
 * Turn a rejected native save into the error a direct backend request would
 * have produced: an `ApiError` carrying the backend's (localized) detail, or
 * the plain message without Electron's IPC wrapper (#2616).
 */
export async function nativeSaveError(error: unknown): Promise<Error> {
  const failure = decodeDownloadFailure(error);
  if (failure && failure.status >= 200 && failure.status <= 599)
    return errorFromResponse(
      new Response(failure.body, { status: failure.status, statusText: failure.statusText }),
    );
  const message = error instanceof Error ? error.message : String(error);
  return new Error(stripIpcErrorPrefix(message));
}

/** Every native save of a backend URL goes through here so failures read the same. */
export async function saveBackendFile(req: SaveAudioRequest): Promise<SaveAudioResult | null> {
  const bridge = getBridge();
  if (!bridge) return null;
  try {
    return await bridge.files.saveAudio(req);
  } catch (error) {
    throw await nativeSaveError(error);
  }
}

/** Native save of in-memory data (no backend request), with the same error shape. */
export async function saveNativeData(req: SaveDataRequest): Promise<SaveAudioResult | null> {
  const bridge = getBridge();
  if (!bridge) return null;
  try {
    return await bridge.files.saveData(req);
  } catch (error) {
    throw await nativeSaveError(error);
  }
}
