import { useState, useEffect, useRef } from 'react';
import { Volume2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import { CATEGORIES } from '../utils/constants';
import { Segmented } from '../ui';
import { useAppStore } from '../store';
import { API, apiPost, apiFetch } from '../api/client';
import { mergeDescribedAttrs, applyVdState } from '../utils/voiceInstruct';
import { useEngines } from '../api/hooks';
import { claimPlayback, stopActivePlayback, usePlaybackSource } from '../utils/playback';
import ScriptPanel from '../components/clone/ScriptPanel';
import AudioMethodPanel from '../components/clone/AudioMethodPanel';
import DesignMethodPanel from '../components/clone/DesignMethodPanel';
import ConvertMethodPanel from '../components/clone/ConvertMethodPanel';
import ActionBar from '../components/clone/ActionBar';
import EngineQuickSwitch from '../components/EngineQuickSwitch';

export default function CloneDesignTab(props) {
  const {
    textAreaRef,
    text,
    setText,
    language,
    setLanguage,
    steps,
    setSteps,
    cfg,
    setCfg,
    speed,
    setSpeed,
    tShift,
    setTShift,
    posTemp,
    setPosTemp,
    classTemp,
    setClassTemp,
    layerPenalty,
    setLayerPenalty,
    duration,
    setDuration,
    denoise,
    setDenoise,
    postprocess,
    setPostprocess,
    showOverrides,
    setShowOverrides,
    profiles,
    selectedProfile,
    setSelectedProfile,
    refAudio,
    refText,
    setRefText,
    instruct,
    setInstruct,
    profileName,
    setProfileName,
    showSaveProfile,
    setShowSaveProfile,
    isRecording,
    isCleaning,
    recordingTime,
    audioInputs,
    selectedAudioInputId,
    setSelectedAudioInputId,
    channelMode,
    setChannelMode,
    inputLevelStore,
    vdStates,
    setVdStates,
    isGenerating,
    generationTime,
    applyPreset,
    insertTag,
    handleSaveProfile,
    handleSaveDesignProfile,
    handleGenerate,
    startRecording,
    stopRecording,
    ingestRefAudio,
  } = props;

  const { t } = useTranslation();
  // "Define voice" method — 'audio' (was the Clone tab) | 'design' (was the
  // Design tab). Lives in the store so navigation shims / profile selection
  // can preset it (voice-studio-unification P4).
  const defineMethod = useAppStore((s) => s.defineMethod);
  const setDefineMethod = useAppStore((s) => s.setDefineMethod);
  // Voice-design seed (#526): show the seed the last synth used, let the user
  // pin it ("keep this seed") so tweaks stay on the same base timbre, or roll
  // a new one.
  const designSeed = useAppStore((s) => s.designSeed);
  const keepSeed = useAppStore((s) => s.keepSeed);
  const setDesignSeed = useAppStore((s) => s.setDesignSeed);
  const setKeepSeed = useAppStore((s) => s.setKeepSeed);
  const [activePersonality, setActivePersonality] = useState('');
  const [insertOpen, setInsertOpen] = useState(false);

  // Details recipe line (10x §1.5, #1771 follow-up): the non-Auto category
  // picks as one readable string, shown on the collapsed summary.
  const identityPicks = Object.values(vdStates || {}).filter((v) => v && v !== 'Auto');
  const identityRecipe = identityPicks.length
    ? identityPicks.join(' · ')
    : t('clone.identity_auto', { defaultValue: 'Auto — the model decides' });
  // #1771 follow-up: the whole point of collapsing the 12-row block to one
  // line is that it STAYS collapsed until asked — it must never start open,
  // including on first run (the old design opened it whenever every category
  // was still Auto; that behavior does not carry over).
  const [identityOpen, setIdentityOpen] = useState(false);

  // ── "Describe your voice" (#317): free-text → design parameters ──────────
  // Debounced call to the local deterministic mapper (POST /design/describe);
  // the result overwrites the category controls live, and the user can still
  // hand-tune any of them afterwards. Unmappable fragments are surfaced
  // instead of silently dropped (the #115/#114 validator-feedback lesson).
  const [describeText, setDescribeText] = useState('');
  const [describeUnmatched, setDescribeUnmatched] = useState([]);
  const [describeMatchedAny, setDescribeMatchedAny] = useState(true);

  // describeRequestRef guards a describe response landing after it's been
  // superseded. It fires on every keystroke's debounce AND on-demand from
  // the reset button, so two /design/describe calls can be in flight
  // together, and whichever resolves LAST used to always win regardless of
  // which was actually issued last (the original inline effect closed over
  // its own `cancelled` flag per call, which extracting it into
  // applyDescribeToVdStates dropped — greptile caught it in review).
  //
  // That first fix only ordered describe-vs-describe correctly — it left a
  // second, worse hole greptile found on the next pass: describe-vs-MANUAL
  // races. A describe request can still be in flight when the user picks a
  // category by hand, clicks a personality/preset chip, or clears the
  // description entirely; none of those bumped the token, so a describe
  // response landing afterwards silently overwrote the user's manual pick
  // (or re-applied attributes for a description that no longer exists).
  // invalidateDescribe() is the one place that bumps it — call it from
  // EVERY path that should beat an in-flight describe response, not just
  // from inside applyDescribeToVdStates itself.
  const describeRequestRef = useRef(0);
  const invalidateDescribe = () => {
    describeRequestRef.current += 1;
  };

  const onDescribeChange = (e) => {
    const value = e.target.value;
    setDescribeText(value);
    if (!value.trim()) {
      // Cleared: an in-flight response for the old (now-gone) description
      // must never land and re-apply attributes for text that no longer
      // exists — bump the token so it's discarded on arrival.
      invalidateDescribe();
      setDescribeUnmatched([]);
      setDescribeMatchedAny(true);
    }
  };

  // Shared "description -> vdStates" path: the debounced live-typing effect
  // below and the Details editor's "Reset to description" button (#1771
  // follow-up) both drive the category picks from describeText, so this is
  // the ONE place that does it — resetToDescription must never grow a
  // second, divergent implementation of the same mapping.
  const applyDescribeToVdStates = async (q) => {
    const requestId = ++describeRequestRef.current;
    if (!q) {
      // No description to reset to: every category goes back to Auto.
      setVdStates(Object.fromEntries(Object.keys(CATEGORIES).map((k) => [k, 'Auto'])));
      setDescribeUnmatched([]);
      setDescribeMatchedAny(true);
      setActivePersonality('');
      setInstruct('');
      return;
    }
    try {
      const res = await apiPost('/design/describe', { description: q });
      if (requestId !== describeRequestRef.current) return; // superseded — discard
      setVdStates(mergeDescribedAttrs(res.attrs));
      setDescribeUnmatched(res.unmatched || []);
      setDescribeMatchedAny((res.matched || []).length > 0);
      // The description now owns the design parameters — clear any stale
      // personality instruct so the synthesize path can't merge conflicting
      // tokens from two sources (the issue-#114 failure mode).
      setActivePersonality('');
      setInstruct('');
    } catch {
      // Backend unreachable — leave the controls untouched; the live-typing
      // path retries on the next keystroke, the reset button on the next click.
    }
  };

  useEffect(() => {
    const q = describeText.trim();
    if (!q) return undefined;
    let cancelled = false;
    const id = setTimeout(() => {
      if (!cancelled) applyDescribeToVdStates(q);
    }, 450);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [describeText]);

  // "Reset to description" (#1771 follow-up): returns every category to what
  // the current description implies, or all-Auto when there is none —
  // without waiting for the 450ms debounce or requiring a fresh keystroke.
  const resetToDescription = () => applyDescribeToVdStates(describeText.trim());

  // Fetch personality presets from backend
  const { data: personalities = [] } = useQuery({
    queryKey: ['personalities'],
    queryFn: () => apiFetch(`${API}/personalities`).then((r) => r.json()),
    staleTime: Infinity,
  });

  const applyPersonality = (p) => {
    // A manual pick always beats an in-flight describe response (greptile,
    // PR #1793 second pass) — bump the token before touching state.
    invalidateDescribe();
    if (activePersonality === p.id) {
      setActivePersonality('');
      return;
    }
    setActivePersonality(p.id);
    setInstruct(p.instruct);
    // Reset category sliders to Auto so the synthesize path doesn't
    // merge stale slider tokens with the personality's instruct string —
    // that combination caused issue #114 (conflicting items in the same
    // category, e.g. "low pitch" from a prior preset + "moderate pitch"
    // from the personality).
    const resetVd = Object.fromEntries(Object.keys(CATEGORIES).map((k) => [k, 'Auto']));
    setVdStates(resetVd);
  };

  // Engine readiness for the demo fallback. Model installs invalidate this
  // shared query, so a newly-ready engine replaces the demo without a stale
  // local snapshot or a second poller.
  const { data: enginesData } = useEngines();
  const anyTtsReady = !!(enginesData?.tts?.backends || []).some((b) => b.available);

  // Demo coach-mark: when the user is on the "From audio" method with the
  // bundled demo profile (demo0001) freshly selected and the textarea is empty,
  // prefill a punchy starter prompt and show a one-line coach-mark above
  // the textarea. Both auto-dismiss as soon as the user types anything.
  // Tracked via localStorage so we don't re-prefill on every visit.
  const DEMO_PROFILE_ID = 'demo0001';
  const DEMO_PROMPT =
    "Welcome aboard. I was just a three-second clip a moment ago — now I can say anything you'd like, in your voice or mine.";
  const [showDemoCoachmark, setShowDemoCoachmark] = useState(false);

  useEffect(() => {
    if (defineMethod !== 'audio') return;
    if (selectedProfile !== DEMO_PROFILE_ID) return;
    if (typeof window === 'undefined') return;
    if (localStorage.getItem('omnivoice.demoClonePrompted') === '1') return;
    if (text) return; // user already typed something
    setText(DEMO_PROMPT);
    setShowDemoCoachmark(true);
    localStorage.setItem('omnivoice.demoClonePrompted', '1');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defineMethod, selectedProfile]);

  // "Hear demo" fallback: when no TTS engine is ready and the user is on
  // the demo profile, the Synthesize button is swapped for one that plays
  // the pre-rendered demo_clone_output.wav. This guarantees a working
  // "wow moment" on first launch before any model downloads finish.
  const showHearDemo =
    defineMethod === 'audio' && selectedProfile === DEMO_PROFILE_ID && !anyTtsReady;

  // Cmd/Ctrl+Enter synthesizes from anywhere in the workspace (10x spec 1.1).
  // Not on Convert: there is no script to synthesize there.
  useEffect(() => {
    if (defineMethod === 'convert') return undefined;
    const onKey = (e) => {
      if (!(e.metaKey || e.ctrlKey) || e.key !== 'Enter') return;
      e.preventDefault();
      if (!isGenerating && !showHearDemo) handleGenerate();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGenerating, showHearDemo, handleGenerate, defineMethod]);
  const demoAudioRef = useRef(null);
  const demoReleaseRef = useRef(null);
  const [demoAudioPlaying, setDemoAudioPlaying] = useState(false);

  // Global playback state (#316): while a synthesized output (or another
  // unmanaged blob playback) is audible, the footer CTA becomes a Stop
  // button so the user can halt it immediately.
  const playbackSource = usePlaybackSource();
  const outputPlaying = playbackSource === 'output';

  const playDemoOutput = () => {
    const audio = demoAudioRef.current;
    if (!audio) return;
    if (demoAudioPlaying) {
      stopActivePlayback();
      return;
    }
    // Claim the global playback slot so this demo stops any other preview
    // first — and can itself be stopped from anywhere (#316).
    demoReleaseRef.current = claimPlayback(() => {
      audio.pause();
      setDemoAudioPlaying(false);
    }, 'demo-output');
    audio.src = `${API}/demo_audio/demo_clone_output.wav`;
    audio.currentTime = 0;
    audio
      .play()
      .then(() => setDemoAudioPlaying(true))
      .catch(() => {
        demoReleaseRef.current?.();
        demoReleaseRef.current = null;
        setDemoAudioPlaying(false);
      });
  };

  // #1771: the ONE place a category pick is applied — mirrors the engine's
  // dialect-vs-accent exclusivity (omnivoice/models/omnivoice.py::_resolve_
  // instruct) live in the picker, so ChineseDialect and EnglishAccent can
  // never both be set from the form. When picking one clears the other, say
  // so instead of a silent reset.
  const handleVdChange = (key, value) => {
    // A manual pick always beats an in-flight describe response (greptile,
    // PR #1793 second pass) — bump the token before touching state.
    invalidateDescribe();
    const { vdStates: next, clearedCategory } = applyVdState(vdStates, key, value);
    setVdStates(next);
    if (clearedCategory) {
      toast(t('clone.vd_exclusive_cleared', { cleared: t(`clone.cat_${clearedCategory}`) }), {
        icon: '⚠️',
      });
    }
  };

  // 10x P4 a11y (spec §3): chip strips get a roving tabindex —
  // ArrowLeft/ArrowRight move FOCUS ONLY, per the WAI-ARIA toolbar pattern
  // (a native <button> already activates on Enter/Space/click, so the
  // handler never needs to fire selection itself). Gender/Age/Pitch/Style/
  // Accent-or-Dialect moved to native <select>s in the #1771 follow-up
  // (free keyboard nav from the browser), so the only remaining chip strip
  // is the "Starting points" row — including chips currently hidden behind
  // its overflow toggle, once revealed.
  //
  // This USED to also apply the newly-focused option (`onSelect`), which was
  // correct for the old CATEGORIES radiogroups (a true single-value pick,
  // where "arrow lands on X" and "X is now selected" are the same thing) but
  // wrong for this toggle strip — CodeRabbit caught it in review: every
  // arrow press was calling applyPersonality, which resets every vdStates
  // category, so merely traversing the row with the keyboard destroyed the
  // user's Details recipe. `currentIndex` is the target button's OWN index
  // (from the render loop), not a lookup by "currently active id" — the old
  // lookup-based `cur` silently reset to 0 whenever nothing was active
  // (activePersonality can be '' most of the time), so it never advanced
  // past the first two chips.
  const onChipKeyDown = (e, ids, currentIndex) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const next = (currentIndex + (e.key === 'ArrowRight' ? 1 : -1) + ids.length) % ids.length;
    e.currentTarget.closest('[role="group"]')?.querySelectorAll('[data-chip-nav]')[next]?.focus();
  };

  // 10x P4 a11y (spec §3): once a generation has run, the persistent status
  // region below announces its finish — not just its start.
  const wasGeneratingRef = useRef(false);
  useEffect(() => {
    if (isGenerating) wasGeneratingRef.current = true;
  }, [isGenerating]);

  // Partition personalities into legacy chips vs. new demo cards.
  // `is_demo: true` entries get the rich card grid; the rest keep their
  // existing chip-strip rendering (backward-compatible with v0.2.x users
  // who learned the chips and shouldn't see them suddenly missing).
  const demoPresets = personalities.filter((p) => p.is_demo);
  const chipPersonalities = personalities.filter((p) => !p.is_demo);

  // Apply a full demo preset: pre-fill the textarea, set the category
  // sliders, clear any stale free-text instruct, switch language, and
  // highlight the chip equivalent. After this fires, the user can hit
  // Synthesize Audio immediately — no further input needed.
  const applyDemoPreset = (p) => {
    // A manual pick always beats an in-flight describe response (greptile,
    // PR #1793 second pass) — bump the token before touching state.
    invalidateDescribe();
    if (p.script) setText(p.script);
    if (p.attrs) {
      // #1771: apply one category at a time through the same exclusivity
      // guard the picker uses — a preset merged wholesale onto the existing
      // vdStates could otherwise set a dialect on top of an already-picked
      // accent (or vice versa).
      let next = vdStates;
      for (const [key, value] of Object.entries(p.attrs)) {
        next = applyVdState(next, key, value).vdStates;
      }
      setVdStates(next);
    }
    setInstruct('');
    if (p.language) setLanguage(p.language);
    setActivePersonality(p.id);
  };

  // `applyPreset` is owned by useTTS.js (it writes straight to the global
  // store), not this component — but the "Starting points" PRESETS chips
  // still fire it from inside DesignMethodPanel, and a manual pick always
  // beats an in-flight describe response (greptile, PR #1793 second pass).
  // Wrap it here so invalidateDescribe fires before the prop's own logic
  // runs, rather than reaching into useTTS.js for a second describe-aware
  // implementation.
  const applyPresetAndInvalidate = (p) => {
    invalidateDescribe();
    applyPreset(p);
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 min-w-0">
      {/* #1771 follow-up (item 6): NOT flex-1 — the old always-open 12-row
          Design block was tall enough that this area routinely filled the
          full column height on its own. Now that Details defaults collapsed,
          forcing this to grow-fill would strand a dead gap between it and
          ActionBar below. Sizing to content (min-h-0 + overflow-y-auto still
          in play) lets it shrink for short content and still scroll when
          content genuinely exceeds the available height. */}
      <div className="flex flex-col gap-[6px] min-h-0 overflow-y-auto">
        {/* ═══ SCRIPT — what should it say ═══
            Hidden for Convert: the source clip IS the script (the backend
            transcribes it), so a text panel would only mislead. */}
        {defineMethod !== 'convert' && (
          <ScriptPanel
            t={t}
            defineMethod={defineMethod}
            text={text}
            setText={setText}
            activePersonality={activePersonality}
            demoPresets={demoPresets}
            applyDemoPreset={applyDemoPreset}
            showDemoCoachmark={showDemoCoachmark}
            setShowDemoCoachmark={setShowDemoCoachmark}
            selectedProfile={selectedProfile}
            DEMO_PROFILE_ID={DEMO_PROFILE_ID}
            textAreaRef={textAreaRef}
            insertOpen={insertOpen}
            setInsertOpen={setInsertOpen}
            insertTag={insertTag}
          />
        )}

        {/* ═══ VOICE — who says it ═══ */}
        <div className="flex flex-col gap-[6px] flex-none min-h-0 relative z-[1]">
          <div className="flex flex-col min-h-0 overflow-auto bg-[var(--chrome-bg)] border border-transparent rounded-none py-[10px] px-[12px] max-[800px]:px-[10px] max-[600px]:px-[6px] max-[600px]:py-[8px]">
            <div className="label-row justify-between">
              <span className="label-row mb-0">
                <Volume2 className="label-icon" size={14} />{' '}
                {t('clone.voice_kicker', { defaultValue: 'Voice' })}
              </span>
              <div className="flex items-center gap-[6px]">
                <EngineQuickSwitch />
                <Segmented
                  size="sm"
                  value={defineMethod}
                  onChange={setDefineMethod}
                  items={[
                    {
                      value: 'audio',
                      label: t('clone.define_from_audio', { defaultValue: 'From audio' }),
                    },
                    {
                      value: 'design',
                      label: t('clone.define_by_design', { defaultValue: 'By design' }),
                    },
                    {
                      value: 'convert',
                      label: t('clone.define_convert', { defaultValue: 'Convert' }),
                    },
                  ]}
                />
              </div>
            </div>

            {defineMethod === 'audio' ? (
              <AudioMethodPanel
                t={t}
                selectedProfile={selectedProfile}
                setSelectedProfile={setSelectedProfile}
                profiles={profiles}
                ingestRefAudio={ingestRefAudio}
                refAudio={refAudio}
                isCleaning={isCleaning}
                isRecording={isRecording}
                recordingTime={recordingTime}
                audioInputs={audioInputs}
                selectedAudioInputId={selectedAudioInputId}
                setSelectedAudioInputId={setSelectedAudioInputId}
                channelMode={channelMode}
                setChannelMode={setChannelMode}
                inputLevelStore={inputLevelStore}
                startRecording={startRecording}
                stopRecording={stopRecording}
                refText={refText}
                setRefText={setRefText}
                instruct={instruct}
                setInstruct={setInstruct}
                defineMethod={defineMethod}
                designSeed={designSeed}
                setDesignSeed={setDesignSeed}
                keepSeed={keepSeed}
                setKeepSeed={setKeepSeed}
                showSaveProfile={showSaveProfile}
                setShowSaveProfile={setShowSaveProfile}
                profileName={profileName}
                setProfileName={setProfileName}
                handleSaveProfile={handleSaveProfile}
              />
            ) : defineMethod === 'convert' ? (
              <ConvertMethodPanel t={t} profiles={profiles} />
            ) : (
              <DesignMethodPanel
                t={t}
                describeText={describeText}
                onDescribeChange={onDescribeChange}
                describeMatchedAny={describeMatchedAny}
                describeUnmatched={describeUnmatched}
                chipPersonalities={chipPersonalities}
                activePersonality={activePersonality}
                applyPersonality={applyPersonality}
                applyPreset={applyPresetAndInvalidate}
                identityOpen={identityOpen}
                setIdentityOpen={setIdentityOpen}
                identityRecipe={identityRecipe}
                vdStates={vdStates}
                onVdChange={handleVdChange}
                onChipKeyDown={onChipKeyDown}
                resetToDescription={resetToDescription}
                showSaveProfile={showSaveProfile}
                setShowSaveProfile={setShowSaveProfile}
                profileName={profileName}
                setProfileName={setProfileName}
                handleSaveDesignProfile={handleSaveDesignProfile}
                instruct={instruct}
                language={language}
              />
            )}
          </div>
        </div>
      </div>

      {/* ═══ ACTION BAR — pinned to the column bottom ═══
          Hidden for Convert: it drives text synthesis (script + overrides),
          and Convert owns its action button inside its panel. */}
      {defineMethod !== 'convert' && (
        <ActionBar
          t={t}
          showOverrides={showOverrides}
          setShowOverrides={setShowOverrides}
          cfg={cfg}
          setCfg={setCfg}
          speed={speed}
          setSpeed={setSpeed}
          tShift={tShift}
          setTShift={setTShift}
          posTemp={posTemp}
          setPosTemp={setPosTemp}
          classTemp={classTemp}
          setClassTemp={setClassTemp}
          layerPenalty={layerPenalty}
          setLayerPenalty={setLayerPenalty}
          duration={duration}
          setDuration={setDuration}
          denoise={denoise}
          setDenoise={setDenoise}
          postprocess={postprocess}
          setPostprocess={setPostprocess}
          language={language}
          setLanguage={setLanguage}
          steps={steps}
          setSteps={setSteps}
          showHearDemo={showHearDemo}
          playDemoOutput={playDemoOutput}
          demoAudioPlaying={demoAudioPlaying}
          demoAudioRef={demoAudioRef}
          demoReleaseRef={demoReleaseRef}
          setDemoAudioPlaying={setDemoAudioPlaying}
          outputPlaying={outputPlaying}
          isGenerating={isGenerating}
          handleGenerate={handleGenerate}
          generationTime={generationTime}
          wasGeneratingRef={wasGeneratingRef}
        />
      )}
    </div>
  );
}
