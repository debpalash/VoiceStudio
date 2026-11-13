import { describe, expect, it } from 'vitest';
import { refineFailureNoteKey } from '@shared/components/settings/refineStatus';
import en from '@/i18n/locales/en.json';

const dictation = en.dictation as Record<string, string>;

describe('refineFailureNoteKey', () => {
  it('is silent without a status or after a successful refinement', () => {
    expect(refineFailureNoteKey(null)).toBeNull();
    expect(refineFailureNoteKey(undefined)).toBeNull();
    expect(refineFailureNoteKey({ ok: true })).toBeNull();
  });

  it('maps a timeout and any other failure to distinct, translated notes', () => {
    const timeout = refineFailureNoteKey({ ok: false, reason: 'timeout' });
    const failed = refineFailureNoteKey({ ok: false, reason: 'RuntimeError' });
    expect(timeout).toBe('dictation.refine_timeout_note');
    expect(failed).toBe('dictation.refine_failed_note');
    expect(dictation.refine_timeout_note).toMatch(/timed out/i);
    expect(dictation.refine_failed_note).toMatch(/failed/i);
  });
});
