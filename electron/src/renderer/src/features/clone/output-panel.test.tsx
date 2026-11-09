import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  output: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/lib/store/output', () => ({
  useLatestOutput: mocks.output,
  clearLatestOutput: vi.fn(),
}));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: ({ src }: { src: string }) => <div data-testid="player" data-src={src} />,
}));
vi.mock('@/components/save-audio-button', () => ({ SaveAudioButton: () => null }));
vi.mock('./audio-quality', () => ({ AudioQuality: () => null }));
vi.mock('./audio-file-details', () => ({ AudioFileDetails: () => null }));

import type { GenerateResult } from '@/lib/api/types';
import { OutputPanel } from './output-panel';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function result(audioPath: string | null): GenerateResult {
  return {
    blob: new Blob(['audio'], { type: 'audio/wav' }),
    id: 'take-1',
    audioPath,
    durationSeconds: 1,
    genTimeSeconds: 2,
    seed: 3,
    routing: null,
    dropped: null,
  };
}

it('plays a completed take from its persistent audio path instead of a revocable blob URL', () => {
  mocks.output.mockReturnValue({
    result: result('take-1.wav'),
    objectUrl: 'blob:http://localhost/revoked',
    text: 'Hello',
  });

  render(<OutputPanel />);

  expect(screen.getByTestId('player')).toHaveAttribute(
    'data-src',
    '/api/audio/take-1.wav',
  );
});
