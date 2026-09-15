import i18next from 'i18next';
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
  return {
    reason:
      (event.error_code === 'dub_speech_missing' ? i18next.t('dubIntegrity.missingSpeech') :
        event.error_code === 'dub_timing_overflow' ? i18next.t('dubIntegrity.timingOverflow') : undefined) ||
      text(event.reason) ||
      text(event.detail) ||
      text(event.error) ||
      text(event.message) ||
      fallback,
    errorClass: text(event.error_class),
    stage: text(event.stage),
    hint: text(event.hint),
    docsTopic: text(event.docs_topic),
    docsUrl: text(event.docs_url),
    diagnostic: text(event.diagnostic),
  };
}
