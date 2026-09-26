import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../api/external', () => ({
  openExternal: vi.fn(async (_url: string) => {}),
}));

import { openExternal } from '../api/external';
import {
  classifyError,
  ERROR_DOCS,
  ERROR_CLASS_KEYS,
  DEFAULT_DOCS,
  openDocsFor,
  urlFor,
} from './errorDocsMap';

describe('errorDocsMap', () => {
  beforeEach(() => {
    vi.mocked(openExternal).mockClear();
  });

  it('openDocsFor known class calls openExternal with the right URL', async () => {
    await openDocsFor('HF_AUTH_FAILED');
    expect(openExternal).toHaveBeenCalledTimes(1);
    expect(openExternal).toHaveBeenCalledWith(ERROR_DOCS.HF_AUTH_FAILED);
  });

  it('openDocsFor unknown class falls back to default', async () => {
    await openDocsFor('BOGUS_NOT_A_REAL_CLASS');
    expect(openExternal).toHaveBeenCalledWith(DEFAULT_DOCS);
  });

  it('openDocsFor null falls back to default', async () => {
    await openDocsFor(null);
    expect(openExternal).toHaveBeenCalledWith(DEFAULT_DOCS);
  });

  // Sentinel test — locks the taxonomy in step with the Python map
  // (backend/core/error_docs_map.py). Adding a class is
  // a contract change; update both sides + this list.
  it('keys match the locked taxonomy (mirror of Python map)', () => {
    expect(Object.keys(ERROR_DOCS).sort()).toEqual([...ERROR_CLASS_KEYS].sort());
    expect(Object.keys(ERROR_DOCS).sort()).toEqual(
      [
        'GPU_ARCH_UNSUPPORTED',
        'WINDOWS_APP_CONTROL_BLOCKED',
        'AUDIO_IO_FAILED',
        'DIARIZATION_MODEL_MISSING',
        'DIARIZATION_LOAD_FAILED',
        'APPIMAGE_WEBKIT_WHITESCREEN',
        'GATEKEEPER_QUARANTINE',
        'HF_AUTH_FAILED',
        'PKG_RESOURCES_MISSING',
        'POCKETTTS_GATED_WEIGHTS',
        'PYANNOTE_LICENSE_REQUIRED',
      ].sort(),
    );
  });

  it('classifyError maps PocketTTS access failures to its setup guide', () => {
    expect(classifyError(new Error('PocketTTS gated model: share your contact information'))).toBe(
      'POCKETTTS_GATED_WEIGHTS',
    );
  });

  it('every URL resolves under the project repo blob', () => {
    const base = 'https://github.com/debpalash/VoiceStudio/blob/main';
    for (const [key, url] of Object.entries(ERROR_DOCS)) {
      expect(url.startsWith(base), `${key} not under ${base}: ${url}`).toBe(true);
    }
    expect(DEFAULT_DOCS.startsWith(base)).toBe(true);
  });

  it('classifyError maps pkg_resources to PKG_RESOURCES_MISSING', () => {
    expect(classifyError(new Error('ModuleNotFoundError: No module named pkg_resources'))).toBe(
      'PKG_RESOURCES_MISSING',
    );
  });

  it('classifyError maps 401 / HfHubHTTPError to HF_AUTH_FAILED', () => {
    expect(classifyError(new Error('HfHubHTTPError: 401 Unauthorized'))).toBe('HF_AUTH_FAILED');
    expect(classifyError(new Error('Got 401 from HuggingFace'))).toBe('HF_AUTH_FAILED');
  });

  // Issue #78 — diarization-specific 401/gated repo lands on the
  // diarization docs deeplink, not the generic token-setup one.
  it('classifyError maps pyannote / diarization gated-model errors to PYANNOTE_LICENSE_REQUIRED', () => {
    expect(
      classifyError(new Error('pyannote/speaker-diarization-3.1 is gated; 401 Unauthorized')),
    ).toBe('PYANNOTE_LICENSE_REQUIRED');
    expect(classifyError(new Error('Speaker diarization model failed to load'))).toBe(
      'DIARIZATION_LOAD_FAILED',
    );
    expect(classifyError(new Error('You must accept the user conditions for this model'))).toBe(
      'PYANNOTE_LICENSE_REQUIRED',
    );
  });

  it('classifyError maps WebKit / white screen to APPIMAGE_WEBKIT_WHITESCREEN', () => {
    expect(classifyError(new Error('webkit compositing failed'))).toBe(
      'APPIMAGE_WEBKIT_WHITESCREEN',
    );
    expect(classifyError(new Error('white screen on Fedora'))).toBe('APPIMAGE_WEBKIT_WHITESCREEN');
  });

  it('classifyError maps quarantine / Gatekeeper to GATEKEEPER_QUARANTINE', () => {
    expect(classifyError(new Error('com.apple.quarantine flag'))).toBe('GATEKEEPER_QUARANTINE');
    expect(classifyError(new Error('Gatekeeper blocked the launch'))).toBe('GATEKEEPER_QUARANTINE');
    // Issue #72: macOS reports "app is damaged" in English and "已损坏" in
    // localized Chinese builds — both should land on the same docs page.
    expect(classifyError(new Error('VoiceStudio is damaged'))).toBe('GATEKEEPER_QUARANTINE');
    expect(classifyError(new Error('VoiceStudio已损坏，无法打开'))).toBe('GATEKEEPER_QUARANTINE');
  });

  it('classifyError returns null on unknown messages', () => {
    expect(classifyError(new Error('Something totally unrelated'))).toBeNull();
  });

  it('urlFor null returns DEFAULT_DOCS', () => {
    expect(urlFor(null)).toBe(DEFAULT_DOCS);
  });

  it('urlFor a known class returns that URL', () => {
    expect(urlFor('GATEKEEPER_QUARANTINE')).toBe(ERROR_DOCS.GATEKEEPER_QUARANTINE);
  });
});
