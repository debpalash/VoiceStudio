import { FAILURE_CATALOG, isFailureTopic } from './failureCatalog.ts';

/** Translate known diagnostic topics, never a translation key supplied by a server. */
export function generationFailureMessage(
  value: unknown,
  translate: (key: string) => string,
): string | undefined {
  if (!value || typeof value !== 'object') return undefined;
  // Stable transcription failure codes (backend/core/public_errors.py): the
  // backend sends fixed English text, the app shows the localized equivalent.
  switch ((value as { code?: unknown }).code) {
    case 'transcription_media_tool':
      return translate('tts_errors.transcription_media_tool');
    case 'transcription_disk_full':
      return translate('tts_errors.disk_full');
    case 'transcription_pipe_lost':
      return translate('tts_errors.transcription_pipe_lost');
  }
  // Every diagnosed class has a catalog message (failureCatalog.ts).
  const topic = (value as { docs_topic?: unknown }).docs_topic;
  return isFailureTopic(topic) ? translate(FAILURE_CATALOG[topic].messageKey) : undefined;
}
