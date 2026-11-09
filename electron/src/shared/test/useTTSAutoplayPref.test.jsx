import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import useTTS from '../hooks/useTTS';
import { useAppStore } from '../store';
import { playBlobAudio } from '../utils/media';
import {
  StreamingPreviewError,
  streamGenerateSpeech,
  supportsStreamingPreview,
} from '../utils/streamingTts';
import { generateSpeech } from '../api/generate';
import toast from 'react-hot-toast';

// #1032: Settings → Appearance "Auto-play preview" ("play the output as soon
// as a render finishes", #666/#667) only gated the WaveformPlayer preview
// sites — the main generate path (useTTS → playBlobAudio) kept auto-playing
// unconditionally, with no visible way to stop it outside the Voice
// workspace. The pref must gate the generate auto-play too; default ON keeps
// the long-standing behavior.

vi.mock('../utils/media', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    playBlobAudio: vi.fn().mockResolvedValue(undefined),
    playPing: vi.fn(),
  };
});

vi.mock('../api/generate', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    generateSpeech: vi.fn().mockImplementation(async () => {
      let served = false;
      return {
        body: {
          getReader: () => ({
            read: async () => {
              if (served) return { done: true, value: undefined };
              served = true;
              return { done: false, value: new Uint8Array([0, 1, 2]) };
            },
          }),
        },
        headers: { get: () => null },
      };
    }),
  };
});

vi.mock('../utils/streamingTts', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    // Defaults to the real answer (jsdom has no Web Audio → classic path), so
    // the auto-play tests above keep exercising what they always did; the
    // delivery-path tests below turn it on explicitly.
    supportsStreamingPreview: vi.fn(actual.supportsStreamingPreview),
    streamGenerateSpeech: vi.fn().mockResolvedValue({ id: 'x', audio_path: 'x.wav' }),
  };
});

vi.mock('react-hot-toast', () => {
  const fn = vi.fn();
  fn.error = vi.fn();
  fn.success = vi.fn();
  fn.dismiss = vi.fn();
  fn.loading = vi.fn();
  fn.custom = vi.fn();
  return { default: fn, toast: fn, Toaster: () => null };
});

const hookProps = () => ({
  selectedProfile: null,
  setSelectedProfile: vi.fn(),
  loadHistory: vi.fn().mockResolvedValue(undefined),
  profiles: [],
});

async function runGenerate() {
  const { result } = renderHook(() => useTTS(hookProps()));
  await act(async () => {
    await result.current.handleGenerate();
  });
}

beforeEach(() => {
  vi.mocked(playBlobAudio).mockClear();
  // Design path needs no reference audio; non-empty text passes validation.
  useAppStore.setState({ text: 'Hello there', defineMethod: 'design', ttsInflight: 0 });
});

describe('useTTS auto-play pref (#1032)', () => {
  it('auto-plays the finished render when autoPlayPreview is ON (default)', async () => {
    useAppStore.setState({ autoPlayPreview: true });
    await runGenerate();
    expect(playBlobAudio).toHaveBeenCalledTimes(1);
    expect(playBlobAudio.mock.calls[0][0]).toBeInstanceOf(Blob);
    // Global mini-player metadata: the generate path labels its playback so
    // the bar reads "Generated audio" instead of a bare untitled track.
    expect(playBlobAudio.mock.calls[0][1]).toMatchObject({ label: 'Synthesized audio' });
  });

  it('does NOT auto-play when autoPlayPreview is OFF', async () => {
    useAppStore.setState({ autoPlayPreview: false });
    await runGenerate();
    expect(playBlobAudio).not.toHaveBeenCalled();
  });
});

describe('useTTS delivery path', () => {
  beforeEach(() => {
    useAppStore.setState({ autoPlayPreview: true });
    vi.mocked(supportsStreamingPreview).mockReturnValue(true);
    vi.mocked(streamGenerateSpeech).mockClear();
    vi.mocked(generateSpeech).mockClear();
    vi.mocked(toast).mockClear();
  });

  it('streams progressively when the work runs on this machine', async () => {
    await runGenerate();
    expect(streamGenerateSpeech).toHaveBeenCalledTimes(1);
    expect(generateSpeech).not.toHaveBeenCalled();
  });

  it('exposes streamed progress apart from the elapsed timer and clears it afterwards', async () => {
    let reportProgress;
    let finish;
    vi.mocked(streamGenerateSpeech).mockImplementationOnce((_fd, opts) => {
      reportProgress = opts.onProgress;
      return new Promise((resolve) => {
        finish = () => resolve({ id: 'x', audio_path: 'x.wav' });
      });
    });
    const { result } = renderHook(() => useTTS(hookProps()));
    expect(result.current.generationProgress).toBeNull();

    let run;
    act(() => {
      run = result.current.handleGenerate();
    });
    await waitFor(() => expect(reportProgress).toBeTypeOf('function'));
    act(() => reportProgress(37));

    expect(result.current.isGenerating).toBe(true);
    expect(result.current.generationProgress).toBe(37);
    expect(String(result.current.generationTime)).not.toContain('%');

    await act(async () => {
      finish();
      await run;
    });
    expect(result.current.isGenerating).toBe(false);
    expect(result.current.generationProgress).toBeNull();
  });

  it('drops streamed progress when it falls back to the classic request', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(streamGenerateSpeech).mockImplementationOnce(async (_fd, opts) => {
      opts.onProgress(80);
      throw new StreamingPreviewError('stream dropped');
    });
    let releaseClassic;
    vi.mocked(generateSpeech).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseClassic = () =>
            resolve({
              body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }) }) },
              headers: { get: () => null },
            });
        }),
    );
    const { result } = renderHook(() => useTTS(hookProps()));
    let run;
    act(() => {
      run = result.current.handleGenerate();
    });
    await waitFor(() => expect(generateSpeech).toHaveBeenCalledTimes(1));
    expect(result.current.isGenerating).toBe(true);
    expect(result.current.generationProgress).toBeNull();

    await act(async () => {
      releaseClassic();
      await run;
    });
  });
});
