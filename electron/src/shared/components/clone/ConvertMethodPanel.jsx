import { useEffect, useRef, useState } from 'react';
import {
  UploadCloud,
  X,
  ArrowRightLeft,
  Loader,
  AudioLines,
  Fingerprint,
  Timer,
  Info,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Button } from '../../ui';
import { API } from '../../api/client';
import { convertSpeech } from '../../api/convert';
import { TtsGenerationBusyError } from '../../api/generate';
import { asrMissingPayload, toastAsrModelMissing } from '../../utils/asrModelMissing';
import { modelNotDownloadedPayload, toastModelNotDownloaded } from '../../utils/modelNotDownloaded';
import { toastErrorWithReport } from '../../utils/errorToast';
import useRecording from '../../hooks/useRecording';
import MicButton from './MicButton';
import VoiceSelector from '../VoiceSelector';
import WaveformPlayer from '../WaveformPlayer';

/**
 * Studio "Convert" method — speech-to-speech voice changer.
 *
 * Drop or record a source clip, pick an existing voice profile, Convert:
 * the backend transcribes the clip with the active ASR engine and re-says
 * it with the active TTS engine in the chosen profile's voice
 * (POST /convert). "Match source duration" (default on) time-stretches the
 * take — pitch preserved — to land near the source clip's length.
 *
 * Self-contained on purpose: unlike the audio/design methods it doesn't use
 * ScriptPanel (the script IS the source clip) or the shared ActionBar, so it
 * owns its source file, target voice, and result state locally.
 */
export default function ConvertMethodPanel({ t, profiles = [], onRecordingBusyChange }) {
  const [sourceFile, setSourceFile] = useState(null);
  const [voiceId, setVoiceId] = useState('');
  const [matchDuration, setMatchDuration] = useState(true);
  const [isConverting, setIsConverting] = useState(false);
  const [result, setResult] = useState(null);
  // Stale-request guard: every source/voice change bumps the sequence AND
  // aborts any in-flight convert, so an obsolete take never renders against
  // the newly-chosen inputs and the abort releases the shared TTS admission
  // slot — the next convert isn't blocked behind a request nobody wants.
  const convertSeqRef = useRef(0);
  const abortRef = useRef(null);
  const invalidateInFlight = () => {
    convertSeqRef.current += 1;
    const obsolete = abortRef.current;
    if (obsolete) {
      abortRef.current = null;
      obsolete.abort();
      setIsConverting(false);
    }
  };

  const ingestSource = (file) => {
    if (!file) return;
    if (
      !(file.type.startsWith('audio/') || /\.(mp3|wav|m4a|flac|ogg|aac|webm)$/i.test(file.name))
    ) {
      toast.error(t('clone.unsupported_audio'));
      return;
    }
    invalidateInFlight();
    // Re-wrap the picked/dropped File with a metacharacter-free name before it
    // enters state (CodeQL js/xss-through-dom): a file's NAME is DOM-derived
    // text, and this object later feeds the shared player and the upload
    // filename. Same bytes, same type — only the name is constrained.
    const safeName = (file.name || 'source.wav').replace(/[^\w .()-]+/g, '_');
    setSourceFile(new File([file], safeName, { type: file.type }));
    setResult(null);
  };

  const selectVoice = (id) => {
    invalidateInFlight();
    setVoiceId(id);
    // A finished take belongs to the previously chosen voice — showing it
    // against the new selection would misattribute the audio.
    setResult(null);
  };

  // Own mic instance: a convert source is not the clone reference, so it
  // must never overwrite the audio method's refAudio.
  const {
    isRecording,
    isStartingRecording,
    isCleaning,
    recordingTime,
    startRecording,
    stopRecording,
  } = useRecording(async (file) => ingestSource(file));

  useEffect(() => {
    onRecordingBusyChange?.(
      Boolean(isStartingRecording || isRecording || isCleaning || isConverting),
    );
    return () => onRecordingBusyChange?.(false);
  }, [isStartingRecording, isRecording, isCleaning, isConverting, onRecordingBusyChange]);

  const canConvert = !!sourceFile && !!voiceId && !isConverting;

  const handleConvert = async () => {
    if (!canConvert) return;
    const seq = convertSeqRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setIsConverting(true);
    setResult(null);
    try {
      const fd = new FormData();
      // Re-wrap the File as a fresh Blob — same Tauri/WebKit quirk workaround
      // as useTTS's ref_audio append (a stale File handle can upload empty).
      const buf = await sourceFile.arrayBuffer();
      // File reads are not abortable. If the inputs changed while this read
      // was pending, stop before the obsolete source can enter admission.
      if (seq !== convertSeqRef.current || controller.signal.aborted) return;
      fd.append(
        'audio',
        new Blob([buf], { type: sourceFile.type || 'audio/wav' }),
        sourceFile.name || 'source.wav',
      );
      fd.append('profile_id', voiceId);
      fd.append('match_duration', matchDuration ? '1' : '0');
      const res = await convertSpeech(fd, { signal: controller.signal });
      if (seq === convertSeqRef.current) setResult(res);
    } catch (e) {
      // Obsolete request (inputs changed, which also aborted it) — silent.
      if (seq !== convertSeqRef.current || e?.name === 'AbortError') return;
      // Same error ladder as useTTS's generate catch: structured payloads get
      // their one-click CTA, the busy guard gets its localized notice, and
      // everything else goes through toastErrorWithReport — which maps the
      // backend's [shutting_down]/[clone_ref_*] markers to localized guidance
      // and gives real failures the "Report" action instead of a raw string.
      const missing = asrMissingPayload(e);
      const notDownloaded = modelNotDownloadedPayload(e);
      if (missing) {
        toastAsrModelMissing(missing);
      } else if (notDownloaded) {
        toastModelNotDownloaded(notDownloaded);
      } else if (e instanceof TtsGenerationBusyError) {
        toast(t('tts_errors.generation_in_progress'), { icon: '⏳' });
      } else {
        toastErrorWithReport(t('tts_errors.error_prefix', { message: e?.message || String(e) }), e);
      }
    } finally {
      // An obsolete request may finish after its replacement has started.
      // Only the request that still owns the ref may release the busy state.
      if (abortRef.current === controller) {
        abortRef.current = null;
        setIsConverting(false);
      }
    }
  };

  return (
    <div data-testid="convert-method-panel" className="flex flex-1 min-h-0 flex-col">
      <div
        data-testid="convert-form"
        className="convert-form flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-5"
      >
        {/* ── Source clip: drop / pick / record ── */}
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--chrome-fg)]">
          <AudioLines size={17} aria-hidden="true" />
          {t('convert.source_kicker')}
        </div>
        <div className="flex flex-wrap gap-3 items-stretch rounded-lg bg-[var(--chrome-hover-bg)] p-4">
          <input
            type="file"
            accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg"
            onChange={(e) => {
              ingestSource(e.target.files[0]);
              e.target.value = '';
            }}
            className="sr-only"
            id="convert-audio-upload"
          />
          <label
            htmlFor="convert-audio-upload"
            tabIndex={0}
            role="button"
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                document.getElementById('convert-audio-upload')?.click();
              }
            }}
            className="flex-1 basis-56 min-w-0 min-h-48 rounded-lg p-5 text-center cursor-pointer flex flex-col justify-center items-center gap-3 bg-[var(--chrome-hover-bg)] transition-colors hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)] [&.is-dragging]:bg-[var(--chrome-accent-bg)]"
            onDragOver={(e) => {
              e.preventDefault();
              e.currentTarget.classList.add('is-dragging');
            }}
            onDragLeave={(e) => {
              e.currentTarget.classList.remove('is-dragging');
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.currentTarget.classList.remove('is-dragging');
              const file = e.dataTransfer.files[0];
              ingestSource(file);
            }}
          >
            <UploadCloud className="text-[var(--chrome-accent)]" size={28} aria-hidden="true" />
            <p className="m-0 text-sm text-[color:var(--chrome-fg-muted)] font-[family-name:var(--font-sans)] font-medium">
              {sourceFile ? (
                <span className="text-fg">{sourceFile.name}</span>
              ) : (
                t('convert.drop_audio')
              )}
            </p>
          </label>
          <MicButton
            isCleaning={isCleaning}
            isStarting={isStartingRecording}
            isRecording={isRecording}
            recordingTime={recordingTime}
            onStart={startRecording}
            onStop={stopRecording}
          />
        </div>

        {sourceFile && (
          <div className="mt-2 flex items-center gap-[8px]">
            <div className="flex-1 min-w-0">
              <WaveformPlayer src={sourceFile} source="convert-source" height={34} compact />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                invalidateInFlight();
                setSourceFile(null);
                setResult(null);
              }}
              leading={<X size={11} />}
            >
              {t('clone.clear')}
            </Button>
          </div>
        )}

        {/* ── Target voice ── */}
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--chrome-fg)]">
          <Fingerprint size={17} aria-hidden="true" />
          {t('convert.target_voice')}
        </div>
        <div className="rounded-lg bg-[var(--chrome-hover-bg)] p-3">
          <VoiceSelector
            value={voiceId}
            onChange={selectVoice}
            profiles={profiles}
            engineDefault={false}
            gallery={false}
            placeholder={t('convert.pick_voice')}
            ariaLabel={t('convert.target_voice')}
            recentsKey="convert-target"
            menuPortal
            buttonClassName="min-h-12 px-3 text-sm border-0 rounded-lg bg-transparent text-[var(--chrome-fg)] hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)]"
          />
        </div>

        {/* ── Options + action ── */}
        <div className="flex flex-col items-start gap-3 rounded-lg bg-[var(--chrome-hover-bg)] p-4">
          <label
            className="inline-flex items-center gap-[6px] text-[0.85em] text-fg-muted cursor-pointer select-none whitespace-nowrap"
            title={t('convert.match_duration_hint')}
          >
            <input
              type="checkbox"
              checked={matchDuration}
              onChange={(e) => {
                invalidateInFlight();
                setMatchDuration(e.target.checked);
                setResult(null);
              }}
            />
            <Timer size={16} aria-hidden="true" />
            <span>{t('convert.match_duration')}</span>
          </label>
          <p className="m-0 text-xs text-[var(--chrome-fg-muted)]">
            {t('convert.match_duration_hint')}
          </p>
          {!sourceFile || !voiceId ? (
            <span className="inline-flex items-center gap-2 text-xs text-fg-muted">
              <Info size={14} aria-hidden="true" />
              {t('convert.need_source_and_voice')}
            </span>
          ) : null}
        </div>

        {/* ── Result: converted take + what the ASR heard ── */}
        {result && (
          <div className="mt-[var(--space-4)]" data-testid="convert-result">
            <div className="label-row">{t('convert.result_kicker')}</div>
            <WaveformPlayer
              src={`${API}${result.audio_url}`}
              source="output"
              height={40}
              autoPlay
            />
            <div className="mt-2 text-[0.78rem] text-fg-muted">
              <span className="font-medium">{t('convert.transcript')}:</span> {result.text}
            </div>
          </div>
        )}
      </div>
      <div className="studio-action-bar" data-testid="convert-action-bar">
        <Button
          variant="primary"
          block
          onClick={handleConvert}
          disabled={!canConvert}
          leading={
            isConverting ? (
              <Loader size={14} className="animate-spin" />
            ) : (
              <ArrowRightLeft size={14} />
            )
          }
        >
          {isConverting ? t('convert.converting') : t('convert.convert')}
        </Button>
      </div>
    </div>
  );
}
