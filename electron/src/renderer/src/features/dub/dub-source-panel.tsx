import { useRef, useState, type DragEvent, type RefObject } from 'react';
import { useStore } from '@tanstack/react-store';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { CheckIcon, FileTextIcon, UploadIcon } from 'lucide-react';
import { describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { LanguagePicker } from '@/features/clone/language-picker';
import { TRANSLATION_TARGET_LABELS } from '@/lib/translation-targets';
import { cn } from '@/lib/utils';
import { LANG_CODES } from '@shared/utils/languages';
import { DUB_CARD, DubFieldRow, DubStepHeading } from './dub-section';
import { setDubProduction, setDubTarget, type DubSession } from './dub-session';
import {
  dubInputSelection,
  setDubInputSelection,
  submitDubInput,
  type PrepareInputResult,
  type DubInputOptions,
} from './dub-input-selection';
import { DubSourceRow } from './dub-source-row';
import { DubUrlForm } from './dub-url-form';

export interface DubSourcePanelProps {
  session: DubSession;
  busy: boolean;
  cancelling: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  localSize?: number;
  removable: boolean;
  confirmRemove: boolean;
  diarisationReady?: boolean;
  onFile: (
    file: File,
    subtitles?: File,
    profileId?: string,
    options?: DubInputOptions,
  ) => PrepareInputResult;
  onUrl: (url: string, cookieFile: File | undefined, fetchSubs: boolean) => void;
  onSubtitles: (file: File, profileId?: string, options?: DubInputOptions) => PrepareInputResult;
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
  onFile,
  onUrl,
  onSubtitles,
  onRemove,
}: DubSourcePanelProps) {
  const { t } = useTranslation();
  const subtitles = useRef<HTMLInputElement>(null);
  const selected = useStore(dubInputSelection, (state) => state);
  const { media, captions, submitting, separateBackground, subtitlesInTarget, narrationWanted } =
    selected;
  const step = selected.step || 0;
  const [dragging, setDragging] = useState(false);
  const [linkModeJob, setLinkModeJob] = useState<string | null>(null);
  // A link form opened for one project closes once another project replaces it.
  const linkMode = Boolean(session.jobId) && linkModeJob === session.jobId;
  const locked = busy || submitting || Boolean(session.recovery);
  const staging = !session.jobId || Boolean(media || captions);
  const sourceName =
    LANG_CODES.find((item) => item.code === session.sourceLanguage)?.label ||
    t('dub.num_speakers_auto');
  const importedName =
    LANG_CODES.find((item) => item.code === (session.subtitleTargetLang || session.sourceLang))
      ?.label || session.sourceLang;
  const activeSteps = [
    0,
    1,
    ...(selected.srtChoice || captions ? [2] : []),
    ...(media ? [3] : []),
    4,
    5,
  ];
  const steps = [
    'flow_file',
    'flow_subtitles',
    'flow_srt_language',
    'keep_background',
    'flow_script',
    'flow_summary',
  ];
  const move = (direction: number) => {
    const index = activeSteps.indexOf(step);
    setDubInputSelection({
      step: activeSteps[Math.max(0, Math.min(activeSteps.length - 1, index + direction))],
    });
  };
  const choose = (file: File) => {
    if (/\.(srt|vtt)$/i.test(file.name))
      setDubInputSelection({ captions: file, srtChoice: true, srtLanguageChosen: false, step: 2 });
    else setDubInputSelection({ media: file, step: 1 });
  };
  const linkForm = (
    <DubUrlForm
      disabled={locked || cancelling}
      autoFocus={linkMode}
      onCancel={() => setLinkModeJob(null)}
      onSubmit={(url, cookies, fetchSubs) => {
        onUrl(url, cookies, fetchSubs);
        setLinkModeJob(null);
      }}
    />
  );
  const option = (
    name: string,
    value: boolean,
    checked: boolean,
    title: string,
    hint: string,
    change: () => void,
  ) => (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-2 rounded-lg border p-3 text-xs',
        checked ? 'border-primary/50 bg-primary/5' : 'border-border/50 bg-background/30',
      )}
    >
      <input
        type="radio"
        name={name}
        value={String(value)}
        checked={checked}
        disabled={locked}
        onChange={change}
        className="mt-0.5 accent-primary"
      />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="mt-1 block text-muted-foreground">{hint}</span>
      </span>
    </label>
  );
  const valid =
    step === 0
      ? Boolean(media || captions)
      : step === 1
        ? selected.srtChoice === false || Boolean(captions)
        : step === 2
          ? Boolean(captions && selected.srtLanguageChosen)
          : true;
  const subtitleControl = (
    <Button
      variant="outline"
      className="w-full"
      disabled={locked}
      onClick={() => subtitles.current?.click()}
    >
      <FileTextIcon />
      {t('dub.import_srt')}
    </Button>
  );
  const pendingSummary = (
    <dl className="space-y-2 text-xs">
      <div>
        <dt className="text-muted-foreground">{t('dub.flow_file')}</dt>
        <dd className="break-all">
          {media?.name || (session.jobId ? session.filename : t('dub.subtitle_only_hint'))}
        </dd>
      </div>
      {captions && (
        <div>
          <dt className="text-muted-foreground">{t('dub.flow_subtitles')}</dt>
          <dd className="break-all">{captions.name}</dd>
        </div>
      )}
      <div>
        <dt className="text-muted-foreground">{t('dub.source_language')}</dt>
        <dd>{sourceName}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">{t('dub.target_language')}</dt>
        <dd>{session.target}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">{t('nav.transcribe')}</dt>
        <dd>{t(captions ? 'dub.skip_asr_hint' : 'dub.transcribe_hint')}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">{t('dub.guide_text')}</dt>
        <dd>
          {t(captions && subtitlesInTarget ? 'dub.srt_target_hint' : 'dub.srt_source_hint', {
            language: session.target,
          })}
        </dd>
      </div>
      <div>
        <dt className="text-muted-foreground">{t('dub.keep_background')}</dt>
        <dd>
          {t(
            media && separateBackground !== false
              ? 'dub.flow_background_yes'
              : 'dub.flow_background_no',
          )}
        </dd>
      </div>
      <div>
        <dt className="text-muted-foreground">{t('dub.flow_script')}</dt>
        <dd>{t(narrationWanted ? 'dub.narration_prepare' : 'dub.narration_use_captions')}</dd>
      </div>
    </dl>
  );
  // Transcription reads the speaker count as it starts, so it is chosen with the
  // file, before Prepare project, as well as afterwards under source tools.
  const speakersRow = (
    <DubFieldRow label={t('dub.num_speakers_label')}>
      <Input
        type="number"
        aria-label={t('dub.num_speakers_label')}
        min={1}
        max={20}
        placeholder={t('dub.num_speakers_auto')}
        value={session.numSpeakers ?? ''}
        disabled={locked}
        onChange={(e) => {
          const n = Number(e.target.value);
          setDubProduction({ numSpeakers: n >= 1 && n <= 20 ? n : undefined });
        }}
      />
    </DubFieldRow>
  );
  return (
    <section
      className={cn(DUB_CARD, 'space-y-3', dragging && 'border-primary/60 bg-primary/5')}
      onDragEnter={(e) => {
        e.preventDefault();
        if (!locked) setDragging(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={(e: DragEvent<HTMLElement>) => {
        e.preventDefault();
        setDragging(false);
        if (!locked) Array.from(e.dataTransfer.files).forEach(choose);
      }}
    >
      <DubStepHeading step={1}>
        {t(staging ? 'dub.prepare_project' : 'dub.flow_material_ready')}
      </DubStepHeading>
      <input
        ref={inputRef}
        type="file"
        accept="audio/*,video/*,.mkv,.mov,.mp4,.wav,.mp3"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) choose(file);
        }}
      />
      <input
        ref={subtitles}
        type="file"
        accept=".srt,application/x-subrip"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) choose(file);
        }}
      />
      {staging ? (
        <>
          <ol className="space-y-1 text-xs" aria-label={t('dub.prepare_project')}>
            {activeSteps.map((n, i) => (
              <li
                key={n}
                className={cn(
                  'flex items-center gap-2',
                  n === step
                    ? 'font-medium text-primary'
                    : n < step
                      ? 'text-emerald-600 dark:text-emerald-400'
                      : 'text-muted-foreground',
                )}
                aria-current={n === step ? 'step' : undefined}
              >
                <span aria-hidden="true">{n < step ? '✓' : n === step ? '→' : i + 1}</span>
                {t('dub.' + steps[n])}
              </li>
            ))}
          </ol>
          <hr role="presentation" className="border-border/40" />
          {step === 0 && (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">{t('dub.flow_no_processing')}</p>
              <Button
                className="w-full"
                disabled={locked}
                onClick={() => inputRef.current?.click()}
              >
                <UploadIcon />
                {t('dub.flow_file')}
              </Button>
              <p className="text-xs text-muted-foreground">{t('dub.drop_here')}</p>
              {media && (
                <p className="break-all text-xs text-emerald-600 dark:text-emerald-400">
                  ✓ {media.name}
                </p>
              )}
              {subtitleControl}
              <p className="text-xs text-muted-foreground">{t('dub.subtitle_only_hint')}</p>
              <details>
                <summary className="cursor-pointer text-xs text-muted-foreground">
                  {t('dub.use_link')}
                </summary>
                {linkForm}
              </details>
            </div>
          )}
          {step === 1 && (
            <div className="space-y-2">
              <p className="break-all text-xs text-emerald-600 dark:text-emerald-400">
                ✓ {media?.name}
              </p>
              <h3 className="text-sm font-medium">{t('dub.flow_subtitles')}</h3>
              {option(
                'srt',
                true,
                selected.srtChoice === true,
                t('dub.import_srt'),
                t('dub.skip_asr_hint'),
                () => setDubInputSelection({ srtChoice: true }),
              )}
              {option(
                'srt',
                false,
                selected.srtChoice === false,
                t('nav.transcribe'),
                t('dub.transcribe_hint'),
                () =>
                  setDubInputSelection({
                    srtChoice: false,
                    captions: undefined,
                    subtitlesInTarget: false,
                    srtLanguageChosen: false,
                  }),
              )}
              {selected.srtChoice && subtitleControl}
              {captions && <p className="break-all text-xs">✓ {captions.name}</p>}
            </div>
          )}
          {(step === 1 || step === 2) && (
            <div className="space-y-2">
              <DubFieldRow label={t('dub.source_language')}>
                <LanguagePicker
                  ariaLabel={t('dub.source_language')}
                  value={sourceName}
                  options={[t('dub.num_speakers_auto'), ...LANG_CODES.map((x) => x.label)]}
                  disabled={locked}
                  onValueChange={(value) => {
                    setDubProduction({
                      sourceLanguage: LANG_CODES.find((x) => x.label === value)?.code,
                    });
                    setDubInputSelection({ srtLanguageChosen: false });
                  }}
                />
              </DubFieldRow>
              <DubFieldRow label={t('dub.target_language')}>
                <LanguagePicker
                  ariaLabel={t('dub.target_language')}
                  value={session.target}
                  options={TRANSLATION_TARGET_LABELS}
                  disabled={locked}
                  onValueChange={(value) => {
                    setDubTarget(value);
                    setDubInputSelection({ srtLanguageChosen: false });
                  }}
                />
              </DubFieldRow>
              {selected.srtChoice === false && speakersRow}
            </div>
          )}
          {step === 2 && (
            <div className="space-y-2">
              <p className="break-all text-xs text-emerald-600 dark:text-emerald-400">
                ✓ {captions?.name}
              </p>
              <h3 className="text-sm font-medium">{t('dub.flow_srt_language')}</h3>
              {option(
                'srtLanguage',
                false,
                selected.srtLanguageChosen === true && !subtitlesInTarget,
                t('dub.flow_srt_original', { language: sourceName }),
                t('dub.srt_source_hint'),
                () => setDubInputSelection({ subtitlesInTarget: false, srtLanguageChosen: true }),
              )}
              {option(
                'srtLanguage',
                true,
                selected.srtLanguageChosen === true && subtitlesInTarget === true,
                t('dub.flow_srt_target', { language: session.target }),
                t('dub.srt_target_hint', { language: session.target }),
                () => setDubInputSelection({ subtitlesInTarget: true, srtLanguageChosen: true }),
              )}
            </div>
          )}
          {step === 3 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">{t('dub.keep_background')}</h3>
              {option(
                'background',
                false,
                separateBackground === false,
                t('dub.flow_background_no'),
                t('dub.keep_background_hint'),
                () => setDubInputSelection({ separateBackground: false }),
              )}
              {option(
                'background',
                true,
                separateBackground !== false,
                t('dub.flow_background_yes'),
                t('dub.flow_background_cost'),
                () => setDubInputSelection({ separateBackground: true }),
              )}
            </div>
          )}
          {step === 4 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">{t('dub.flow_script')}</h3>
              {option(
                'script',
                true,
                narrationWanted === true,
                t('dub.narration_prepare'),
                t('dub.narration_intro'),
                () => setDubInputSelection({ narrationWanted: true }),
              )}
              {narrationWanted && (
                <p className="text-xs text-warning">{t('dub.narration_active_hint')}</p>
              )}
              {option(
                'script',
                false,
                !narrationWanted,
                t('dub.narration_use_captions'),
                t('dub.flow_individual_hint'),
                () => setDubInputSelection({ narrationWanted: false }),
              )}
              <p className="text-xs text-muted-foreground">{t('dub.narration_need_text')}</p>
            </div>
          )}
          {step === 5 && (
            <div className="space-y-3">
              {pendingSummary}
              <p className="text-xs text-muted-foreground">{t('dub.flow_prepare_hint')}</p>
            </div>
          )}
          <div className="flex gap-2">
            {step > 0 && (
              <Button variant="outline" disabled={locked} onClick={() => move(-1)}>
                {t('common.back')}
              </Button>
            )}
            <Button
              className="min-w-0 flex-1 whitespace-normal"
              disabled={
                locked ||
                !valid ||
                (step === 5 &&
                  !media &&
                  !session.jobId &&
                  !session.sourceLanguage &&
                  !subtitlesInTarget)
              }
              onClick={() =>
                step === 5
                  ? void submitDubInput(onFile, onSubtitles).catch((error) =>
                      toast.error(describeError(error)),
                    )
                  : move(1)
              }
            >
              {submitting && <CheckIcon />}
              {t(
                step === 5
                  ? captions && (!media || separateBackground === false)
                    ? 'dub.flow_import'
                    : 'dub.prepare_project'
                  : step === 4
                    ? 'dub.flow_summary'
                    : 'dub.guide_continue',
              )}
            </Button>
          </div>
          {step === 5 &&
            !media &&
            !session.jobId &&
            !session.sourceLanguage &&
            !subtitlesInTarget && (
              <p className="text-xs text-warning">{t('dub.flow_choose_source')}</p>
            )}
          <Button
            size="xs"
            variant="ghost"
            disabled={locked}
            onClick={() =>
              setDubInputSelection({
                media: undefined,
                captions: undefined,
                step: 0,
                srtChoice: undefined,
                srtLanguageChosen: false,
              })
            }
          >
            {t('common.clear')}
          </Button>
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
          {linkMode && linkForm}
          <dl className="space-y-2 text-xs">
            <div>
              <dt className="text-muted-foreground">{t('dub.source_language')}</dt>
              <dd>{sourceName}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">{t('dub.flow_srt_language')}</dt>
              <dd>{importedName || t('dub.num_speakers_auto')}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">{t('dub.target_language')}</dt>
              <dd>{session.target}</dd>
            </div>
          </dl>
          <p className="text-xs text-muted-foreground">
            {t(
              session.separateBackground === false || session.subtitleOnly
                ? 'dub.flow_background_no'
                : 'dub.flow_background_yes',
            )}
          </p>
          {session.subtitleImport && (
            <p role="status" className="break-words text-xs text-emerald-600 dark:text-emerald-400">
              ✓{' '}
              {t('dub_workflow.imported_cues', {
                count: session.subtitleImport.stats.imported,
                file: session.subtitleImport.file,
              })}
              {/* A lossy import must say what it skipped, dropped or clamped. */}
              {(['skipped_malformed', 'dropped_overlap', 'clamped_to_duration'] as const).map(
                (key) =>
                  session.subtitleImport!.stats[key] ? (
                    <span key={key} className="block text-muted-foreground">
                      {t('dub_workflow.' + key, { count: session.subtitleImport!.stats[key] })}
                    </span>
                  ) : null,
              )}
            </p>
          )}
          <details>
            <summary className="cursor-pointer text-xs text-muted-foreground">
              {t('dub.flow_source_tools')}
            </summary>
            <div className="mt-3 space-y-2">
              <p className="text-xs text-warning">{t('dub.flow_replace_srt')}</p>
              {subtitleControl}
              {speakersRow}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
