import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import useTTS from '../hooks/useTTS';
import { useAppStore } from '../store';
import { playBlobAudio } from '../utils/media';
import {
  StreamingPreviewError,
  resolveRemoteTtsTarget,
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

// Streaming renders in THIS process, so with a worker selected the classic
// path is the only one that reaches it. The delivery path is chosen here, so
// this is where "did it actually go remote?" is decided.
vi.mock('../utils/streamingTts', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    // Defaults to the real answer (jsdom has no Web Audio → classic path), so
    // the auto-play tests above keep exercising what they always did; the
    // delivery-path tests below turn it on explicitly.
    supportsStreamingPreview: vi.fn(actual.supportsStreamingPreview),
    streamGenerateSpeech: vi.fn().mockResolvedValue({ id: 'x', audio_path: 'x.wav' }),
    resolveRemoteTtsTarget: vi.fn().mockResolvedValue(null),
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
    expect(playBlobAudio.mock.calls[0][1]).toMatchObject({ label: 'Generated audio' });
  });

  it('does NOT auto-play when autoPlayPreview is OFF', async () => {
    useAppStore.setState({ autoPlayPreview: false });
    await runGenerate();
    expect(playBlobAudio).not.toHaveBeenCalled();
  });
});

describe('useTTS delivery path vs the chosen GPU', () => {
  beforeEach(() => {
    useAppStore.setState({ autoPlayPreview: true });
    vi.mocked(supportsStreamingPreview).mockReturnValue(true);
    vi.mocked(streamGenerateSpeech).mockClear();
    vi.mocked(generateSpeech).mockClear();
    vi.mocked(toast).mockClear();
    vi.mocked(resolveRemoteTtsTarget).mockResolvedValue(null);
  });

  it('streams progressively when the work runs on this machine', async () => {
    await runGenerate();
    expect(streamGenerateSpeech).toHaveBeenCalledTimes(1);
    expect(generateSpeech).not.toHaveBeenCalled();
  });

  it('keeps real stream progress separate from the elapsed timer', async () => {
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    vi.mocked(streamGenerateSpeech).mockImplementationOnce(async (_formData, { onProgress }) => {
      onProgress(42);
      await gate;
      return { id: 'x', audio_path: 'x.wav' };
    });

    const { result } = renderHook(() => useTTS(hookProps()));
    let generation;
    act(() => {
      generation = result.current.handleGenerate();
    });

    await waitFor(() => expect(result.current.generationProgress).toBe(42));
    expect(String(result.current.generationTime)).not.toContain('%');

    await act(async () => {
      release();
      await generation;
    });
    expect(result.current.generationProgress).toBeNull();
  });

  it('clears stale stream progress before a classic fallback', async () => {
    let releaseClassic;
    const classicGate = new Promise((resolve) => {
      releaseClassic = resolve;
    });
    vi.mocked(streamGenerateSpeech).mockImplementationOnce(async (_formData, { onProgress }) => {
      onProgress(42);
      throw new StreamingPreviewError('stream transport dropped');
    });
    vi.mocked(generateSpeech).mockImplementationOnce(async () => ({
      body: {
        getReader: () => ({
          read: async () => {
            await classicGate;
            return { done: true, value: undefined };
          },
        }),
      },
      headers: { get: () => null },
    }));

    const { result } = renderHook(() => useTTS(hookProps()));
    let generation;
    act(() => {
      generation = result.current.handleGenerate();
    });

    await waitFor(() => expect(generateSpeech).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.generationProgress).toBeNull());

    await act(async () => {
      releaseClassic();
      await generation;
    });
  });

  it('takes the classic path when the resolved target is a worker', async () => {
    // Streaming would have rendered here — a local job wearing the badge of
    // the 4090 the user picked. The classic path is the one that goes remote.
    vi.mocked(resolveRemoteTtsTarget).mockResolvedValue({
      workerId: 'w1',
      label: 'desktop-4090',
    });
    await runGenerate();

    expect(streamGenerateSpeech).not.toHaveBeenCalled();
    expect(generateSpeech).toHaveBeenCalledTimes(1);
  });

  it('says why progressive playback stopped, once per worker', async () => {
    // Silently dropping the feature reads as "the app got slower"; the user
    // has to be able to connect it to the choice they made.
    vi.mocked(resolveRemoteTtsTarget).mockResolvedValue({ workerId: 'w9', label: 'gpu2' });
    await runGenerate();
    await runGenerate();

    const said = vi.mocked(toast).mock.calls.map(([msg]) => msg);
    const notices = said.filter((m) => typeof m === 'string' && m.includes('gpu2'));
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatch(/gpu2/);
    expect(notices[0]).not.toMatch(/streamingOffRemote/); // a real string, not the key
  });
});
