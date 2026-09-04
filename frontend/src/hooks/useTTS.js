import { useState, useRef, useCallback } from 'react';
import { useAppStore } from '../store';
import { generateSpeech, TtsGenerationBusyError } from '../api/generate';
import { pickDesignSeed } from '../utils/seed';
import { playBlobAudio, playPing } from '../utils/media';
import {
  StreamingPreviewError,
  resolveRemoteTtsTarget,
  shouldFallbackToClassic,
  streamGenerateSpeech,
  supportsStreamingPreview,
} from '../utils/streamingTts';
import { probeAudioDuration } from '../utils/format';
import { CLONE_MAX_SECONDS, PRESETS } from '../utils/constants';
import {
  buildDesignInstruct,
  designModeProfileId,
  mergeDescribedAttrs,
} from '../utils/voiceInstruct';
import { toast } from 'react-hot-toast';
import { toastErrorWithReport } from '../utils/errorToast';
import { modelNotDownloadedPayload, toastModelNotDownloaded } from '../utils/modelNotDownloaded';
import { addBreadcrumb } from '../utils/breadcrumbs';
import i18next from 'i18next';
const t = i18next.t.bind(i18next);

// #21: in-memory de-dup for the synth-time routing toast — a 50-clip batch
// shouldn't fire one toast per request. Tracks the last status surfaced this
// session (module scope, no localStorage); resets on full reload.
let _lastRoutingStatus = null;

// Same de-dup, for "progressive playback is off because your GPU is the one
// across the room". Keyed by worker so switching machines re-announces, while
// ten renders in a row on the same worker say it once.
let _lastStreamingOffWorker = null;

/**
 * Encapsulates TTS generation logic, streaming response handling,
 * audio ingestion (with trim gate), and preset/tag helpers.
 */
export default function useTTS({ selectedProfile, setSelectedProfile, loadHistory, profiles }) {
  const text = useAppStore((s) => s.text);
  const setText = useAppStore((s) => s.setText);
  const language = useAppStore((s) => s.language);
  const instruct = useAppStore((s) => s.instruct);
  const refText = useAppStore((s) => s.refText);
  const speed = useAppStore((s) => s.speed);
  const steps = useAppStore((s) => s.steps);
  const cfg = useAppStore((s) => s.cfg);
  const denoise = useAppStore((s) => s.denoise);
  const tShift = useAppStore((s) => s.tShift);
  const posTemp = useAppStore((s) => s.posTemp);
  const classTemp = useAppStore((s) => s.classTemp);
  const layerPenalty = useAppStore((s) => s.layerPenalty);
  const postprocess = useAppStore((s) => s.postprocess);
  const duration = useAppStore((s) => s.duration);
  const vdStates = useAppStore((s) => s.vdStates);
  // Which "Define voice" method is active in the Voice (studio) workspace —
  // 'audio' (reference clip) vs 'design' (described attributes). Replaces the
  // old clone/design navigation-mode checks (voice-studio-unification P4).
  const defineMethod = useAppStore((s) => s.defineMethod);
  const setSidebarTab = useAppStore((s) => s.setSidebarTab);
  // Voice-design seed (#526): reuse the pinned seed when "keep this seed" is on
  // so tweaks stay on the same base timbre; otherwise roll a fresh one.
  const keepSeed = useAppStore((s) => s.keepSeed);
  const designSeed = useAppStore((s) => s.designSeed);
  const setDesignSeed = useAppStore((s) => s.setDesignSeed);

  const [refAudio, setRefAudio] = useState(null);
  const [pendingTrimFile, setPendingTrimFile] = useState(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [generationTime, setGenerationTime] = useState(0);
  const timerRef = useRef(null);
  const textAreaRef = useRef(null);

  const ingestRefAudio = useCallback(
    async (file) => {
      if (!file) {
        setRefAudio(null);
        return;
      }
      const dur = await probeAudioDuration(file);
      if (dur && dur > CLONE_MAX_SECONDS) {
        setPendingTrimFile(file);
        setSelectedProfile(null);
        toast(t('tts_errors.trim_hint', { duration: dur.toFixed(1), max: CLONE_MAX_SECONDS }));
        return;
      }
      setRefAudio(file);
      setSelectedProfile(null);
    },
    [setSelectedProfile],
  );

  const insertTag = useCallback(
    (tag) => {
      if (!textAreaRef.current) return;
      const start = textAreaRef.current.selectionStart;
      const end = textAreaRef.current.selectionEnd;
      setText(text.substring(0, start) + tag + text.substring(end));
      setTimeout(() => {
        textAreaRef.current.focus();
        textAreaRef.current.setSelectionRange(start + tag.length, start + tag.length);
      }, 0);
    },
    [text, setText],
  );

  const applyPreset = useCallback(
    (preset) => {
      // #1771: run every "replace the whole vdStates" entry point through the
      // same accent/dialect exclusivity guard the live picker uses — today's
      // hardcoded PRESETS never set both, but a future one (or a hand-edited
      // constants.js) shouldn't be able to rebuild the conflict the engine
      // rejects.
      useAppStore.getState().setVdStates(mergeDescribedAttrs(preset.attrs));
      if (preset.tags && !text.includes(preset.tags.trim())) insertTag(preset.tags);
    },
    [text, insertTag],
  );

  const handleGenerate = useCallback(async () => {
    if (!text.trim()) return toast.error(t('tts_errors.enter_text'));
    if (defineMethod === 'audio' && !refAudio && !selectedProfile)
      return toast.error(t('tts_errors.upload_or_select'));
    addBreadcrumb(`generate:start (${defineMethod})`);
    setIsGenerating(true);
    setGenerationTime(0);
    const st = Date.now();
    timerRef.current = setInterval(() => {
      const elapsed = ((Date.now() - st) / 1000).toFixed(1);
      setGenerationTime((prev) => {
        const suffix = /\(\d+%\)$/.exec(String(prev))?.[0];
        return suffix ? `${elapsed} ${suffix}` : elapsed;
      });
    }, 100);
    let abortTimer = null;
    try {
      const formData = new FormData();
      formData.append('text', text);
      if (language !== 'Auto') formData.append('language', language);
      formData.append('num_step', steps);
      formData.append('guidance_scale', cfg);
      formData.append('speed', speed);
      formData.append('denoise', denoise);
      formData.append('t_shift', tShift);
      formData.append('position_temperature', posTemp);
      formData.append('class_temperature', classTemp);
      formData.append('layer_penalty_factor', layerPenalty);
      formData.append('postprocess_output', postprocess);
      if (duration) formData.append('duration', parseFloat(duration));

      if (defineMethod === 'audio') {
        if (selectedProfile) {
          formData.append('profile_id', selectedProfile);
        } else if (refAudio) {
          const arrBuf = await refAudio.arrayBuffer();
          const safeBlob = new Blob([arrBuf], { type: refAudio.type });
          formData.append('ref_audio', safeBlob, refAudio.name || 'audio.wav');
          formData.append('ref_text', refText);
        }
        // #612: a clone's free-text style field also passes through the backend
        // instruct whitelist, so raw prose (e.g. a non-EN/ZH description like the
        // Vietnamese report) 400s with "Unsupported instruct items". Apply the
        // SAME validator-safe guard the design path uses: keep valid style tags,
        // drop the rest, and surface a localized warning toast — never round-trip
        // a 400. vdStates is empty here (clone has no design sliders).
        if (instruct) {
          const {
            instruct: safeInstruct,
            unsupported,
            duplicates,
            conflicts,
          } = buildDesignInstruct({}, instruct);
          if (unsupported.length) {
            toast(t('tts_errors.ignored_unsupported', { items: unsupported.join(', ') }), {
              icon: '⚠️',
            });
          }
          if (duplicates.length) {
            toast(t('tts_errors.ignored_duplicate', { items: duplicates.join(', ') }), {
              icon: '⚠️',
            });
          }
          // #1771: a Chinese dialect and an English accent typed together
          // (e.g. into a clone's free-text style field) — the engine rejects
          // the combination outright, so drop the loser client-side instead of
          // round-tripping a 400.
          if (conflicts.length) {
            toast(t('tts_errors.ignored_conflict', { items: conflicts.join(', ') }), {
              icon: '⚠️',
            });
          }
          if (safeInstruct) formData.append('instruct', safeInstruct);
        }
      } else {
        // #526: reuse the pinned seed when "keep this seed" is on (stable
        // tweaks), else roll a fresh one. The backend echoes the seed it used
        // back via X-Seed so the UI can show + pin it.
        formData.append('seed', pickDesignSeed(keepSeed, designSeed));
        // plan-05 (#132): build a validator-safe instruct (one valid tag per
        // category; drop unsupported free-text) so Synthesize stops failing
        // with "Unsupported instruct items" (#115) / "conflicting items within
        // the same category" (#114).
        const {
          instruct: finalInstruct,
          unsupported,
          duplicates,
          conflicts,
        } = buildDesignInstruct(vdStates, instruct);
        if (unsupported.length) {
          toast(t('tts_errors.ignored_unsupported', { items: unsupported.join(', ') }), {
            icon: '⚠️',
          });
        }
        if (duplicates.length) {
          toast(t('tts_errors.ignored_duplicate', { items: duplicates.join(', ') }), {
            icon: '⚠️',
          });
        }
        // #1771: backstop for a dialect+accent pick that reached here anyway
        // (the picker and vdStates-restore paths already prevent this) — drop
        // the loser instead of letting the engine 400 on it.
        if (conflicts.length) {
          toast(t('tts_errors.ignored_conflict', { items: conflicts.join(', ') }), {
            icon: '⚠️',
          });
        }
        if (finalInstruct) formData.append('instruct', finalInstruct);
        // #674: in design mode, never forward a CLONE profile_id — its reference
        // voice would override the design attributes (e.g. "Male" has no effect).
        // Design profiles still pass through (re-render a designed voice).
        const designProfileId = designModeProfileId(selectedProfile, profiles);
        if (designProfileId) {
          formData.append('profile_id', designProfileId);
        }
      }

      // The first /generate may cold-load/download the model. The backend now
      // bounds that and returns an error rather than hanging; this client-side
      // abort is a backstop so the UI never spins forever even if the backend
      // is unreachable. The ceiling sits just above the backend's load timeout
      // so the backend's descriptive error wins in the normal case.
      const ac = new AbortController();
      abortTimer = setTimeout(() => ac.abort(), 21 * 60 * 1000);

      // #1330 — one voice for both delivery paths. A dropped chunk is not an
      // error (the audio is real), so it is a persistent-ish warning toast
      // rather than a thrown failure, and it quotes the lost text so the user
      // can tell at a glance what to re-render.
      const announceDroppedText = (count, sample) => {
        const preview = (sample || '').trim().slice(0, 120);
        toast(
          preview
            ? t('tts.droppedChunksWithText', { count, text: preview })
            : t('tts.droppedChunks', { count }),
          { icon: '\u26a0\ufe0f', duration: 8000 },
        );
      };

      // Header handling shared by both delivery paths (streaming + classic).
      const applyResponseHeaders = (response) => {
        // #526: surface the seed the backend actually used so the Design tab
        // can display it and offer "keep this seed". Authoritative over the
        // client guess (covers the profile-seed / materialized-seed paths too).
        const xSeed = parseInt(response.headers.get('X-Seed') || '', 10);
        if (Number.isInteger(xSeed)) setDesignSeed(xSeed);

        // #21: one-time, non-blocking routing notice. The backend sets these
        // headers only on cpu_fallback / accelerated-with-caveat (never on the
        // benign cpu_only / clean-accelerated paths), so their mere presence is
        // the signal. De-duped by status so a batch doesn't spam.
        // #1330: some of the text rendered to no audio. The take that came
        // back is clean and simply short, so nothing else would ever tell the
        // user — they reported it by reading along. Classic path only; the
        // streaming path learns this from a `warning` frame (headers are sent
        // before the render starts).
        const droppedCount = parseInt(response.headers.get('X-OmniVoice-Dropped-Chunks') || '', 10);
        if (Number.isInteger(droppedCount) && droppedCount > 0) {
          announceDroppedText(droppedCount, response.headers.get('X-OmniVoice-Dropped-Text'));
        }

        const routingStatus = response.headers.get('X-OmniVoice-Routing');
        if (routingStatus && routingStatus !== _lastRoutingStatus) {
          _lastRoutingStatus = routingStatus;
          const reason = response.headers.get('X-OmniVoice-Routing-Reason') || '';
          if (routingStatus === 'cpu_fallback') {
            toast(t('tts.routingFallback', { reason }), { icon: '🐢' });
          } else if (routingStatus === 'accelerated' && reason) {
            // accelerated is only surfaced WITH a driver/arch caveat reason.
            toast(t('tts.routingCaveat', { reason }), { icon: '⚠️' });
          }
        }
      };
      const setProgressPct = (pct) =>
        setGenerationTime((prev) => `${prev.toString().split(' ')[0]} (${pct}%)`);

      // Streaming preview (feat: streaming-tts-preview): playback starts from
      // the FIRST synthesized chunk while the rest is still rendering, via
      // /generate stream=true — instead of staring at the spinner until the
      // whole render lands. Only when the preview would auto-play anyway, and
      // only when Web Audio exists (all three Tauri webviews have it; if it's
      // ever missing we take the classic path below — identical to today).
      // Any MID-stream failure falls back to the classic whole-file flow with
      // no user-visible difference beyond the old wait; pre-stream HTTP errors
      // (ApiError) throw straight to the shared catch, exactly like before.
      //
      // Remote GPU is the one case where it must NOT run: the stream is
      // rendered by this process, so taking it would silently ignore the
      // worker the user picked — a local render dressed as a remote one. The
      // classic path below is the one that goes remote, so it wins, and the
      // user is told why their progressive playback stopped rather than left
      // to conclude the app got slower.
      let streamed = false;
      const wantsStreaming = useAppStore.getState().autoPlayPreview && supportsStreamingPreview();
      const remoteTarget = wantsStreaming
        ? await resolveRemoteTtsTarget({ signal: ac.signal })
        : null;
      if (remoteTarget) {
        const who = remoteTarget.label || remoteTarget.workerId || '';
        if (who !== _lastStreamingOffWorker) {
          _lastStreamingOffWorker = who;
          toast(t('tts.streamingOffRemote', { label: who }), { icon: '🖥️', duration: 6000 });
        }
      }
      if (wantsStreaming && !remoteTarget) {
        try {
          await streamGenerateSpeech(formData, {
            signal: ac.signal,
            label: t('player.streaming_preview'),
            finalLabel: t('player.generated_audio'),
            onHeaders: applyResponseHeaders,
            onProgress: setProgressPct,
            onWarning: (ev) => {
              if (ev?.code === 'dropped_chunks' && ev.count > 0)
                announceDroppedText(ev.count, (ev.text || []).join(' | '));
            },
          });
          streamed = true;
        } catch (err) {
          if (!(err instanceof StreamingPreviewError)) throw err;
          // #1190: a RETRYABLE mid-stream failure (GPU timeout / saturated
          // pool) must not trigger the classic re-render. The backend already
          // spent the full budget on this text, and the abandoned job keeps
          // holding the device until it drains — re-synthesizing the whole
          // text right now makes the user wait out a second timeout to see the
          // same error. Surface the backend's actionable message instead.
          // Non-retryable failures (transport drop, Web Audio glitch) keep the
          // classic fallback: there the whole-file path genuinely can succeed.
          if (!shouldFallbackToClassic(err)) {
            addBreadcrumb('generate:stream-retryable-abort');
            throw err;
          }
          console.warn(
            'Streaming preview failed mid-stream; falling back to the classic generate:',
            err?.message || err,
          );
          addBreadcrumb('generate:stream-fallback');
        }
      }

      if (!streamed) {
        const response = await generateSpeech(formData, { signal: ac.signal });
        const reader = response.body.getReader();
        const chunks = [];
        let receivedLength = 0;
        const contentLength = parseInt(response.headers.get('Content-Length') || '0', 10);

        applyResponseHeaders(response);

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          receivedLength += value.length;
          if (contentLength > 0) {
            setProgressPct(Math.round((receivedLength / contentLength) * 100));
          }
        }

        const blob = new Blob(chunks, { type: 'audio/wav' });
        // #1032: honor the Settings → Appearance "Auto-play preview" pref here
        // too. #667 added the toggle ("play the output as soon as a render
        // finishes") but only wired the WaveformPlayer preview sites — the main
        // generate path kept auto-playing unconditionally. getState() (not a
        // subscription) so the freshest value is read at completion time.
        if (useAppStore.getState().autoPlayPreview) {
          try {
            await playBlobAudio(blob, { label: t('player.generated_audio') });
          } catch (e) {}
        }
      }

      await loadHistory();
      setSidebarTab('history');
      playPing();
    } catch (err) {
      // Timeouts are user-recoverable (retry / shorter input) — plain toast.
      // Real generation failures get the "Report this bug" action.
      if (err instanceof TtsGenerationBusyError) {
        toast(t('tts_errors.generation_in_progress'), { icon: '⏳' });
      } else if (err?.name === 'AbortError') {
        toast.error(t('tts_errors.timeout'));
      } else if (modelNotDownloadedPayload(err)) {
        toastModelNotDownloaded(modelNotDownloadedPayload(err));
      } else {
        toastErrorWithReport(t('tts_errors.error_prefix', { message: err.message }), err);
      }
    } finally {
      if (abortTimer) clearTimeout(abortTimer);
      clearInterval(timerRef.current);
      setIsGenerating(false);
    }
  }, [
    text,
    defineMethod,
    selectedProfile,
    refAudio,
    refText,
    language,
    instruct,
    steps,
    cfg,
    speed,
    denoise,
    tShift,
    posTemp,
    classTemp,
    layerPenalty,
    postprocess,
    duration,
    vdStates,
    keepSeed,
    designSeed,
    setDesignSeed,
    loadHistory,
    setSidebarTab,
  ]);

  return {
    refAudio,
    setRefAudio,
    pendingTrimFile,
    setPendingTrimFile,
    isGenerating,
    generationTime,
    textAreaRef,
    ingestRefAudio,
    insertTag,
    applyPreset,
    handleGenerate,
  };
}
