import { useState, useRef, useEffect, useCallback } from 'react';
import { useAppStore } from '../store';
import {
  dubUpload,
  dubIngestUrl,
  dubAbort as apiDubAbort,
  dubCleanupSegments,
  dubTranslate,
  dubGenerate,
  tasksStreamUrl,
  tasksCancel,
  transcribeStreamUrl,
  dubImportSrt,
  DUB_COOKIE_TRANSPORT_ERROR,
  DUB_COOKIE_SIZE_ERROR,
} from '../api/dub';
import { dialectMatchesLang } from '../api/dialects';
import {
  segmentGenInputs,
  applySpeakerCloneDefaults,
  cueSourceId,
  withoutCueSource,
} from '../utils/segments';
import { apiPost, apiFetch } from '../api/client';
import { API } from '../api/client';
import { streamDropError } from '../utils/backendCrash';
import { playPing } from '../utils/media';
import { toast } from 'react-hot-toast';
import { toastErrorWithReport } from '../utils/errorToast';
import { asrMissingPayload, installRecommendedAsr } from '../utils/asrModelMissing';
import { cancelInstallModel } from '../api/setup';
import { addBreadcrumb } from '../utils/breadcrumbs';
import { recordValueMoment } from '../utils/donationMoments';
import i18next from 'i18next';
const t = i18next.t.bind(i18next);

/**
 * True when a dub error means the job no longer exists on the backend — a
 * persisted `dubJobId` outlived the backend's in-memory job store (the backend
 * restarted, or the job was cleaned up). The backend reports this as
 * "Job not found. It may have been cleaned up or was never created." (dub_core)
 * or "This dub session has expired or was never created…" (dub_generate). That
 * is an EXPECTED stale-session state on resume/retry, not a bug — so the UI
 * should reset to a clean slate and invite a fresh upload rather than fire an
 * error toast with a "report a bug" prompt (#660). Exported + pure for testing.
 */
export function isExpiredDubJobError(err) {
  const m = (err && err.message ? String(err.message) : '').toLowerCase();
  return (
    m.includes('job not found') ||
    m.includes('session has expired') ||
    m.includes('was never created') ||
    m.includes('cleaned up')
  );
}

export function shouldQueueSrtImport(dubStep, sourceAnalysisComplete = false) {
  return !sourceAnalysisComplete && ['uploading', 'transcribing'].includes(dubStep);
}

export async function applyQueuedSrtImport(pendingRef, jobId, signal, performImport) {
  const file = pendingRef.current;
  if (!file) return false;
  const imported = await performImport(jobId, file, signal);
  if (imported && pendingRef.current === file) pendingRef.current = null;
  return imported;
}

/**
 * Encapsulates the entire dub pipeline workflow:
 *   upload → prep → transcribe → translate → generate → export
 *
 * Extracts ~700 LOC of handler logic from App.jsx.
 */
export default function useDubWorkflow({
  loadProjects,
  loadProfiles,
  loadDubHistory,
  setLastGenFingerprints,
}) {
  const dubJobId = useAppStore((s) => s.dubJobId);
  const setDubJobId = useAppStore((s) => s.setDubJobId);
  const dubStep = useAppStore((s) => s.dubStep);
  const setDubStep = useAppStore((s) => s.setDubStep);
  const dubSegments = useAppStore((s) => s.dubSegments);
  const setDubSegments = useAppStore((s) => s.setDubSegments);
  const dubLang = useAppStore((s) => s.dubLang);
  const dubLangCode = useAppStore((s) => s.dubLangCode);
  const dubSourceLangCode = useAppStore((s) => s.dubSourceLangCode);
  const dubInstruct = useAppStore((s) => s.dubInstruct);
  const setDubFilename = useAppStore((s) => s.setDubFilename);
  const setDubDuration = useAppStore((s) => s.setDubDuration);
  const setDubError = useAppStore((s) => s.setDubError);
  const setDubFailure = useAppStore((s) => s.setDubFailure);
  const setDubTracks = useAppStore((s) => s.setDubTracks);
  const setDubTranscript = useAppStore((s) => s.setDubTranscript);
  const setDubProgress = useAppStore((s) => s.setDubProgress);
  const setIsTranslating = useAppStore((s) => s.setIsTranslating);
  const dubTaskId = useAppStore((s) => s.dubTaskId);
  const setDubTaskId = useAppStore((s) => s.setDubTaskId);
  const setDubPrepStage = useAppStore((s) => s.setDubPrepStage);
  const setDubPrepProgress = useAppStore((s) => s.setDubPrepProgress);
  const setSpeakerClones = useAppStore((s) => s.setSpeakerClones);
  const setPreviewSegIds = useAppStore((s) => s.setPreviewSegIds);
  const steps = useAppStore((s) => s.steps);
  const cfg = useAppStore((s) => s.cfg);
  const speed = useAppStore((s) => s.speed);
  const translateQuality = useAppStore((s) => s.translateQuality);
  const condenseSuggest = useAppStore((s) => s.condenseSuggest);
  const timingStrategy = useAppStore((s) => s.timingStrategy);
  const fitOptions = useAppStore((s) => s.fitOptions);
  const voiceMatch = useAppStore((s) => s.voiceMatch);
  const glossaryTerms = useAppStore((s) => s.glossaryTerms);
  const dubDialect = useAppStore((s) => s.dubDialect);

  const [translateProvider, setTranslateProvider] = useState('argos');
  const [showTranscript, setShowTranscript] = useState(false);
  const [previewAudios, setPreviewAudios] = useState({});
  const [transcribeStart, setTranscribeStart] = useState(null);
  const [transcribeElapsed, setTranscribeElapsed] = useState(0);
  // Real fraction of chunks transcribed, straight from the backend's `segments`
  // events. The overlay used to *invent* an ETA from the video's duration
  // instead (#1127) — it assumed ~20x-realtime transcription, which is roughly
  // true on a CUDA GPU and 50x wrong on a CPU, so it showed "~0s remaining" for
  // 45 minutes. A measured fraction is the only thing that can't lie.
  const [transcribeProgress, setTranscribeProgress] = useState(0);
  const [asrInstall, setAsrInstall] = useState(null);

  const dubAbortCtrlRef = useRef(null);
  const dubClientJobIdRef = useRef(null);
  const asrInstallTaskRef = useRef(null);
  const retryTranscribeRef = useRef(null);
  const pendingSrtRef = useRef(null);

  const _showMissingAsr = useCallback(
    (payload) => {
      const rec = payload?.recommended;
      setAsrInstall({
        phase: 'missing',
        percent: null,
        payload,
        repoId: rec?.repo_id || '',
        label: rec?.label || rec?.repo_id || '',
        sizeGb: rec?.size_gb,
        jobId: useAppStore.getState().dubJobId,
      });
      setDubError(t('asr_missing.message'));
      setDubStep('idle');
      useAppStore.getState().dismissPill();
    },
    [setDubError, setDubStep],
  );

  const handleInstallMissingAsr = useCallback(async () => {
    if (!asrInstall?.payload || asrInstall.phase === 'installing') return;
    const initiatingJobId = asrInstall.jobId;
    const ctrl = new AbortController();
    const installTask = {
      ctrl,
      repoId: asrInstall.repoId,
      jobId: initiatingJobId,
    };
    asrInstallTaskRef.current = installTask;
    setDubError('');
    setDubStep('installing-asr');
    setAsrInstall((current) => ({ ...current, phase: 'installing', percent: 0 }));
    useAppStore
      .getState()
      .showPill('loading-model', t('dub.install_progress', { engine: asrInstall.label }), {
        progress: 0,
        cancellable: true,
        homeMode: 'dub',
      });
    try {
      await installRecommendedAsr(asrInstall.payload, {
        signal: ctrl.signal,
        onProgress: ({ percent }) => {
          setAsrInstall((current) =>
            current ? { ...current, phase: 'installing', percent } : current,
          );
          useAppStore.getState().setPillProgress(percent);
        },
      });
      if (useAppStore.getState().dubJobId !== initiatingJobId) return;
      setAsrInstall(null);
      setDubError('');
      setDubStep('idle');
      useAppStore.getState().completePill(t('dub.install_ok', { engine: asrInstall.label }));
      await retryTranscribeRef.current?.();
    } catch (error) {
      if (useAppStore.getState().dubJobId !== initiatingJobId) return;
      const aborted = error?.name === 'AbortError';
      const message = aborted
        ? t('dub_workflow.retry_cancelled')
        : t('asr_missing.install_failed', { message: error?.message || String(error) });
      setDubStep('idle');
      setDubError(message);
      setAsrInstall((current) =>
        current ? { ...current, phase: 'missing', percent: null } : current,
      );
      if (aborted) useAppStore.getState().dismissPill();
      else useAppStore.getState().errorPill(message);
    } finally {
      if (asrInstallTaskRef.current === installTask) asrInstallTaskRef.current = null;
    }
  }, [asrInstall, setDubError, setDubStep]);

  // Reset a stale dub session (the persisted job is gone server-side, #660):
  // clear the dead id/state, drop any pill, and prompt a fresh upload with a
  // calm info toast — never a bug-report prompt, since this is expected.
  const _resetStaleDubSession = useCallback(() => {
    pendingSrtRef.current = null;
    setDubJobId('');
    setDubTaskId('');
    setDubSegments([]);
    setDubError('');
    setAsrInstall(null);
    setDubStep('idle');
    setTranscribeStart(null);
    try {
      useAppStore.getState().dismissPill();
    } catch {
      /* no pill */
    }
    toast(
      t(
        'dub_workflow.session_expired',
        'This dub session expired or was cleaned up — re-upload your video to start a new one.',
      ),
      { icon: 'ℹ️', duration: 7000 },
    );
  }, [setDubJobId, setDubTaskId, setDubSegments, setDubError, setDubStep]);

  const _performSrtImport = useCallback(
    async (jobId, file, signal) => {
      if (
        signal?.aborted ||
        useAppStore.getState().dubJobId !== jobId ||
        pendingSrtRef.current !== file
      )
        return false;
      try {
        setDubError('');
        const res = await dubImportSrt(jobId, file, { signal });
        if (
          signal?.aborted ||
          useAppStore.getState().dubJobId !== jobId ||
          pendingSrtRef.current !== file
        )
          return false;
        const segs = (res && res.segments) || [];
        setDubSegments(
          segs.map((s) => ({
            ...s,
            id: s.id != null ? String(s.id) : String(Math.random()),
          })),
        );
        setDubStep('editing');
        const stats = res?.stats || {};
        const noteParts = [
          t('dub_workflow.imported_cues', {
            count: stats.imported ?? segs.length,
            file: file.name || '.srt',
          }),
        ];
        if (stats.skipped_malformed)
          noteParts.push(t('dub_workflow.skipped_malformed', { count: stats.skipped_malformed }));
        if (stats.dropped_overlap)
          noteParts.push(t('dub_workflow.dropped_overlap', { count: stats.dropped_overlap }));
        if (stats.clamped_to_duration)
          noteParts.push(
            t('dub_workflow.clamped_to_duration', { count: stats.clamped_to_duration }),
          );
        toast.success(noteParts.join(' · '), { duration: 6000 });
        loadProjects();
        return true;
      } catch (err) {
        if (err?.name === 'AbortError') throw err;
        if (
          signal?.aborted ||
          useAppStore.getState().dubJobId !== jobId ||
          pendingSrtRef.current !== file
        )
          return false;
        if (isExpiredDubJobError(err)) {
          _resetStaleDubSession();
          return false;
        }
        const msg = err?.message || t('dub_workflow.srt_import_failed');
        setDubError(msg);
        setDubStep('editing');
        toast.error(msg);
        return false;
      }
    },
    [setDubError, setDubSegments, setDubStep, loadProjects, _resetStaleDubSession],
  );

  const _applyQueuedSrt = useCallback(
    async (jobId, ctrl) => {
      let queuedSrt = pendingSrtRef.current;
      if (!queuedSrt) {
        setDubStep('editing');
        return true;
      }
      while (true) {
        const imported = await applyQueuedSrtImport(
          pendingSrtRef,
          jobId,
          ctrl?.signal,
          _performSrtImport,
        );
        if (ctrl?.signal.aborted || useAppStore.getState().dubJobId !== jobId) return false;
        const replacement = pendingSrtRef.current;
        if (!replacement) return imported;
        if (replacement === queuedSrt) return false;
        queuedSrt = replacement;
      }
    },
    [setDubStep, _performSrtImport],
  );

  // Timer for transcribe elapsed
  useEffect(() => {
    if (!transcribeStart) {
      setTranscribeElapsed(0);
      setTranscribeProgress(0);
      return;
    }
    const iv = setInterval(
      () => setTranscribeElapsed(Math.floor((Date.now() - transcribeStart) / 1000)),
      500,
    );
    return () => clearInterval(iv);
  }, [transcribeStart]);

  // ── SSE: wait for transcription stream ──
  const _waitForTranscribe = useCallback(
    (jobId, ctrl) =>
      new Promise((resolve, reject) => {
        // Read the optional speaker-count hint at stream-open time (#274) so the
        // user's choice for this job is honoured without threading it through the
        // three call sites. null → pyannote auto-detect.
        const numSpeakers = useAppStore.getState().dubNumSpeakers;
        const evt = new EventSource(transcribeStreamUrl(jobId, numSpeakers));
        let gotFinal = false;
        // Latch the real cause from a named `error` event. EventSource also fires
        // its *native* error (no `data`) on any connection close, which can race
        // and win against the backend's structured `error` event — if it did, we
        // used to throw away the real cause and show the generic "stream dropped …
        // ASR backend failed" message (#578). Holding the detail here lets the
        // native-close handler surface the real cause instead.
        let lastErrorDetail = null;
        const close = () => {
          try {
            evt.close();
          } catch {}
        };
        const onAbortSignal = () => {
          close();
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        };
        ctrl.signal.addEventListener('abort', onAbortSignal, { once: true });

        evt.addEventListener('start', () => {});
        evt.addEventListener('segments', (e) => {
          try {
            const m = JSON.parse(e.data);
            const incoming = (m.segments || []).map((s, i) => ({
              ...s,
              id: s.id != null ? String(s.id) : `c${m.chunk}-${i}`,
              text_original: s.text_original || s.text || '',
            }));
            setDubSegments((prev) => [...prev, ...incoming]);
            if (typeof m.progress === 'number' && m.progress > 0) {
              setTranscribeProgress(Math.min(1, m.progress));
            }
          } catch (err) {
            /* ignore parse errors */
          }
        });
        evt.addEventListener('final', (e) => {
          try {
            const m = JSON.parse(e.data);
            gotFinal = true;
            const normalized = (m.segments || []).map((s, i) => ({
              ...s,
              id: s.id != null ? String(s.id) : String(i),
              text_original: s.text_original || s.text || '',
            }));
            // #486: bind each segment to its detected speaker's clone up front, so
            // a 2-speaker dub doesn't land every row on "Default".
            const castSources = m.cast_sources || m.speaker_clones || {};
            setDubSegments(applySpeakerCloneDefaults(normalized, castSources));
            setDubTranscript(m.full_transcript || '');
            if (castSources && typeof castSources === 'object') {
              setSpeakerClones(castSources);
            }
          } catch (err) {
            console.warn('Transcribe SSE handler failed:', err);
          }
        });
        evt.addEventListener('warning', (e) => {
          try {
            const m = JSON.parse(e.data);
            if (m && m.detail) {
              toast(m.detail, { icon: '⚠️', duration: 8000 });
            }
          } catch {
            /* malformed warning event */
          }
        });
        evt.addEventListener('done', () => {
          close();
          ctrl.signal.removeEventListener('abort', onAbortSignal);
          resolve();
        });
        evt.addEventListener('aborted', () => {
          close();
          ctrl.signal.removeEventListener('abort', onAbortSignal);
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        });
        evt.addEventListener('error', (e) => {
          // A named `error` event carries the real cause in `data.detail`. Latch
          // it AND reject with it. The backend now always follows it with `done`,
          // but we reject eagerly here so the cause survives even if the native
          // connection-drop error arrives first/concurrently (#578).
          try {
            const m = e.data ? JSON.parse(e.data) : null;
            if (m && m.detail) {
              lastErrorDetail = m.detail;
              close();
              // Typed "no ASR model installed" preflight (TTS-only install):
              // tag the rejection so the catch sites can render the one-click
              // download CTA instead of the generic report toast.
              const err = new Error(m.detail);
              if (m.error === 'asr_model_missing') err.asrModelMissing = m;
              reject(err);
              return;
            }
          } catch {
            /* malformed error payload — fall through */
          }
          // No fresh detail on this event (native EventSource connection error).
          // If we already saw a structured error, surface its cause, not the
          // generic message.
          if (lastErrorDetail) {
            close();
            reject(new Error(lastErrorDetail));
            return;
          }
          if (gotFinal) {
            close();
            resolve();
            return;
          }
          close();
          // The stream died with NO terminal event, which the backend contract
          // forbids — so the backend PROCESS went away (on small GPUs, a VRAM
          // abort while loading ASR is the usual trigger). Ask the shell's crash
          // forensics rather than guessing "ASR failed to load" (#1062).
          // The fallback no longer names a cause. streamDropError() checks the
          // crash forensics AND whether the backend is still answering, and
          // only this message survives when both are inconclusive — asserting
          // "ASR failed to load" there sent #1242's reporter after a model
          // that had loaded fine.
          streamDropError(t('dub_workflow.transcribe_stream_dropped')).then(reject, reject);
        });
      }),
    [setDubSegments, setDubTranscript, setSpeakerClones],
  );

  // ── SSE: wait for prep pipeline ──
  const _waitForPrep = useCallback(
    (taskId, ctrl) =>
      new Promise((resolve, reject) => {
        const evt = new EventSource(tasksStreamUrl(taskId));
        const close = () => {
          try {
            evt.close();
          } catch {}
        };
        const onAbort = () => {
          close();
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        };
        ctrl.signal.addEventListener('abort', onAbort, { once: true });
        let lastData = null;
        evt.onmessage = (e) => {
          if (!e.data) return;
          let m;
          try {
            m = JSON.parse(e.data);
          } catch {
            return;
          }
          lastData = m;
          switch (m.type) {
            case 'download_start':
              setDubPrepStage('download');
              setDubPrepProgress({
                percent: null,
                speedBps: null,
                etaS: null,
                stageStartedAt: Date.now(),
              });
              break;
            case 'download_progress':
              setDubPrepProgress((prev) => ({
                ...prev,
                percent: typeof m.percent === 'number' ? m.percent : prev.percent,
                speedBps: typeof m.speed_bps === 'number' ? m.speed_bps : null,
                etaS: typeof m.eta_s === 'number' ? m.eta_s : null,
              }));
              break;
            case 'download_done':
              if (m.filename) setDubFilename(m.filename);
              break;
            case 'extract_start':
              setDubPrepStage('extract');
              setDubPrepProgress({
                percent: null,
                speedBps: null,
                etaS: null,
                stageStartedAt: Date.now(),
              });
              break;
            case 'extract_done':
              if (m.job_id) setDubJobId(m.job_id);
              if (typeof m.duration === 'number') setDubDuration(m.duration);
              if (m.filename) setDubFilename(m.filename);
              break;
            case 'demucs_start':
              setDubPrepStage('demucs');
              setDubPrepProgress({
                percent: null,
                speedBps: null,
                etaS: null,
                stageStartedAt: Date.now(),
              });
              break;
            case 'demucs_progress':
              setDubPrepProgress((prev) => ({
                ...prev,
                percent: typeof m.percent === 'number' ? m.percent : prev.percent,
              }));
              break;
            case 'demucs_done':
              break;
            case 'scene_start':
              setDubPrepStage('scene');
              setDubPrepProgress({
                percent: null,
                speedBps: null,
                etaS: null,
                stageStartedAt: Date.now(),
              });
              break;
            case 'scene_done':
              break;
            case 'cached':
              setDubPrepStage('cached');
              setDubPrepProgress({
                percent: 100,
                speedBps: null,
                etaS: null,
                stageStartedAt: Date.now(),
              });
              break;
            case 'ready':
              close();
              ctrl.signal.removeEventListener('abort', onAbort);
              resolve(m);
              return;
            case 'error': {
              close();
              ctrl.signal.removeEventListener('abort', onAbort);
              // plan-04 (#131): the backend now always sends a non-empty reason;
              // capture the structured failure so the UI can show a hint + docs link
              // + copyable diagnostic instead of a bare "unknown error".
              const reason = m.reason || m.error || 'unknown error';
              setDubFailure({
                reason,
                errorClass: m.error_class,
                stage: m.stage,
                hint: m.hint,
                docsTopic: m.docs_topic,
                diagnostic: m.diagnostic,
              });
              reject(new Error(`${m.stage || 'prep'}: ${reason}`));
              return;
            }
            case 'cancelled':
              close();
              ctrl.signal.removeEventListener('abort', onAbort);
              reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
              return;
            default:
              break;
          }
        };
        evt.onerror = () => {
          if (evt.readyState === EventSource.CLOSED) {
            close();
            ctrl.signal.removeEventListener('abort', onAbort);
            if (lastData && lastData.type === 'ready') resolve(lastData);
            // Same class as the transcribe drop (#1062): a prep stream that
            // closes with no terminal event means the backend died under it.
            else streamDropError('prep stream closed unexpectedly').then(reject, reject);
          }
        };
      }),
    [
      setDubPrepStage,
      setDubPrepProgress,
      setDubJobId,
      setDubDuration,
      setDubFilename,
      setDubFailure,
    ],
  );

  // ── Handlers ──
  const handleDubUpload = useCallback(
    async (dubVideoFile) => {
      if (!dubVideoFile) return;
      const pendingInstall = asrInstallTaskRef.current;
      pendingInstall?.ctrl.abort();
      if (pendingInstall?.repoId) {
        void cancelInstallModel(pendingInstall.repoId).catch(() => {});
      }
      addBreadcrumb('dub:upload');
      setDubStep('uploading');
      setAsrInstall(null);
      setDubError('');
      setDubFailure(null);
      setDubTracks([]);
      pendingSrtRef.current = null;
      setDubPrepStage('download');
      setDubPrepProgress({ percent: null, speedBps: null, etaS: null, stageStartedAt: Date.now() });
      const ctrl = new AbortController();
      dubAbortCtrlRef.current = ctrl;
      const clientJobId = Math.random().toString(36).slice(2, 10);
      dubClientJobIdRef.current = clientJobId;
      setDubJobId(clientJobId);
      const inputType = useAppStore.getState().dubInputType || 'video'; // #119
      useAppStore
        .getState()
        .showPill(
          'loading-model',
          inputType === 'audio'
            ? t('dub_workflow.preparing_audio')
            : t('dub_workflow.preparing_video'),
          { cancellable: true, homeMode: 'dub' },
        );
      try {
        const data = await dubUpload(dubVideoFile, clientJobId, {
          signal: ctrl.signal,
          inputType,
          sourceLang: dubSourceLangCode,
        });
        setDubJobId(data.job_id);
        if (data.filename) setDubFilename(data.filename);
        setDubTaskId(data.task_id);
        setDubPrepStage('extract');
        useAppStore
          .getState()
          .showPill('loading-model', t('dub_workflow.extracting_audio_scenes'), {
            cancellable: true,
            homeMode: 'dub',
          });
        await _waitForPrep(data.task_id, ctrl);
        setDubStep('transcribing');
        setDubPrepStage(null);
        setTranscribeStart(Date.now());
        setDubSegments([]);
        useAppStore.getState().showPill('transcribing', t('dub_workflow.transcribing_audio'), {
          cancellable: true,
          homeMode: 'dub',
        });
        await _waitForTranscribe(data.job_id, ctrl);
        setTranscribeStart(null);
        await _applyQueuedSrt(data.job_id, ctrl);
        useAppStore.getState().completePill(t('dub_workflow.transcription_complete'));
        loadProjects();
        loadProfiles();
      } catch (err) {
        setDubPrepStage(null);
        if (err.name === 'AbortError') {
          toast(t('dub_workflow.upload_cancelled'));
          setDubStep('idle');
          useAppStore.getState().dismissPill();
        } else if (isExpiredDubJobError(err)) {
          _resetStaleDubSession();
        } else if (asrMissingPayload(err)) {
          // Typed preflight: no ASR model installed → download CTA, not a report.
          _showMissingAsr(asrMissingPayload(err));
        } else {
          setDubError(err.message);
          setDubStep('idle');
          toastErrorWithReport(t('dub_workflow.upload_failed', { message: err.message }), err);
          useAppStore.getState().errorPill(err.message);
        }
        setTranscribeStart(null);
      } finally {
        dubAbortCtrlRef.current = null;
      }
    },
    [
      setDubStep,
      setDubError,
      setDubFailure,
      setDubTracks,
      setDubPrepStage,
      setDubJobId,
      setDubFilename,
      setDubTaskId,
      setDubSegments,
      _waitForPrep,
      _waitForTranscribe,
      _applyQueuedSrt,
      loadProjects,
      loadProfiles,
      _resetStaleDubSession,
      _showMissingAsr,
      dubSourceLangCode,
    ],
  );

  const handleDubIngestUrl = useCallback(
    async (url, opts = {}) => {
      const clean = (url || '').trim();
      if (!clean) return;
      const pendingInstall = asrInstallTaskRef.current;
      pendingInstall?.ctrl.abort();
      if (pendingInstall?.repoId) {
        void cancelInstallModel(pendingInstall.repoId).catch(() => {});
      }
      addBreadcrumb('dub:ingest-url');
      setDubStep('uploading');
      setAsrInstall(null);
      setDubError('');
      setDubFailure(null);
      setDubTracks([]);
      pendingSrtRef.current = null;
      setDubPrepStage('download');
      setDubPrepProgress({ percent: null, speedBps: null, etaS: null, stageStartedAt: Date.now() });
      const ctrl = new AbortController();
      dubAbortCtrlRef.current = ctrl;
      const clientJobId = Math.random().toString(36).slice(2, 10);
      dubClientJobIdRef.current = clientJobId;
      setDubJobId(clientJobId);
      useAppStore.getState().showPill('loading-model', t('dub_workflow.downloading_video'), {
        cancellable: true,
        homeMode: 'dub',
      });
      try {
        const data = await dubIngestUrl(clean, clientJobId, {
          signal: ctrl.signal,
          fetchSubs: !!opts.fetchSubs,
          subLangs: opts.subLangs,
          cookieFile: opts.cookieFile,
          sourceLang: dubSourceLangCode,
        });
        setDubJobId(data.job_id);
        setDubTaskId(data.task_id);
        useAppStore
          .getState()
          .showPill('loading-model', t('dub_workflow.extracting_audio_scenes'), {
            cancellable: true,
            homeMode: 'dub',
          });
        await _waitForPrep(data.task_id, ctrl);
        setDubStep('transcribing');
        setDubPrepStage(null);
        setTranscribeStart(Date.now());
        setDubSegments([]);
        useAppStore.getState().showPill('transcribing', t('dub_workflow.transcribing_audio'), {
          cancellable: true,
          homeMode: 'dub',
        });
        await _waitForTranscribe(data.job_id, ctrl);
        setTranscribeStart(null);
        await _applyQueuedSrt(data.job_id, ctrl);
        useAppStore.getState().completePill(t('dub_workflow.transcription_complete'));
        loadProjects();
        loadProfiles();
        toast.success(t('dub_workflow.ingested', { url: clean.slice(0, 60) }));
      } catch (err) {
        setDubPrepStage(null);
        if (err.name === 'AbortError') {
          toast(t('dub_workflow.ingest_cancelled'));
          setDubStep('idle');
          useAppStore.getState().dismissPill();
        } else if (isExpiredDubJobError(err)) {
          _resetStaleDubSession();
        } else if (asrMissingPayload(err)) {
          _showMissingAsr(asrMissingPayload(err));
        } else {
          const cookieErrorKey =
            err?.code === DUB_COOKIE_TRANSPORT_ERROR
              ? 'dub.cookie_transport_error'
              : err?.code === DUB_COOKIE_SIZE_ERROR
                ? 'dub.cookie_size_error'
                : null;
          const message = cookieErrorKey ? t(cookieErrorKey) : err.message;
          setDubError(message);
          setDubStep('idle');
          toastErrorWithReport(t('dub_workflow.ingest_failed', { message }), err);
          useAppStore.getState().errorPill(message);
        }
        setTranscribeStart(null);
      } finally {
        dubAbortCtrlRef.current = null;
      }
    },
    [
      setDubStep,
      setDubError,
      setDubFailure,
      setDubTracks,
      setDubPrepStage,
      setDubJobId,
      setDubTaskId,
      setDubSegments,
      _waitForPrep,
      _waitForTranscribe,
      _applyQueuedSrt,
      loadProjects,
      loadProfiles,
      _resetStaleDubSession,
      _showMissingAsr,
      dubSourceLangCode,
    ],
  );

  const handleDubAbort = useCallback(async () => {
    pendingSrtRef.current = null;
    const pendingInstall = asrInstallTaskRef.current;
    if (pendingInstall) {
      pendingInstall.ctrl.abort();
      if (pendingInstall.repoId) {
        await cancelInstallModel(pendingInstall.repoId).catch(() => {});
      }
      return;
    }
    const jobId = dubClientJobIdRef.current || dubJobId;
    if (dubAbortCtrlRef.current) dubAbortCtrlRef.current.abort();
    if (jobId) await apiDubAbort(jobId);
  }, [dubJobId]);

  const handleDubRetryTranscribe = useCallback(async () => {
    if (!dubJobId) return;
    setAsrInstall(null);
    const ctrl = new AbortController();
    dubAbortCtrlRef.current = ctrl;
    setDubError('');
    setDubSegments([]);
    setDubStep('transcribing');
    setTranscribeStart(Date.now());
    try {
      await _waitForTranscribe(dubJobId, ctrl);
      setTranscribeStart(null);
      await _applyQueuedSrt(dubJobId, ctrl);
      loadProjects();
    } catch (err) {
      setTranscribeStart(null);
      if (err.name === 'AbortError') {
        toast(t('dub_workflow.retry_cancelled'));
        setDubStep('idle');
      } else if (isExpiredDubJobError(err)) {
        _resetStaleDubSession();
      } else if (asrMissingPayload(err)) {
        _showMissingAsr(asrMissingPayload(err));
      } else {
        setDubError(err.message);
        setDubStep('idle');
        toastErrorWithReport(t('dub_workflow.transcription_failed', { message: err.message }), err);
      }
    } finally {
      dubAbortCtrlRef.current = null;
    }
  }, [
    dubJobId,
    setDubError,
    setDubSegments,
    setDubStep,
    _waitForTranscribe,
    _applyQueuedSrt,
    loadProjects,
    _resetStaleDubSession,
    _showMissingAsr,
  ]);
  useEffect(() => {
    retryTranscribeRef.current = handleDubRetryTranscribe;
  }, [handleDubRetryTranscribe]);

  const handleDubImportSrt = useCallback(
    async (file, { jobId = dubJobId, sourceAnalysisComplete = false, signal } = {}) => {
      if (!jobId) {
        toast.error(t('dub_workflow.import_srt_no_job'));
        return;
      }
      if (!file) return;
      if (shouldQueueSrtImport(dubStep, sourceAnalysisComplete)) {
        pendingSrtRef.current = file;
        toast(t('dub_workflow.import_srt_after_speakers'));
        return false;
      }
      pendingSrtRef.current = file;
      return applyQueuedSrtImport(pendingSrtRef, jobId, signal, _performSrtImport);
    },
    [dubJobId, dubStep, _performSrtImport],
  );

  const handleCleanupSegments = useCallback(async () => {
    if (!dubJobId || !dubSegments.length) return;
    const before = dubSegments.length;
    try {
      const data = await dubCleanupSegments(dubJobId);
      setDubSegments(data.segments || []);
      const delta = before - (data.after ?? data.segments.length);
      toast.success(
        delta > 0 ? t('dub_workflow.cleaned', { count: delta }) : t('dub_workflow.segments_clean'),
      );
    } catch (err) {
      toast.error(t('dub_workflow.cleanup_failed', { message: err.message }));
    }
  }, [dubJobId, dubSegments, setDubSegments]);

  // `langOverride` (optional ISO code string) is the multi-language batch path:
  // the generate loop translates INTO each pick before dubbing it. No-arg calls
  // (the Translate All button, the review checkpoint) behave exactly as before
  // — the guard also shields the direct `onClick={handleTranslateAll}` usages,
  // where the first argument is a click event, not a language.
  // Resolves `true` when a translation landed in the segments, `false` when the
  // request failed or nothing got translated — the batch loop skips generating
  // that language rather than rendering a wrong-language track.
  const handleTranslateAll = useCallback(
    async (langOverride) => {
      const options =
        langOverride && typeof langOverride === 'object' && !('preventDefault' in langOverride)
          ? langOverride
          : {};
      const targetLang =
        typeof langOverride === 'string' && langOverride
          ? langOverride
          : options.langOverride || dubLangCode;
      // Snapshot segments at call time: inside the multi-language loop the
      // click-time closure is stale after the previous pick's translate pass.
      const allSegments = useAppStore.getState().dubSegments;
      const retryFailed = !!options.retryFailed;
      const segs = retryFailed
        ? allSegments.filter(
            (segment) =>
              segment.translate_errors?.[targetLang] ||
              (!segment.translate_errors && segment.translate_error),
          )
        : allSegments;
      if (!segs.length || !targetLang) return false;
      setIsTranslating(true);
      // Root cause of the "sticky TRANSLATION FAILED banner": a new translate
      // attempt never cleared the previous failure, so a stale 400 survived even
      // a successful retry. Clear it up front — the whole class of translate/
      // pipeline error banners should reset on the next relevant action.
      setDubError('');
      let ok = false;
      try {
        const data = await dubTranslate({
          segments: segs.map((s) => ({
            id: String(s.id),
            text: s.text_original && s.text_original.trim() ? s.text_original : s.text,
            target_lang: s.target_lang,
            direction: s.direction || undefined,
            slot_seconds: s.end != null && s.start != null ? s.end - s.start : undefined,
            // Timeline position — lets the backend's duration planner borrow
            // silence from the gap to the next segment when classifying
            // fits/tight/impossible before any GPU time is spent.
            start: s.start != null ? s.start : undefined,
            end: s.end != null ? s.end : undefined,
          })),
          target_lang: targetLang,
          source_lang:
            dubSourceLangCode && dubSourceLangCode !== 'auto' ? dubSourceLangCode : undefined,
          provider: translateProvider,
          quality: translateQuality,
          // Lets the backend resolve the ASR-detected source language AND
          // cache the auto-glossary context on the job (survives restarts).
          job_id: dubJobId || undefined,
          // Two-stage LLM translation quality (LLM engine only; MT engines
          // ignore both): full-transcript auto-glossary + per-segment
          // reflect/rewrite polish. Read at call time — the multi-language
          // loop reuses this callback long after the click-time closure.
          auto_glossary: useAppStore.getState().autoGlossary,
          reflect: useAppStore.getState().reflectPass,
          // Opt-in (default OFF): ask the LLM for shorter rewrites of
          // segments the planner marks impossible — suggestions only.
          condense: condenseSuggest || undefined,
          // #280: regional dialect — only sent when it matches the target
          // language so a stale "es-AR" never rides on a French translate.
          dialect: dialectMatchesLang(dubDialect, targetLang) ? dubDialect : undefined,
          glossary: glossaryTerms.length
            ? glossaryTerms.map((t) => ({ source: t.source, target: t.target, note: t.note || '' }))
            : undefined,
        });
        const translatedMap = {};
        const errors = [];
        const degraded = [];
        (data.translated || []).forEach((t) => {
          translatedMap[t.id] = t;
          if (t.error) errors.push({ id: t.id, error: t.error });
          // Degraded ≠ failed: the segment translated fine but the cinematic
          // polish pass was skipped (rate limit, budget, divergent reply) and
          // the literal text is in use. Counting these as errors used to show
          // "4/4 segment(s) failed" over a translate that succeeded.
          else if (t.degraded) degraded.push({ id: t.id, reason: t.degraded });
        });
        setDubSegments((prev) =>
          prev.map((s) => {
            const hit = translatedMap[s.id];
            if (!hit) return s;
            const gotText = !!(hit.text && hit.text.trim());
            const translateErrors = { ...s.translate_errors };
            if (!s.translate_errors && s.translate_error) {
              translateErrors[targetLang] = s.translate_error;
            }
            if (hit.error) translateErrors[targetLang] = hit.error;
            else delete translateErrors[targetLang];
            return {
              ...(gotText ? withoutCueSource(s) : s),
              text: gotText ? hit.text : s.text,
              // P1.2 — keep every language's translation, keyed by target.
              // `text` stays the currently-shown language (legacy single-slot
              // contract); switching the target language swaps from this map
              // instead of destroying the previous language's work.
              ...(gotText ? { translations: { ...s.translations, [targetLang]: hit.text } } : {}),
              ...(gotText ? { merge_parts: undefined } : {}),
              translate_error: hit.error || undefined,
              translate_errors: Object.keys(translateErrors).length ? translateErrors : undefined,
              translate_degraded: hit.degraded || undefined,
              translate_literal: hit.literal || undefined,
              translate_critique: hit.critique || undefined,
              // Carry over the predicted compression ratio so the per-row
              // badge + job-level compression warning can light up before
              // the user clicks Generate Dub.
              rate_ratio: hit.rate_ratio != null ? hit.rate_ratio : s.rate_ratio,
              rate_error: hit.rate_error || s.rate_error,
              // Pre-synthesis duration plan (fits/tight/impossible + optional
              // condensed-rewrite suggestion) — drives the row badge so a
              // doomed segment is visible before Generate Dub is clicked.
              plan: hit.plan != null ? hit.plan : s.plan,
            };
          }),
        );
        // "Translated" for the batch loop means at least one segment actually
        // got new text — an empty result or an all-errors result would make
        // the follow-up generate render the source language verbatim.
        const total = (data.translated || []).length;
        ok = total > 0 && errors.length < total;
        if (data.cinematic_skipped === 'no-llm-configured') {
          toast(t('dub_workflow.cinematic_no_llm'), { icon: 'ℹ️', duration: 8000 });
          // #372: the backend fell back to Fast — reflect that in the toggle so
          // the UI doesn't claim Cinematic while delivering Fast.
          useAppStore.getState().setTranslateQuality?.('fast');
        }
        // #280: the user picked a dialect but the chosen engine can't honor it
        // (Argos/NLLB/Google in Fast mode). Tell them how to make it count.
        // #372: skip when the cinematic toast above already fired — both at once
        // sent users in a circle ("pick Cinematic" ↔ "Cinematic needs an LLM").
        if (
          data.dialect &&
          data.dialect_applied === false &&
          data.cinematic_skipped !== 'no-llm-configured'
        ) {
          toast(t('dub_workflow.dialect_not_applied'), { icon: 'ℹ️', duration: 8000 });
        }
        if (errors.length) {
          const unique = [...new Set(errors.map((e) => e.error))];
          toast.error(
            t('dub_workflow.translate_errors', {
              errorCount: errors.length,
              totalCount: data.translated.length,
              firstError: unique[0].slice(0, 120),
            }),
            { duration: 6000 },
          );
        }
        if (degraded.length) {
          // Some segments missed the polish pass but translated fine — a
          // warning with the honest story, not a red "failed" over a success.
          // Fires ALONGSIDE the error toast when a response carries both:
          // real failures shouldn't erase the story of the rows that
          // succeeded plainly.
          const unique = [...new Set(degraded.map((d) => d.reason))];
          toast(
            t('dub_workflow.translate_degraded', {
              count: degraded.length,
              totalCount: data.translated.length,
              reason: unique[0].slice(0, 120),
            }),
            { icon: '⚠️', duration: 8000 },
          );
        }
        if (!errors.length && !degraded.length) {
          const qLabel =
            data.quality_used === 'cinematic' ? t('dub_workflow.translated_cinematic_suffix') : '';
          toast.success(
            t('dub_workflow.translated_segments', {
              count: data.translated.length,
              lang: data.target_lang,
            }) + qLabel,
          );
        }
      } catch (err) {
        setDubError(t('dub_workflow.translation_failed', { message: err.message }));
      }
      setIsTranslating(false);
      return ok;
    },
    [
      dubLangCode,
      dubSourceLangCode,
      dubDialect,
      dubJobId,
      translateProvider,
      translateQuality,
      condenseSuggest,
      glossaryTerms,
      setIsTranslating,
      setDubSegments,
      setDubError,
    ],
  );

  const handleDubGenerate = useCallback(
    async (opts = {}) => {
      addBreadcrumb('dub:generate');
      const regenOnly =
        Array.isArray(opts.regenOnly) && opts.regenOnly.length ? opts.regenOnly : null;
      const preview = !!opts.preview;
      // Batch multi-language: the caller loops over languages and passes each one
      // here, overriding the store's single selection (which is stale inside the
      // loop). Each run appends its track to the job's dubbed_tracks.
      const langOv = opts.langOverride || null;
      // Snapshot segments at call time, not click time: the multi-language
      // loop awaits a translate pass right before each generate, and the
      // click-time closure would still hold the pre-translation text.
      const segs = useAppStore.getState().dubSegments;
      setDubStep('generating');
      setDubProgress({ current: 0, total: segs.length, text: '' });
      setDubError('');
      const genLabel = regenOnly
        ? t('dub_workflow.regenerating', { count: regenOnly.length })
        : t('dub_workflow.generating_dub');
      useAppStore
        .getState()
        .showPill('generating', genLabel, { cancellable: true, homeMode: 'dub' });
      try {
        const body = {
          segment_ids: segs.map((s) => String(s.id)),
          regen_only: regenOnly,
          // Generation inputs come from the shared helper so the stored
          // fingerprints (seg_hashes) match what /tools/incremental recomputes
          // later — see utils/segments.js (#281).
          segments: segs.map((s) => ({
            start: s.start,
            end: s.end,
            gain: s.gain !== undefined && s.gain !== 1.0 ? s.gain : undefined,
            ...segmentGenInputs(s),
            // Keeps the imported caption markup for unchanged exports (#2295).
            cue_source_id: cueSourceId(s),
          })),
          language: langOv ? langOv.language : dubLang === 'Auto' ? 'Auto' : dubLang,
          language_code: langOv ? langOv.language_code : dubLangCode,
          instruct: dubInstruct,
          num_step: steps,
          guidance_scale: cfg,
          speed,
          preview,
          timing_strategy: timingStrategy || 'strict_slot',
          // Voice-identity mode for auto-clone bindings (per_line default =
          // unchanged behaviour; consistent = one reference per speaker).
          voice_match: voiceMatch || 'per_line',
          // Smart Fit knob overrides — only when the user customised them;
          // otherwise the backend's canonical defaults apply.
          ...(timingStrategy === 'smart_fit' && fitOptions ? { fit_options: fitOptions } : {}),
        };
        const data = await dubGenerate(dubJobId, body);
        setDubTaskId(data.task_id);
        const streamRes = await apiFetch(tasksStreamUrl(data.task_id));
        const reader = streamRes.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let wasCancelled = false;
        let generationError = null;
        let sawDone = false;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const evt = JSON.parse(line.slice(6));
                if (evt.type === 'progress') {
                  setDubProgress({ current: evt.current + 1, total: evt.total, text: evt.text });
                  useAppStore
                    .getState()
                    .setPillProgress(Math.round(((evt.current + 1) / evt.total) * 100));
                  useAppStore.getState().setPillLabel(
                    t('dub_workflow.generating_progress', {
                      current: evt.current + 1,
                      total: evt.total,
                    }),
                  );
                } else if (evt.type === 'done') {
                  sawDone = true;
                  setDubStep('done');
                  setDubTracks(evt.tracks || []);
                  // Invalidate the dubbed preview-video URL so the player
                  // re-fetches the freshly generated dub (#281).
                  useAppStore.getState().bumpDubGenNonce();
                  // Merge sync_scores (back-compat) and the new richer
                  // fit_status array onto each segment so the row badge can
                  // show truthful "Fits / Overflows +0.4s / Video stretched
                  // 1.18×" labels.
                  if (evt.sync_scores || evt.fit_status) {
                    setDubSegments((prev) =>
                      prev.map((s, idx) => ({
                        ...s,
                        sync_ratio: evt.sync_scores ? evt.sync_scores[idx] : s.sync_ratio,
                        fit_status: evt.fit_status ? evt.fit_status[idx] : s.fit_status,
                      })),
                    );
                  }
                  if (evt.seg_num_step && typeof evt.seg_num_step === 'object') {
                    const previewIds = Object.entries(evt.seg_num_step)
                      .filter(([, n]) => typeof n === 'number' && n < steps)
                      .map(([id]) => id);
                    setPreviewSegIds(previewIds);
                  }
                  // P1.3 — hashes belong to the track that just generated
                  // (the event carries its language), not to whatever the
                  // store's selection is when the stream drains.
                  const genLang = evt.language_code || body.language_code;
                  if (evt.seg_hashes && Object.keys(evt.seg_hashes).length > 0) {
                    setLastGenFingerprints(evt.seg_hashes, genLang);
                  } else {
                    try {
                      const plan = await apiPost('/tools/incremental', {
                        segments: segs.map((s) => ({
                          id: String(s.id),
                          ...segmentGenInputs(s),
                        })),
                        lang: genLang,
                        // Must match the mode this generate ran with — it's
                        // part of the fingerprint when non-default (#281).
                        voice_match: voiceMatch || 'per_line',
                      });
                      setLastGenFingerprints(plan.fingerprints || {}, genLang);
                    } catch (err) {
                      console.warn('Incremental plan fallback failed:', err);
                    }
                  }
                } else if (evt.type === 'cancelled') {
                  wasCancelled = true;
                  setDubStep('editing');
                  setDubError(t('dub_workflow.generation_aborted'));
                  toast(t('dub_workflow.dubbing_aborted'), { icon: '⏹' });
                } else if (evt.type === 'error') {
                  generationError =
                    evt.error_code === 'dub_speech_missing'
                      ? t('dubIntegrity.missingSpeech')
                      : evt.error_code === 'dub_timing_overflow'
                        ? t('dubIntegrity.timingOverflow')
                        : evt.reason || evt.error || t('dub_workflow.generation_stream_ended');
                  setDubError(generationError);
                }
              } catch (err) {
                console.warn('Dub generate SSE handler failed:', err);
              }
            }
          }
        }
        setDubTaskId(null);
        if (!wasCancelled) {
          if (!sawDone || generationError)
            throw new Error(generationError || t('dub_workflow.generation_stream_ended'));
          if (dubStep !== 'done') setDubStep('done');
          loadDubHistory();
          loadProjects();
          playPing();
          useAppStore.getState().completePill(t('dub_workflow.dub_complete'));
          // Success-only donation moment — a finished dub is a real
          // deliverable. Never fires on the error / cancel branches below.
          recordValueMoment('dub');
        } else {
          useAppStore.getState().dismissPill();
        }
      } catch (err) {
        setDubError(err.message);
        setDubStep('editing');
        setDubTaskId(null);
        useAppStore.getState().errorPill(err.message);
      }
    },
    [
      dubJobId,
      dubLang,
      dubLangCode,
      dubInstruct,
      steps,
      cfg,
      speed,
      dubStep,
      timingStrategy,
      fitOptions,
      voiceMatch,
      setDubStep,
      setDubProgress,
      setDubError,
      setDubTracks,
      setDubSegments,
      setDubTaskId,
      setPreviewSegIds,
      setLastGenFingerprints,
      loadDubHistory,
      loadProjects,
    ],
  );

  const handleDubStop = useCallback(async () => {
    if (!dubTaskId) return;
    const prevStep = dubStep;
    setDubStep('stopping');
    try {
      await tasksCancel(dubTaskId);
    } catch (e) {
      setDubStep(prevStep);
      toast.error(t('dub_workflow.stop_failed'));
    }
  }, [dubTaskId, dubStep, setDubStep]);

  return {
    translateProvider,
    setTranslateProvider,
    showTranscript,
    setShowTranscript,
    previewAudios,
    setPreviewAudios,
    transcribeElapsed,
    transcribeProgress,
    asrInstall,
    handleDubUpload,
    handleDubIngestUrl,
    handleDubAbort,
    handleDubRetryTranscribe,
    handleInstallMissingAsr,
    handleDubStop,
    handleDubGenerate,
    handleCleanupSegments,
    handleTranslateAll,
    handleDubImportSrt,
  };
}
