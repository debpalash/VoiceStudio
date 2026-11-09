import { describe, expect, it } from 'vitest';
import { largestFittingTier, resolvePerformanceModelPack } from './performance-model-packs';
import type { CatalogueModel } from './model-catalogue-query';

const model = (
  repo_id: string,
  size_gb: number,
  installed = false,
  supported = true,
): CatalogueModel => ({
  repo_id,
  label: repo_id,
  role: 'ASR',
  size_gb,
  installed,
  supported,
});

describe('performance model packs', () => {
  it('uses a small ASR and dictation set for Fast', () => {
    const pack = resolvePerformanceModelPack(
      [
        model('k2-fsa/OmniVoice', 2.4, true),
        model('Systran/faster-whisper-base', 0.15),
        model('csukuangfj/sherpa-onnx-whisper-tiny', 0.104),
        model('facebook/nllb-200-distilled-600M', 2.4),
      ],
      'fast',
    );

    expect(pack.models.map((item) => item.repo_id)).toEqual([
      'k2-fsa/OmniVoice',
      'Systran/faster-whisper-base',
      'csukuangfj/sherpa-onnx-whisper-tiny',
    ]);
    expect(pack.missing).toHaveLength(2);
    expect(pack.downloadGb).toBeCloseTo(0.254);
  });

  it('adds accurate ASR, multilingual dictation fallback, and NLLB for Quality', () => {
    const repos = [
      model('k2-fsa/OmniVoice', 2.4, true),
      model('Systran/faster-whisper-large-v3', 2.9),
      model('csukuangfj/sherpa-onnx-whisper-tiny', 0.104),
      model('csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8', 0.67),
      model('facebook/nllb-200-distilled-600M', 2.4),
    ];
    const pack = resolvePerformanceModelPack(repos, 'quality');

    expect(pack.models).toHaveLength(5);
    expect(pack.downloadGb).toBeCloseTo(6.074);
  });

  it('omits models unsupported by this device', () => {
    const pack = resolvePerformanceModelPack(
      [
        model('k2-fsa/OmniVoice', 2.4, true),
        model('Systran/faster-whisper-base', 0.15, false, false),
        model('csukuangfj/sherpa-onnx-whisper-tiny', 0.104),
      ],
      'fast',
    );

    expect(pack.models.map((item) => item.repo_id)).not.toContain('Systran/faster-whisper-base');
  });

  it('preselects the largest tier whose download fits the model disk', () => {
    const catalogue = [
      model('k2-fsa/OmniVoice', 2.4),
      model('Systran/faster-whisper-base', 0.15),
      model('csukuangfj/sherpa-onnx-whisper-tiny', 0.1),
      model('deepdml/faster-whisper-large-v3-turbo-ct2', 1.6),
      model('csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8', 0.7),
      model('Systran/faster-whisper-large-v3', 3.1),
      model('facebook/nllb-200-distilled-600M', 2.4),
    ];
    // Max needs 8.7 GB; Balanced 4.8 GB; Fast 2.65 GB (plus 1 GB headroom).
    expect(largestFittingTier(catalogue, 'max', 5, 1)).toBe('fast');
    expect(largestFittingTier(catalogue, 'max', 6, 1)).toBe('balanced');
    expect(largestFittingTier(catalogue, 'max', 100, 10)).toBe('max');
    expect(largestFittingTier(catalogue, 'balanced', 100, 10)).toBe('balanced');
    expect(largestFittingTier(catalogue, 'max', 1, 10)).toBe('fast');
    expect(largestFittingTier(catalogue, 'quality', undefined, 10)).toBe('quality');
  });

  it.each([
    [true, 'mlx-community/whisper-large-v3-mlx'],
    [false, 'Systran/faster-whisper-large-v3'],
  ])('picks the Metal Whisper only where the device supports it (%s)', (mlxSupported, expected) => {
    const pack = resolvePerformanceModelPack(
      [
        model('Systran/faster-whisper-large-v3', 2.9),
        model('mlx-community/whisper-large-v3-mlx', 3.0, false, mlxSupported),
      ],
      'max',
    );

    expect(pack.models.map((item) => item.repo_id)).toEqual([expected]);
  });
});
