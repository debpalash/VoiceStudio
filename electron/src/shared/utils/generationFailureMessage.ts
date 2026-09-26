/** Translate known diagnostic topics, never a translation key supplied by a server. */
export function generationFailureMessage(
  value: unknown,
  translate: (key: string) => string,
): string | undefined {
  if (!value || typeof value !== 'object') return undefined;
  switch ((value as { docs_topic?: unknown }).docs_topic) {
    case 'GPU_ARCH_UNSUPPORTED':
      return translate('tts_errors.gpu_arch_unsupported');
    case 'WINDOWS_APP_CONTROL_BLOCKED':
      return translate('tts_errors.windows_app_control_blocked');
    case 'AUDIO_IO_FAILED':
      return translate('tts_errors.audio_io_failed');
    case 'NO_AUDIO_TRACK':
      return translate('tts_errors.no_audio_track');
    default:
      return undefined;
  }
}
