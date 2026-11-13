import { toast } from 'sonner';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GenerationProvider, useGenerateClone } from './use-generate';
import {
  beginTranscriptSave,
  beginVoiceChange,
  commitTranscriptSave,
} from '@/lib/store/transcript-saves';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { generateClone } from '@/lib/api/generate';
import { setLatestOutput } from '@/lib/store/output';
import { queryKeys } from '@/lib/query';
import type { Profile } from '@/lib/api/types';
import { tr } from '@/lib/i18n-text';
import { acquireSynthesis } from '@/lib/synthesis-lock';
const modelStatus = vi.hoisted(() => ({
  value: { status: 'loading', loading: true } as {
    status: string;
    loading: boolean;
    sub_stage?: string;
    progress?: number;
  },
}));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn().mockImplementation(() => Promise.resolve(modelStatus.value)),
}));
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));
vi.mock('./use-clone-readiness', () => ({ useCloneInputsReadiness: () => null }));
vi.mock('./use-tts-readiness', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./use-tts-readiness')>()),
  useTtsReadiness: () => null,
}));
const engine = vi.hoisted(() => ({
  vocabulary: undefined as 'tags' | 'freeform' | undefined,
  design: undefined as boolean | null | undefined,
  cloning: undefined as boolean | null | undefined,
  presets: undefined as string[] | undefined,
  emotion: undefined as boolean | undefined,
  caps: undefined as { emotion: 'graded' | 'direction' | 'none'; tags: string[] } | undefined,
}));
vi.mock('./use-engines', () => ({
  useEngines: () => ({
    activeTts: {
      id: 'omnivoice',
      effective_device: 'cuda',
      instruct_vocabulary: engine.vocabulary,
      supports_voice_design: engine.design,
      supports_cloning: engine.cloning,
      preset_voices: engine.presets,
      supports_emotion: engine.emotion,
      expression_caps: engine.caps,
    },
  }),
}));
vi.mock('@/lib/api/generate', () => ({
  generateClone: vi.fn(),
  sanitizeInstruct: () => ({ instruct: '', unsupported: [], duplicates: [], conflicts: [] }),
}));
vi.mock('@/lib/store/output', () => ({ setLatestOutput: vi.fn() }));
function Consumer({ name }: { name: string }) {
  const state = useGenerateClone();
  return (
    <>
      <button onClick={() => void state.generate()}>
        {name}:{state.isGenerating ? 'running' : 'idle'}
      </button>
      <span data-testid={name + '-stage'}>{state.stage}</span>
      <span data-testid={name + '-model-stage'}>{state.modelStage}</span>
      <span data-testid={name + '-model-progress'}>{state.modelProgress}</span>
    </>
  );
}
describe('shared generation lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    modelStatus.value = { status: 'loading', loading: true };
  });
  it('keeps failures inline without a duplicate toast', async () => {
    patchCloneSettings({ text: 'Hello', selectedProfileId: 'voice-1', autoPlay: false });
    vi.mocked(generateClone).mockRejectedValueOnce(new Error('write failed'));
    function ErrorConsumer() {
      const state = useGenerateClone();
      return (
        <>
          <button onClick={() => void state.generate()}>retry test</button>
          <span>{state.error}</span>
        </>
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <ErrorConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('retry test'));
    await screen.findByText('write failed');
    expect(toast.error).not.toHaveBeenCalled();
    vi.mocked(generateClone).mockClear();
  });
  it('shares progress across consumers and survives a view unmount', async () => {
    patchCloneSettings({ text: 'Hello', selectedProfileId: 'voice-1', autoPlay: false });
    let complete!: (value: unknown) => void;
    vi.mocked(generateClone).mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve as (value: unknown) => void;
        }),
    );
    const client = new QueryClient();
    const tree = (editor: boolean) => (
      <QueryClientProvider client={client}>
        <GenerationProvider>
          {editor && <Consumer name="editor" />}
          <Consumer name="composer" />
        </GenerationProvider>
      </QueryClientProvider>
    );
    const view = render(tree(true));
    fireEvent.click(screen.getByText('editor:idle'));
    await screen.findByText('composer:running');
    await waitFor(() => expect(screen.getByTestId('composer-stage')).toHaveTextContent('loading'));
    act(() => {
      vi.mocked(generateClone).mock.calls[0]?.[1]?.onProgress?.(25);
    });
    await waitFor(() =>
      expect(screen.getByTestId('composer-stage')).toHaveTextContent('receiving'),
    );
    view.rerender(tree(false));
    expect(vi.mocked(generateClone).mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
    const result = {
      blob: new Blob(),
      id: 'take-1',
      audioPath: 'take.wav',
      durationSeconds: 1,
      genTimeSeconds: 1,
      seed: 1,
      routing: null,
      dropped: null,
    };
    await act(async () => {
      complete(result);
    });
    await waitFor(() => expect(screen.getByText('composer:idle')).toBeInTheDocument());
    expect(generateClone).toHaveBeenCalledTimes(1);
    expect(setLatestOutput).toHaveBeenCalledWith(result, 'Hello');
    expect(toast.success).toHaveBeenCalledWith(expect.any(String), { position: 'top-right' });
  });

  it('surfaces the backend model-loading sub-stage and progress', async () => {
    patchCloneSettings({ text: 'Hello', selectedProfileId: 'voice-1', autoPlay: false });
    modelStatus.value = {
      status: 'loading',
      loading: true,
      sub_stage: 'compiling',
      progress: 62,
    };
    let complete!: (value: unknown) => void;
    vi.mocked(generateClone).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve as (value: unknown) => void;
        }),
    );

    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <Consumer name="runtime" />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('runtime:idle'));

    try {
      await waitFor(() =>
        expect(screen.getByTestId('runtime-model-stage')).toHaveTextContent('compiling'),
      );
      expect(screen.getByTestId('runtime-model-progress')).toHaveTextContent('62');
    } finally {
      await act(async () => {
        complete({
          blob: new Blob(),
          id: 'take-2',
          audioPath: 'take.wav',
          durationSeconds: 1,
          genTimeSeconds: 1,
          seed: 1,
          routing: null,
          dropped: null,
        });
      });
    }
  });
});

it('design generation ignores a selected clone profile and sends its own script and seed', async () => {
  // The composer's language, not a saved voice's or an opened take's, drives the take.
  patchCloneSettings({
    text: 'Clone script',
    selectedProfileId: 'clone-profile',
    autoPlay: false,
    language: 'English',
  });
  vi.mocked(generateClone).mockClear().mockRejectedValueOnce(new Error('stop after request'));
  function DesignConsumer() {
    const state = useGenerateClone();
    return (
      <>
        <button
          onClick={() =>
            void state.generateDesign({ text: 'Design script', instruct: 'male', seed: 42 })
          }
        >
          design test
        </button>
        <span>{state.error}</span>
      </>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GenerationProvider>
        <DesignConsumer />
      </GenerationProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('design test'));
  await screen.findByText('stop after request');
  expect(generateClone).toHaveBeenCalledWith(
    expect.objectContaining({
      text: 'Design script',
      seed: 42,
      language: 'English',
      profileId: null,
      refAudio: null,
      refText: undefined,
    }),
    expect.anything(),
  );
});

it('attributes generation to an explicitly selected designed voice', async () => {
  vi.mocked(generateClone).mockClear().mockRejectedValueOnce(new Error('stop after request'));
  function DesignConsumer() {
    const state = useGenerateClone();
    return (
      <>
        <button
          onClick={() =>
            void state.generateDesign({
              text: 'Profile script',
              instruct: 'female',
              seed: 7,
              profileId: 'design-profile',
            })
          }
        >
          profile design test
        </button>
        <span>{state.error}</span>
      </>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GenerationProvider>
        <DesignConsumer />
      </GenerationProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('profile design test'));
  await screen.findByText('stop after request');
  expect(generateClone).toHaveBeenCalledWith(
    expect.objectContaining({ profileId: 'design-profile', text: 'Profile script', seed: 7 }),
    expect.anything(),
  );
});

it.each([
  ['freeform', 'a weary sea captain, slow', 'a weary sea captain, slow'],
  [undefined, 'a weary sea captain, slow', ''],
] as const)(
  'generateDesign with vocabulary %s forwards the right instruct',
  async (vocabulary, written, sent) => {
    engine.vocabulary = vocabulary;
    vi.mocked(generateClone).mockReset().mockRejectedValue(new Error('stop'));
    const recipe = { description: written, picks: {} };
    function DesignConsumer() {
      const state = useGenerateClone();
      return (
        <button
          onClick={() =>
            void state.generateDesign({ text: 'Hi', instruct: `  ${written} `, seed: 1, recipe })
          }
        >
          vocabulary {state.instructVocabulary}
        </button>
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <DesignConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    const expected = vocabulary ?? 'tags';
    fireEvent.click(screen.getByText(`vocabulary ${expected}`));
    await waitFor(() => expect(generateClone).toHaveBeenCalled());
    expect(generateClone).toHaveBeenCalledWith(
      expect.objectContaining({
        instruct: sent,
        instructVocabulary: expected,
        designRecipe: recipe,
      }),
      expect.anything(),
    );
    engine.vocabulary = undefined;
  },
);

it.each([
  [false, 'design', false, false],
  [null, 'none', true, true],
  [true, 'none', true, true],
] as const)(
  'uses the declared Voice Design capability %s',
  async (capability, expectedBlocker, expectedAllowed, sends) => {
    engine.design = capability;
    vi.mocked(generateClone).mockReset().mockRejectedValue(new Error('request ended'));
    function CapabilityConsumer() {
      const state = useGenerateClone();
      return (
        <button
          onClick={() =>
            void state.generateDesign({ text: 'Capability check', instruct: 'calm', seed: 19 })
          }
        >
          {state.designBlocker ?? 'none'}:{String(state.canGenerateDesign)}
        </button>
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <CapabilityConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    const action = screen.getByRole('button', {
      name: `${expectedBlocker}:${String(expectedAllowed)}`,
    });
    fireEvent.click(action);
    if (sends) await waitFor(() => expect(generateClone).toHaveBeenCalled());
    else {
      await act(async () => {});
      expect(generateClone).not.toHaveBeenCalled();
    }
    engine.design = undefined;
  },
);

it.each([
  [
    'a saved design voice with a sample',
    { ref_audio_path: 'design.wav' },
    false,
    true,
    'none',
    true,
  ],
  [
    'a locked design voice',
    { is_locked: 1, locked_audio_path: 'locked.wav' },
    false,
    true,
    'none',
    true,
  ],
  ['a saved design voice without a sample', {}, false, true, 'design', false],
  // KittenTTS / Supertonic-3 ignore reference audio: the saved voice would
  // silently become a preset one, so the re-render stays blocked.
  [
    'a saved sample on a preset-only engine',
    { ref_audio_path: 'design.wav' },
    false,
    false,
    'cloning',
    false,
  ],
  [
    'a saved sample on an undeclared preset-only engine',
    { ref_audio_path: 'design.wav' },
    null,
    false,
    'cloning',
    false,
  ],
  [
    'a locked take on a preset-only engine',
    { is_locked: 1, locked_audio_path: 'locked.wav' },
    false,
    false,
    'cloning',
    false,
  ],
  ['a sampleless design on a preset-only engine', {}, false, false, 'design', false],
] as const)(
  're-renders %s only when the engine can clone its sample',
  async (_label, sample, design, cloning, expectedBlocker, sends) => {
    engine.design = design;
    engine.cloning = cloning;
    vi.mocked(generateClone).mockReset().mockRejectedValue(new Error('stop'));
    const profile = {
      id: 'voice-design',
      kind: 'design',
      ref_audio_path: null,
      ...sample,
    } as unknown as Profile;
    const client = new QueryClient();
    client.setQueryData(queryKeys.profiles, [profile]);
    function DesignConsumer() {
      const state = useGenerateClone();
      return (
        <button
          onClick={() =>
            void state.generateDesign({ text: 'Hi', instruct: 'male', profileId: profile.id })
          }
        >
          linked {state.designBlockerFor(profile) ?? 'none'}
        </button>
      );
    }
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <DesignConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText(`linked ${expectedBlocker}`));
    if (sends) {
      await waitFor(() => expect(generateClone).toHaveBeenCalled());
      expect(generateClone).toHaveBeenCalledWith(
        expect.objectContaining({ profileId: profile.id, seed: undefined }),
        expect.anything(),
      );
    } else {
      await act(async () => {});
      expect(generateClone).not.toHaveBeenCalled();
    }
    engine.design = undefined;
    engine.cloning = undefined;
  },
);

it.each([
  ['the stored transcript unchanged', 'Stored words.', undefined],
  ['a manual correction', 'Corrected words.', 'Corrected words.'],
  ['a blank field', '   ', undefined],
])('sends a saved voice %s only as a manual override', async (_label, refText, expected) => {
  patchCloneSettings({
    text: 'Clone script',
    selectedProfileId: 'saved',
    refText,
    autoPlay: false,
  });
  vi.mocked(generateClone).mockClear().mockRejectedValueOnce(new Error('stop after request'));
  const client = new QueryClient();
  client.setQueryData(queryKeys.profiles, [{ id: 'saved', ref_text: 'Stored words.' } as Profile]);
  function CloneConsumer() {
    const state = useGenerateClone();
    return (
      <>
        <button onClick={() => void state.generate()}>clone ref test</button>
        <span>{state.error}</span>
      </>
    );
  }
  render(
    <QueryClientProvider client={client}>
      <GenerationProvider>
        <CloneConsumer />
      </GenerationProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText('clone ref test'));
  await screen.findByText('stop after request');
  expect(generateClone).toHaveBeenCalledWith(
    expect.objectContaining({ profileId: 'saved', refText: expected }),
    expect.anything(),
  );
});

it.each([
  [false, ''],
  [null, 'calm'],
] as const)(
  'a preset-only engine generates without a clip or profile (supports_voice_design=%s)',
  async (design, sent) => {
    Object.assign(engine, {
      design,
      cloning: false,
      presets: ['expr-voice-2-f'],
      vocabulary: 'freeform',
    });
    patchCloneSettings({
      text: 'Preset script',
      instruct: 'calm',
      selectedProfileId: null,
      autoPlay: false,
    });
    vi.mocked(generateClone).mockReset().mockRejectedValue(new Error('stop'));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <Consumer name="preset" />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('preset:idle'));
    // The instruct is a design request /generate refuses on an engine that
    // can't design; it never reaches a preset voice anyway (#3413).
    await waitFor(() =>
      expect(generateClone).toHaveBeenCalledWith(
        expect.objectContaining({
          text: 'Preset script',
          instruct: sent,
          profileId: null,
          refAudio: null,
        }),
        expect.anything(),
      ),
    );
    Object.assign(engine, {
      design: undefined,
      cloning: undefined,
      presets: undefined,
      vocabulary: undefined,
    });
  },
);

it.each([
  ['text', true, false, 'text'],
  ['vector', true, false, 'vector'],
  ['unsupported engine', false, false, 'text'],
  ['Voice Design', true, true, 'text'],
] as const)(
  'forwards IndexTTS2 emotion only on supported clone takes (%s)',
  async (_name, supportsEmotion, design, mode) => {
    engine.emotion = supportsEmotion;
    engine.design = true;
    patchCloneSettings({
      text: 'A cloned voice line.',
      selectedProfileId: 'speaker-emotion',
      emotionMode: mode,
      emotionText: '  gently reassuring  ',
      emotionAlpha: 0.4,
      emotionVector: [0.2, 0, 0.5, 0, 0, 0.1, 0, 0],
    });
    vi.mocked(generateClone).mockReset().mockRejectedValue(new Error('stop'));

    function EmotionConsumer() {
      const generation = useGenerateClone();
      return (
        <button
          onClick={() =>
            void (design
              ? generation.generateDesign({ text: 'A designed voice.', instruct: 'male' })
              : generation.generate())
          }
        >
          emotion render
        </button>
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <EmotionConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('emotion render'));
    await waitFor(() => expect(generateClone).toHaveBeenCalled());
    const input = vi.mocked(generateClone).mock.calls[0][0];
    if (!supportsEmotion || design) {
      expect(input.emoText).toBeUndefined();
      expect(input.emoVector).toBeUndefined();
      expect(input.emoAlpha).toBeUndefined();
    } else if (mode === 'text') {
      expect(input.emoText).toBe('gently reassuring');
      expect(input.emoAlpha).toBe(0.4);
      expect(input.emoVector).toBeUndefined();
    } else {
      expect(input.emoVector).toEqual([0.2, 0, 0.5, 0, 0, 0.1, 0, 0]);
      expect(input.emoText).toBeUndefined();
      expect(input.emoAlpha).toBeUndefined();
    }
    engine.emotion = undefined;
    engine.design = undefined;
  },
);

it.each([
  ['an engine without emotion controls', false, 'none'],
  ['IndexTTS2 with text emotion', true, 'text'],
] as const)(
  'saves the emotion a take actually used (%s)',
  async (_name, supportsEmotion, saved) => {
    engine.emotion = supportsEmotion;
    patchCloneSettings({
      text: 'A cloned voice line.',
      selectedProfileId: 'speaker-emotion',
      emotionMode: 'text',
      emotionText: 'gently reassuring',
      autoPlay: false,
    });
    vi.mocked(generateClone)
      .mockReset()
      .mockResolvedValue({ id: `take-${saved}` } as Awaited<ReturnType<typeof generateClone>>);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <Consumer name="saved-emotion" />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('saved-emotion:idle'));
    await waitFor(() => {
      const records = JSON.parse(localStorage.getItem('voicestudio.take-settings.v1') ?? '{}');
      expect(records[`take-${saved}`]?.emotionMode).toBe(saved);
    });
    engine.emotion = undefined;
  },
);

it.each([
  ['CosyVoice (direction)', 'direction', false, 'warm, intimate'],
  ['OmniVoice after switching back (#3422)', 'none', false, 'warm'],
  ['Voice Design', 'direction', true, 'male'],
] as const)(
  'sends the delivery direction only where instruct steers a clone (%s)',
  async (_name, emotion, design, instruct) => {
    engine.vocabulary = 'freeform';
    engine.design = true;
    engine.caps = { emotion, tags: [] };
    patchCloneSettings({
      text: 'A cloned voice line.',
      selectedProfileId: 'speaker-direction',
      instruct: 'warm',
      direction: '  intimate  ',
      emotionMode: 'none',
    });
    vi.mocked(generateClone).mockReset().mockRejectedValue(new Error('stop'));

    function DirectionConsumer() {
      const generation = useGenerateClone();
      return (
        <button
          onClick={() =>
            void (design
              ? generation.generateDesign({ text: 'A designed voice.', instruct: 'male' })
              : generation.generate())
          }
        >
          direction render
        </button>
      );
    }
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <DirectionConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('direction render'));
    await waitFor(() => expect(generateClone).toHaveBeenCalled());
    expect(vi.mocked(generateClone).mock.calls[0][0].instruct).toBe(instruct);
    engine.vocabulary = undefined;
    engine.design = undefined;
    engine.caps = undefined;
  },
);

it.each([
  ['clone', false],
  ['design', false],
  ['clone', true],
  ['design', true],
] as const)(
  'clears the %s synthesis banner after acceptance (close while pending: %s)',
  async (mode, closeWhilePending) => {
    const { ApiError, apiJson } = await import('@/lib/api/client');
    const { announceModelLicenceRequired } =
      await import('@/features/settings/model-license-contract');
    const { ModelLicenceGate } = await import('@/features/settings/model-licence-acceptance');
    const { default: en } = await import('@/i18n/locales/en.json');
    const { createInstance } = await import('i18next');
    const { I18nextProvider } = await import('react-i18next');
    const i18n = createInstance();
    await i18n.init({ lng: 'en', resources: { en: { translation: en } } });
    const models = [
      { repo_id: 'test/voice', fingerprint: 'voice-terms', category: 'noncommercial' },
      { repo_id: 'test/tokenizer', fingerprint: 'tokenizer-terms', category: 'conditions' },
    ];
    const accepted = new Set<string>();
    let releaseAcceptance!: () => void;
    const acceptanceReady = new Promise<void>((resolve) => {
      releaseAcceptance = resolve;
    });
    if (!closeWhilePending) releaseAcceptance();
    vi.mocked(apiJson).mockImplementation(async (path, init) => {
      if (path === '/models/licenses/accept') {
        await acceptanceReady;
        const body = JSON.parse(String(init?.body));
        accepted.add(body.repo_id);
        return { ...body, accepted: true };
      }
      if (path.startsWith('/models/licenses/details/')) {
        const model = models.find((item) => path.endsWith(item.repo_id))!;
        return {
          repo_id: model.repo_id,
          info: {
            license: 'Test licence',
            credit: 'Test holder',
            license_category: model.category,
          },
          acceptance: { ...model, required: true, accepted: accepted.has(model.repo_id) },
          history: [],
        };
      }
      return modelStatus.value;
    });
    patchCloneSettings({ text: 'Hello', selectedProfileId: 'voice-1', autoPlay: false });
    vi.mocked(generateClone)
      .mockReset()
      .mockImplementationOnce(async () => {
        const payload = { detail: { code: 'model_licence_required', models } };
        announceModelLicenceRequired(payload);
        throw new ApiError(403, en.modelLicense.requiredError, payload);
      });
    function Synthesis() {
      const state = useGenerateClone();
      return (
        <>
          <button
            onClick={() =>
              void (mode === 'clone'
                ? state.generate()
                : state.generateDesign({ text: 'Hello', instruct: 'male' }))
            }
          >
            Synthesize test
          </button>
          {state.error && <p role="alert">{state.error}</p>}
        </>
      );
    }
    const view = render(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <GenerationProvider>
            <Synthesis />
            <ModelLicenceGate />
          </GenerationProvider>
        </I18nextProvider>
      </QueryClientProvider>,
    );
    try {
      fireEvent.click(screen.getByText('Synthesize test'));
      await screen.findByText(en.modelLicense.requiredError);
      const boxes = await screen.findAllByRole('checkbox');
      await waitFor(() => expect(boxes[0]).not.toBeDisabled());
      for (const box of boxes) fireEvent.click(box);
      fireEvent.click(
        screen.getByRole('button', { name: en.modelLicense.acceptAll.replace('{{count}}', '2') }),
      );
      if (closeWhilePending) {
        fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape', code: 'Escape' });
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(screen.getByText(en.modelLicense.requiredError)).toBeInTheDocument();
        releaseAcceptance();
      }
      await waitFor(() => expect(accepted.size).toBe(2));
      await waitFor(() =>
        expect(screen.queryByText(en.modelLicense.requiredError)).not.toBeInTheDocument(),
      );
      if (!closeWhilePending)
        fireEvent.click(screen.getByRole('button', { name: en.common.close }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(generateClone).toHaveBeenCalledOnce();
    } finally {
      view.unmount();
      vi.mocked(apiJson).mockImplementation(() => Promise.resolve(modelStatus.value));
    }
  },
);

it.each([
  ['records a warm take', { status: 'ready', loading: false }, 1],
  ['skips a take that waited for model loading', { status: 'loading', loading: true }, 0],
] as const)(
  'measures render speed locally per engine and device (%s)',
  async (_name, status, count) => {
    modelStatus.value = { ...status };
    localStorage.removeItem('voicestudio.render-speed.v1');
    patchCloneSettings({
      text: 'A cloned voice line long enough to measure.',
      selectedProfileId: 'speaker-speed',
      steps: null,
      autoPlay: false,
    });
    vi.mocked(generateClone)
      .mockReset()
      .mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(
              () =>
                resolve({
                  id: `take-speed-${count}`,
                  durationSeconds: 3,
                  genTimeSeconds: 9,
                } as Awaited<ReturnType<typeof generateClone>>),
              50,
            ),
          ),
      );
    const client = new QueryClient();
    client.setQueryData(queryKeys.engines, { tts: { active: 'omnivoice', backends: [] } });
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <Consumer name={`speed-${count}`} />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText(`speed-${count}:idle`));
    await waitFor(() => {
      const takes = JSON.parse(localStorage.getItem('voicestudio.take-settings.v1') ?? '{}');
      // The take keeps the steps it rendered with, not "best for whatever engine is active".
      expect(takes[`take-speed-${count}`]?.steps).toBe(32);
    });
    expect(vi.mocked(generateClone).mock.calls[0][0].steps).toBe(32);
    const stored = JSON.parse(localStorage.getItem('voicestudio.render-speed.v1') ?? '{}');
    expect(stored['omnivoice|cuda'] ?? []).toHaveLength(count);
    if (count)
      expect(stored['omnivoice|cuda'][0]).toMatchObject({
        chars: 43,
        audioSeconds: 3,
        renderSeconds: 9,
        steps: 32,
      });
  },
);

describe('generation queue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    modelStatus.value = { status: 'ready', loading: false };
  });
  function QueueConsumer() {
    const state = useGenerateClone();
    return (
      <>
        <button onClick={() => void state.generate()}>go</button>
        <button onClick={() => state.cancelAll()}>cancel all</button>
        <button onClick={() => void state.generateDesign({ text: 'Designed', instruct: '' })}>
          design
        </button>
        <span data-testid="waiting">{state.queuedCount}</span>
        <span data-testid="queue-stage">{state.stage}</span>
        <ul>
          {state.queue.map((item) => (
            <li key={item.id} data-testid="item" data-status={item.status}>
              {item.label}
            </li>
          ))}
        </ul>
      </>
    );
  }
  const take = (id: string) => ({
    blob: new Blob(),
    id,
    audioPath: id + '.wav',
    durationSeconds: 1,
    genTimeSeconds: 1,
    seed: 1,
    routing: null,
    dropped: null,
  });
  function deferredRenders() {
    const pending: Array<{ resolve: (value: unknown) => void; reject: (err: unknown) => void }> =
      [];
    vi.mocked(generateClone).mockImplementation(
      (_input, options) =>
        new Promise((resolve, reject) => {
          pending.push({ resolve: resolve as (value: unknown) => void, reject });
          options?.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          );
        }),
    );
    return pending;
  }
  const statuses = () => screen.getAllByTestId('item').map((el) => el.dataset.status);
  const mount = () =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationProvider>
          <QueueConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );

  it('queues takes requested mid-render and runs them in order with their own script', async () => {
    const renders = deferredRenders();
    mount();
    patchCloneSettings({ text: 'First line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    patchCloneSettings({ text: 'Second line' });
    fireEvent.click(screen.getByText('go'));
    patchCloneSettings({ text: 'Third line' });
    fireEvent.click(screen.getByText('go'));

    // Only one render reaches the backend at a time; the rest wait.
    expect(generateClone).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('waiting')).toHaveTextContent('2');
    expect(statuses()).toEqual(['running', 'queued', 'queued']);
    expect(toast.info).toHaveBeenCalledTimes(2);

    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(2));
    // The queued take kept the script typed when it was requested.
    expect(vi.mocked(generateClone).mock.calls[1]![0].text).toBe('Second line');

    // A failure is recorded and the queue moves on.
    await act(async () => renders[1]!.reject(new Error('engine crashed')));
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(3));
    expect(vi.mocked(generateClone).mock.calls[2]![0].text).toBe('Third line');
    await act(async () => renders[2]!.resolve(take('c')));
    await waitFor(() => expect(statuses()).toEqual(['done', 'failed', 'done']));
    expect(screen.getByTestId('waiting')).toHaveTextContent('0');
  });

  it('pins each queued take to the engine active when it was requested', async () => {
    const renders = deferredRenders();
    const client = new QueryClient();
    client.setQueryData(queryKeys.engines, { tts: { active: 'supertonic3', backends: [] } });
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <QueueConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    patchCloneSettings({
      text: 'First',
      selectedProfileId: 'voice-1',
      steps: null,
      autoPlay: false,
    });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    // The user switches engines while the second take waits.
    act(() => {
      client.setQueryData(queryKeys.engines, { tts: { active: 'omnivoice', backends: [] } });
    });
    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(2));
    const waited = vi.mocked(generateClone).mock.calls[1]![0];
    expect(waited.engine).toBe('supertonic3');
    expect(waited.steps).toBe(12);
  });

  it("never shows the previous take's last progress on the next queued take", async () => {
    const renders = deferredRenders();
    mount();
    patchCloneSettings({ text: 'One', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    // Take A streams: the first update lands, the second waits on the throttle.
    const progressA = vi.mocked(generateClone).mock.calls[0]![1]!.onProgress!;
    act(() => {
      progressA(10);
      progressA(90);
    });
    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(2));
    // A's throttled trailing update fires after B has started.
    await act(() => new Promise((resolve) => setTimeout(resolve, 250)));
    expect(screen.getByTestId('queue-stage')).not.toHaveTextContent('receiving');
  });

  it('skips a waiting take whose saved voice changed before it started', async () => {
    const renders = deferredRenders();
    const client = new QueryClient();
    const voice = {
      id: 'voice-1',
      name: 'Narrator',
      ref_audio_path: 'voice-1-aaaa.wav',
      ref_text: 'Hello there.',
    } as Profile;
    client.setQueryData(queryKeys.profiles, [voice]);
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <QueueConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    patchCloneSettings({ text: 'Line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    // The reference clip is replaced (a new versioned file) while take 2 waits.
    act(() => {
      client.setQueryData(queryKeys.profiles, [{ ...voice, ref_audio_path: 'voice-1-bbbb.wav' }]);
    });
    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(statuses()).toEqual(['done', 'failed']));
    expect(generateClone).toHaveBeenCalledTimes(1);
  });

  it('holds a waiting take while an unlock is in flight, then skips it once the voice changed', async () => {
    const renders = deferredRenders();
    const client = new QueryClient();
    const voice = {
      id: 'voice-1',
      name: 'Narrator',
      ref_audio_path: 'voice-1-aaaa.wav',
      locked_audio_path: 'voice-1-lock.wav',
      is_locked: true,
      ref_text: 'Hello there.',
    } as Profile;
    client.setQueryData(queryKeys.profiles, [voice]);
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <QueueConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    patchCloneSettings({ text: 'Line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    // The unlock has reached the backend, but the profile list is not refreshed yet.
    const settled = beginVoiceChange('voice-1');
    await act(async () => renders[0]!.resolve(take('a')));
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(generateClone).toHaveBeenCalledTimes(1);
    act(() => {
      client.setQueryData(queryKeys.profiles, [{ ...voice, is_locked: false }]);
      settled();
    });
    await waitFor(() => expect(statuses()).toEqual(['done', 'failed']));
    expect(generateClone).toHaveBeenCalledTimes(1);
  });

  it('lets Cancel all stop a take that waits for an in-flight voice change', async () => {
    const renders = deferredRenders();
    const client = new QueryClient();
    const voice = { id: 'voice-1', name: 'Narrator', ref_audio_path: 'a.wav' } as Profile;
    client.setQueryData(queryKeys.profiles, [voice]);
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <QueueConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    patchCloneSettings({ text: 'Line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    const settled = beginVoiceChange('voice-1');
    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(statuses()).toEqual(['done', 'running']));
    fireEvent.click(screen.getByText('cancel all'));
    await waitFor(() => expect(statuses()).toEqual(['done', 'cancelled']));
    // The shared synthesis slot is free again: a new take runs once the change settles.
    fireEvent.click(screen.getByText('go'));
    act(() => settled());
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(2));
  });

  it('keeps waiting takes when the first render stores an automatic transcript', async () => {
    const renders = deferredRenders();
    const client = new QueryClient();
    const voice = {
      id: 'voice-1',
      name: 'Narrator',
      ref_audio_path: 'voice-1-aaaa.wav',
      ref_text: null,
    } as Profile;
    client.setQueryData(queryKeys.profiles, [voice]);
    render(
      <QueryClientProvider client={client}>
        <GenerationProvider>
          <QueueConsumer />
        </GenerationProvider>
      </QueryClientProvider>,
    );
    patchCloneSettings({ text: 'Line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    // /generate transcribes the clip on the first render and stores it.
    act(() => {
      client.setQueryData(queryKeys.profiles, [{ ...voice, ref_text: 'Automatic words.' }]);
    });
    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(2));
    await act(async () => renders[1]!.resolve(take('b')));
    await waitFor(() => expect(statuses()).toEqual(['done', 'done']));
  });

  it.each([
    ['corrected', 'Hello there.', 'Hello, friend.'],
    ['typed in for a voice that had none', null, 'Typed by the user.'],
    ['edited at a legacy cut length', 'a'.repeat(100), 'b'.repeat(100)],
  ])(
    'skips a waiting take when the user saved a transcript %s after it was queued',
    async (_label, before, saved) => {
      const renders = deferredRenders();
      const client = new QueryClient();
      const voice = {
        id: 'voice-edit',
        name: 'Narrator',
        ref_audio_path: 'voice-edit-aaaa.wav',
        ref_text: before,
      } as Profile;
      client.setQueryData(queryKeys.profiles, [voice]);
      render(
        <QueryClientProvider client={client}>
          <GenerationProvider>
            <QueueConsumer />
          </GenerationProvider>
        </QueryClientProvider>,
      );
      patchCloneSettings({
        text: 'Line',
        selectedProfileId: 'voice-edit',
        refText: before ?? '',
        autoPlay: false,
      });
      fireEvent.click(screen.getByText('go'));
      fireEvent.click(screen.getByText('go'));
      // The user saves a new transcript in the voice editor while take 2 waits;
      // a saved voice sends no unchanged transcript (#3192), so /generate
      // would read the edit.
      act(() => {
        commitTranscriptSave(beginTranscriptSave('voice-edit', saved));
        client.setQueryData(queryKeys.profiles, [{ ...voice, ref_text: saved }]);
      });
      await act(async () => renders[0]!.resolve(take('a')));
      await waitFor(() => expect(statuses()).toEqual(['done', 'failed']));
      expect(generateClone).toHaveBeenCalledTimes(1);
    },
  );

  it('never lets Voice Design jump ahead of a waiting clone take', async () => {
    deferredRenders();
    mount();
    // Another feature (voice convert, SFX) holds the synthesis slot.
    const releaseOther = acquireSynthesis()!;
    patchCloneSettings({ text: 'Clone line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    expect(statuses()).toEqual(['queued']);
    // The slot frees up before the queue's retry runs.
    releaseOther();
    fireEvent.click(screen.getByText('design'));
    expect(toast.info).toHaveBeenLastCalledWith(tr('tts_errors.generation_in_progress'));
    expect(generateClone).not.toHaveBeenCalled();
    // The waiting clone take still gets its turn.
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(1), { timeout: 3000 });
    expect(vi.mocked(generateClone).mock.calls[0]![0].text).toBe('Clone line');
  });

  it('keeps Voice Design one at a time instead of queuing it out of sight', async () => {
    const renders = deferredRenders();
    mount();
    patchCloneSettings({ text: 'Clone line', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('design'));
    expect(toast.info).toHaveBeenCalledWith(tr('tts_errors.generation_in_progress'));
    expect(statuses()).toEqual(['running']);

    await act(async () => renders[0]!.resolve(take('a')));
    await waitFor(() => expect(statuses()).toEqual(['done']));
    // Idle again: a design take renders straight away.
    fireEvent.click(screen.getByText('design'));
    await waitFor(() => expect(generateClone).toHaveBeenCalledTimes(2));
    expect(vi.mocked(generateClone).mock.calls[1]![0].text).toBe('Designed');
  });

  it('cancel all stops the running take and drops the waiting ones', async () => {
    deferredRenders();
    mount();
    patchCloneSettings({ text: 'One', selectedProfileId: 'voice-1', autoPlay: false });
    fireEvent.click(screen.getByText('go'));
    fireEvent.click(screen.getByText('go'));
    expect(statuses()).toEqual(['running', 'queued']);

    fireEvent.click(screen.getByText('cancel all'));
    expect(vi.mocked(generateClone).mock.calls[0]![1]!.signal!.aborted).toBe(true);
    await waitFor(() => expect(statuses()).toEqual(['cancelled', 'cancelled']));
    expect(generateClone).toHaveBeenCalledTimes(1);
  });
});
