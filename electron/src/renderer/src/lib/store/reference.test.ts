import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { probeAudioDuration } = vi.hoisted(() => ({
  probeAudioDuration: vi.fn<(file: Blob) => Promise<number | null>>(),
}));
vi.mock('@/lib/audio/probe', () => ({ probeAudioDuration }));

import { clearReference, referenceStore, selectCloneProfile, setReferenceFile } from './reference';
import { cloneSettingsStore, setCloneSetting } from './clone-settings';

const createObjectURL = vi.fn(() => `blob:mock-${createObjectURL.mock.calls.length}`);
const revokeObjectURL = vi.fn();

function file(name = 'ref.wav'): File {
  return new File([new Uint8Array(16)], name, { type: 'audio/wav' });
}

beforeEach(() => {
  Object.assign(URL, { createObjectURL, revokeObjectURL });
  probeAudioDuration.mockReset();
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  clearReference();
});

afterEach(() => {
  clearReference();
});

describe('setReferenceFile', () => {
  it('accepts a short clip and deselects the saved voice', async () => {
    setCloneSetting('selectedProfileId', 'p1');
    setCloneSetting('language', 'French');
    setCloneSetting('refText', 'Old transcript');
    probeAudioDuration.mockResolvedValue(9.5);
    const f = file();
    const result = await setReferenceFile(f);
    expect(result).toEqual({ ok: true, durationSeconds: 9.5, tooLong: false });
    expect(referenceStore.state.file).toBe(f);
    expect(referenceStore.state.durationSeconds).toBe(9.5);
    expect(referenceStore.state.objectUrl).toMatch(/^blob:mock-/);
    expect(cloneSettingsStore.state.selectedProfileId).toBeNull();
    expect(cloneSettingsStore.state.language).toBe('French');
    expect(cloneSettingsStore.state.refText).toBe('');
  });

  it('flags a clip over CLONE_MAX_SECONDS as tooLong but still accepts it', async () => {
    probeAudioDuration.mockResolvedValue(20);
    const f = file();
    const result = await setReferenceFile(f);
    expect(result).toEqual({ ok: true, durationSeconds: 20, tooLong: true });
    expect(referenceStore.state.file).toBe(f);
  });

  it('accepts exactly REF_HARD_MAX_SECONDS and rejects beyond it without touching state', async () => {
    probeAudioDuration.mockResolvedValue(75);
    const kept = file('kept.wav');
    await setReferenceFile(kept);
    expect(referenceStore.state.file).toBe(kept);
    const urlBefore = referenceStore.state.objectUrl;

    probeAudioDuration.mockResolvedValue(75.5);
    const result = await setReferenceFile(file('too-long.wav'));
    expect(result).toEqual({ ok: false, durationSeconds: 75.5, tooLong: true });
    expect(referenceStore.state.file).toBe(kept);
    expect(referenceStore.state.objectUrl).toBe(urlBefore);
  });

  it('accepts a clip whose duration cannot be probed', async () => {
    probeAudioDuration.mockResolvedValue(null);
    const result = await setReferenceFile(file());
    expect(result).toEqual({ ok: true, durationSeconds: null, tooLong: false });
    expect(referenceStore.state.file).not.toBeNull();
  });

  it('clears the clip and revokes the previous object URL', async () => {
    probeAudioDuration.mockResolvedValue(3);
    await setReferenceFile(file());
    const url = referenceStore.state.objectUrl;
    const result = await setReferenceFile(null);
    expect(result).toEqual({ ok: true, durationSeconds: null, tooLong: false });
    expect(referenceStore.state).toEqual({ file: null, durationSeconds: null, objectUrl: null });
    expect(revokeObjectURL).toHaveBeenCalledWith(url);
  });

  it('revokes the old URL when replacing a clip', async () => {
    probeAudioDuration.mockResolvedValue(3);
    await setReferenceFile(file('a.wav'));
    const first = referenceStore.state.objectUrl;
    await setReferenceFile(file('b.wav'));
    expect(revokeObjectURL).toHaveBeenCalledWith(first);
    expect(referenceStore.state.objectUrl).not.toBe(first);
  });

  it('lets the newest pick win when probes resolve out of order', async () => {
    let resolveSlow: (value: number) => void = () => {};
    probeAudioDuration.mockImplementationOnce(
      () => new Promise<number | null>((resolve) => (resolveSlow = resolve)),
    );
    const slow = file('slow.wav');
    const slowResult = setReferenceFile(slow);

    probeAudioDuration.mockResolvedValueOnce(4);
    const fast = file('fast.wav');
    await setReferenceFile(fast);
    expect(referenceStore.state.file).toBe(fast);

    resolveSlow(5);
    await slowResult;
    expect(referenceStore.state.file).toBe(fast);
  });
});

describe('selectCloneProfile', () => {
  it.each(['Auto', 'French', 'Japanese'])(
    'preserves output language %s while replacing the reference identity',
    async (language) => {
      probeAudioDuration.mockResolvedValue(5);
      await setReferenceFile(file());
      setCloneSetting('language', language);
      setCloneSetting('text', 'The current script');
      selectCloneProfile({
        id: 'german-voice',
        ref_text: 'Guten Tag',
        instruct: '',
        language: 'German',
      });
      expect(referenceStore.state.file).toBeNull();
      expect(cloneSettingsStore.state.selectedProfileId).toBe('german-voice');
      expect(cloneSettingsStore.state.refText).toBe('Guten Tag');
      expect(cloneSettingsStore.state.language).toBe(language);
      expect(cloneSettingsStore.state.text).toBe('The current script');
    },
  );
});
