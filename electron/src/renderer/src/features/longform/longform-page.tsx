import { readTextFile } from '../../../../../../frontend/src/utils/readTextFile';
import { ProfileAvatar } from '@/components/profile-avatar';
import {
  BookOpenTextIcon,
  FileAudioIcon,
  FingerprintIcon,
  ImportIcon,
  LanguagesIcon,
  Trash2Icon,
} from 'lucide-react';
import { AudioLinesIcon } from 'lucide-react';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { PipelineFailure } from '@/components/pipeline-failure';
import { importToText } from '../../../../../../frontend/src/utils/importStory';
import { cueSheetFor } from './cue-sheet';
import { saveLocalFile } from '@/lib/local-export';
import { StoryCast, StoryEditor } from './story-editor';
import { clearedScriptPatch, scriptSize } from './story-clear';
import { ConfirmDialog } from '../clone/confirm-dialog';
import { AudiobookMarkupToolbar } from './audiobook-markup-toolbar';
import { storyVoicesReady } from './story-inputs';
import { StorySpeed } from './story-speed';
import { ProjectSettings } from './project-settings';
import { ProductionSettings } from './production-settings';
import { ChapterPreviews } from './chapter-previews';
import { castVoice } from './cast-map';
import { CastSettings } from './cast-settings';
import {
  parseCastNames,
  scriptStats,
  formatRuntimeClock,
  validateScript,
} from '../../../../../../frontend/src/utils/audiobookScript';
import { BookSettings } from './book-settings';
import { duplicateWords } from './book-options';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, ListIcon, SparklesIcon } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { WaveformPlayer } from '@/components/waveform-player';
import { SyncedAudiobookPlayer } from './synced-audiobook-player';
import { GeneratePanel } from './generate-panel';
import { generateBlocker } from './generate-blocker';
import { EngineNotice } from '@/components/engine-notice';
import { ValidationWarnings, type ScriptWarning } from './validation-warnings';
import { getBridge } from '@/components/bridge';
import { useProfiles } from '@/hooks/use-profiles';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { saveExport } from '@/lib/export-history';
import { EngineLanguagePicker } from '@/features/clone/engine-language-picker';
import { LANG_CODES } from '../../../../../../frontend/src/utils/languages';
import {
  editLongform,
  dismissLongformError,
  renderLongform,
  stopLongform,
  useLongformSession,
  storiesImportEpoch,
  type Mode,
} from './longform-session';
import { SAMPLE_AUDIOBOOK_SCRIPT } from '../../../../../../frontend/src/data/sampleAudiobook';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
interface Recovery {
  job_id: string;
  type: string;
  title: string;
  chapters_done: number;
  total_chapters: number;
}
export function LongformPage({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  const session = useLongformSession();
  const ttsOperation = mode === 'stories' ? 'longform' : 'audiobook';
  const ttsBlocker = useTtsReadiness(ttsOperation);
  const draft = session.drafts[mode];
  const text =
    mode === 'audiobook' ? draft.script : draft.lines.map((line) => line.text).join('\n');
  const names = useMemo(() => parseCastNames(text), [text]);
  const stats = useMemo(() => scriptStats(text), [text]);
  const { data: profiles = [] } = useProfiles();
  const [importing, setImporting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [warningsDismissed, setWarningsDismissed] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const audiobookInput = useRef<HTMLTextAreaElement>(null);
  const locked = !!session.active || importing;
  const scriptLines = scriptSize(mode, draft);
  const query = useQuery({
    queryKey: ['longform-recovery'],
    queryFn: ({ signal }) => apiJson<{ jobs: Recovery[] }>('/audiobook/jobs', { signal }),
  });
  useEffect(() => {
    if (!session.active) void query.refetch();
  }, [session.active]);
  const set = (value: Parameters<typeof editLongform>[1]) => editLongform(mode, value);
  const usable =
    mode === 'audiobook'
      ? draft.script.trim().length > 0
      : draft.lines.some((line) => line.text.trim() && !line.text.trim().startsWith('#'));
  const castReady = names.every(
    (name) =>
      !castVoice(draft.voiceCast, name) ||
      profiles.some((profile) => profile.id === castVoice(draft.voiceCast, name)),
  );
  const voicesReady =
    castReady &&
    (mode === 'stories'
      ? storyVoicesReady(draft, profiles)
      : profiles.some((profile) => profile.id === draft.voice));
  const warnings = useMemo(
    () =>
      mode === 'audiobook'
        ? (validateScript(draft.script, {
            mappedNames: Object.keys(draft.voiceCast).filter((name) =>
              Boolean(draft.voiceCast[name]),
            ),
            profileIds: profiles.map((profile) => profile.id),
          }) as ScriptWarning[])
        : [],
    [draft.script, draft.voiceCast, mode, profiles],
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const blocker = generateBlocker({
    mode,
    busyElsewhere: Boolean(session.active) && session.active !== mode,
    importing,
    tts: ttsBlocker,
    usable,
    voicesReady,
    duplicateLexicon: duplicateWords(draft.lexicon),
  });
  const generatePanel = (
    <GeneratePanel
      mode={mode}
      session={session}
      blocker={blocker}
      onGenerate={() => void renderLongform(mode)}
      onStop={stopLongform}
    />
  );
  const importFile = async (file: File) => {
    const importEpoch = storiesImportEpoch.current;
    setImporting(true);
    setLocalError(null);
    try {
      let text: string;
      if (mode === 'stories' && /\.(txt|md|srt|vtt)$/i.test(file.name))
        text = importToText(file.name, await readTextFile(file));
      else {
        const body = new FormData();
        body.set('file', file);
        const data = await apiJson<{ text: string }>('/audiobook/import', {
          method: 'POST',
          body,
        });
        text = data.text;
      }
      if (mode === 'stories' && importEpoch !== storiesImportEpoch.current) return;
      set(mode === 'audiobook' ? { script: text } : { importText: text });
    } catch (cause) {
      if (mode === 'stories' && importEpoch !== storiesImportEpoch.current) return;
      setLocalError(describeError(cause));
    } finally {
      setImporting(false);
    }
  };
  const download = async () => {
    setExporting(true);
    setLocalError(null);
    try {
      const url = apiPath('/audio/' + encodeURIComponent(draft.output));
      const bridge = getBridge();
      if (bridge) await saveExport(url, draft.output.split('/').pop() || 'audiobook.m4b');
      else {
        const link = document.createElement('a');
        link.href = url;
        link.download = draft.output;
        link.click();
      }
    } catch (cause) {
      setLocalError(describeError(cause));
    } finally {
      setExporting(false);
    }
  };
  // The m4b embeds its chapters, but mp3 has no portable way to carry them, and
  // players, podcast hosts and show-notes want the timestamps as text. What
  // belongs in the sheet is decided in cue-sheet.ts; this only saves it.
  const cueSheet = cueSheetFor(draft.outputChapters, draft.output, (n) =>
    t('audiobook.chapter_n', { n }),
  );
  const downloadCueSheet = async () => {
    if (!cueSheet) return;
    setLocalError(null);
    try {
      // Native save dialog under Electron, like every other local export.
      await saveLocalFile(
        new Blob([cueSheet.body], { type: 'text/plain;charset=utf-8' }),
        cueSheet.filename,
      );
    } catch (cause) {
      setLocalError(describeError(cause));
    }
  };
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">
          {t(mode === 'stories' ? 'nav.stories' : 'audiobook.title')}
        </h1>
        <Link
          to={mode === 'stories' ? '/audiobook' : '/stories'}
          className={buttonVariants({ variant: 'ghost', size: 'sm' })}
        >
          {t(mode === 'stories' ? 'audiobook.title' : 'nav.stories')}
        </Link>
      </WorkspaceHeader>
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t(mode === 'stories' ? 'nav.stories' : 'audiobook.title')}
          icon={mode === 'stories' ? AudioLinesIcon : BookOpenTextIcon}
          size="wide"
          variant="controls"
          footer={generatePanel}
          onCollapsedChange={setSidebarCollapsed}
          className="space-y-3 [&>details]:rounded-xl [&>details]:border [&>details]:border-border/60 [&>details]:bg-muted/20 [&>details]:p-3 [&>section]:rounded-xl [&>section]:border [&>section]:border-border/60 [&>section]:bg-muted/20 [&>section]:p-3"
        >
          <div className="space-y-4 rounded-xl border border-border/60 bg-muted/20 p-3">
            <label className="block space-y-2 text-xs font-medium text-muted-foreground">
              <span className="flex items-center gap-2">
                <BookOpenTextIcon className="size-4" aria-hidden="true" />
                {t('audiobook.meta_title')}
              </span>
              <Input
                value={draft.title}
                disabled={locked}
                placeholder={t(mode === 'stories' ? 'stories.untitled' : 'audiobook.untitled')}
                onChange={(e) => set({ title: e.target.value })}
              />
            </label>
            <div className="space-y-1">
              <h2 className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <FingerprintIcon className="size-4" aria-hidden="true" />
                {t('stories.voice')}
              </h2>
              {!profiles.length && (
                <p className="text-xs text-muted-foreground">{t('stories.noProfiles')}</p>
              )}
              {profiles.map((profile) => (
                <Button
                  key={profile.id}
                  variant={draft.voice === profile.id ? 'secondary' : 'ghost'}
                  className="w-full justify-start truncate"
                  disabled={locked}
                  onClick={() => set({ voice: profile.id })}
                >
                  <ProfileAvatar name={profile.name} className="size-6 shrink-0" />
                  <span className="truncate">{profile.name}</span>
                </Button>
              ))}
            </div>
            {mode === 'stories' && <StorySpeed draft={draft} disabled={locked} onChange={set} />}
            <div className="space-y-2">
              <h2 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <LanguagesIcon className="size-4" aria-hidden="true" />
                {t('clone.language')}
              </h2>
              <EngineLanguagePicker
                value={draft.language}
                options={['Auto', ...LANG_CODES.map((l) => l.label)]}
                disabled={locked}
                onValueChange={(language) => set({ language })}
              />
            </div>
            <div className="space-y-2">
              <h2 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <FileAudioIcon className="size-4" aria-hidden="true" />
                {t('audiobook.format')}
              </h2>
              <div className="flex gap-1 rounded-lg bg-background/40 p-1">
                {(['m4b', 'mp3'] as const).map((format) => (
                  <Button
                    key={format}
                    variant={draft.format === format ? 'secondary' : 'ghost'}
                    disabled={locked}
                    onClick={() => set({ format })}
                  >
                    {t('audiobook.format_' + format)}
                  </Button>
                ))}
              </div>
            </div>
          </div>
          {mode === 'stories' && (
            <StoryCast draft={draft} profiles={profiles} disabled={locked} onChange={set} />
          )}
          {mode === 'audiobook' && (
            <CastSettings
              names={names}
              cast={draft.voiceCast}
              profiles={profiles}
              disabled={locked}
              onChange={(voiceCast) => set({ voiceCast })}
            />
          )}
          {mode === 'audiobook' && (
            <ChapterPreviews
              draft={draft}
              disabled={locked}
              canPreview={ttsBlocker === null && voicesReady && !duplicateWords(draft.lexicon)}
              onBusy={setImporting}
            />
          )}
          <ProjectSettings
            mode={mode}
            draft={draft}
            disabled={locked}
            onChange={set}
            onBusy={setImporting}
          />
          <ProductionSettings
            value={draft.overrides}
            disabled={locked}
            onChange={(overrides) => set({ overrides })}
          />
          <BookSettings
            draft={draft}
            disabled={locked}
            pronunciation={mode === 'audiobook'}
            onChange={set}
            onBusy={setImporting}
          />
          {(query.data?.jobs || [])
            .filter((job) => job.type === (mode === 'stories' ? 'longform' : 'audiobook'))
            .map((job) => (
              <div key={job.job_id} className="space-y-2 border-t border-border/50 pt-4 text-xs">
                <h2 className="font-medium">{t('audiobook.recovery_title')}</h2>
                <p>{job.title || t('audiobook.untitled')}</p>
                <p>
                  {t('audiobook.recovery_progress', {
                    done: job.chapters_done,
                    total: job.total_chapters,
                  })}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={locked || ttsBlocker !== null}
                  onClick={() => void renderLongform(mode, job.job_id)}
                >
                  {t('common.resume')}
                </Button>
              </div>
            ))}
        </SecondarySidebar>
        <section className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          <div className="mx-auto flex w-full max-w-6xl min-h-0 flex-1 flex-col gap-4 px-6 py-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-medium">
                  <BookOpenTextIcon className="size-4 text-muted-foreground" />
                  {t('clone.script')}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">{t(mode + '.subtitle')}</p>
              </div>
              <label className={buttonVariants({ variant: 'outline', size: 'sm' })}>
                <ImportIcon />
                {t('audiobook.import')}
                <input
                  aria-label={t('audiobook.import')}
                  type="file"
                  accept={
                    mode === 'stories' ? '.txt,.md,.srt,.vtt,.epub,.pdf' : '.txt,.md,.epub,.pdf'
                  }
                  disabled={locked}
                  className="sr-only"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    if (file) void importFile(file);
                  }}
                />
              </label>
              <Button
                variant="ghost"
                size="sm"
                title={t('stories.clearScriptHint')}
                disabled={locked || scriptLines === 0}
                onClick={() => setClearOpen(true)}
              >
                <Trash2Icon />
                {t('stories.clearScript')}
              </Button>
              <ConfirmDialog
                open={clearOpen}
                onOpenChange={setClearOpen}
                title={t('stories.clearScript')}
                description={
                  mode === 'audiobook'
                    ? t('stories.clearConfirmScript')
                    : t('stories.clearConfirm', { count: scriptLines })
                }
                confirmLabel={t('stories.clearScript')}
                onConfirm={() => set(clearedScriptPatch(mode))}
              />
              {mode === 'audiobook' && !draft.script.trim() && (
                <Button
                  variant="ghost"
                  size="sm"
                  title={t('audiobook.load_sample_hint')}
                  disabled={locked}
                  onClick={() => set({ script: SAMPLE_AUDIOBOOK_SCRIPT })}
                >
                  <SparklesIcon />
                  {t('audiobook.load_sample')}
                </Button>
              )}
            </div>
            {mode === 'audiobook' ? (
              <div className="flex min-h-64 flex-1 flex-col gap-3">
                <AudiobookMarkupToolbar
                  textareaRef={audiobookInput}
                  text={draft.script}
                  disabled={locked}
                  setText={(script) => set({ script })}
                />
                <textarea
                  ref={audiobookInput}
                  aria-label={t('clone.script')}
                  className="min-h-64 flex-1 resize-none bg-transparent text-base leading-7 outline-none"
                  value={draft.script}
                  placeholder={t('audiobook.script_placeholder')}
                  disabled={locked}
                  onChange={(e) => {
                    set({ script: e.target.value });
                    if (warningsDismissed) setWarningsDismissed(false);
                  }}
                />
              </div>
            ) : (
              <StoryEditor
                draft={draft}
                profiles={profiles}
                disabled={locked}
                canSynthesize={ttsBlocker === null}
                onChange={set}
                onBusy={setImporting}
              />
            )}
            <p className="text-xs text-muted-foreground">
              {t('audiobook.stats', {
                chapters: stats.chapters,
                words: stats.words,
                runtime: formatRuntimeClock(stats.runtimeSec),
              })}
            </p>
            {!warningsDismissed && warnings.length > 0 && !session.active && (
              <ValidationWarnings
                warnings={warnings}
                onDismiss={() => setWarningsDismissed(true)}
              />
            )}
            {session.error && (
              <PipelineFailure
                failure={session.failure}
                fallback={session.error}
                onDismiss={dismissLongformError}
              />
            )}
            {localError && (
              <PipelineFailure fallback={localError} onDismiss={() => setLocalError(null)} />
            )}
            <EngineNotice operation={ttsOperation} />
            {session.storageError && (
              <div role="alert" className="text-sm text-destructive">
                {t('common.error')}
                <Link to="/settings/logs" className="ml-3 underline">
                  {t('settings.logs')}
                </Link>
              </div>
            )}
            {sidebarCollapsed && (
              // The setup pane is collapsed: keep Generate and the tracker in reach.
              <div className="sticky bottom-0 z-10 -mx-2 shrink-0 rounded-xl border border-border/60 bg-background/90 p-3 backdrop-blur-xl">
                {generatePanel}
              </div>
            )}
            {draft.output && (
              <section className="shrink-0 space-y-3 border-t border-border/50 pt-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-medium">{t('audiobook.ready')}</h2>
                    {mode === 'audiobook' && draft.outputCachedChapters > 0 && (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {t('audiobook.cached_note', {
                          count: draft.outputCachedChapters,
                        })}
                      </p>
                    )}
                    {draft.outputFailedChapters > 0 && (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {t('audiobook.failed_note', {
                          count: draft.outputFailedChapters,
                        })}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {cueSheet && (
                      <Button variant="ghost" size="sm" onClick={() => void downloadCueSheet()}>
                        <ListIcon />
                        {t('audiobook.download_cues')}
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={exporting}
                      onClick={() => void download()}
                    >
                      <DownloadIcon />
                      {t('audiobook.download')}
                    </Button>
                  </div>
                </div>
                {mode === 'audiobook' ? (
                  <SyncedAudiobookPlayer
                    src={apiPath('/audio/' + encodeURIComponent(draft.output))}
                    script={draft.outputScript}
                    chapters={draft.outputChapters}
                  />
                ) : (
                  <WaveformPlayer
                    showWaveform={false}
                    src={apiPath('/audio/' + encodeURIComponent(draft.output))}
                    source={'longform-' + mode}
                  />
                )}
              </section>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

export function StoriesPage() {
  return <LongformPage key="stories" mode="stories" />;
}
export function AudiobookPage() {
  return <LongformPage key="audiobook" mode="audiobook" />;
}
