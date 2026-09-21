import { Link } from '@tanstack/react-router';
import { RecordingInputs } from '@/components/recording-inputs';
import { AgentFixButton } from '@/components/agent-fix-button';
import type { useReferenceTranscript } from '@/hooks/use-reference-transcript';
import { profileAudioUrl } from '@/lib/api/client';
import { PortraitSearch } from './portrait-search';
import { ProfileImageEditor } from './profile-image-editor';
import { ProfileAvatar } from '@/components/profile-avatar';
import { useForm } from '@tanstack/react-form';
import {
  ChevronDownIcon,
  FileAudioIcon,
  LoaderCircleIcon,
  MicIcon,
  SaveIcon,
  SparklesIcon,
  SquareIcon,
  UploadCloudIcon,
  XIcon,
} from 'lucide-react';
import { useEffect, useId, useRef, useState, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { WaveformPlayer } from '@/components/waveform-player';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useProfiles, useCreateCloneProfile } from '@/hooks/use-profiles';
import { useRecording } from '@/hooks/use-recording';
import { CLONE_MAX_SECONDS, REF_HARD_MAX_SECONDS } from '@/lib/api/generate';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { selectCloneProfile, setReferenceFile, useReference } from '@/lib/store/reference';
import { cn } from '@/lib/utils';

const ACCEPT = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac,.webm';
const AUDIO_EXT = /\.(mp3|wav|m4a|flac|ogg|aac|webm)$/i;
const LEVEL_THRESHOLD = 0.025;

function isAudioFile(file: File): boolean {
  return file.type.startsWith('audio/') || AUDIO_EXT.test(file.name);
}

type IngestFn = (file: File | null) => Promise<void>;

/** Validate + load a reference clip, surfacing the length checks as toasts. */
function useIngest(): IngestFn {
  const { t } = useTranslation();
  return async (file) => {
    if (!file) return;
    if (!isAudioFile(file)) {
      toast.error(t('clone.unsupported_audio'));
      return;
    }
    const result = await setReferenceFile(file);
    const duration = Math.round(result.durationSeconds ?? 0);
    if (!result.ok) {
      toast.error(t('tts_errors.too_long', { duration, max: REF_HARD_MAX_SECONDS }));
    } else if (result.tooLong) {
      toast.warning(t('tts_errors.trim_hint', { duration, max: CLONE_MAX_SECONDS }));
    }
  };
}

function UploadZone() {
  const { t } = useTranslation();
  const ingestFile = useIngest();
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

function RecordZone() {
  const { t } = useTranslation();
  const ingestFile = useIngest();
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
        <span className="tabular-nums">{rec.seconds}s</span>
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

interface SaveProfileFormProps {
  metadata?: { refText: string; instruct: string; language: string; seed: number | null };
  selectOnSave?: boolean;
  transcribing?: boolean;
  file: File;
  onDone: () => void;
}

export function SaveProfileForm({
  file,
  onDone,
  transcribing = false,
  metadata,
  selectOnSave = true,
}: SaveProfileFormProps) {
  const { t } = useTranslation();
  const refText = useCloneSetting('refText');
  const instruct = useCloneSetting('instruct');
  const language = useCloneSetting('language');
  const create = useCreateCloneProfile();
  const [image, setImage] = useState<File>();
  const [imageUrl, setImageUrl] = useState<string>();
  useEffect(() => {
    if (!image) {
      setImageUrl(undefined);
      return;
    }
    const url = URL.createObjectURL(image);
    setImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);
  const nameId = useId();
  const errorId = useId();

  const form = useForm({
    defaultValues: { name: '' },
    onSubmit: async ({ value }) => {
      if (transcribing || create.isPending) return;
      try {
        const created = await create.mutateAsync({
          name: value.name.trim(),
          image,
          refAudio: file,
          refAudioName: file.name,
          refText: metadata?.refText ?? refText,
          instruct: metadata?.instruct ?? instruct,
          language: metadata?.language ?? language,
          seed: metadata?.seed,
        });
        if (selectOnSave) {
          // A saved profile becomes the one identity source. Release the raw
          // upload and keep metadata returned by the server, including its
          // best-effort local ASR transcript when the draft was blank.
          selectCloneProfile(created);
        }
        onDone();
      } catch {
        /* The mutation displays the localized error; keep this draft. */
      }
    },
  });

  const required = ({ value }: { value: string }) =>
    value.trim() ? undefined : t('clone.profile_name_required');

  return (
    <form
      className="flex flex-col gap-4 rounded-xl border border-border/60 bg-muted/20 p-5"
      onSubmit={(event) => {
        event.preventDefault();
        event.stopPropagation();
        void form.handleSubmit();
      }}
    >
      <div>
        <h3 className="text-base font-semibold">{t('clone.save_as_profile')}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{t('profileIdentity.save_hint')}</p>
      </div>
      <div className="flex flex-col gap-4">
        <form.Field name="name" validators={{ onChange: required, onSubmit: required }}>
          {(field) => {
            const errors = field.state.meta.errors.filter(Boolean).map(String);
            const invalid = field.state.meta.isTouched && errors.length > 0;
            return (
              <div className="flex items-center gap-4">
                <ProfileAvatar
                  name={field.state.value}
                  imageUrl={imageUrl}
                  className="size-14 text-lg"
                />
                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  <Label htmlFor={nameId}>{t('clone.profile_name')}</Label>
                  <Input
                    id={nameId}
                    autoFocus
                    value={field.state.value}
                    onChange={(event) => field.handleChange(event.target.value)}
                    onBlur={field.handleBlur}
                    placeholder={`${t('clone.profile_name')}…`}
                    aria-label={t('clone.profile_name')}
                    aria-invalid={invalid || undefined}
                    aria-describedby={invalid ? errorId : undefined}
                  />
                </div>
                {invalid ? (
                  <p
                    id={errorId}
                    className="text-[length:var(--text-label)] text-destructive"
                    role="alert"
                  >
                    {errors[0]}
                  </p>
                ) : null}
              </div>
            );
          }}
        </form.Field>
        <Label className="cursor-pointer self-start text-sm text-muted-foreground hover:text-foreground">
          {t('profileIdentity.upload_image')}
          <input
            type="file"
            className="sr-only"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) {
                if (selected.size > 5 * 1024 * 1024) toast.error(t('profileIdentity.image_limit'));
                else setImage(selected);
              }
              event.target.value = '';
            }}
          />
        </Label>
        <form.Subscribe selector={(state) => state.values.name}>
          {(name) => <PortraitSearch name={name} onSelect={setImage} />}
        </form.Subscribe>
        <div className="flex items-center justify-end gap-2">
          <form.Subscribe selector={(state) => state.isSubmitting}>
            {(isSubmitting) => (
              <Button type="submit" size="default" disabled={isSubmitting || transcribing}>
                {isSubmitting || transcribing ? (
                  <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <SaveIcon />
                )}
                {t(
                  transcribing
                    ? 'referenceAsr.busy'
                    : isSubmitting
                      ? 'preferences.loading'
                      : 'clone.save',
                )}
              </Button>
            )}
          </form.Subscribe>
          <Button type="button" variant="ghost" disabled={create.isPending} onClick={onDone}>
            {t(selectOnSave ? 'profileIdentity.use_once' : 'common.cancel')}
          </Button>
        </div>
      </div>
    </form>
  );
}

export function OptionalDetails({
  transcription,
}: {
  transcription?: ReturnType<typeof useReferenceTranscript>;
}) {
  const { t } = useTranslation();
  const refText = useCloneSetting('refText');
  const instruct = useCloneSetting('instruct');
  const [open, setOpen] = useState(() => Boolean(refText || instruct));
  useEffect(() => {
    if (
      refText ||
      transcription?.state === 'busy' ||
      transcription?.state === 'failed' ||
      transcription?.state === 'unavailable'
    )
      setOpen(true);
  }, [refText, transcription?.state]);
  const transcriptId = useId();
  const styleId = useId();
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg bg-muted/30">
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-[length:var(--text-label)] font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
        {t('clone.optional_details')}
        <ChevronDownIcon
          className={cn('size-3.5 transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="grid grid-cols-1 gap-3 px-3 pb-3 @min-[420px]:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor={transcriptId} className="text-[length:var(--text-label)]">
              {t('clone.transcript')}
            </Label>
            <Textarea
              id={transcriptId}
              rows={2}
              value={refText}
              className="min-h-16 resize-y"
              onChange={(event) => setCloneSetting('refText', event.target.value)}
              placeholder={t('clone.optional')}
            />
            <p
              role="status"
              className={cn(
                'text-[length:var(--text-caption)] text-muted-foreground',
                transcription?.state === 'failed' && 'text-destructive',
              )}
            >
              {t(
                transcription?.state === 'busy'
                  ? 'referenceAsr.busy'
                  : transcription?.state === 'failed'
                    ? 'transcriptions.failed'
                    : 'clone.transcript_hint',
              )}
            </p>
            {transcription?.state === 'unavailable' && (
              <div className="space-y-1 text-xs text-muted-foreground">
                <p>{t('asr_missing.message')}</p>
                <div className="flex flex-wrap items-center gap-2">
                  <AgentFixButton request="Restore local reference-audio transcription. Install and activate the best compatible ASR model through VoiceStudio, preserve the uploaded reference and manual transcript, then verify automatic transcription is ready to retry." />
                  <Button
                    variant="ghost"
                    size="sm"
                    nativeButton={false}
                    render={<Link to="/settings/models/$family" params={{ family: 'asr' }} />}
                  >
                    <MicIcon />
                    {t('engineSidebar.asr')} · {t('modelSettings.models')}
                  </Button>
                </div>
              </div>
            )}
            {(transcription?.state === 'failed' || transcription?.state === 'unavailable') && (
              <Button type="button" variant="ghost" size="sm" onClick={transcription.retry}>
                {t('referenceAsr.retry')}
              </Button>
            )}
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor={styleId} className="text-[length:var(--text-label)]">
              {t('clone.style')}
            </Label>
            <Input
              id={styleId}
              value={instruct}
              onChange={(event) => setCloneSetting('instruct', event.target.value)}
              placeholder={t('clone.style_placeholder')}
            />
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ReferencePanel({
  setup = false,
  hideSave = false,
  transcription,
}: {
  setup?: boolean;
  hideSave?: boolean;
  transcription?: ReturnType<typeof useReferenceTranscript>;
} = {}) {
  const { t } = useTranslation();
  const reference = useReference();
  const profiles = useProfiles();
  const selectedProfileId = useCloneSetting('selectedProfileId');
  const [mode, setMode] = useState<'upload' | 'record'>('upload');
  const [saving, setSaving] = useState(false);

  const profile =
    !setup && selectedProfileId
      ? (profiles.data?.find((p) => p.id === selectedProfileId) ?? null)
      : null;
  const file = profile || setup ? null : reference.file;
  const hasReference = Boolean(profile || file);

  return (
    <Card size="sm" className="rounded-none bg-transparent py-0 ring-0">
      <CardContent className="flex flex-col gap-3 px-0 [container-type:inline-size]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-0.5">
            {!hasReference && (
              <span className="text-[length:var(--text-label)] text-muted-foreground">
                {t('clone.reference_hint')}
              </span>
            )}
          </div>
          {!hasReference ? (
            <Tabs
              value={mode}
              onValueChange={(value) => {
                if (value === 'upload' || value === 'record') setMode(value);
              }}
            >
              <TabsList aria-label={t('clone.reference_audio')}>
                <TabsTrigger value="upload">
                  <UploadCloudIcon data-icon="inline-start" />
                  {t('clone.upload_audio')}
                </TabsTrigger>
                <TabsTrigger value="record">
                  <MicIcon data-icon="inline-start" />
                  {t('clone.record')}
                </TabsTrigger>
              </TabsList>
            </Tabs>
          ) : null}
        </div>

        {!hasReference && mode === 'upload' ? <UploadZone /> : null}
        {!hasReference && mode === 'record' ? <RecordZone /> : null}

        {file ? (
          <div className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3">
            <div className="flex min-w-0 items-center gap-2">
              <FileAudioIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{file.name}</span>
              {reference.durationSeconds != null ? (
                <span className="text-[length:var(--text-caption)] text-muted-foreground tabular-nums">
                  {t('clone.duration_seconds', { seconds: reference.durationSeconds.toFixed(1) })}
                </span>
              ) : null}
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  setSaving(false);
                  void setReferenceFile(null);
                }}
              >
                <XIcon data-icon="inline-start" />
                {t('clone.clear')}
              </Button>
            </div>
            {reference.objectUrl ? (
              <WaveformPlayer
                src={reference.objectUrl}
                source="clone-reference"
                height={36}
                compact
              />
            ) : null}
          </div>
        ) : null}

        {profile ? (
          <>
            <ProfileImageEditor profile={profile} />
            {profile.ref_audio_path && (
              <WaveformPlayer
                key={profile.id}
                src={profileAudioUrl(profile.id)}
                source="profile-reference"
                height={56}
              />
            )}
          </>
        ) : null}

        {hasReference ? <OptionalDetails transcription={transcription} /> : null}

        {file && !hideSave ? (
          saving ? (
            <SaveProfileForm
              file={file}
              transcribing={transcription?.state === 'busy'}
              onDone={() => setSaving(false)}
            />
          ) : (
            <div>
              <Button variant="secondary" size="sm" onClick={() => setSaving(true)}>
                <SaveIcon data-icon="inline-start" />
                {t('clone.save_as_profile')}
              </Button>
            </div>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
