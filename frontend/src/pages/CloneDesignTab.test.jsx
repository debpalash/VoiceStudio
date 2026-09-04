// #1771 follow-up regressions (coordinator-verified in a real browser run):
// the test suite passed while both of these were live, because DesignMethodPanel's
// own unit tests take identityOpen/chipPersonalities as PROPS rather than
// exercising CloneDesignTab's real initial-state computation and its real
// "personalities + PRESETS share one lane" data flow. These tests mount the
// actual CloneDesignTab so both bugs would fail here.
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CloneDesignTab from './CloneDesignTab';
import { useAppStore } from '../store';
import { CATEGORIES } from '../utils/constants';
import { apiPost } from '../api/client';

vi.mock('../api/hooks', () => ({
  useEngines: () => ({ data: { tts: { backends: [] }, asr: { backends: [] } } }),
  useSelectEngine: () => ({ mutate: vi.fn(), isPending: false }),
}));

// Real personality ids/names — matches the six clone.personality_* locale
// keys, so the component's real t() lookups render the exact chip labels
// the coordinator saw in the browser repro (Narrator, Casual, News Anchor,
// Storyteller, Corporate, Energetic).
const PERSONALITIES = [
  { id: 'narrator', name: 'Narrator', is_demo: false },
  { id: 'casual', name: 'Casual', is_demo: false },
  { id: 'news_anchor', name: 'News Anchor', is_demo: false },
  { id: 'storyteller', name: 'Storyteller', is_demo: false },
  { id: 'corporate', name: 'Corporate', is_demo: false },
  { id: 'energetic', name: 'Energetic', is_demo: false },
];

vi.mock('../api/client', () => ({
  API: '',
  apiFetch: vi.fn(() => Promise.resolve({ json: () => Promise.resolve(PERSONALITIES) })),
  apiPost: vi.fn(() => Promise.resolve({})),
}));

const NOOP = () => {};

function baseProps(overrides = {}) {
  const vdStates = Object.fromEntries(Object.keys(CATEGORIES).map((k) => [k, 'Auto']));
  return {
    textAreaRef: { current: null },
    text: '',
    setText: NOOP,
    language: 'en',
    setLanguage: NOOP,
    steps: 16,
    setSteps: NOOP,
    cfg: 2,
    setCfg: NOOP,
    speed: 1,
    setSpeed: NOOP,
    tShift: 0,
    setTShift: NOOP,
    posTemp: 0,
    setPosTemp: NOOP,
    classTemp: 0,
    setClassTemp: NOOP,
    layerPenalty: 0,
    setLayerPenalty: NOOP,
    duration: '',
    setDuration: NOOP,
    denoise: false,
    setDenoise: NOOP,
    postprocess: false,
    setPostprocess: NOOP,
    showOverrides: false,
    setShowOverrides: NOOP,
    profiles: [],
    selectedProfile: '',
    setSelectedProfile: NOOP,
    refAudio: null,
    refText: '',
    setRefText: NOOP,
    instruct: '',
    setInstruct: NOOP,
    profileName: '',
    setProfileName: NOOP,
    showSaveProfile: false,
    setShowSaveProfile: NOOP,
    isRecording: false,
    isCleaning: false,
    recordingTime: 0,
    audioInputs: [],
    selectedAudioInputId: '',
    setSelectedAudioInputId: NOOP,
    channelMode: 'mono',
    setChannelMode: NOOP,
    inputLevelStore: undefined,
    vdStates,
    setVdStates: NOOP,
    isGenerating: false,
    generationTime: 0,
    applyPreset: NOOP,
    insertTag: NOOP,
    handleSaveProfile: NOOP,
    handleSaveDesignProfile: NOOP,
    handleGenerate: NOOP,
    startRecording: NOOP,
    stopRecording: NOOP,
    ingestRefAudio: NOOP,
    ...overrides,
  };
}

function renderDesignTab(overrides = {}) {
  // Default defineMethod is 'audio' — jump straight to the Design method so
  // DesignMethodPanel (not AudioMethodPanel) mounts.
  useAppStore.getState().setDefineMethod('design');
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <CloneDesignTab {...baseProps(overrides)} />
    </QueryClientProvider>,
  );
}

describe('CloneDesignTab — Voice Design panel redesign regressions', () => {
  it('starts the Details summary collapsed, not expanded', () => {
    renderDesignTab();
    const summary = screen.getByRole('button', { name: /details/i });
    expect(summary).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById('design-details-fields')).toBeNull();
  });

  it('shows exactly 5 combined starting-point chips, with the overflow count covering BOTH personalities and PRESETS', async () => {
    renderDesignTab();
    await screen.findByText('Narrator');
    expect(screen.getByText('Casual')).toBeInTheDocument();
    expect(screen.getByText('News Anchor')).toBeInTheDocument();
    expect(screen.getByText('Storyteller')).toBeInTheDocument();
    expect(screen.getByText('Corporate')).toBeInTheDocument();

    // Hidden behind the toggle: the 6th personality (Energetic) + all 6
    // hardcoded PRESETS (12 total - 5 visible = 7).
    expect(screen.queryByText('Energetic')).toBeNull();
    expect(screen.queryByText(/Authoritative/)).toBeNull();
    const more = screen.getByText('7 more…');

    fireEvent.click(more);
    expect(screen.getByText('Energetic')).toBeInTheDocument();
    expect(screen.getByText(/Authoritative/)).toBeInTheDocument();
    expect(screen.queryByText(/more…/)).toBeNull();
  });

  // CodeRabbit (PR #1793 review): the Starting Points chip strip is a toggle
  // group, not a true radio group — clicking the active chip again clears
  // it (applyPersonality). Arrow-key navigation must move focus ONLY; it
  // used to also re-apply the newly-focused chip on every press, which
  // reset every vdStates category to Auto just from tabbing through with
  // the keyboard, and (since nothing is "active" most of the time) got
  // stuck re-selecting index 0→1 instead of actually advancing.
  it('ArrowRight only moves focus across chips — it never re-applies a personality/preset', async () => {
    const setVdStates = vi.fn();
    const setInstruct = vi.fn();
    renderDesignTab({ setVdStates, setInstruct });
    const narrator = await screen.findByRole('button', { name: /narrator/i });
    const casual = screen.getByRole('button', { name: /casual/i });

    narrator.focus();
    expect(document.activeElement).toBe(narrator);

    fireEvent.keyDown(narrator, { key: 'ArrowRight' });

    expect(document.activeElement).toBe(casual);
    expect(setVdStates).not.toHaveBeenCalled();
    expect(setInstruct).not.toHaveBeenCalled();
  });
});

// Bot review on PR #1793 (greptile + coderabbit, independently): extracting
// the debounced describe→vdStates body into applyDescribeToVdStates() (so
// the live-typing effect and the "Reset to description" button share it)
// dropped the original inline effect's `if (cancelled) return;` guard AFTER
// the await. Two overlapping /design/describe calls can resolve out of
// order (a slow first request racing a fast, newer second one); the fix is
// a describeRequestRef sequence token that discards a response once a newer
// call has started.
describe('CloneDesignTab — stale /design/describe response race guard', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // apiPost is a module-level mock shared across every test in this file —
    // clear its call history (not its queued mockImplementationOnce, which
    // each test sets up fresh) so `toHaveBeenCalledTimes` counts THIS test's
    // calls only.
    apiPost.mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('a slow, superseded response never overwrites a faster, newer one', async () => {
    let resolveFirst;
    let resolveSecond;
    apiPost
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveSecond = resolve)));

    const setVdStates = vi.fn();
    renderDesignTab({ setVdStates });
    const textarea = screen.getByPlaceholderText(
      'e.g. a warm elderly British storyteller, slightly raspy',
    );

    // First description — its debounce fires and its (slow) apiPost call
    // starts, but nothing has resolved yet.
    fireEvent.change(textarea, { target: { value: 'a deep male voice' } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(apiPost).toHaveBeenCalledTimes(1);

    // A second, different description supersedes it before the first
    // request resolves.
    fireEvent.change(textarea, { target: { value: 'a bright female voice' } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(apiPost).toHaveBeenCalledTimes(2);

    // The NEWER request resolves first (plausible under real network
    // jitter) — its result must be applied.
    await act(async () => {
      resolveSecond({ attrs: { Gender: 'female' }, matched: ['female'], unmatched: [] });
    });
    expect(setVdStates).toHaveBeenCalledTimes(1);
    expect(setVdStates).toHaveBeenLastCalledWith(expect.objectContaining({ Gender: 'female' }));

    // The OLDER, now-stale request finally resolves — it must be discarded,
    // not silently overwrite the newer state that already landed.
    await act(async () => {
      resolveFirst({ attrs: { Gender: 'male' }, matched: ['male'], unmatched: [] });
    });
    expect(setVdStates).toHaveBeenCalledTimes(1); // still just the one, newer call
  });

  // Greptile's second-pass finding on PR #1793: the sequence-token fix above
  // only orders describe-vs-describe correctly. It missed describe-vs-MANUAL
  // races — a describe request can still be in flight when the user hand-
  // picks a category, and the response landing afterward used to silently
  // discard the manual pick because nothing bumped the token except another
  // describe call. invalidateDescribe() must fire from every manual
  // mutation path.
  it('a manual vdStates edit made while a describe is in flight is never overwritten by that response', async () => {
    let resolveDescribe;
    apiPost.mockImplementationOnce(() => new Promise((resolve) => (resolveDescribe = resolve)));

    const setVdStates = vi.fn();
    renderDesignTab({ setVdStates });
    const textarea = screen.getByPlaceholderText(
      'e.g. a warm elderly British storyteller, slightly raspy',
    );

    // A describe request is in flight (not yet resolved).
    fireEvent.change(textarea, { target: { value: 'a deep male voice' } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(apiPost).toHaveBeenCalledTimes(1);

    // The user hand-picks Gender before that response lands.
    fireEvent.click(screen.getByRole('button', { name: /details/i }));
    const genderSelect = document.getElementById('vd-Gender');
    fireEvent.change(genderSelect, { target: { value: 'male' } });
    expect(setVdStates).toHaveBeenCalledTimes(1);
    expect(setVdStates).toHaveBeenLastCalledWith(expect.objectContaining({ Gender: 'male' }));

    // The in-flight describe response finally lands with a DIFFERENT
    // Gender — it must not clobber the manual pick that came after it was
    // issued.
    await act(async () => {
      resolveDescribe({ attrs: { Gender: 'female' }, matched: ['female'], unmatched: [] });
    });
    expect(setVdStates).toHaveBeenCalledTimes(1); // still just the manual edit
  });

  // The second shape of the same bug: clearing the description entirely.
  // `onDescribeChange`'s cleared branch never scheduled a NEW describe call
  // (there's nothing to describe), but it also never invalidated the OLD
  // one already in flight — so a response for text that no longer exists
  // could still land and re-apply its attributes.
  it('clearing the description discards an in-flight response for the old text', async () => {
    let resolveDescribe;
    apiPost.mockImplementationOnce(() => new Promise((resolve) => (resolveDescribe = resolve)));

    const setVdStates = vi.fn();
    renderDesignTab({ setVdStates });
    const textarea = screen.getByPlaceholderText(
      'e.g. a warm elderly British storyteller, slightly raspy',
    );

    fireEvent.change(textarea, { target: { value: 'a deep male voice' } });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(apiPost).toHaveBeenCalledTimes(1);

    // Cleared before the request resolves.
    fireEvent.change(textarea, { target: { value: '' } });

    await act(async () => {
      resolveDescribe({ attrs: { Gender: 'male' }, matched: ['male'], unmatched: [] });
    });
    // A description that no longer exists must never re-apply its attrs.
    expect(setVdStates).not.toHaveBeenCalled();
  });
});
