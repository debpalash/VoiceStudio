import { LoaderCircleIcon, MicIcon, SparklesIcon, SquareIcon, UploadCloudIcon } from 'lucide-react';
import { useEffect, useId, useRef, useState, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { RecordingInputs } from '@/components/recording-inputs';
import { useRecording } from '@/hooks/use-recording';
import { useEngines } from '@/hooks/use-engines';
import { CLONE_MAX_SECONDS, REF_HARD_MAX_SECONDS } from '@/lib/api/generate';
import { probeAudioDuration } from '@/lib/audio/probe';
import { referenceUsageNote } from '@/lib/reference-usage';
import { setReferenceFile, type SetReferenceResult } from '@/lib/store/reference';
import { cn } from '@/lib/utils';

const ACCEPT = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac,.webm';
const AUDIO_EXT = /\.(mp3|wav|m4a|flac|ogg|aac|webm)$/i;
const LEVEL_THRESHOLD = 0.025;

function isAudioFile(file: File): boolean {
  return file.type.startsWith('audio/') || AUDIO_EXT.test(file.name);
}

type IngestFn = (file: File | null) => Promise<void>;

/** Receives an accepted clip instead of the composer's shared reference. */
export type AcceptReference = (file: File, durationSeconds: number | null) => void;

async function checkClip(file: File): Promise<SetReferenceResult> {
  const durationSeconds = await probeAudioDuration(file);
  return {
    ok: !(durationSeconds !== null && durationSeconds > REF_HARD_MAX_SECONDS),
    durationSeconds,
    tooLong: durationSeconds !== null && durationSeconds > CLONE_MAX_SECONDS,
  };
}

/**
 * Validate + load a reference clip, surfacing the length checks as toasts.
 * Without `onAccept` the clip becomes the composer's reference; with it (the
 * saved-profile editor) the same checks run and the clip is handed back.
 */
type PickToken = { current: number };

function useIngest(onAccept?: AcceptReference, sharedPick?: PickToken): IngestFn {
  const { t } = useTranslation();
  // Monotonic pick token: a slow probe for an earlier clip must never replace
  // (or toast over) a later one. Zones shown together share one token, so an
  // upload probe cannot land after a newer recording.
  const ownPick = useRef(0);
  const latestPick = sharedPick ?? ownPick;
  return async (file) => {
    if (!file) return;
    const pick = ++latestPick.current;
    if (!isAudioFile(file)) {
      toast.error(t('clone.unsupported_audio'));
      return;
    }
    const result: SetReferenceResult = onAccept
      ? await checkClip(file)
      : await setReferenceFile(file);
    if (pick !== latestPick.current) return;
    if (onAccept && result.ok) onAccept(file, result.durationSeconds);
    // An accepted long clip gets ReferenceUsageNote beside it instead: how
    // much of it the active engine really uses (#2281).
    if (!result.ok) {
      const duration = Math.round(result.durationSeconds ?? 0);
      toast.error(t('tts_errors.too_long', { duration, max: REF_HARD_MAX_SECONDS }));
    }
  };
}

/** How much of a clip longer than the 5–15 s recommendation the active engine uses. */
export function ReferenceUsageNote({ durationSeconds }: { durationSeconds: number | null }) {
  const { t } = useTranslation();
  const { activeTts } = useEngines();
  const note = referenceUsageNote(activeTts, durationSeconds);
  if (!note) return null;
  return (
    <p className="text-[length:var(--text-caption)] text-muted-foreground" role="status">
      {note.kind === 'best_window'
        ? t('clone.ref_usage_best_window', { seconds: note.seconds })
        : note.kind === 'head'
          ? t('clone.ref_usage_head', { seconds: note.seconds })
          : t('clone.ref_usage_long', { seconds: note.seconds })}
    </p>
  );
}

export function UploadZone({
  onAccept,
  pickToken,
}: { onAccept?: AcceptReference; pickToken?: PickToken } = {}) {
  const { t } = useTranslation();
  const ingestFile = useIngest(onAccept, pickToken);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const id = useId();

  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    void ingestFile(event.dataTransfer.files[0] ?? null);
  };

  return (
    <div>
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        onChange={(event) => {
          void ingestFile(event.target.files?.[0] ?? null);
          event.target.value = '';
        }}
      />
      <label
        htmlFor={id}
        className={cn(
          'flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-muted/30 px-4 py-6 text-center transition-colors hover:border-primary/60 hover:bg-muted/50 focus-within:ring-3 focus-within:ring-ring/50',
          dragging && 'border-primary bg-primary/10',
        )}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <UploadCloudIcon className="size-6 text-muted-foreground" aria-hidden="true" />
        <span className="text-[length:var(--text-label)] font-medium text-muted-foreground">
          {t('clone.drop_audio')}
        </span>
      </label>
    </div>
  );
}

export function RecordZone({
  onAccept,
  pickToken,
}: { onAccept?: AcceptReference; pickToken?: PickToken } = {}) {
  const { t } = useTranslation();
  const ingestFile = useIngest(onAccept, pickToken);
  const rec = useRecording((file) => void ingestFile(file));
  const hasSignal = rec.level >= LEVEL_THRESHOLD;
  let micButton;
  if (rec.isStarting || rec.isCleaning) {
    micButton = (
      <div
        className="flex size-24 flex-col items-center justify-center gap-1.5 rounded-full bg-muted text-[length:var(--text-label)] font-medium text-muted-foreground"
        role="status"
        aria-live="polite"
      >
        {rec.isStarting ? (
          <LoaderCircleIcon className="size-5 animate-spin motion-reduce:animate-none" />
        ) : (
          <SparklesIcon className="size-5 animate-pulse motion-reduce:animate-none" />
        )}
        {rec.isStarting ? t('clone.starting_recording') : t('clone.cleaning')}
      </div>
    );
  } else if (rec.isRecording) {
    micButton = (
      <button
        type="button"
        onClick={() => rec.stop()}
        aria-label={t('clone.stop_recording')}
        className="relative flex size-24 flex-col items-center justify-center gap-1.5 rounded-full border-2 border-destructive bg-destructive/10 text-[length:var(--text-label)] font-semibold text-destructive outline-none focus-visible:ring-3 focus-visible:ring-destructive/40"
      >
        <span
          className="absolute inset-0 rounded-full border-2 border-destructive/60 animate-ping motion-reduce:animate-none"
          aria-hidden="true"
        />
        <SquareIcon className="size-5 fill-current" />
        <span className="tabular-nums">
          {t('clone.duration_seconds', { seconds: rec.seconds })}
        </span>
      </button>
    );
  } else {
    micButton = (
      <button
        type="button"
        onClick={() => void rec.start()}
        className="flex size-24 flex-col items-center justify-center gap-1.5 rounded-full bg-muted text-[length:var(--text-label)] font-medium text-muted-foreground transition-colors outline-none hover:bg-destructive/10 hover:text-destructive focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <MicIcon className="size-5" />
        {t('clone.record')}
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg bg-muted/30 p-4">
      <div className="flex justify-center py-2">{micButton}</div>
      <RecordingInputs rec={rec} />
      {rec.isRecording ? (
        <div
          className="flex items-center gap-2 text-[length:var(--text-label)]"
          role="status"
          aria-live="polite"
        >
          <span
            className={cn(
              'size-2 shrink-0 rounded-full',
              hasSignal ? 'bg-success' : 'bg-muted-foreground',
            )}
            aria-hidden="true"
          />
          <div
            className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted"
            role="meter"
            aria-label={t('recording.input_level')}
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={Number(rec.level.toFixed(3))}
          >
            <div
              className="h-full rounded-full bg-success transition-[width] duration-75 motion-reduce:transition-none"
              style={{ width: `${Math.min(100, Math.round(rec.level * 100))}%` }}
            />
          </div>
          <span className={hasSignal ? 'text-success' : 'text-muted-foreground'}>
            {hasSignal ? t('recording.input_detected') : t('recording.no_input_detected')}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Upload and record side by side: both ways in are visible at once instead of
 * hidden behind a toggle. Stacks on narrow containers.
 */
export function ReferenceSourcePicker({ onAccept }: { onAccept?: AcceptReference } = {}) {
  const pickToken = useRef(0);
  // Leaving the picker (a clip was accepted, or the user cancelled) retires
  // every probe still running, so none can replace the choice afterwards.
  useEffect(() => {
    const token = pickToken;
    return () => {
      token.current += 1;
    };
  }, []);
  return (
    <div className="@container">
      <div className="grid gap-3 @md:grid-cols-2 [&>*]:min-w-0">
        <UploadZone onAccept={onAccept} pickToken={pickToken} />
        <RecordZone onAccept={onAccept} pickToken={pickToken} />
      </div>
    </div>
  );
}
