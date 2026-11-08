import { readTextFile } from '@shared/utils/readTextFile';
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
import { importToText } from '@shared/utils/importStory';
import { cueSheetFor } from './cue-sheet';
import { saveLocalFile } from '@/lib/local-export';
import { StoryEditor } from './story-editor';
import { StoryCast } from './story-cast';
import { StorySpeed } from './story-speed';
import { clearedScriptPatch, scriptSize } from './story-clear';
import { ConfirmDialog } from '../clone/confirm-dialog';
import { AudiobookMarkupToolbar } from './audiobook-markup-toolbar';
import { ChapterOutline } from './chapter-outline';
import { storyVoicesReady } from './story-inputs';
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
} from '@shared/utils/audiobookScript';
import { BookSettings } from './book-settings';
import { duplicateWords } from './book-options';
import { useMemo, useRef, useState } from 'react';
import { useEngines } from '@/hooks/use-engines';
import { engineShortName, expressionCaps } from '@/lib/expression';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, ListIcon, SparklesIcon } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { WaveformPlayer } from '@/components/waveform-player';
import { SyncedAudiobookPlayer } from './synced-audiobook-player';
import { GeneratePanel } from './generate-panel';
import { generateBlocker } from './generate-blocker';
import { EngineNotice } from '@/components/engine-notice';
import { ValidationWarnings, type ScriptWarning } from './validation-warnings';
import { getBridge } from '@/components/bridge';
import { useProfiles } from '@/hooks/use-profiles';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { RenderRecovery } from './render-recovery';
import { saveExport } from '@/lib/export-history';
import { EngineLanguagePicker } from '@/features/clone/engine-language-picker';
import { outputLanguageOptions } from './output-languages';
import {
  editLongform,
  dismissLongformError,
  renderLongform,
  stopLongform,
  useLongformSession,
  storiesImportEpoch,
  type Mode,
} from './longform-session';
import { SAMPLE_AUDIOBOOK_SCRIPT } from '@shared/data/sampleAudiobook';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
const FORMATS = ['m4b', 'mp3'] as const;
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
  const languageOptions = useMemo(() => outputLanguageOptions(draft.language), [draft.language]);
  const { data: profiles = [] } = useProfiles();
  const [importing, setImporting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [warningsDismissed, setWarningsDismissed] = useState(false);
  const [scriptResetRequested, setScriptResetRequested] = useState(false);
  const [scriptCaret, setScriptCaret] = useState(0);
  const audiobookInput = useRef<HTMLTextAreaElement>(null);
  const locked = !!session.active || importing;
  const resetEntryCount = scriptSize(mode, draft);
  const resetIsUnavailable = locked || resetEntryCount < 1;
  const resetMessage =
    mode === 'audiobook'
      ? t('stories.clearConfirmScript')
      : t('stories.clearConfirm', { count: resetEntryCount });
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
  const { activeTts } = useEngines();
  // Name the engine only when it skips [emotion:] (#3422), so the warning can say so.
  const emotionIgnoredBy =
    activeTts && expressionCaps(activeTts).emotion === 'none'
      ? engineShortName(activeTts) || t('engineSidebar.tts')
      : null;
  const warnings = useMemo(
    () =>
      mode === 'audiobook'
        ? (validateScript(draft.script, {
            mappedNames: Object.keys(draft.voiceCast).filter((name) =>
              Boolean(draft.voiceCast[name]),
            ),
            profileIds: profiles.map((profile) => profile.id),
            emotionIgnoredBy,
          }) as ScriptWarning[])
        : [],
    [draft.script, draft.voiceCast, mode, profiles, emotionIgnoredBy],
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const otherModeRendering = session.active !== null && session.active !== mode;
  const blocker = generateBlocker({
    mode,
    busyElsewhere: otherModeRendering,
    importing,
    tts: ttsBlocker,
    usable,
    voicesReady,
    duplicateLexicon: duplicateWords(draft.lexicon),
  });
  const resumeRender = (jobId: string) => void renderLongform(mode, jobId);
  const startRender = () => void renderLongform(mode);
  // One element, rendered either pinned in the setup pane or, once that is folded away, over the editor.
  const generatePanel = (
    <div className="flex min-h-0 flex-col gap-2">
      <RenderRecovery
        mode={mode}
        active={session.active !== null}
        disabled={locked || ttsBlocker !== null}
        disabledReason={
          ttsBlocker !== null ? t('engines.none_ready_title') : t('audiobook.recovery_busy')
        }
        onResume={resumeRender}
      />
      <GeneratePanel
        mode={mode}
        session={session}
        blocker={blocker}
        onGenerate={startRender}
        onStop={stopLongform}
      />
    </div>
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
  const downloadableCues = cueSheetFor(draft.outputChapters, draft.output, (n) =>
    t('audiobook.chapter_n', { n }),
  );
  const saveCueSheet = async () => {
    if (downloadableCues === null) return;
    setLocalError(null);
    const { body, filename } = downloadableCues;
    try {
      const textFile = new Blob([body], { type: 'text/plain;charset=utf-8' });
      await saveLocalFile(textFile, filename);
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
          className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }), 'ms-auto')}
        >
          {mode === 'stories' ? <BookOpenTextIcon /> : <AudioLinesIcon />}
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
          library={
            <div className="studio-scrollbar min-h-0 flex-1 overflow-y-auto p-3.5 text-[13px]">
              <ProjectSettings
                bare
                mode={mode}
                draft={draft}
                disabled={locked}
                onChange={set}
                onBusy={setImporting}
              />
            </div>
          }
          libraryLabel={t('projects.title')}
          onCollapsedChange={setSidebarCollapsed}
          className="space-y-3"
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
                {t('stories.voiceLabel')}
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
            {mode === 'stories' ? (
              <StorySpeed draft={draft} disabled={locked} onChange={set} />
            ) : null}
            <div className="space-y-2">
              <h2 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <LanguagesIcon className="size-4" aria-hidden="true" />
                {t('clone.language')}
              </h2>
              <EngineLanguagePicker
                operation={ttsOperation}
                value={draft.language}
                options={languageOptions}
                disabled={locked}
                onValueChange={(language) => set({ language })}
              />
            </div>
            <div className="space-y-2">
              <h2 className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <FileAudioIcon className="size-4" aria-hidden="true" />
                {t('audiobook.format')}
              </h2>
              <ToggleGroup
                aria-label={t('audiobook.format')}
                value={[draft.format]}
                disabled={locked}
                onValueChange={(values) => {
                  const format = FORMATS.find((candidate) => candidate === values[0]);
                  if (format) set({ format });
                }}
                spacing={0}
                className="grid w-full grid-cols-2 gap-0.5 rounded-lg bg-muted/40 p-0.5 ring-1 ring-inset ring-border/50"
              >
                {FORMATS.map((format) => (
                  <ToggleGroupItem
                    key={format}
                    value={format}
                    size="sm"
                    className="h-7 w-full rounded-md text-muted-foreground hover:bg-background/60 aria-pressed:bg-background aria-pressed:text-foreground aria-pressed:shadow-sm"
                  >
                    {t('audiobook.format_' + format)}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
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
        </SecondarySidebar>
        <section className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          {/* Grows to fill the pane but never shrinks below its content: the
              editor keeps its height and the notes below it stay in normal flow,
              so a short window scrolls the pane instead of stacking them. */}
          <div className="mx-auto flex w-full max-w-6xl flex-[1_0_auto] flex-col gap-4 px-6 py-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-medium">
                  <BookOpenTextIcon className="size-4 text-muted-foreground" />
                  {t('clone.script')}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t(mode === 'stories' ? 'stories.scriptSubtitle' : 'audiobook.subtitle')}
                </p>
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
                disabled={resetIsUnavailable}
                onClick={() => setScriptResetRequested(true)}
              >
                <Trash2Icon />
                {t('stories.clearScript')}
              </Button>
              <ConfirmDialog
                open={scriptResetRequested}
                onOpenChange={(nextOpen) => setScriptResetRequested(nextOpen)}
                title={t('stories.clearScript')}
                description={resetMessage}
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
                <div className="flex min-h-64 flex-1 flex-col gap-3 lg:flex-row">
                  <ChapterOutline
                    script={draft.script}
                    caret={scriptCaret}
                    textareaRef={audiobookInput}
                    disabled={locked}
                    onJump={setScriptCaret}
                  />
                  <textarea
                    dir="auto"
                    ref={audiobookInput}
                    aria-label={t('clone.script')}
                    className="min-h-64 min-w-0 flex-1 resize-none bg-transparent text-base leading-7 outline-none"
                    value={draft.script}
                    placeholder={t('audiobook.script_placeholder')}
                    disabled={locked}
                    onSelect={(event) => setScriptCaret(event.currentTarget.selectionStart)}
                    onClick={(event) => setScriptCaret(event.currentTarget.selectionStart)}
                    onKeyUp={(event) => setScriptCaret(event.currentTarget.selectionStart)}
                    onChange={(e) => {
                      set({ script: e.target.value });
                      setScriptCaret(e.target.selectionStart);
                      if (warningsDismissed) setWarningsDismissed(false);
                    }}
                  />
                </div>
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
                count: stats.chapters,
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
              <div className="sticky bottom-0 z-10 -mx-3 mt-auto shrink-0 rounded-2xl border border-border/70 bg-background/75 p-3.5 shadow-lg backdrop-blur-lg">
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
                    {downloadableCues !== null && (
                      <Button variant="ghost" size="sm" onClick={() => void saveCueSheet()}>
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
