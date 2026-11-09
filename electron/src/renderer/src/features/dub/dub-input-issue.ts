import type { ApiError } from '@/lib/api/client';
import type { PublicFailure } from '@/lib/api/failure';

/**
 * A source the user can fix by choosing another file. These are not crashes:
 * the page shows them on the file row instead of the failure banner, with no
 * diagnostic to copy.
 */
export const DUB_INPUT_ISSUES = ['no_audio', 'invalid_media', 'unsupported', 'empty'] as const;
export type DubInputIssue = (typeof DUB_INPUT_ISSUES)[number];

const BY_TOPIC: Record<string, DubInputIssue> = {
  NO_AUDIO_TRACK: 'no_audio',
  INVALID_MEDIA_FILE: 'invalid_media',
};
const BY_CODE: Record<string, DubInputIssue> = {
  no_audio_track: 'no_audio',
  invalid_media_file: 'invalid_media',
};

function detailField(detail: unknown, field: 'code' | 'docs_topic'): string | undefined {
  if (!detail || typeof detail !== 'object') return undefined;
  const value = (detail as Record<string, unknown>)[field];
  return typeof value === 'string' ? value : undefined;
}

function isApiError(error: unknown): error is Pick<ApiError, 'status' | 'payload'> {
  return (
    typeof error === 'object' &&
    error !== null &&
    typeof (error as { status?: unknown }).status === 'number' &&
    'payload' in error
  );
}

/** The input problem behind a failed upload or transcription, or null for a real failure. */
export function dubInputIssue(
  error: unknown,
  failure?: Pick<PublicFailure, 'docsTopic' | 'errorClass'> | null,
): DubInputIssue | null {
  const topic = failure?.docsTopic || failure?.errorClass;
  if (topic && BY_TOPIC[topic]) return BY_TOPIC[topic];
  // Duck-typed so the check holds wherever the API client is mocked or re-bundled.
  if (!isApiError(error)) return null;
  const detail = error.payload?.detail;
  const code = detailField(detail, 'code') || error.payload?.code;
  if (code && BY_CODE[code]) return BY_CODE[code];
  const docsTopic = detailField(detail, 'docs_topic');
  if (docsTopic && BY_TOPIC[docsTopic]) return BY_TOPIC[docsTopic];
  // 415 is only ever the file-type check on the upload routes.
  if (error.status === 415) return 'unsupported';
  return null;
}

export const DUB_INPUT_ISSUE_KEYS: Record<DubInputIssue, string> = {
  no_audio: 'dub.input_issue_no_audio',
  invalid_media: 'dub.input_issue_invalid_media',
  unsupported: 'dub.input_issue_unsupported',
  empty: 'dub.input_issue_empty',
};
