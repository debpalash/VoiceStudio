import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store';
import { API } from '../api/client';
import { listTranslationEngines, installTranslationEngine } from '../api/engines';
import { dubQc } from '../api/dub';
import toast from 'react-hot-toast';
import { toastErrorWithReport } from '../utils/errorToast';
import useTimelineOnsets from '../hooks/useTimelineOnsets';
import ExportModal from '../components/ExportModal';
import DubPipelineStepper from '../components/dub/DubPipelineStepper';
import IdleSkeleton from '../components/dub/IdleSkeleton';
import DubHeader from '../components/dub/DubHeader';
import DubLeftColumn from '../components/dub/DubLeftColumn';
import DubRightColumn from '../components/dub/DubRightColumn';
import DubResizableColumns from '../components/dub/DubResizableColumns';
import DubFooter from '../components/dub/DubFooter';
import DubTrackSummary from '../components/dub/DubTrackSummary';
import {
  hasCompleteTranslation,
  multiLangTargets,
  translationProgressByCode,
} from '../utils/multiLang';

export default function DubTab(props) {
  const { t, i18n } = useTranslation();
  const {
    // Props that stay prop-threaded: non-serialisable state + handlers that
    // close over App.jsx's scope (uploads, SSE wiring, project CRUD, etc.).
    dubVideoFile,
    dubLocalBlobUrl,
    transcribeElapsed,
    transcribeProgress,
    asrInstall,
    translateProvider,
    setTranslateProvider,
    showTranscript,
    setShowTranscript,
    onGlossaryChange,
    profiles,
    segmentPreviewLoading,
    selectedSegIds,
    setDubVideoFile,
    setDubLocalBlobUrl,
    handleDubAbort,
    handleDubUpload,
    handleDubIngestUrl,
    handleDubRetryTranscribe,
    handleInstallMissingAsr,
    handleDubStop,
    handleDubGenerate,
    handleDubImportSrt,
    handleDubDownload,
    handleDubAudioDownload,
    handleAudioExport,
    speakerClones = {},
    handleSegmentPreview,
    onDirectSegment,
    handleTranslateAll,
    handleCleanupSegments,
    incrementalPlan,
    triggerDownload,
    fileToMediaUrl,
    editSegments,
    saveProject,
    resetDub,
    segmentEditField,
    segmentDelete,
    segmentRestoreOriginal,
    pasteTranslations,
    segmentSplit,
    segmentMerge,
    segmentInsert,
    segmentMoveResize,
    timelineSelSegId,
    setTimelineSelSegId,
    toggleSegSelect,
    selectAllSegs,
    clearSegSelection,
    bulkApplyToSelected,
    bulkDeleteSelected,
  } = props;

  // ── Store reads (Phase 2.2) — drop ~30 props from the App.jsx contract.
  const dubJobId = useAppStore((s) => s.dubJobId);
  const dubStep = useAppStore((s) => s.dubStep);
  const setDubStep = useAppStore((s) => s.setDubStep);
  const setDubInputType = useAppStore((s) => s.setDubInputType);
  const dubPrepStage = useAppStore((s) => s.dubPrepStage);
  const dubPrepProgress = useAppStore((s) => s.dubPrepProgress);
  const dubFilename = useAppStore((s) => s.dubFilename);
  const dubDuration = useAppStore((s) => s.dubDuration);
  const dubSegments = useAppStore((s) => s.dubSegments);
  const setDubSegments = useAppStore((s) => s.setDubSegments);
  const dubTranscript = useAppStore((s) => s.dubTranscript);
  const dubLang = useAppStore((s) => s.dubLang);
  const setDubLang = useAppStore((s) => s.setDubLang);
  const dubLangCode = useAppStore((s) => s.dubLangCode);
  const dubSourceLangCode = useAppStore((s) => s.dubSourceLangCode);
  const setDubSourceLangCode = useAppStore((s) => s.setDubSourceLangCode);
  // User-driven language switches go through switchDubLangCode (P1.2): it
  // swaps segment text through the per-language `translations` map instead
  // of leaving the previous language's text on screen (and previously,
  // letting the next translate destroy it). Non-user rehydration paths
  // (project load, history restore) keep the plain setter.
  const switchDubLangCode = useAppStore((s) => s.switchDubLangCode);
  const dubNumSpeakers = useAppStore((s) => s.dubNumSpeakers);
  const setDubNumSpeakers = useAppStore((s) => s.setDubNumSpeakers);
  const dubDialect = useAppStore((s) => s.dubDialect);
  const setDubDialect = useAppStore((s) => s.setDubDialect);
  const dubInstruct = useAppStore((s) => s.dubInstruct);
  const setDubInstruct = useAppStore((s) => s.setDubInstruct);
  const dubTracks = useAppStore((s) => s.dubTracks);
  const dubError = useAppStore((s) => s.dubError);
  const setDubError = useAppStore((s) => s.setDubError);
  const dubFailure = useAppStore((s) => s.dubFailure);
  const dubProgress = useAppStore((s) => s.dubProgress);
  const isTranslating = useAppStore((s) => s.isTranslating);
  const preserveBg = useAppStore((s) => s.preserveBg);
  const setPreserveBg = useAppStore((s) => s.setPreserveBg);
  const defaultTrack = useAppStore((s) => s.defaultTrack);
  const setDefaultTrack = useAppStore((s) => s.setDefaultTrack);
  const exportTracks = useAppStore((s) => s.exportTracks);
  const setExportTracks = useAppStore((s) => s.setExportTracks);
  const activeProjectName = useAppStore((s) => s.activeProjectName);
  const translateQuality = useAppStore((s) => s.translateQuality);
  const setTranslateQuality = useAppStore((s) => s.setTranslateQuality);
  const dualSubs = useAppStore((s) => s.dualSubs);
  const setDualSubs = useAppStore((s) => s.setDualSubs);
  const burnSubs = useAppStore((s) => s.burnSubs);
  const setBurnSubs = useAppStore((s) => s.setBurnSubs);
  const karaokeSubs = useAppStore((s) => s.karaokeSubs);
  const setKaraokeSubs = useAppStore((s) => s.setKaraokeSubs);
  const timingStrategy = useAppStore((s) => s.timingStrategy);
  const setTimingStrategy = useAppStore((s) => s.setTimingStrategy);
  const voiceMatch = useAppStore((s) => s.voiceMatch);
  const setVoiceMatch = useAppStore((s) => s.setVoiceMatch);

  const showIdleSkeleton = !(
    dubJobId &&
    (dubStep === 'editing' || dubStep === 'generating' || dubStep === 'done')
  );
  // Imperative handle to the post-job waveform so the transcript table can
  // seek the player when the user clicks a row.
  const waveformRef = useRef(null);
  const seekWaveform = useCallback((time) => {
    waveformRef.current?.seekTo?.(time);
  }, []);
  // Speech-onset ticks for the timeline editor (#280, item 3). Lazy: only
  // fetched while the editor is live; re-fetched after a re-transcription
  // because the step leaves and re-enters the editing state.
  const editorActive =
    !!dubJobId && (dubStep === 'editing' || dubStep === 'generating' || dubStep === 'done');
  const { onsets: timelineOnsets } = useTimelineOnsets(dubJobId, editorActive);
  // "Preview dub here" from a timeline box: park the player at the slot
  // start (so the video frame matches), then synthesize + play the line.
  const onTimelinePreviewSegment = useCallback(
    (seg) => {
      seekWaveform(seg.start);
      handleSegmentPreview?.(seg, { preventDefault() {} });
    },
    [seekWaveform, handleSegmentPreview],
  );
  const [ingestUrl, setIngestUrl] = useState('');
  // Dubbing demo: show the side-by-side player above the drop zone on
  // first-run / no-project state. localStorage flag persists dismissal
  // across sessions so power users don't see it every launch.
  const [demoDismissed, setDemoDismissed] = useState(() => {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem('omnivoice.dubbingDemoDismissed') === '1';
  });
  const dismissDubDemo = () => {
    setDemoDismissed(true);
    try {
      localStorage.setItem('omnivoice.dubbingDemoDismissed', '1');
    } catch {
      /* noop */
    }
  };
  const [previewMode, setPreviewMode] = useState('original'); // 'original' | 'dubbed'
  const [exportOpen, setExportOpen] = useState(false);
  const [qcRunning, setQcRunning] = useState(false);

  // Multi-language mode — store-backed (P1.4) so the picks survive tab
  // switches and ride the project save/load payload.
  const multiLangMode = useAppStore((s) => s.multiLangMode);
  const setMultiLangMode = useAppStore((s) => s.setMultiLangMode);
  const multiLangs = useAppStore((s) => s.multiLangs);
  const setMultiLangs = useAppStore((s) => s.setMultiLangs);
  // The primary target picker and the segment-language switch share the
  // legacy visible-text slot, but only the picker changes batch membership.
  const primaryTargetRef = useRef({ lang: dubLang, code: dubLangCode });
  useEffect(() => {
    primaryTargetRef.current = { lang: dubLang, code: dubLangCode };
    // A new backend job starts with the store's restored primary target.
    // Segment-language switches never change dubJobId and therefore leave it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dubJobId]);
  const setPrimaryDubLang = useCallback(
    (lang) => {
      primaryTargetRef.current = { ...primaryTargetRef.current, lang };
      setDubLang(lang);
    },
    [setDubLang],
  );
  const setPrimaryDubLangCode = useCallback(
    (code) => {
      primaryTargetRef.current = { ...primaryTargetRef.current, code };
      switchDubLangCode(code);
    },
    [switchDubLangCode],
  );
  // The primary target dropdown is part of a multi-language job too. The
  // chip picker adds extra targets; it does not replace the active one.
  const batchTargets = useMemo(
    () =>
      multiLangTargets(primaryTargetRef.current.lang, primaryTargetRef.current.code, multiLangs),
    // The store language values intentionally invalidate this memo after a
    // primary-picker change; segment switches recompute the same ref-backed
    // target list without changing batch membership.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dubLang, dubLangCode, multiLangs],
  );
  const multiLangProgress = useMemo(
    () => translationProgressByCode(dubSegments, batchTargets),
    [batchTargets, dubSegments],
  );
  // Landing "Advanced" disclosure (pre-upload options).
  const [landingAdvOpen, setLandingAdvOpen] = useState(false);

  // Generate CTA — when multi-language mode has picks, dub each language
  // sequentially; every run appends its track to dubbed_tracks, so the
  // preview switcher pills fill up one by one.
  //
  // P1.1: each language is TRANSLATED first (`handleTranslateAll(code)`), then
  // generated — the backend synthesizes segment text verbatim, so without the
  // translate pass every "multi-language" track rendered the same words.
  // A pick whose translate fails is skipped (never render a wrong-language
  // track); the batch continues and the skips are reported at the end.
  const multiBatchRunningRef = useRef(false);
  const [multiBatchBusy, setMultiBatchBusy] = useState(false);
  const onGenerateClick = useCallback(async () => {
    if (multiLangMode && batchTargets.length > 0) {
      if (multiBatchRunningRef.current) return; // ignore re-clicks mid-batch
      multiBatchRunningRef.current = true;
      setMultiBatchBusy(true);
      const skipped = [];
      const { lang: primaryLanguage, code: primaryCode } = primaryTargetRef.current;
      try {
        for (let i = 0; i < batchTargets.length; i++) {
          const l = batchTargets[i];
          try {
            setDubLang(l.lang);
            // Keep UI/exports in sync AND snapshot the previous pick's
            // translations before this pick's translate pass overwrites the
            // visible text (P1.2).
            switchDubLangCode(l.code);
            // A changed visible text is not proof that it belongs to this
            // target (switching to an untranslated language intentionally
            // leaves the previous text visible). Only the per-language map
            // can safely suppress a redundant translation request.
            const cached = hasCompleteTranslation(useAppStore.getState().dubSegments, l.code);
            if (!cached) {
              // Honest phase label: this pill slot otherwise only says
              // "Generating…", hiding the translate pass entirely.
              useAppStore.getState().showPill(
                'translating',
                t('dub.multi_translating', {
                  lang: l.lang,
                  current: i + 1,
                  total: batchTargets.length,
                }),
                { homeMode: 'dub' },
              );
              const ok = await handleTranslateAll(l.code);
              // Partial translations are useful for manual review, but an
              // automated batch must never synthesize the untranslated rows
              // into a mixed-language track.
              const complete = hasCompleteTranslation(useAppStore.getState().dubSegments, l.code);
              if (!ok || !complete) {
                // Error already surfaced by handleTranslateAll (banner/toast);
                // drop the phase pill and move on to the next language.
                useAppStore.getState().dismissPill();
                skipped.push(l.lang);
                continue;
              }
            }
            await handleDubGenerate({
              langOverride: { language: l.lang, language_code: l.code },
            });
          } catch {
            // Keep independent target languages moving after one unexpected
            // failure; the underlying action already surfaces its error.
            useAppStore.getState().dismissPill();
            if (!skipped.includes(l.lang)) {
              skipped.push(l.lang);
            }
          }
        }
      } finally {
        setDubLang(primaryLanguage);
        switchDubLangCode(primaryCode);
        multiBatchRunningRef.current = false;
        setMultiBatchBusy(false);
      }
      if (skipped.length) {
        toast.error(t('dub.multi_lang_skipped', { langs: skipped.join(', ') }), {
          duration: 8000,
        });
      }
    } else {
      handleDubGenerate();
    }
  }, [
    multiLangMode,
    batchTargets,
    handleTranslateAll,
    handleDubGenerate,
    setDubLang,
    switchDubLangCode,
    t,
  ]);

  // In multi-language mode, "Translate All" means all configured targets,
  // while still restoring the primary target in the editor afterwards. Each
  // translation lands in segments[].translations[code], so Generate can
  // reuse the complete maps without retranslating or losing another language.
  const onTranslateClick = useCallback(
    async (options = {}) => {
      if (!multiLangMode) return handleTranslateAll(options);
      if (multiBatchRunningRef.current) return false;
      multiBatchRunningRef.current = true;
      setMultiBatchBusy(true);
      const { lang: primaryLanguage, code: primaryCode } = primaryTargetRef.current;
      let allOk = true;
      try {
        for (const target of batchTargets) {
          setDubLang(target.lang);
          switchDubLangCode(target.code);
          const ok = await handleTranslateAll({ ...options, langOverride: target.code });
          if (!ok) allOk = false;
        }
      } finally {
        setDubLang(primaryLanguage);
        switchDubLangCode(primaryCode);
        multiBatchRunningRef.current = false;
        setMultiBatchBusy(false);
      }
      return allOk;
    },
    [multiLangMode, batchTargets, handleTranslateAll, setDubLang, switchDubLangCode],
  );

  // Live ETA while generating — elapsed ticks each second; remaining is
  // extrapolated from the current/total rate so it's only meaningful once
  // at least one segment has rendered and ~2s of clock has passed.
  const [genElapsed, setGenElapsed] = useState(0);
  useEffect(() => {
    if (dubStep !== 'generating') {
      setGenElapsed(0);
      return;
    }
    const start = Date.now();
    setGenElapsed(0);
    const id = setInterval(() => setGenElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, [dubStep]);
  const genRemaining = (() => {
    if (dubStep !== 'generating') return null;
    if (!dubProgress.total || !dubProgress.current || genElapsed < 2) return null;
    const perSeg = genElapsed / dubProgress.current;
    return Math.max(0, Math.round(perSeg * (dubProgress.total - dubProgress.current)));
  })();

  // Translation-engine availability → drives the Engine dropdown's disabled
  // state and the inline Install chip. Lazy-fetched once; refreshed after
  // any install/uninstall so the chip disappears on success.
  const [engines, setEngines] = useState([]);
  const [enginesSandboxed, setEnginesSandboxed] = useState(false);
  const [engineInstalling, setEngineInstalling] = useState(null); // engine id being installed
  const refreshEngines = useCallback(async () => {
    try {
      const res = await listTranslationEngines();
      setEngines(res.engines || []);
      setEnginesSandboxed(!!res.sandboxed);
    } catch {
      setEngines([]);
    }
  }, []);
  useEffect(() => {
    refreshEngines();
  }, [refreshEngines]);
  const activeEngineEntry = engines.find((e) => e.id === translateProvider);
  const activeEngineUnavailable = activeEngineEntry && !activeEngineEntry.installed;
  // Changing the translation engine is a corrective action — clear any stale
  // translate/pipeline error banner so it doesn't outlive the choice that
  // caused it (same class-fix as clearing on a new translate attempt). Covers
  // both the <select> and the popover's "Switch to Argos" path.
  const handleSelectTranslateProvider = useCallback(
    (id) => {
      setDubError('');
      setTranslateProvider(id);
    },
    [setDubError, setTranslateProvider],
  );
  const handleInstallEngine = async (engineId) => {
    if (!engineId || enginesSandboxed) return;
    // Installing the missing package is corrective too — drop the banner that
    // told the user to install it in the first place.
    setDubError('');
    setEngineInstalling(engineId);
    const progressToast = toast.loading(t('dub.install_progress', { engine: engineId }));
    try {
      const res = await installTranslationEngine(engineId);
      await refreshEngines();
      if (res.restart_required) {
        toast(t('dub.install_restart', { engine: engineId }), {
          icon: '🔄',
          id: progressToast,
          duration: 7000,
        });
      } else if (res.status === 'already_installed') {
        toast(t('dub.install_already', { engine: engineId }), { icon: 'ℹ️', id: progressToast });
      } else {
        toast.success(t('dub.install_ok', { engine: engineId }), { id: progressToast });
      }
    } catch (err) {
      toast.dismiss(progressToast);
      toastErrorWithReport(
        t('dub.install_failed', { message: String(err.message || err).slice(0, 200) }),
        err,
      );
    } finally {
      setEngineInstalling(null);
    }
  };

  // Secondary settings (Language/ISO/Style/Engine/Quality/Multi-lang) are
  // expanded by default so the user can pick a target language and quality
  // without an extra click on first open. They stay an accordion so the
  // user can collapse them once happy with the choice.
  const [settingsOpen, setSettingsOpen] = useState(true);
  const hasAnyTranslation = dubSegments.some((s) => s.text_original && s.text_original !== s.text);

  // Glossary: hide behind a chip when empty, auto-open once terms exist.
  const glossaryTermCount = useAppStore((s) => s.glossaryTerms.length);
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const [glossaryHidden, setGlossaryHidden] = useState(false);
  const glossaryVisible = glossaryOpen || (glossaryTermCount > 0 && !glossaryHidden);

  // Phase 4.3 — between-stage checkpoint banner.
  const reviewMode = useAppStore((s) => s.reviewMode);
  const [dismissedStages, setDismissedStages] = useState(() => new Set());
  const hasTranslations = dubSegments.some((s) => s.text_original && s.text_original !== s.text);
  const checkpointStage =
    dubStep === 'editing' && !hasTranslations
      ? 'asr'
      : dubStep === 'editing' && hasTranslations
        ? 'translate'
        : dubStep === 'done'
          ? 'done'
          : null;
  const showCheckpoint =
    reviewMode === 'on' && checkpointStage && !dismissedStages.has(checkpointStage);
  const onCheckpointContinue = () => {
    if (checkpointStage === 'asr') handleTranslateAll?.();
    else if (checkpointStage === 'translate') handleDubGenerate?.();
  };
  const onCheckpointDismiss = () => {
    setDismissedStages((prev) => {
      const next = new Set(prev);
      if (checkpointStage) next.add(checkpointStage);
      return next;
    });
  };
  // Persist the "pull YouTube captions" intent across ingests — it's opt-in
  // per-URL but almost always on once the user discovers it. Stored on the
  // component instead of the global store to avoid polluting cross-project
  // prefs with what's really a per-ingest choice.
  const [fetchYtSubs, setFetchYtSubs] = useState(false);
  const [youtubeCookieFile, setYoutubeCookieFile] = useState(null);
  const resetDubAndCredentials = useCallback(() => {
    setYoutubeCookieFile(null);
    resetDub?.();
  }, [resetDub]);
  const pipelineBusy =
    isTranslating ||
    ['uploading', 'installing-asr', 'transcribing', 'generating', 'stopping'].includes(dubStep);
  const pipelineSteps = pipelineBusy
    ? []
    : [
        ...(dubJobId || dubStep !== 'idle' ? ['upload'] : []),
        ...(dubVideoFile ? ['prepare'] : []),
        ...(dubJobId ? ['transcribe'] : []),
        ...(dubSegments.length ? ['edit'] : []),
        ...(dubStep === 'done' ? ['export'] : []),
      ];
  const onPipelineStep = useCallback(
    (step) => {
      if (pipelineBusy) return;
      if (step === 'upload') {
        if (dubSegments.length && !window.confirm(`${t('dub.reset')}?`)) return;
        resetDubAndCredentials();
      } else if (step === 'prepare' && dubVideoFile) {
        handleDubUpload?.();
      } else if (step === 'transcribe' && dubJobId) {
        const transcriptComplete =
          dubSegments.length > 0 && ['editing', 'generating', 'done'].includes(dubStep);
        if (transcriptComplete && !window.confirm(`${t('dub.retry_transcription')}?`)) return;
        handleDubRetryTranscribe?.();
      } else if (step === 'edit' && dubSegments.length) {
        setDubStep('editing');
      } else if (step === 'export' && dubStep === 'done') {
        setExportOpen(true);
      }
    },
    [
      pipelineBusy,
      dubSegments.length,
      t,
      resetDubAndCredentials,
      dubVideoFile,
      handleDubUpload,
      dubJobId,
      handleDubRetryTranscribe,
      setDubStep,
      dubStep,
    ],
  );
  const onIngestUrl = () => {
    if (!ingestUrl.trim() || !handleDubIngestUrl) return;
    handleDubIngestUrl(ingestUrl.trim(), {
      fetchSubs: fetchYtSubs,
      subLangs: undefined,
      cookieFile: youtubeCookieFile || undefined,
    });
    setIngestUrl('');
    setYoutubeCookieFile(null);
  };
  // Track-switcher visibility is keyed to the persisted tracks ONLY — not the
  // language dropdown. Restored projects can carry finished tracks while
  // dubLangCode reads 'und' (older dub_history rows froze language_code at
  // ""), and the old `dubLangCode !== 'und'` guard hid their tabs until the
  // user re-picked a language.
  const hasDubbedTrack = dubStep === 'done' && dubTracks.length > 0;
  // Cache-busting nonce, bumped every time a generation completes (see
  // useDubWorkflow's done handler). The preview URL is otherwise identical
  // across re-dubs, so the WebView could keep serving the previously
  // buffered MP4 and the user would see the old dub after editing +
  // regenerating (#281). The backend ignores `v`.
  const dubGenNonce = useAppStore((s) => s.dubGenNonce);
  // previewMode is 'original' or a dubbed language code (multi-language switcher).
  const previewIsDub = previewMode !== 'original' && hasDubbedTrack;
  const videoSrc = previewIsDub
    ? `${API}/dub/preview-video/${dubJobId}?mix=surgical2&lang=${encodeURIComponent(previewMode)}&preserve_bg=${preserveBg ? 1 : 0}&v=${dubGenNonce}`
    : `${API}/dub/media/${dubJobId}`;
  // The video is the normal transport, but WaveSurfer can fall back to a
  // companion audio element when a WebView decodes the picture without its
  // audio. Keep that fallback language-aware so a dubbed preview never
  // silently swaps back to the original track (#1692).
  const playbackAudioSrc = previewIsDub
    ? `${API}/dub/download-audio/${dubJobId}?lang=${encodeURIComponent(previewMode)}&preserve_bg=${preserveBg ? 1 : 0}`
    : `${API}/dub/audio/${dubJobId}`;
  // When a dub finishes, jump the preview to the freshly-dubbed language so the
  // result plays immediately — the user can tap back to Original any time.
  // Membership guard: only jump to a language that actually has a track,
  // otherwise fall back to the first track. Restored projects can have
  // dubLangCode out of sync with the tracks (e.g. 'en'/'und' with tracks
  // ['bn']) and an unguarded jump would point the player at
  // /dub/preview-video?lang=en — a guaranteed 404.
  useEffect(() => {
    if (hasDubbedTrack && previewMode === 'original') {
      setPreviewMode(dubTracks.includes(dubLangCode) ? dubLangCode : dubTracks[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasDubbedTrack, dubLangCode]);

  // Second-pass timing QC (Wave 3.3): re-recognize the dubbed audio and merge
  // the per-line drift scores back onto the segments so DubSegmentRow can flag
  // lines worth a re-listen. Non-destructive — generated text is untouched.
  const handleDubQc = useCallback(async () => {
    if (!dubJobId || qcRunning) return;
    setQcRunning(true);
    const loadingId = toast.loading(t('dub.qc_running', { defaultValue: 'Checking dub timing…' }));
    try {
      const lang = previewMode !== 'original' ? previewMode : undefined;
      const res = await dubQc(dubJobId, lang);
      // A generation that finishes while QC is in flight invalidates these
      // measurements. Ignore the stale response instead of attaching timing
      // results from the previous audio to the new dub.
      if (useAppStore.getState().dubGenNonce !== dubGenNonce) {
        toast.dismiss(loadingId);
        return;
      }
      const byId = new Map((res.segments || []).map((q) => [String(q.seg_id), q]));
      setDubSegments((currentSegments) =>
        currentSegments.map((s, i) => {
          const q = byId.get(String(s.id ?? i));
          if (!q) return s;
          return {
            ...s,
            qc_drift: q.drift,
            qc_flagged: q.flagged,
            qc_recognized: q.recognized_text,
            ...(q.measured_start != null
              ? { qc_measured_start: q.measured_start, qc_measured_end: q.measured_end }
              : {}),
          };
        }),
      );
      if (res.flagged_count > 0) {
        toast(
          t('dub.qc_result', {
            flagged: res.flagged_count,
            total: res.total,
            defaultValue: '{{flagged}} of {{total}} lines may need a re-listen',
          }),
          { icon: '⚠️', id: loadingId, duration: 6000 },
        );
      } else {
        toast.success(
          t('dub.qc_clean', {
            total: res.total,
            defaultValue: 'All {{total}} lines match the script',
          }),
          { id: loadingId },
        );
      }
    } catch (err) {
      toast.dismiss(loadingId);
      toastErrorWithReport(
        t('dub.qc_failed', {
          message: String(err?.message || err).slice(0, 200),
          defaultValue: 'Timing check failed: {{message}}',
        }),
        err,
      );
    } finally {
      setQcRunning(false);
    }
  }, [dubJobId, qcRunning, previewMode, dubGenNonce, setDubSegments, t]);

  return (
    <div className="dub-workspace flex-1 flex flex-col min-h-0 min-w-0 [container-type:inline-size] [container-name:dub-shell]">
      {/* Pipeline spine — shown once a file/job is in play so the user always
          knows which stage they're at (Upload → … → Export). In the editor view
          it's inlined onto the DubHeader row (see below), so only render the
          standalone spine before the editor exists. */}
      {(dubVideoFile || dubJobId || dubStep !== 'idle') &&
        !(
          dubJobId &&
          (dubStep === 'editing' || dubStep === 'generating' || dubStep === 'done')
        ) && (
          <DubPipelineStepper
            dubStep={dubStep}
            selectableSteps={pipelineSteps}
            onStepSelect={onPipelineStep}
          />
        )}
      {/* ── Idle: show full editor skeleton with drop zone ── */}
      {showIdleSkeleton && (
        <IdleSkeleton
          t={t}
          uiLocale={i18n.language}
          dubVideoFile={dubVideoFile}
          activeProjectName={activeProjectName}
          dubFilename={dubFilename}
          dubError={dubError}
          dubJobId={dubJobId}
          dubStep={dubStep}
          dubFailure={dubFailure}
          asrInstall={asrInstall}
          handleInstallMissingAsr={handleInstallMissingAsr}
          handleDubRetryTranscribe={handleDubRetryTranscribe}
          handleDubImportSrt={handleDubImportSrt}
          dubLocalBlobUrl={dubLocalBlobUrl}
          dubPrepStage={dubPrepStage}
          dubPrepProgress={dubPrepProgress}
          handleDubAbort={handleDubAbort}
          transcribeElapsed={transcribeElapsed}
          transcribeProgress={transcribeProgress}
          dubDuration={dubDuration}
          dubNumSpeakers={dubNumSpeakers}
          setDubNumSpeakers={setDubNumSpeakers}
          handleDubUpload={handleDubUpload}
          demoDismissed={demoDismissed}
          dismissDubDemo={dismissDubDemo}
          setDubVideoFile={setDubVideoFile}
          setDubInputType={setDubInputType}
          setDubStep={setDubStep}
          fileToMediaUrl={fileToMediaUrl}
          setDubLocalBlobUrl={setDubLocalBlobUrl}
          ingestUrl={ingestUrl}
          setIngestUrl={setIngestUrl}
          onIngestUrl={onIngestUrl}
          fetchYtSubs={fetchYtSubs}
          setFetchYtSubs={setFetchYtSubs}
          youtubeCookieFile={youtubeCookieFile}
          setYoutubeCookieFile={setYoutubeCookieFile}
          dubLangCode={dubLangCode}
          dubSourceLangCode={dubSourceLangCode}
          setDubSourceLangCode={setDubSourceLangCode}
          setDubLangCode={switchDubLangCode}
          setDubLang={setDubLang}
          landingAdvOpen={landingAdvOpen}
          setLandingAdvOpen={setLandingAdvOpen}
          dubInstruct={dubInstruct}
          setDubInstruct={setDubInstruct}
          onOpenQueue={() => useAppStore.getState().setMode?.('batch')}
        />
      )}

      {/* ── After transcription: side-by-side editor ── */}
      {dubJobId && (dubStep === 'editing' || dubStep === 'generating' || dubStep === 'done') && (
        <div className="dub-editor flex-1 flex flex-col min-h-0 min-w-0">
          <DubHeader
            t={t}
            dubFilename={dubFilename}
            dubDuration={dubDuration}
            dubSegments={dubSegments}
            activeProjectName={activeProjectName}
            saveProject={saveProject}
            resetDub={resetDubAndCredentials}
            dubStep={dubStep}
            handleDubStop={handleDubStop}
            dubProgress={dubProgress}
            onGenerateClick={onGenerateClick}
            isTranslating={isTranslating}
            multiLangMode={multiLangMode}
            multiLangs={batchTargets}
            incrementalPlan={incrementalPlan}
            handleDubGenerate={handleDubGenerate}
            qcRunning={qcRunning}
            handleDubQc={handleDubQc}
            setExportOpen={setExportOpen}
            pipelineSteps={pipelineSteps}
            onPipelineStep={onPipelineStep}
          />
          <DubResizableColumns resizeLabel={t('logs.drag_resize')}>
            <DubLeftColumn
              trackControls={
                <DubTrackSummary
                  t={t}
                  dubStep={dubStep}
                  dubTracks={dubTracks}
                  incrementalPlan={incrementalPlan}
                  exportTracks={exportTracks}
                  setExportTracks={setExportTracks}
                  dubLangCode={dubLangCode}
                />
              }
              hasDubbedTrack={hasDubbedTrack}
              t={t}
              previewMode={previewMode}
              setPreviewMode={setPreviewMode}
              dubTracks={dubTracks}
              videoSrc={videoSrc}
              playbackAudioSrc={playbackAudioSrc}
              waveformRef={waveformRef}
              dubJobId={dubJobId}
              dubSegments={dubSegments}
              timelineOnsets={timelineOnsets}
              timelineSelSegId={timelineSelSegId}
              setTimelineSelSegId={setTimelineSelSegId}
              incrementalPlan={incrementalPlan}
              segmentMoveResize={segmentMoveResize}
              segmentDelete={segmentDelete}
              onTimelinePreviewSegment={onTimelinePreviewSegment}
              dubStep={dubStep}
              dubProgress={dubProgress}
              fmtDur={fmtDur}
              genElapsed={genElapsed}
              genRemaining={genRemaining}
              speakerClones={speakerClones}
              setDubSegments={setDubSegments}
              profiles={profiles}
              settingsOpen={settingsOpen}
              setSettingsOpen={setSettingsOpen}
              dubLang={dubLang}
              dubLangCode={dubLangCode}
              translateQuality={translateQuality}
              activeEngineUnavailable={activeEngineUnavailable}
              translateProvider={translateProvider}
              dubInstruct={dubInstruct}
              setDubInstruct={setDubInstruct}
              handleTranslateAll={onTranslateClick}
              isTranslating={isTranslating}
              hasAnyTranslation={hasAnyTranslation}
              handleCleanupSegments={handleCleanupSegments}
              setDubLang={setPrimaryDubLang}
              setDubLangCode={setPrimaryDubLangCode}
              dubDialect={dubDialect}
              setDubDialect={setDubDialect}
              i18n={i18n}
              enginesSandboxed={enginesSandboxed}
              handleInstallEngine={handleInstallEngine}
              engineInstalling={engineInstalling}
              activeEngineEntry={activeEngineEntry}
              engines={engines}
              setTranslateProvider={handleSelectTranslateProvider}
              setTranslateQuality={setTranslateQuality}
              multiLangMode={multiLangMode}
              setMultiLangMode={setMultiLangMode}
              multiLangs={multiLangs}
              setMultiLangs={setMultiLangs}
              multiLangProgress={multiLangProgress}
              editSegments={editSegments}
            />
            <DubRightColumn
              t={t}
              preserveBg={preserveBg}
              setPreserveBg={setPreserveBg}
              dualSubs={dualSubs}
              setDualSubs={setDualSubs}
              burnSubs={burnSubs}
              setBurnSubs={setBurnSubs}
              defaultTrack={defaultTrack}
              setDefaultTrack={setDefaultTrack}
              dubLangCode={dubLangCode}
              multiLangMode={multiLangMode}
              batchTargets={batchTargets}
              multiBatchBusy={multiBatchBusy}
              setDubLang={setDubLang}
              setDubLangCode={switchDubLangCode}
              dubTracks={dubTracks}
              timingStrategy={timingStrategy}
              setTimingStrategy={setTimingStrategy}
              voiceMatch={voiceMatch}
              setVoiceMatch={setVoiceMatch}
              dubTranscript={dubTranscript}
              showTranscript={showTranscript}
              setShowTranscript={setShowTranscript}
              dubJobId={dubJobId}
              glossaryVisible={glossaryVisible}
              setGlossaryOpen={setGlossaryOpen}
              setGlossaryHidden={setGlossaryHidden}
              glossaryTermCount={glossaryTermCount}
              dubLang={dubLang}
              dubSegments={dubSegments}
              onGlossaryChange={onGlossaryChange}
              selectedSegIds={selectedSegIds}
              bulkApplyToSelected={bulkApplyToSelected}
              speakerClones={speakerClones}
              profiles={profiles}
              clearSegSelection={clearSegSelection}
              bulkDeleteSelected={bulkDeleteSelected}
              showCheckpoint={showCheckpoint}
              checkpointStage={checkpointStage}
              onCheckpointContinue={onCheckpointContinue}
              onCheckpointDismiss={onCheckpointDismiss}
              isTranslating={isTranslating}
              segmentPreviewLoading={segmentPreviewLoading}
              toggleSegSelect={toggleSegSelect}
              selectAllSegs={selectAllSegs}
              segmentEditField={segmentEditField}
              segmentDelete={segmentDelete}
              segmentRestoreOriginal={segmentRestoreOriginal}
              pasteTranslations={pasteTranslations}
              handleSegmentPreview={handleSegmentPreview}
              onDirectSegment={onDirectSegment}
              segmentSplit={segmentSplit}
              segmentMerge={segmentMerge}
              segmentInsert={segmentInsert}
              segmentMoveResize={segmentMoveResize}
              seekWaveform={seekWaveform}
              timelineSelSegId={timelineSelSegId}
              dubStep={dubStep}
              dubProgress={dubProgress}
            />
          </DubResizableColumns>
          <DubFooter
            hideTracks
            t={t}
            dubStep={dubStep}
            dubTracks={dubTracks}
            dubLangCode={dubLangCode}
            incrementalPlan={incrementalPlan}
            dubError={dubError}
            dubFailure={dubFailure}
            onDismissError={() => setDubError('')}
            exportTracks={exportTracks}
            setExportTracks={setExportTracks}
            dubSegments={dubSegments}
            translateQuality={translateQuality}
          />
        </div>
      )}

      <ExportModal
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        jobId={dubJobId}
        filename={dubFilename}
        dubTracks={dubTracks}
        dubLangCode={dubLangCode}
        preserveBg={preserveBg}
        setPreserveBg={setPreserveBg}
        defaultTrack={defaultTrack}
        setDefaultTrack={setDefaultTrack}
        exportTracks={exportTracks}
        setExportTracks={setExportTracks}
        dualSubs={dualSubs}
        setDualSubs={setDualSubs}
        burnSubs={burnSubs}
        setBurnSubs={setBurnSubs}
        karaokeSubs={karaokeSubs}
        setKaraokeSubs={setKaraokeSubs}
        API={API}
        triggerDownload={triggerDownload}
        handleDubDownload={handleDubDownload}
        handleDubAudioDownload={handleDubAudioDownload}
        handleAudioExport={handleAudioExport}
        segmentCount={dubSegments.length}
        timingStrategy={timingStrategy}
        onEnterprise={() => useAppStore.getState().setMode?.('enterprise')}
      />
    </div>
  );
}

function fmtDur(s) {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return sec ? `${m}m ${sec}s` : `${m}m`;
}
