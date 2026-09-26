import { describe, it, expect } from 'vitest';
import i18n from '../i18n';
import { refineFailureNoteKey } from '../components/settings/refineStatus';

// The honesty layer behind `llm_ready` (which only means "an endpoint is
// configured"): when the backend reports the last dictation refinement failed
// or timed out, the panel surfaces WHY and points at the LLM Providers Test
// button. A healthy/absent status shows nothing. The helper returns an i18n
// key (not hardcoded English) — resolve it like the panel does.
const note = (status) => {
  const key = refineFailureNoteKey(status);
  return key == null ? null : i18n.t(key);
};

describe('refineFailureNoteKey — RefinementPanel honesty hint', () => {
  it('is silent when there is no status', () => {
    expect(note(null)).toBeNull();
    expect(note(undefined)).toBeNull();
  });

  it('is silent when the last refinement succeeded', () => {
    expect(note({ ok: true, reason: null })).toBeNull();
  });

  it('flags a timeout as a slow/unreachable endpoint (dictation still works)', () => {
    const msg = note({ ok: false, reason: 'timeout' });
    expect(msg).toMatch(/timed out/i);
    expect(msg).toMatch(/raw transcript is inserted/i);
    expect(msg).toMatch(/LLM Providers/i);
  });

  it('flags a non-timeout failure as a rejected request', () => {
    const msg = note({ ok: false, reason: 'RuntimeError' });
    expect(msg).toMatch(/failed/i);
    expect(msg).toMatch(/LLM Providers/i);
  });

  it('resolves to real translations (no raw-key leakage through t())', () => {
    expect(note({ ok: false, reason: 'timeout' })).not.toMatch(/^dictation\./);
    expect(note({ ok: false, reason: 'RuntimeError' })).not.toMatch(/^dictation\./);
  });
});
