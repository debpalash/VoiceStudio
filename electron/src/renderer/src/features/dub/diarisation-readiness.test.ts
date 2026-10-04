import { describe, expect, it } from 'vitest';
import type { CatalogueModel } from '@/features/settings/model-catalogue-query';
import { isDiarisationReady, type DiarisationStatus } from './diarisation-readiness';

const pyannote: CatalogueModel = {
  repo_id: 'pyannote/speaker-diarization-3.1',
  label: 'pyannote 3.1',
  role: 'Diarization',
  size_gb: 0.2,
  installed: true,
  supported: true,
};
const sortformer: CatalogueModel = {
  repo_id: 'audio-cpp/audio.cpp-gguf',
  label: 'audio.cpp native bundle',
  role: 'TTS',
  families: ['tts', 'diarisation'],
  size_gb: 4.98,
  installed: true,
  supported: true,
};
const pyannoteStatus: DiarisationStatus = {
  active: 'pyannote',
  model: pyannote.repo_id,
  installed: true,
};
const sortformerStatus: DiarisationStatus = {
  active: 'audiocpp-sortformer',
  model: sortformer.repo_id,
  installed: true,
};

describe('selected diarisation readiness', () => {
  it('uses selected Sortformer even when the first diarisation model is unavailable', () => {
    expect(
      isDiarisationReady([{ ...pyannote, installed: false }, sortformer], sortformerStatus),
    ).toBe(true);
  });

  it('does not replace an unavailable selected native runtime with installed pyannote', () => {
    expect(
      isDiarisationReady([pyannote, sortformer], { ...sortformerStatus, installed: false }),
    ).toBe(false);
  });

  it('does not replace unavailable selected pyannote with installed Sortformer', () => {
    expect(
      isDiarisationReady([sortformer, pyannote], { ...pyannoteStatus, installed: false }),
    ).toBe(false);
  });

  it('uses the selected model identity for a bundle with multiple families', () => {
    expect(isDiarisationReady([sortformer], sortformerStatus)).toBe(true);
  });

  it('honours backend readiness for a configured native model outside the HF catalogue cache', () => {
    expect(
      isDiarisationReady(
        [
          { ...pyannote, installed: false },
          { ...sortformer, installed: false },
        ],
        sortformerStatus,
      ),
    ).toBe(true);
  });

  it('accepts installed Sortformer without the unrelated Breeze TTS weights', () => {
    expect(
      isDiarisationReady([{ ...sortformer, incomplete: true }], sortformerStatus),
    ).toBe(true);
  });

  it('accepts an external Sortformer model even when the repository cache is partial', () => {
    expect(
      isDiarisationReady(
        [{ ...sortformer, installed: false, incomplete: true }],
        sortformerStatus,
      ),
    ).toBe(true);
  });

  it('rejects a partial native bundle when the selected runtime is unavailable', () => {
    expect(
      isDiarisationReady(
        [{ ...sortformer, incomplete: true }],
        { ...sortformerStatus, installed: false },
      ),
    ).toBe(false);
  });

  it.each(['Diarisation', 'Diarization'])(
    'preserves ready default pyannote with role %s',
    (role) => {
      expect(isDiarisationReady([{ ...pyannote, role }, sortformer], pyannoteStatus)).toBe(true);
    },
  );

  it('preserves unavailable default pyannote', () => {
    expect(
      isDiarisationReady([{ ...pyannote, installed: false }], {
        ...pyannoteStatus,
        installed: false,
      }),
    ).toBe(false);
  });

  it('does not let an unrelated unsupported incomplete model veto the selected backend', () => {
    expect(
      isDiarisationReady(
        [{ ...pyannote, supported: false, incomplete: true }, sortformer],
        sortformerStatus,
      ),
    ).toBe(true);
  });

  it('waits for the effective backend status instead of assuming the first model is selected', () => {
    expect(isDiarisationReady([pyannote, sortformer], undefined)).toBe(false);
  });

  it('does not infer readiness when the selected model is absent from the catalogue', () => {
    expect(isDiarisationReady([pyannote], sortformerStatus)).toBe(false);
  });

  it('waits for the catalogue', () => {
    expect(isDiarisationReady(undefined, pyannoteStatus)).toBe(false);
    expect(isDiarisationReady([], pyannoteStatus)).toBe(false);
  });
});

describe.each([
  { model: pyannote, status: pyannoteStatus },
  { model: sortformer, status: sortformerStatus },
])('$status.active catalogue guards', ({ model, status }) => {
  it('rejects an unsupported selected model even when the runtime reports installed', () => {
    expect(isDiarisationReady([{ ...model, supported: false }], status)).toBe(false);
  });

  it('uses engine-specific readiness rather than whole-repository completeness', () => {
    expect(isDiarisationReady([{ ...model, incomplete: true }], status)).toBe(true);
  });

  it('accepts a supported complete selected model', () => {
    expect(isDiarisationReady([{ ...model, incomplete: false }], status)).toBe(true);
  });
});
