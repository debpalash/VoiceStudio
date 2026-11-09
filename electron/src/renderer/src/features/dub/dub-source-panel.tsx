import { useRef, useState, type DragEvent, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from '@tanstack/react-router';
import { CheckIcon, FileTextIcon, Settings2Icon, UploadIcon, UsersRoundIcon } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { LanguagePicker } from '@/features/clone/language-picker';
import { cn } from '@/lib/utils';
import { LANG_CODES } from '@shared/utils/languages';
import { DUB_CARD, DubFieldRow, DubStepHeading } from './dub-section';
import { setDubProduction, type DubSession } from './dub-session';
import { DubSourceRow } from './dub-source-row';
import { DubUrlForm } from './dub-url-form';

// The source picker stays on what speech recognition can transcribe.
const sourceOptions = LANG_CODES.map((item) => item.label);

export interface DubSourcePanelProps {
  session: DubSession;
  busy: boolean;
  cancelling: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  /** Size of the file picked in this window, when known. */
  localSize?: number;
  removable: boolean;
  confirmRemove: boolean;
  /** Speaker-detection model state; undefined while the catalogue loads. */
  diarisationReady?: boolean;
  onFile: (file: File) => void;
  onUrl: (url: string, cookieFile: File | undefined, fetchSubs: boolean) => void;
  onSubtitles: (file: File) => void;
  onRemove: () => void;
}

export function DubSourcePanel({
  session,
  busy,
  cancelling,
  inputRef,
  localSize,
  removable,
  confirmRemove,
  diarisationReady,
  onFile,
  onUrl,
  onSubtitles,
  onRemove,
}: DubSourcePanelProps) {
  const { t } = useTranslation();
  const subtitles = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  // Job the "Use link" form was opened for; a new or removed source closes it.
  const [linkModeJob, setLinkModeJob] = useState<string | null>(null);
  const locked = busy || Boolean(session.recovery);
  const linkMode = Boolean(session.jobId) && linkModeJob === session.jobId;
  const urlForm = (
    <DubUrlForm
      disabled={busy || cancelling || Boolean(session.recovery)}
      autoFocus={linkMode}
      onCancel={linkMode ? () => setLinkModeJob(null) : undefined}
      onSubmit={(url, cookieFile, fetchSubs) => {
        onUrl(url, cookieFile, fetchSubs);
        setLinkModeJob(null);
      }}
    />
  );
  const auto = t('dub.num_speakers_auto');
  const diarisationLabel = `${t('engineSidebar.diarisation')} · ${t('modelMaintenance.installed')}`;
  return (
    <section
      className={cn(
        DUB_CARD,
        'space-y-3 transition-colors',
        session.jobId && !['idle', 'preparing'].includes(session.phase) && 'rounded-b-md',
        dragging && 'border-primary/60 bg-primary/5',
      )}
      onDragEnter={(event) => {
        event.preventDefault();
        if (!locked) setDragging(true);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={(event: DragEvent<HTMLElement>) => {
        event.preventDefault();
        setDragging(false);
        const file = event.dataTransfer.files[0];
        if (file && !locked) onFile(file);
      }}
    >
      <DubStepHeading step={1}>{t('dub.upload_transcribe')}</DubStepHeading>
      <input
        ref={inputRef}
        type="file"
        accept="audio/*,video/*,.mkv,.mov,.mp4,.wav,.mp3"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onFile(file);
        }}
      />
      {!session.jobId ? (
        <>
          <Button className="w-full" disabled={locked} onClick={() => inputRef.current?.click()}>
            <UploadIcon />
            {t('settings.audio_tools_choose_file')}
          </Button>
          <p className="text-center text-xs text-muted-foreground">{t('dub.drop_here')}</p>
          {urlForm}
        </>
      ) : (
        <>
          <DubSourceRow
            filename={session.filename}
            inputType={session.inputType}
            duration={session.duration}
            size={localSize}
            issue={session.inputIssue}
            disabled={locked}
            removable={removable}
            linkMode={linkMode}
            confirmRemove={confirmRemove}
            recovering={Boolean(session.recovery)}
            onChangeFile={() => inputRef.current?.click()}
            onToggleLink={() => setLinkModeJob(linkMode ? null : session.jobId)}
            onRemove={onRemove}
          />
          {linkMode && urlForm}
        </>
      )}
      <div className="space-y-1 border-t border-border/40 pt-2">
        <DubFieldRow label={t('dub.source_language')}>
          <LanguagePicker
            ariaLabel={t('dub.source_language')}
            value={LANG_CODES.find((item) => item.code === session.sourceLanguage)?.label || auto}
            options={[auto, ...sourceOptions]}
            disabled={locked}
            className="max-w-[60%] border border-border/50 bg-background/40"
            onValueChange={(value) =>
              setDubProduction({
                sourceLanguage: LANG_CODES.find((item) => item.label === value)?.code,
              })
            }
          />
        </DubFieldRow>
        <DubFieldRow
          label={t('dub.num_speakers_label')}
          htmlFor="dub-num-speakers"
          hint={t('dub.num_speakers_help')}
        >
          {diarisationReady && (
            <span
              role="img"
              aria-label={diarisationLabel}
              title={diarisationLabel}
              className="shrink-0 text-emerald-500"
            >
              <CheckIcon aria-hidden="true" className="size-3.5" />
            </span>
          )}
          <Input
            id="dub-num-speakers"
            type="number"
            min={1}
            max={20}
            step={1}
            placeholder={auto}
            aria-describedby="dub-num-speakers-help"
            className="h-8 w-20"
            value={session.numSpeakers ?? ''}
            disabled={locked}
            onChange={(event) => {
              const value = Number(event.target.value);
              setDubProduction({
                numSpeakers:
                  Number.isInteger(value) && value >= 1 && value <= 20 ? value : undefined,
              });
            }}
          />
        </DubFieldRow>
        <span id="dub-num-speakers-help" className="sr-only">
          {t('dub.num_speakers_help')}
        </span>
        {diarisationReady === false && (
          <div className="flex items-center gap-2 rounded-lg bg-background/35 px-2.5 py-1.5 text-xs">
            <UsersRoundIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {t('engineSidebar.diarisation')} · {t('modelSettings.unavailable')}
            </span>
            <Link
              to="/settings/models/$family"
              params={{ family: 'diarisation' }}
              className={buttonVariants({ variant: 'outline', size: 'xs' })}
            >
              <Settings2Icon />
              {t('modelMaintenance.install')}
            </Link>
          </div>
        )}
      </div>
      <input
        ref={subtitles}
        type="file"
        accept=".srt,application/x-subrip"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onSubtitles(file);
        }}
      />
      {session.jobId && (
        <Button
          size="xs"
          variant="ghost"
          className="-ml-1 text-muted-foreground"
          disabled={locked || Boolean(session.inputIssue)}
          onClick={() => subtitles.current?.click()}
        >
          <FileTextIcon />
          {t('dub.import_srt')}
        </Button>
      )}
      {session.subtitleImport && (
        <p role="status" className="text-xs leading-5 text-muted-foreground">
          {t('dub_workflow.imported_cues', {
            count: session.subtitleImport.stats.imported,
            file: session.subtitleImport.file,
          })}
          {(['skipped_malformed', 'dropped_overlap', 'clamped_to_duration'] as const).map((key) =>
            session.subtitleImport!.stats[key] ? (
              <span key={key} className="block">
                {t('dub_workflow.' + key, { count: session.subtitleImport!.stats[key] })}
              </span>
            ) : null,
          )}
        </p>
      )}
    </section>
  );
}
