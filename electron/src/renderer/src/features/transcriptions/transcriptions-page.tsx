import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { LiveDictation } from './live-dictation';
import { DictationSetup } from './dictation-setup';
import {
  segTimeRange,
  formatTranscriptExport,
  preferredTranscript,
} from '@shared/utils/transcriptionFormat';
import { RecordingInputs } from '@/components/recording-inputs';
import { AgentFixButton } from '@/components/agent-fix-button';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertCircleIcon,
  Clock3Icon,
  KeyboardIcon,
  LanguagesIcon,
  PauseIcon,
  PlayIcon,
  XIcon,
  CopyIcon,
  DownloadIcon,
  FileTextIcon,
  GaugeIcon,
  HistoryIcon,
  MicIcon,
  ScanTextIcon,
  SquareIcon,
  UploadIcon,
  Trash2Icon,
} from 'lucide-react';
import { toast } from 'sonner';
import { runRendererTask } from '@/lib/global-error-recovery';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { WaveformPlayer } from '@/components/waveform-player';
import { useRecording } from '@/hooks/use-recording';
import { useDictationSelection } from '@/hooks/use-dictation-selection';
import { useEngines } from '@/hooks/use-engines';
import { apiJson, describeError } from '@/lib/api/client';
import { setCloneSetting } from '@/lib/store/clone-settings';
import { cn } from '@/lib/utils';
import {
  addTranscription,
  loadTranscriptions,
  subscribeTranscriptions,
  removeTranscription,
  TRANSCRIPTIONS_KEY,
  TRANSCRIPTION_EVENT,
  type TranscriptEntry,
} from '@shared/utils/transcriptionsStore';
import { beginAppActivity } from '@/lib/app-activity';
import { formatRelative } from '@/features/clone/format';
import { useModelCatalogue } from '@/features/settings/model-catalogue-query';
import { useNativeShortcut } from '@/hooks/use-native-dictation';
import { recordActionBreadcrumb } from '@/lib/report-breadcrumb';
import { saveLocalFile } from '@/lib/local-export';
import { formatShortcut } from '@shared/utils/dictationShortcut';

type TranscriptionMode = 'fast' | 'accurate';
const TRANSCRIPTION_MODE_KEY = 'voicestudio.transcription.mode';

function initialTranscriptionMode(): TranscriptionMode {
  try {
    return localStorage.getItem(TRANSCRIPTION_MODE_KEY) === 'accurate' ? 'accurate' : 'fast';
  } catch {
    return 'fast';
  }
}

export function TranscriptionsPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const live = useMemo(() => new LiveDictation(), []);
  const liveState = useSyncExternalStore(live.subscribe, live.getSnapshot);
  const liveBusy = ['starting', 'recording', 'transcribing'].includes(liveState.stage);
  useEffect(() => () => live.cancel(), [live]);
  const [entries, setEntries] = useState(loadTranscriptions);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [mode, setMode] = useState<TranscriptionMode>(initialTranscriptionMode);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const engines = useEngines();
  const dictation = useDictationSelection();
  const shortcut = useNativeShortcut();
  const catalogue = useModelCatalogue();
  const dictationReadiness = useQuery({
    queryKey: ['transcription-readiness'],
    queryFn: () => apiJson<{ ready: boolean }>('/dictation/readiness'),
    refetchInterval: (query) => (query.state.data?.ready === false ? 15_000 : false),
  });
  const accurateReadiness = useQuery({
    queryKey: ['transcription-readiness', 'accurate'],
    queryFn: () => apiJson<{ ready: boolean }>('/dictation/readiness?purpose=transcribe'),
    enabled: mode === 'accurate',
    refetchInterval: (query) =>
      mode === 'accurate' && query.state.data?.ready === false ? 15_000 : false,
  });
  const fileReadiness = mode === 'accurate' ? accurateReadiness : dictationReadiness;
  const selectedModel = useMemo(() => {
    if (mode === 'fast') return dictation.data?.model?.label;
    const modelId = engines.data?.asr.active_model;
    return catalogue.data?.models.find((entry) => entry.repo_id === modelId)?.label ?? modelId;
  }, [catalogue.data?.models, dictation.data?.model?.label, engines.data?.asr.active_model, mode]);
  const chooseMode = (next: string | undefined) => {
    if (next !== 'fast' && next !== 'accurate') return;
    setMode(next);
    setFailed(null);
    try {
      localStorage.setItem(TRANSCRIPTION_MODE_KEY, next);
    } catch {
      // A denied storage write should not prevent the current selection.
    }
  };
  const transcribe = async (audio: File) => {
    if (request.current) return;
    const reportMode = mode;
    recordActionBreadcrumb(`transcribe:${reportMode}:start`);
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setFailed(null);
    setFile(audio);
    const finishActivity = beginAppActivity(mode === 'accurate' ? 'transcription' : 'dictation');
    try {
      const ready = await apiJson<{ ready: boolean }>(
        mode === 'accurate' ? '/dictation/readiness?purpose=transcribe' : '/dictation/readiness',
        { signal: controller.signal },
      );
      if (!ready.ready) {
        recordActionBreadcrumb(`transcribe:${reportMode}:error`);
        void fileReadiness.refetch();
        return;
      }
      const body = new FormData();
      body.set('audio', audio);
      body.set('mode', mode);
      const refinement = await apiJson<{ auto: boolean }>('/api/settings/dictation-refinement', {
        signal: controller.signal,
      }).catch(() => ({ auto: false }));
      if (controller.signal.aborted) return;
      body.set('refine', String(refinement.auto));
      const result = await apiJson<Partial<TranscriptEntry>>('/transcribe', {
        method: 'POST',
        body,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        const saved = addTranscription(result);
        setSelectedId(saved.id);
        recordActionBreadcrumb(`transcribe:${reportMode}:complete`);
      }
    } catch (error) {
      recordActionBreadcrumb(
        `transcribe:${reportMode}:${controller.signal.aborted ? 'cancel' : 'error'}`,
      );
      if (!controller.signal.aborted) setFailed(describeError(error));
    } finally {
      finishActivity();
      if (request.current === controller) {
        request.current = null;
        setBusy(false);
      }
    }
  };
  const recording = useRecording((audio) => void transcribe(audio));
  const capturing =
    liveBusy || recording.isStarting || recording.isRecording || recording.isCleaning;
  useEffect(() => {
    const unsubscribe = subscribeTranscriptions(setEntries);
    return () => {
      unsubscribe();
      request.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!file) {
      setUrl(null);
      return;
    }
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  const visible = useMemo(
    () =>
      entries.filter((entry) =>
        (preferredTranscript(entry) + ' ' + entry.text + ' ' + entry.language)
          .toLocaleLowerCase()
          .includes(search.trim().toLocaleLowerCase()),
      ),
    [entries, search],
  );
  const selected = visible.find((entry) => entry.id === selectedId);
  useEffect(() => {
    const next = visible[0]?.id ?? null;
    if (!visible.some((entry) => entry.id === selectedId) && selectedId !== next) {
      setSelectedId(next);
    }
  }, [selectedId, visible]);
  const remove = (id: number) => {
    try {
      removeTranscription(id);
      if (selectedId === id) setSelectedId(null);
    } catch {
      toast.error(t('modelSettings.failed'));
    }
  };
  const clearAll = () => {
    if (busy || capturing) return;
    try {
      localStorage.setItem(TRANSCRIPTIONS_KEY, '[]');
      setEntries([]);
      setSelectedId(null);
      setConfirmClear(false);
      window.dispatchEvent(new CustomEvent(TRANSCRIPTION_EVENT));
    } catch {
      toast.error(t('modelSettings.failed'));
    }
  };
  const exportText = async (list: TranscriptEntry[]) => {
    try {
      const text = formatTranscriptExport(list);
      const result = await saveLocalFile(
        new Blob([text], { type: 'text/plain;charset=utf-8' }),
        `transcriptions_${new Date().toISOString().slice(0, 10)}.txt`,
      );
      if (!result.canceled) toast.success(t('transcriptions.exported'));
    } catch (cause) {
      toast.error(t('clone.download_failed', { message: describeError(cause) }));
    }
  };
  const startLiveDictation = () =>
    live.start((entry) => {
      const saved = addTranscription(entry);
      setEntries(loadTranscriptions());
      setSelectedId(saved.id);
    }, recording.selectedInputId || undefined);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('nav.transcribe')}</h1>
      </WorkspaceHeader>
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('projects.history')}
          icon={HistoryIcon}
          variant="library"
          className="flex flex-col"
          meta={entries.length}
        >
          {entries.length === 0 ? (
            <div className="grid min-h-32 flex-1 place-items-center px-4 text-center text-muted-foreground">
              <div className="space-y-2">
                <span className="mx-auto grid size-9 place-items-center rounded-xl border border-border/45 bg-background/45 text-muted-foreground">
                  <HistoryIcon className="size-4" aria-hidden="true" />
                </span>
                <p className="max-w-40 text-xs leading-5">{t('transcriptions.empty_title')}</p>
              </div>
            </div>
          ) : null}
          {entries.length > 0 ? (
            <Input
              aria-label={t('transcriptions.search_placeholder')}
              placeholder={t('transcriptions.search_placeholder')}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          ) : null}
          {entries.length > 0 && (
            <div className="mt-3 min-h-0 flex-1 space-y-1 overflow-y-auto">
              {visible.map((entry) => (
                <button
                  key={entry.id}
                  className={cn(
                    'w-full rounded-xl border border-transparent px-3 py-2.5 text-left text-sm transition-[color,background-color,border-color,box-shadow] hover:border-border/55 hover:bg-accent/60',
                    selectedId === entry.id &&
                      'border-primary/20 bg-primary/[0.07] shadow-[inset_2px_0_0_var(--primary)]',
                  )}
                  aria-pressed={selectedId === entry.id}
                  onClick={() => setSelectedId(entry.id)}
                >
                  <p className="line-clamp-2 text-[13px] leading-5 font-medium">
                    {preferredTranscript(entry)}
                  </p>
                  <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                    <span className="inline-flex min-w-0 items-center gap-1">
                      <Clock3Icon className="size-3 shrink-0" aria-hidden="true" />
                      <span className="truncate">
                        {formatRelative(entry.timestamp, i18n.language)}
                      </span>
                    </span>
                    {entry.language && entry.language !== 'unknown' ? (
                      <span className="inline-flex min-w-0 items-center gap-1">
                        <LanguagesIcon className="size-3 shrink-0" aria-hidden="true" />
                        <span className="truncate">{entry.language}</span>
                      </span>
                    ) : null}
                    {Number(entry.duration_s) > 0 ? (
                      <span className="shrink-0 tabular-nums">
                        {Number(entry.duration_s).toFixed(1)}s
                      </span>
                    ) : null}
                  </div>
                </button>
              ))}
            </div>
          )}
          {entries.length > 0 && (
            <Button variant="ghost" onClick={() => void exportText(entries)}>
              <DownloadIcon />
              {t('transcriptions.export_title')}
            </Button>
          )}
          {entries.length > 0 && confirmClear ? (
            <div className="space-y-2 p-3 text-xs">
              <p>
                {t('transcriptions.clear_title')} ({entries.length})
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={busy || capturing}
                  onClick={clearAll}
                >
                  {t('common.confirm')}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmClear(false)}>
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          ) : entries.length > 0 ? (
            <Button
              variant="ghost"
              disabled={!entries.length || busy || capturing}
              onClick={() => setConfirmClear(true)}
            >
              <Trash2Icon />
              {t('transcriptions.clear_title')}
            </Button>
          ) : null}
        </SecondarySidebar>
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="border-b border-border/50 px-6 py-4">
            <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-muted/20 p-2">
              <ToggleGroup
                value={[mode]}
                onValueChange={(value) => chooseMode(value[0])}
                disabled={busy || capturing}
                size="sm"
                spacing={0}
                aria-label={t('transcriptions.mode_label')}
                className="rounded-lg border border-border/50 bg-background/25 p-0.5"
              >
                <ToggleGroupItem
                  value="fast"
                  title={t('transcriptions.mode_fast_desc')}
                  className="gap-1.5"
                >
                  <GaugeIcon />
                  {t('transcriptions.mode_fast')}
                </ToggleGroupItem>
                <ToggleGroupItem
                  value="accurate"
                  title={t('transcriptions.mode_accurate_desc')}
                  className="gap-1.5"
                >
                  <ScanTextIcon />
                  {t('transcriptions.mode_accurate')}
                </ToggleGroupItem>
              </ToggleGroup>
              {selectedModel ? (
                <span
                  className="inline-flex min-w-0 max-w-72 items-center gap-1.5 px-1 text-xs text-muted-foreground"
                  title={`${t(
                    mode === 'accurate' ? 'engineSidebar.asr' : 'engineSidebar.dictation',
                  )} · ${selectedModel}`}
                >
                  {mode === 'accurate' ? (
                    <ScanTextIcon className="size-3.5 shrink-0" aria-hidden="true" />
                  ) : (
                    <KeyboardIcon className="size-3.5 shrink-0" aria-hidden="true" />
                  )}
                  <span className="shrink-0">
                    {t(mode === 'accurate' ? 'engineSidebar.asr' : 'engineSidebar.dictation')}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{selectedModel}</span>
                </span>
              ) : null}
              <input
                ref={input}
                type="file"
                accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
                className="hidden"
                onChange={(event) => {
                  const audio = event.target.files?.[0];
                  event.target.value = '';
                  if (audio) void transcribe(audio);
                }}
              />
              {(entries.length > 0 || busy || capturing) && (
                <>
                  <Button
                    variant="outline"
                    disabled={busy || capturing || !fileReadiness.data?.ready}
                    onClick={() => input.current?.click()}
                  >
                    <UploadIcon />
                    {t('clone.upload_audio')}
                  </Button>
                  <Button
                    variant={recording.isRecording ? 'destructive' : 'outline'}
                    disabled={
                      busy ||
                      liveBusy ||
                      recording.isStarting ||
                      recording.isCleaning ||
                      !fileReadiness.data?.ready
                    }
                    onClick={() =>
                      recording.isRecording ? recording.stop() : void recording.start()
                    }
                  >
                    {recording.isRecording ? <SquareIcon /> : <MicIcon />}
                    {t(recording.isRecording ? 'clone.stop_recording' : 'clone.record')}
                    {recording.isRecording && (
                      <span className="tabular-nums">{Math.floor(recording.seconds)}s</span>
                    )}
                  </Button>
                  {!liveBusy ? (
                    <Button
                      variant="outline"
                      disabled={busy || capturing || !dictationReadiness.data?.ready}
                      title={
                        shortcut.data?.accelerator
                          ? `${t('engineSidebar.dictation')} · ${formatShortcut(shortcut.data.accelerator)}`
                          : undefined
                      }
                      onClick={() => void startLiveDictation()}
                    >
                      <KeyboardIcon />
                      {t('engineSidebar.dictation')}
                      {shortcut.data?.accelerator ? (
                        <kbd className="hidden rounded border border-border/50 bg-background/30 px-1.5 py-0.5 font-mono text-[10px] font-normal text-muted-foreground lg:inline">
                          {formatShortcut(shortcut.data.accelerator)}
                        </kbd>
                      ) : null}
                    </Button>
                  ) : (
                    <>
                      {liveState.stage === 'recording' && (
                        <>
                          <Button
                            variant="outline"
                            onClick={() => live.pause()}
                            aria-label={t(liveState.paused ? 'common.resume' : 'common.pause')}
                          >
                            {liveState.paused ? <PlayIcon /> : <PauseIcon />}
                            {t(liveState.paused ? 'common.resume' : 'common.pause')}
                          </Button>
                          <Button onClick={() => void live.stop()}>
                            <SquareIcon />
                            {t('common.stop')}
                          </Button>
                        </>
                      )}
                      <Button variant="ghost" onClick={() => live.cancel()}>
                        {t('common.cancel')}
                      </Button>
                    </>
                  )}
                  {busy && (
                    <>
                      <span role="status" className="text-sm text-muted-foreground">
                        {t('referenceAsr.busy')}
                      </span>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          request.current?.abort();
                        }}
                      >
                        {t('common.cancel')}
                      </Button>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
          {mode === 'fast' && !dictationReadiness.data?.ready && !dictationReadiness.isPending && (
            <DictationSetup onReady={() => void dictationReadiness.refetch()} />
          )}
          {mode === 'accurate' &&
            !accurateReadiness.data?.ready &&
            !accurateReadiness.isPending && (
              <div className="border-b border-border/50 px-6 py-3">
                <div className="mx-auto flex w-full max-w-4xl items-center gap-3 rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-sm">
                  <ScanTextIcon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1">{t('transcriptions.missing_model')}</span>
                  <AgentFixButton request="Restore accurate file transcription. Install and activate the best compatible local ASR model through VoiceStudio, preserve the selected media and mode, then verify transcription is ready to start." />
                  <Link
                    to="/settings/models/$family"
                    params={{ family: 'asr' }}
                    className={buttonVariants({
                      variant: 'outline',
                      size: 'sm',
                    })}
                  >
                    {t('engineSidebar.asr')}
                  </Link>
                </div>
              </div>
            )}
          <div className="border-b border-border/50 px-6 py-3">
            <details className="mx-auto w-full max-w-4xl">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                {t('recording.input_device')}
              </summary>
              <div className="mt-3 max-w-xl">
                <RecordingInputs rec={recording} disabled={busy || liveBusy} />
              </div>
            </details>
          </div>
          {(liveBusy ||
            liveState.stage === 'error' ||
            (liveState.stage === 'done' && !liveState.text)) && (
            <div className="space-y-2 border-b border-border/50 px-6 py-4">
              <div
                className="flex items-center gap-3 text-sm"
                role={liveState.stage === 'error' ? 'alert' : 'status'}
              >
                <KeyboardIcon className="size-4 text-primary" />
                <span>
                  {liveState.stage === 'error'
                    ? t(
                        liveState.issue === 'model'
                          ? 'transcriptions.missing_model'
                          : 'transcriptions.failed',
                      )
                    : liveState.stage === 'done'
                      ? t('capture.no_speech')
                      : liveState.modelStage
                        ? t(
                            liveState.modelStage === 'downloading'
                              ? 'capture.model_downloading'
                              : 'capture.model_loading',
                          )
                        : t(
                            liveState.stage === 'recording'
                              ? liveState.paused
                                ? 'common.pause'
                                : 'capture.listening_label'
                              : liveState.stage === 'transcribing'
                                ? 'capture.transcribing_label'
                                : 'common.loading',
                          )}
                </span>
                {liveState.issue === 'model' && (
                  <>
                    <AgentFixButton request="Restore live dictation readiness. Install and activate the best compatible dictation model through VoiceStudio, preserve this transcription workspace, and verify recording can start." />
                    <Link
                      to="/settings/models/$family"
                      params={{ family: 'dictation' }}
                      className={buttonVariants({ variant: 'ghost', size: 'sm' })}
                    >
                      {t('nav.settings')}
                    </Link>
                  </>
                )}
                {liveState.stage === 'error' && liveState.text && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      void navigator.clipboard
                        .writeText(liveState.text)
                        .then(() => toast.success(t('transcriptions.copied')))
                        .catch(() => toast.error(t('transcriptions.copy_failed')))
                    }
                  >
                    <CopyIcon />
                    {t('transcriptions.copy')}
                  </Button>
                )}
                {!liveBusy && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => live.cancel()}
                    aria-label={t('common.dismiss')}
                  >
                    <XIcon />
                  </Button>
                )}
              </div>
              {liveState.text && (
                <p
                  className="max-w-4xl select-text text-sm leading-relaxed whitespace-pre-wrap"
                  aria-live="polite"
                >
                  {liveState.text}
                </p>
              )}
            </div>
          )}
          {failed && (
            <div className="mx-6 mt-4 rounded-xl border border-border/60 bg-muted/20 px-4 py-3 text-sm">
              <div className="flex items-center gap-3">
                <AlertCircleIcon className="size-4 shrink-0 text-destructive" />
                <span role="alert" className="min-w-0 flex-1 font-medium">
                  {t('transcriptions.failed')}
                </span>
                <Button
                  variant="ghost"
                  disabled={!file || busy}
                  onClick={() => file && void transcribe(file)}
                >
                  {t('backend.retry')}
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t('common.dismiss')}
                  onClick={() => setFailed(null)}
                >
                  <XIcon />
                </Button>
              </div>
              <details className="mt-1 pl-7 text-xs text-muted-foreground">
                <summary className="cursor-pointer rounded py-1 focus-visible:outline-ring">
                  {t('profileIdentity.details')}
                </summary>
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 p-3 font-mono text-xs">
                  {failed}
                </pre>
                <Link to="/settings/logs" className="mt-2 inline-block underline">
                  {t('settings.logs')}
                </Link>
              </details>
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {selected ? (
              <article className="mx-auto max-w-4xl space-y-5">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-3 text-xs text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <Clock3Icon className="size-3" aria-hidden="true" />
                      {new Date(selected.timestamp).toLocaleString()}
                    </span>
                    {selected.language && selected.language !== 'unknown' ? (
                      <span className="inline-flex items-center gap-1">
                        <LanguagesIcon className="size-3" aria-hidden="true" />
                        {selected.language}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        void navigator.clipboard
                          .writeText(preferredTranscript(selected))
                          .then(() => toast.success(t('transcriptions.copied')))
                          .catch(() => toast.error(t('transcriptions.copy_failed')))
                      }
                    >
                      <CopyIcon />
                      {t('transcriptions.copy')}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => void exportText([selected])}>
                      <DownloadIcon />
                      {t('transcriptions.export')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t('transcriptions.delete')}
                      onClick={() => remove(selected.id)}
                    >
                      <Trash2Icon />
                    </Button>
                  </div>
                </div>
                <p className="whitespace-pre-wrap text-base leading-7">
                  {preferredTranscript(selected)}
                </p>
                {selected.refined_text && selected.refined_text.trim() !== selected.text.trim() ? (
                  <details className="rounded-lg border border-border/50 p-4">
                    <summary className="cursor-pointer text-sm font-medium text-muted-foreground">
                      {t('transcriptions.original_transcript')}
                    </summary>
                    <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-muted-foreground">
                      {selected.text}
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        void navigator.clipboard
                          .writeText(selected.text)
                          .then(() => toast.success(t('transcriptions.copied')))
                          .catch(() => toast.error(t('transcriptions.copy_failed')))
                      }
                    >
                      <CopyIcon />
                      {t('transcriptions.copy')}
                    </Button>
                  </details>
                ) : null}
                {selected.segments?.length > 0 && (
                  <details className="text-sm">
                    <summary className="cursor-pointer text-muted-foreground">
                      {t('transcriptions.segments_title')}
                    </summary>
                    <div className="mt-3 space-y-3">
                      {selected.segments.map((segment, index) => (
                        <p key={index}>
                          <span className="mr-3 font-mono text-xs text-muted-foreground">
                            {segTimeRange(segment)}
                          </span>
                          {segment.text}
                        </p>
                      ))}
                    </div>
                  </details>
                )}
                <Button
                  variant="outline"
                  onClick={() => {
                    const language =
                      selected.language && selected.language !== 'unknown'
                        ? selected.language
                        : undefined;
                    setCloneSetting('text', preferredTranscript(selected));
                    if (language) setCloneSetting('language', language);
                    runRendererTask('Reuse transcription in voice cloning', () =>
                      navigate({ to: '/clone' }),
                    );
                  }}
                >
                  <FileTextIcon />
                  {t('clone.history_reuse')}
                </Button>
              </article>
            ) : (
              <div className="mx-auto flex min-h-[28rem] max-w-lg flex-col items-center justify-center px-6 text-center">
                <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                  {search ? <FileTextIcon className="size-6" /> : <MicIcon className="size-6" />}
                </div>
                <h2 className="text-lg font-semibold">
                  {t(search ? 'transcriptions.empty_search_title' : 'transcriptions.empty_title')}
                </h2>
                {search ? (
                  <p className="mt-2 text-sm text-muted-foreground">
                    {t('transcriptions.empty_search_desc')}
                  </p>
                ) : !busy && !capturing ? (
                  <div className="mt-6 flex flex-wrap justify-center gap-2">
                    <Button
                      disabled={busy || capturing || !dictationReadiness.data?.ready}
                      onClick={() => void startLiveDictation()}
                    >
                      <KeyboardIcon />
                      {t('transcriptions.capture')}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || capturing || !fileReadiness.data?.ready}
                      onClick={() => input.current?.click()}
                    >
                      <UploadIcon />
                      {t('clone.upload_audio')}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || capturing || !fileReadiness.data?.ready}
                      onClick={() => void recording.start()}
                    >
                      <MicIcon />
                      {t('clone.record')}
                    </Button>
                  </div>
                ) : null}
              </div>
            )}
          </div>
          {url && (
            <div className="shrink-0 border-t border-border/50 p-4">
              <p className="mb-2 truncate text-xs text-muted-foreground">{file?.name}</p>
              <WaveformPlayer key={url} src={url} source="transcription-reference" />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
