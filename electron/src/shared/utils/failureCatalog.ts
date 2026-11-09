/**
 * One place for every diagnosed backend failure class: the localized message the
 * app shows instead of the backend's English text, and the in-app action that
 * fixes it. The keys mirror the failure taxonomy in backend/core/failure.py
 * (`_HINTS` plus the dynamic HF_MIRROR_UNREACHABLE); tests/test_failure_catalog.py
 * fails when a class exists there without an entry here, or a message key is
 * missing from a locale.
 */

/** Where an action button takes the user. */
export type FailureTarget =
  | 'storage'
  | 'performance'
  | 'models'
  | 'models_asr'
  | 'models_tts'
  | 'models_diarisation'
  | 'media'
  | 'credentials'
  | 'logs';

/** The fix the banner offers as its primary button. */
export type FailureAction =
  | { kind: 'install_stt' }
  | { kind: 'free_memory' }
  | { kind: 'open'; target: FailureTarget };

export interface FailureEntry {
  messageKey: string;
  action?: FailureAction;
}

const open = (target: FailureTarget): FailureAction => ({ kind: 'open', target });

export const FAILURE_CATALOG = {
  APPIMAGE_WEBKIT_WHITESCREEN: { messageKey: 'failureClass.appimage_webkit_whitescreen' },
  AUDIO_IO_FAILED: { messageKey: 'tts_errors.audio_io_failed', action: open('storage') },
  BROKEN_VENV: { messageKey: 'failureClass.broken_venv' },
  CLONE_REFERENCE_MISSING: { messageKey: 'failureClass.clone_reference_missing' },
  COMPUTE_TYPE_UNSUPPORTED: {
    messageKey: 'failureClass.compute_type_unsupported',
    action: open('performance'),
  },
  DIARIZATION_LOAD_FAILED: {
    messageKey: 'failureClass.diarization_load_failed',
    action: open('logs'),
  },
  DIARIZATION_MODEL_MISSING: {
    messageKey: 'failureClass.diarization_model_missing',
    action: open('models_diarisation'),
  },
  DISK_FULL: { messageKey: 'tts_errors.disk_full', action: open('storage') },
  ENGINE_DEPENDENCY_MISSING: {
    messageKey: 'tts_errors.engine_dependency_missing',
    action: open('models_tts'),
  },
  GATEKEEPER_QUARANTINE: { messageKey: 'failureClass.gatekeeper_quarantine' },
  GPU_ARCH_UNSUPPORTED: {
    messageKey: 'tts_errors.gpu_arch_unsupported',
    action: open('performance'),
  },
  GPU_OOM: { messageKey: 'tts_errors.gpu_oom', action: { kind: 'free_memory' } },
  HF_AUTH_FAILED: { messageKey: 'failureClass.hf_auth_failed', action: open('credentials') },
  HF_MIRROR_GATED: { messageKey: 'modelMaintenance.mirrorGatedAccess', action: open('models') },
  HF_MIRROR_UNREACHABLE: {
    messageKey: 'failureClass.hf_mirror_unreachable',
    action: open('models'),
  },
  HOST_MEMORY_EXHAUSTED: {
    messageKey: 'tts_errors.host_memory_exhausted',
    action: { kind: 'free_memory' },
  },
  INPUT_TOO_SHORT: { messageKey: 'failureClass.input_too_short' },
  INVALID_MEDIA_FILE: { messageKey: 'failureClass.invalid_media_file' },
  MEDIA_TOOL_MISSING: { messageKey: 'failureClass.media_tool_missing', action: open('media') },
  MODEL_CACHE_CORRUPT: { messageKey: 'failureClass.model_cache_corrupt', action: open('models') },
  MODEL_DOWNLOAD_INTERRUPTED: { messageKey: 'failureClass.model_download_interrupted' },
  NO_AUDIO_TRACK: { messageKey: 'tts_errors.no_audio_track' },
  OS_INVALID_ARGUMENT: { messageKey: 'failureClass.os_invalid_argument', action: open('storage') },
  PKG_RESOURCES_MISSING: { messageKey: 'failureClass.pkg_resources_missing' },
  POCKETTTS_GATED_WEIGHTS: {
    messageKey: 'failureClass.pockettts_gated_weights',
    action: open('credentials'),
  },
  PYANNOTE_LICENSE_REQUIRED: {
    messageKey: 'failureClass.pyannote_license_required',
    action: open('credentials'),
  },
  REFERENCE_STT_MISSING: {
    messageKey: 'tts_errors.reference_stt_missing',
    action: { kind: 'install_stt' },
  },
  SOCKS_PROXY_SUPPORT_MISSING: { messageKey: 'failureClass.socks_proxy_support_missing' },
  SOURCE_LANGUAGE_REQUIRED: {
    messageKey: 'tts_errors.source_language_required',
    action: open('models_asr'),
  },
  SSL_HANDSHAKE_FAILURE: { messageKey: 'failureClass.ssl_handshake_failure' },
  TLS_CONNECTION_DROPPED: { messageKey: 'failureClass.tls_connection_dropped' },
  TRANSFORMERS_IMPORT: { messageKey: 'failureClass.transformers_import', action: open('logs') },
  UNSUPPORTED_VIDEO_URL: { messageKey: 'failureClass.unsupported_video_url' },
  VIDEO_DOWNLOAD_BOT_CHECK: { messageKey: 'failureClass.video_download_bot_check' },
  VIDEO_DOWNLOAD_NETWORK: { messageKey: 'failureClass.video_download_network' },
  VIDEO_DOWNLOAD_OS_ERROR: {
    messageKey: 'failureClass.video_download_os_error',
    action: open('storage'),
  },
  VIDEO_DRM_PROTECTED: { messageKey: 'failureClass.video_drm_protected' },
  WINDOWS_APP_CONTROL_BLOCKED: { messageKey: 'tts_errors.windows_app_control_blocked' },
  WINDOWS_PAGING_FILE_TOO_SMALL: { messageKey: 'failureClass.windows_paging_file_too_small' },
  WINDOWS_UNTRUSTED_MOUNT: {
    messageKey: 'failureClass.windows_untrusted_mount',
    action: open('storage'),
  },
} as const satisfies Record<string, FailureEntry>;

export type FailureTopic = keyof typeof FAILURE_CATALOG;

export function isFailureTopic(value: unknown): value is FailureTopic {
  return typeof value === 'string' && Object.hasOwn(FAILURE_CATALOG, value);
}

/**
 * Sentences VoiceStudio itself writes for a class, recognised when a surface
 * received them as bare text (an older backend, or a route that forwards the
 * exception message). Lower-case substrings; tests/test_failure_catalog.py checks
 * the backend still classifies each one to the same class.
 */
export const OWNED_FAILURE_SIGNATURES: ReadonlyArray<readonly [string, FailureTopic]> = [
  ['automatic reference transcription needs an installed speech-to-text', 'REFERENCE_STT_MISSING'],
  ['this file has no audio track', 'NO_AUDIO_TRACK'],
  ['the selected media file is damaged or incomplete', 'INVALID_MEDIA_FILE'],
  ['speech recognition did not report which language', 'SOURCE_LANGUAGE_REQUIRED'],
];

function topicFromText(text: string): FailureTopic | undefined {
  const lower = text.toLowerCase();
  return OWNED_FAILURE_SIGNATURES.find(([signature]) => lower.includes(signature))?.[1];
}

/**
 * The diagnosed class behind any failure shape the app holds: a backend body or
 * stream frame (`docs_topic`, possibly nested under `detail`), an ApiError
 * (`payload`), a PublicFailure (`docsTopic`), an Error, or bare text.
 */
export function failureTopicOf(value: unknown, depth = 0): FailureTopic | undefined {
  if (typeof value === 'string') return topicFromText(value);
  if (!value || typeof value !== 'object' || depth > 3) return undefined;
  const record = value as Record<string, unknown>;
  for (const key of ['docs_topic', 'docsTopic'] as const)
    if (isFailureTopic(record[key])) return record[key];
  for (const key of ['payload', 'detail'] as const) {
    const nested = failureTopicOf(record[key], depth + 1);
    if (nested) return nested;
  }
  for (const key of ['reason', 'message', 'error'] as const)
    if (typeof record[key] === 'string') {
      const nested = topicFromText(record[key]);
      if (nested) return nested;
    }
  return undefined;
}
