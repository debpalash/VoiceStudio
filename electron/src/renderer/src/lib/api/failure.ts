import i18next from 'i18next';
import { generationFailureMessage } from '@shared/utils/generationFailureMessage.ts';
import { ApiError } from './client';
export interface PublicFailure {
  reason: string;
  errorClass?: string;
  stage?: string;
  hint?: string;
  docsTopic?: string;
  docsUrl?: string;
  diagnostic?: string;
}

const text = (value: unknown) => (typeof value === 'string' && value.trim() ? value : undefined);

export function publicFailureFromEvent(
  event: Record<string, unknown>,
  fallback: string,
): PublicFailure {
  const localized = generationFailureMessage(event, i18next.t);
  return {
    reason:
      (event.error_code === 'dub_speech_missing'
        ? i18next.t('dubIntegrity.missingSpeech')
        : event.error_code === 'dub_timing_overflow'
          ? i18next.t('dubIntegrity.timingOverflow')
          : event.error_code === 'dub_source_changed'
            ? i18next.t('dubIntegrity.sourceChanged')
            : undefined) ||
      localized ||
      text(event.reason) ||
      text(event.detail) ||
      text(event.error) ||
      text(event.message) ||
      fallback,
    errorClass: text(event.error_class),
    stage: text(event.stage),
    hint: localized ? undefined : text(event.hint),
    docsTopic: text(event.docs_topic),
    docsUrl: text(event.docs_url),
    diagnostic: text(event.diagnostic),
  };
}

/**
 * The remediation a classified API failure carries — the localized message for a
 * known `docs_topic`/`code`, else the backend's VoiceStudio-owned `hint` — or
 * undefined for an unclassified failure. Surfaces that show a short title and
 * tuck the raw detail behind "Technical details" render this visibly, so a
 * diagnosed cause (a full disk, a missing ffmpeg) is never hidden.
 */
export function apiFailureGuidance(error: unknown): string | undefined {
  if (!(error instanceof ApiError) || !error.payload) return undefined;
  // A classified HTTPException carries its diagnosis under `detail` (/generate 400).
  const { detail } = error.payload;
  const nested =
    detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : undefined;
  return (
    generationFailureMessage(error.payload, i18next.t) ||
    generationFailureMessage(nested, i18next.t) ||
    text(error.payload.hint) ||
    text(nested?.hint)
  );
}
