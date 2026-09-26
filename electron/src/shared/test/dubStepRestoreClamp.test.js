import { describe, it, expect } from 'vitest';
import { clampRestoredDubStep } from '../hooks/useAppData';

// A dub step persisted mid-flight describes work that died with the process.
// Restoring it verbatim left the Dub tab waiting forever on progress that
// will never arrive — every launch opened onto a blank pane with an eternal
// spinner, and reinstalling didn't help because the webview's localStorage
// survives. The clamp keeps settled states and lands in-flight ones on the
// nearest state the user can actually act from.

const SEGMENTS = [{ id: '0', text: 'hola', text_original: 'hello' }];

describe('clampRestoredDubStep', () => {
  it('passes settled states through unchanged', () => {
    for (const step of ['idle', 'editing', 'done']) {
      expect(clampRestoredDubStep(step, SEGMENTS)).toBe(step);
      expect(clampRestoredDubStep(step, [])).toBe(step);
    }
  });

  it('lands an interrupted generate/stop on the editable session', () => {
    expect(clampRestoredDubStep('generating', SEGMENTS)).toBe('editing');
    expect(clampRestoredDubStep('stopping', SEGMENTS)).toBe('editing');
  });

  it('falls back to idle when the interrupted session has nothing to show', () => {
    expect(clampRestoredDubStep('generating', [])).toBe('idle');
    expect(clampRestoredDubStep('uploading', undefined)).toBe('idle');
    expect(clampRestoredDubStep('transcribing', [])).toBe('idle');
  });

  it('an interrupted transcribe with partial segments is still editable', () => {
    expect(clampRestoredDubStep('transcribing', SEGMENTS)).toBe('editing');
  });

  it('treats unknown or corrupt step values as transient', () => {
    expect(clampRestoredDubStep('green', SEGMENTS)).toBe('editing');
    expect(clampRestoredDubStep(42, [])).toBe('idle');
    expect(clampRestoredDubStep(null, SEGMENTS)).toBe('editing');
  });
});
