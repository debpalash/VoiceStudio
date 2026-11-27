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
  compact = false,
}: { onAccept?: AcceptReference; pickToken?: PickToken; compact?: boolean } = {}) {
  const { t } = useTranslation();
  const ingestFile = useIngest(onAccept, pickToken);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const id = useId();

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDragging(false);
    void ingestFile(event.dataTransfer.files[0] ?? null);
  };

  return (
    <div className="flex">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(event) => {
          void ingestFile(event.target.files?.[0] ?? null);
          event.target.value = '';
        }}
      />
      <button
        type="button"
        aria-describedby={compact ? id : undefined}
        aria-label={compact ? t('clone.upload_audio') : undefined}
        onClick={() => inputRef.current?.click()}
        className={cn(
          'flex w-full cursor-pointer rounded-2xl border border-border/70 outline-none transition-colors hover:border-primary/50 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring',
          compact
            ? 'items-start gap-3 bg-card/50 p-4 text-start'
            : 'min-h-36 flex-col items-center justify-center gap-2 border-dashed bg-muted/30 px-4 py-6 text-center',
          dragging && 'border-primary bg-primary/10',
        )}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span
          className={cn(
            'shrink-0',
            compact && 'grid size-9 place-items-center rounded-xl bg-primary/10 text-primary',
          )}
        >
          <UploadCloudIcon
            className={compact ? 'size-4' : 'size-6 text-muted-foreground'}
            aria-hidden="true"
          />
        </span>
        <span className={cn('min-w-0', compact && 'flex flex-col gap-1 pt-1')}>
          {compact && <span className="text-base font-semibold">{t('clone.upload_audio')}</span>}
          <span id={id} className="text-sm text-muted-foreground">
            {t('clone.drop_audio')}
          </span>
        </span>
      </button>
    </div>
  );
}

export function RecordZone({
  onAccept,
  pickToken,
  compact = false,
}: { onAccept?: AcceptReference; pickToken?: PickToken; compact?: boolean } = {}) {
  const { t } = useTranslation();
  const ingestFile = useIngest(onAccept, pickToken);
  const rec = useRecording((file) => void ingestFile(file));
  const hasSignal = rec.level >= LEVEL_THRESHOLD;
  const compactControl =
    'inline-flex min-h-9 items-center gap-3 rounded-xl pe-3 text-base font-semibold';
  let micButton;
  if (rec.isStarting || rec.isCleaning) {
    micButton = (
      <div
        className={cn(
          'text-muted-foreground',
          compact
            ? compactControl
            : 'flex size-24 flex-col items-center justify-center gap-1.5 rounded-full bg-muted text-[length:var(--text-label)] font-medium',
        )}
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
        className={cn(
          'relative text-destructive outline-none focus-visible:ring-2 focus-visible:ring-destructive/40',
          compact
            ? compactControl
            : 'flex size-24 flex-col items-center justify-center gap-1.5 rounded-full border-2 border-destructive bg-destructive/10 text-[length:var(--text-label)] font-semibold',
        )}
      >
        <span
          className={cn(
            'absolute inset-0 rounded-full border border-destructive/60',
            !compact && 'animate-ping motion-reduce:animate-none',
          )}
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
        className={cn(
          'outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
          compact
            ? cn(compactControl, 'hover:bg-muted/40')
            : 'flex size-24 flex-col items-center justify-center gap-2 rounded-full bg-muted text-[length:var(--text-label)] font-medium text-muted-foreground hover:bg-destructive/10 hover:text-destructive',
        )}
      >
        <span
          className={cn(
            'shrink-0',
            compact && 'grid size-9 place-items-center rounded-xl bg-primary/10 text-primary',
          )}
        >
          <MicIcon className={compact ? 'size-4' : 'size-5'} aria-hidden="true" />
        </span>
        {t('clone.record')}
      </button>
    );
  }

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-2xl p-4',
        compact ? 'border border-border/70 bg-card/50' : 'bg-muted/30',
      )}
    >
      <div className={cn('flex', compact ? 'justify-start' : 'justify-center py-2')}>
        {micButton}
      </div>
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
export function ReferenceSourcePicker({
  onAccept,
  compact = false,
}: { onAccept?: AcceptReference; compact?: boolean } = {}) {
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
        <UploadZone compact={compact} onAccept={onAccept} pickToken={pickToken} />
        <RecordZone compact={compact} onAccept={onAccept} pickToken={pickToken} />
      </div>
    </div>
  );
}
