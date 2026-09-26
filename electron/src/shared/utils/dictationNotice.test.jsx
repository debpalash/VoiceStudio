/**
 * dictationNotice — the main window's half of the widget's error bridge.
 *
 * A missing speech model used to arrive here as a bare label. The widget had
 * already tried to show the install toast itself, but in the desktop app its
 * window has no <Toaster>, so that toast rendered nowhere — and the main window
 * showed "Transcription failed: No speech-to-text model is installed" with no
 * way to install one.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  toastAsrModelMissing: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('./asrModelMissing', () => ({ toastAsrModelMissing: mocks.toastAsrModelMissing }));
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { error: mocks.toastError, dismiss: vi.fn() }),
}));
vi.mock('./permissions', () => ({
  inTauri: () => true,
  openAccessibilitySettings: vi.fn(),
  openMicrophoneSettings: vi.fn(),
}));

import { showDictationNotice } from './dictationNotice';

describe('showDictationNotice', () => {
  beforeEach(() => {
    mocks.toastAsrModelMissing.mockReset();
    mocks.toastError.mockReset();
  });

  it('offers the model download for a missing speech model', () => {
    const missing = { recommended: { repo_id: 'x/parakeet', label: 'Parakeet', size_gb: 0.6 } };

    showDictationNotice({ kind: 'asr_missing', label: 'ignored', missing });

    expect(mocks.toastAsrModelMissing).toHaveBeenCalledWith(missing);
    // One toast, not the install toast plus a bare duplicate.
    expect(mocks.toastError).not.toHaveBeenCalled();
  });

  it('still explains a missing model when no recommendation came with it', () => {
    showDictationNotice({ kind: 'asr_missing' });

    expect(mocks.toastAsrModelMissing).toHaveBeenCalledWith({});
  });

  it('keeps plain labels for everything else', () => {
    showDictationNotice({ kind: 'transcription', label: 'Transcription failed: boom' });

    expect(mocks.toastAsrModelMissing).not.toHaveBeenCalled();
    expect(mocks.toastError).toHaveBeenCalledWith('Transcription failed: boom', { duration: 8000 });
  });
});
