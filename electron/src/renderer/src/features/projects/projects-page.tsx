import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { PipelineFailure } from '@/components/pipeline-failure';
import { ProfileAvatar } from '@/components/profile-avatar';
import { getBridge } from '@/components/bridge';
import {
  blankLongformDraft,
  editLongform,
  longformSession,
  useLongformSession,
  type Mode,
} from '../longform/longform-session';
import { projectLibrary } from '../longform/project-library';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AudioLinesIcon,
  BookOpenIcon,
  ClockIcon,
  DownloadIcon,
  FileTextIcon,
  FilmIcon,
  FingerprintIcon,
  FolderOpenIcon,
  Grid2X2Icon,
  ListIcon,
  MicIcon,
  PencilIcon,
  SaveIcon,
  SearchIcon,
  TrashIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { runRendererTask } from '@/lib/global-error-recovery';
import { useProfiles } from '@/hooks/use-profiles';
import { useHistory } from '@/hooks/use-history';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import type { HistoryItem, Profile } from '@/lib/api/types';
import {
  loadTranscriptions,
  subscribeTranscriptions,
  type TranscriptEntry,
} from '../../../../../../frontend/src/utils/transcriptionsStore';
import { preferredTranscript } from '../../../../../../frontend/src/utils/transcriptionFormat';
import {
  useDubSession,
  openDubProject,
  attachDubProject,
  detachDubProject,
} from '../dub/dub-session';
import { projectPayload, type DubProject } from './project-format';
import { RenderDetails, renderRecipe, type RenderRecord } from './render-details';

type LibraryKind =
  | 'dub'
  | 'stories'
  | 'audiobooks'
  | 'profiles'
  | 'transcripts'
  | 'history'
  | 'exports';
type Filter = 'all' | LibraryKind;

interface ExportRecord {
  id: string;
  filename: string;
  destination_path: string;
  mode?: string;
  created_at?: number | string;
}

interface LibraryRow {
  key: string;
  id: string;
  kind: LibraryKind;
  name: string;
  subtitle: string;
  updatedAt: number;
  projectKind?: 'dub' | Mode;
  profile?: Profile;
  transcript?: TranscriptEntry;
  take?: HistoryItem;
  export?: ExportRecord;
  render?: RenderRecord;
}

function timestamp(value: number | string | undefined): number {
  if (!value) return 0;
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return timestamp(numeric);
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function when(value: number): string {
  if (!value) return '';
  return new Intl.DateTimeFormat([], {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(value);
}

export function ProjectsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const session = useDubSession();
  const longform = useLongformSession();
  const profiles = useProfiles();
  const history = useHistory();
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>(loadTranscriptions);
  const [rename, setRename] = useState('');
  const [name, setName] = useState(session.project?.name || session.filename);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [view, setView] = useState<'grid' | 'list'>('list');
  const [confirm, setConfirm] = useState<{
    id: string;
    kind: 'dub' | Mode;
    action: 'open' | 'delete' | 'rename';
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const query = useQuery({
    queryKey: ['projects'],
    queryFn: ({ signal }) => apiJson<DubProject[]>('/projects', { signal }),
  });
  const books = useQuery({
    queryKey: ['longform-projects'],
    queryFn: projectLibrary.list,
  });
  const exports = useQuery({
    queryKey: ['export-history'],
    queryFn: ({ signal }) => apiJson<ExportRecord[]>('/export/history', { signal }),
  });
  const renders = useQuery({
    queryKey: ['longform-jobs'],
    queryFn: ({ signal }) => apiJson<{ jobs: RenderRecord[] }>('/longform/jobs', { signal }),
  });

  useEffect(() => {
    return subscribeTranscriptions(setTranscripts);
  }, []);

  const locked =
    busy ||
    !!longform.active ||
    !!session.recovery ||
    !['idle', 'editing', 'done'].includes(session.phase);
  const act = async (work: () => Promise<void>) => {
    if (pending.current || locked) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await work();
      await Promise.all([
        client.invalidateQueries({ queryKey: ['projects'] }),
        client.invalidateQueries({ queryKey: ['longform-projects'] }),
      ]);
    } catch (error) {
      setError(describeError(error));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  const save = (copy: boolean) =>
    act(async () => {
      const snapshot = session;
      const payload = projectPayload(snapshot, name);
      const id = copy ? undefined : snapshot.project?.id;
      const result = await apiJson<{ id: string }>(
        '/projects' + (id ? '/' + encodeURIComponent(id) : ''),
        { method: id ? 'PUT' : 'POST', body: JSON.stringify(payload) },
      );
      attachDubProject(snapshot, { ...payload, id: result.id });
      setSaved(true);
    });
  const apply = () =>
    act(async () => {
      if (!confirm) return;
      if (confirm.kind !== 'dub') {
        if (longformSession.state.active) throw new Error('Longform work is active');
        if (confirm.action === 'delete') {
          await projectLibrary.remove(confirm.id);
          for (const mode of ['stories', 'audiobook'] as const) {
            if (longformSession.state.drafts[mode].projectId === confirm.id)
              editLongform(mode, { projectId: null });
          }
        } else if (confirm.action === 'rename') await projectLibrary.rename(confirm.id, rename);
        else {
          const project = (await projectLibrary.list()).find(
            (project) => project.id === confirm.id,
          );
          if (!project || longformSession.state.active) throw new Error('Project unavailable');
          editLongform(project.mode, {
            ...blankLongformDraft(),
            ...structuredClone(project.draft),
            projectId: project.id,
          });
          await navigate({
            to: project.mode === 'stories' ? '/stories' : '/audiobook',
          });
        }
      } else {
        const path = '/projects/' + encodeURIComponent(confirm.id);
        if (confirm.action === 'delete') {
          await apiJson(path, { method: 'DELETE' });
          detachDubProject(confirm.id);
        } else if (confirm.action === 'rename') {
          await apiJson(path, {
            method: 'PATCH',
            body: JSON.stringify({ name: rename.trim() }),
          });
          if (session.project?.id === confirm.id) {
            attachDubProject(session, {
              ...session.project,
              name: rename.trim(),
            });
            setName(rename.trim());
          }
        } else {
          const project = await apiJson<DubProject>(path);
          if (!openDubProject(project)) throw new Error('Dubbing work is active');
          await navigate({ to: '/dub' });
        }
      }
      setConfirm(null);
    });

  const rows = useMemo<LibraryRow[]>(() => {
    const result: LibraryRow[] = [];
    for (const project of query.data || []) {
      result.push({
        key: 'dub:' + project.id,
        id: project.id,
        name: project.name,
        kind: 'dub',
        projectKind: 'dub',
        subtitle: t('projects.dub_projects'),
        updatedAt: timestamp(project.updated_at),
      });
    }
    for (const project of books.data || []) {
      result.push({
        key: project.mode + ':' + project.id,
        id: project.id,
        name: project.name,
        kind: project.mode === 'stories' ? 'stories' : 'audiobooks',
        projectKind: project.mode,
        subtitle: t(project.mode === 'stories' ? 'nav.stories' : 'audiobook.title'),
        updatedAt: timestamp(project.updatedAt),
      });
    }
    for (const profile of profiles.data || []) {
      result.push({
        key: 'profile:' + profile.id,
        id: profile.id,
        name: profile.name,
        kind: 'profiles',
        subtitle: t(
          profile.kind === 'design' ? 'projects.designed_voice' : 'projects.cloned_voice',
        ),
        updatedAt: timestamp(profile.created_at),
        profile,
      });
    }
    for (const transcript of transcripts) {
      result.push({
        key: 'transcript:' + transcript.id,
        id: String(transcript.id),
        name: preferredTranscript(transcript) || t('projects.transcription'),
        kind: 'transcripts',
        subtitle: transcript.language === 'unknown' ? '' : transcript.language,
        updatedAt: timestamp(transcript.timestamp),
        transcript,
      });
    }
    for (const take of history.data || []) {
      result.push({
        key: 'take:' + take.id,
        id: take.id,
        name: take.text || t('projects.generated_audio'),
        kind: 'history',
        subtitle: take.language || take.mode,
        updatedAt: timestamp(take.created_at),
        take,
      });
    }
    for (const record of exports.data || []) {
      result.push({
        key: 'export:' + record.id,
        id: record.id,
        name:
          record.filename || record.destination_path.split(/[\\/]/).pop() || t('projects.export'),
        kind: 'exports',
        subtitle: record.mode || '',
        updatedAt: timestamp(record.created_at),
        export: record,
      });
    }
    for (const render of renders.data?.jobs || []) {
      result.push({
        key: 'render:' + render.job_id,
        id: render.job_id,
        name: render.title || render.output,
        kind: render.type === 'story' ? 'stories' : 'audiobooks',
        // Voice · speed · engine · length: what tells two renders of a book apart.
        subtitle:
          renderRecipe(render) ||
          t(render.type === 'story' ? 'projects.story' : 'projects.audiobook'),
        updatedAt: timestamp(render.created_at),
        render,
      });
    }
    return result.sort((a, b) => b.updatedAt - a.updatedAt);
  }, [
    books.data,
    exports.data,
    history.data,
    profiles.data,
    query.data,
    renders.data,
    t,
    transcripts,
  ]);

  const counts = useMemo(() => {
    const next: Record<Filter, number> = {
      all: rows.length,
      dub: 0,
      stories: 0,
      audiobooks: 0,
      profiles: 0,
      transcripts: 0,
      history: 0,
      exports: 0,
    };
    for (const row of rows) next[row.kind]++;
    return next;
  }, [rows]);
  const visible = rows.filter((row) => {
    if (filter !== 'all' && row.kind !== filter) return false;
    const term = search.trim().toLocaleLowerCase();
    return !term || `${row.name} ${row.subtitle}`.toLocaleLowerCase().includes(term);
  });
  const filterItems: {
    id: Filter;
    label: string;
    icon: typeof FolderOpenIcon;
  }[] = [
    { id: 'all', label: t('projects.all'), icon: FolderOpenIcon },
    { id: 'dub', label: t('projects.dub_projects'), icon: FilmIcon },
    { id: 'stories', label: t('projects.stories'), icon: AudioLinesIcon },
    { id: 'audiobooks', label: t('projects.audiobooks'), icon: BookOpenIcon },
    {
      id: 'profiles',
      label: t('projects.voice_profiles'),
      icon: FingerprintIcon,
    },
    { id: 'transcripts', label: t('projects.transcripts'), icon: MicIcon },
    { id: 'history', label: t('projects.history'), icon: AudioLinesIcon },
    { id: 'exports', label: t('projects.exports'), icon: DownloadIcon },
  ];
  const queryError = [
    query.error,
    books.error,
    profiles.error,
    history.error,
    exports.error,
    renders.error,
  ].find(Boolean);
  const failure = error || (queryError ? describeError(queryError) : null);
  const loading =
    query.isPending ||
    books.isPending ||
    profiles.isPending ||
    history.isPending ||
    exports.isPending ||
    renders.isPending;

  const useProfile = async (profile: Profile) => {
    selectCloneProfile(profile);
    await navigate({ to: '/clone' });
  };
  const reuseTake = async (take: HistoryItem) => {
    patchCloneSettings({
      text: take.text,
      language: take.language || 'Auto',
      selectedProfileId: take.profile_id,
      instruct: take.instruct || '',
    });
    await navigate({ to: '/clone' });
  };
  const reveal = async (record: ExportRecord) => {
    const bridge = getBridge();
    if (bridge) await bridge.files.revealPath(record.destination_path);
    else
      await apiJson('/export/reveal', {
        method: 'POST',
        body: JSON.stringify({ path: record.destination_path }),
      });
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('projects.title')}</h1>
      </WorkspaceHeader>
      <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
        <SecondarySidebar
          title={t('projects.title')}
          icon={FolderOpenIcon}
          variant="navigation"
          className="space-y-1"
        >
          {filterItems.map((item) => {
            const Icon = item.icon;
            return (
              <Button
                key={item.id}
                className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
                variant={filter === item.id ? 'secondary' : 'ghost'}
                aria-pressed={filter === item.id}
                onClick={() => setFilter(item.id)}
              >
                <Icon />
                <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
                <span className="rounded-md bg-background/45 px-1.5 py-0.5 text-[11px] tabular-nums text-muted-foreground">
                  {counts[item.id]}
                </span>
              </Button>
            );
          })}
        </SecondarySidebar>
        <section className="min-w-0 flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-6xl space-y-5">
            {session.jobId && (
              <form
                className="space-y-3 rounded-xl border border-border/50 bg-card/35 p-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void save(false);
                }}
              >
                <label htmlFor="project-name" className="text-sm font-medium">
                  {t('sidebar.save_project')}
                </label>
                <div className="flex flex-wrap gap-2">
                  <Input
                    id="project-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    disabled={locked}
                    className="min-w-48 flex-1"
                  />
                  <Button type="submit" disabled={locked || !name.trim()}>
                    <SaveIcon />
                    {t('common.save')}
                  </Button>
                  {session.project && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={locked || !name.trim()}
                      onClick={() => void save(true)}
                    >
                      {t('sidebar.save_new_project')}
                    </Button>
                  )}
                </div>
                {saved && (
                  <p role="status" className="text-sm text-muted-foreground">
                    {t('app.toast_project_saved')}
                  </p>
                )}
              </form>
            )}
            <div className="flex items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="search"
                  className="pl-9"
                  aria-label={t('projects.search_placeholder')}
                  placeholder={t('projects.search_placeholder')}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>
              <div className="flex rounded-lg border border-border/50 bg-muted/20 p-0.5">
                <Button
                  size="icon-sm"
                  variant={view === 'grid' ? 'secondary' : 'ghost'}
                  aria-label={t('projects.card_grid')}
                  aria-pressed={view === 'grid'}
                  onClick={() => setView('grid')}
                >
                  <Grid2X2Icon />
                </Button>
                <Button
                  size="icon-sm"
                  variant={view === 'list' ? 'secondary' : 'ghost'}
                  aria-label={t('projects.list')}
                  aria-pressed={view === 'list'}
                  onClick={() => setView('list')}
                >
                  <ListIcon />
                </Button>
              </div>
            </div>
            {failure && (
              <PipelineFailure
                fallback={failure}
                onDismiss={error ? () => setError(null) : undefined}
                action={
                  queryError ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setError(null);
                        void query.refetch();
                        void books.refetch();
                        void profiles.refetch();
                        void history.refetch();
                        void exports.refetch();
                        void renders.refetch();
                      }}
                    >
                      {t('common.retry')}
                    </Button>
                  ) : undefined
                }
              />
            )}
            {loading && rows.length === 0 && <p role="status">{t('common.loading')}</p>}
            {!loading && visible.length === 0 && (
              <p className="py-12 text-center text-sm text-muted-foreground">
                {search ? t('projects.no_matches', { query: search }) : t('projects.empty')}
              </p>
            )}
            <div
              className={
                view === 'grid'
                  ? 'grid grid-cols-[repeat(auto-fill,minmax(min(280px,100%),1fr))] gap-3'
                  : 'divide-y divide-border/50'
              }
            >
              {visible.map((row) => {
                const typeLabel = t(
                  row.kind === 'dub'
                    ? 'projects.dub_projects'
                    : row.kind === 'stories'
                      ? 'projects.stories'
                      : row.kind === 'audiobooks'
                        ? 'projects.audiobooks'
                        : row.kind === 'profiles'
                          ? 'projects.voice_profiles'
                          : row.kind === 'transcripts'
                            ? 'projects.transcripts'
                            : row.kind === 'history'
                              ? 'projects.history'
                              : 'projects.exports',
                );
                return (
                  <article
                    key={row.key}
                    className={
                      view === 'grid'
                        ? 'flex min-w-0 flex-col gap-4 rounded-xl border border-border/50 bg-card/35 p-4'
                        : 'flex min-w-0 flex-wrap items-center gap-3 py-3'
                    }
                  >
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      {row.profile ? (
                        <ProfileAvatar name={row.profile.name} imageUrl={row.profile.image_url} />
                      ) : (
                        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/60 text-muted-foreground">
                          {row.kind === 'dub' ? (
                            <FilmIcon className="size-4" />
                          ) : row.kind === 'audiobooks' ? (
                            <BookOpenIcon className="size-4" />
                          ) : row.kind === 'profiles' ? (
                            <FingerprintIcon className="size-4" />
                          ) : row.kind === 'transcripts' ? (
                            <FileTextIcon className="size-4" />
                          ) : row.kind === 'exports' ? (
                            <DownloadIcon className="size-4" />
                          ) : (
                            <AudioLinesIcon className="size-4" />
                          )}
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        {row.projectKind ? (
                          <Button
                            className="h-auto max-w-full justify-start p-0 text-left font-medium whitespace-normal hover:bg-transparent"
                            variant="ghost"
                            disabled={locked}
                            onClick={() =>
                              setConfirm({
                                id: row.id,
                                kind: row.projectKind!,
                                action: 'open',
                              })
                            }
                          >
                            <span className="line-clamp-2">{row.name}</span>
                          </Button>
                        ) : (
                          <h2 className="line-clamp-2 text-sm font-medium">{row.name}</h2>
                        )}
                        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                          <span>{typeLabel}</span>
                          {row.subtitle && <span>{row.subtitle}</span>}
                          {row.updatedAt > 0 && (
                            <span className="inline-flex items-center gap-1">
                              <ClockIcon className="size-3" />
                              {when(row.updatedAt)}
                            </span>
                          )}
                        </p>
                        {row.render && (
                          <details className="mt-1.5">
                            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                              {t('projects.render_details')}
                            </summary>
                            <div className="mt-2">
                              <RenderDetails render={row.render} />
                            </div>
                          </details>
                        )}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {row.profile && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void useProfile(row.profile!)}
                        >
                          {t('clone.select_profile')}
                        </Button>
                      )}
                      {row.transcript && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            const transcript = row.transcript!;
                            patchCloneSettings({
                              text: preferredTranscript(transcript),
                              ...(transcript.language && transcript.language !== 'unknown'
                                ? { language: transcript.language }
                                : {}),
                            });
                            runRendererTask('Open profile in voice cloning', () =>
                              navigate({ to: '/clone' }),
                            );
                          }}
                        >
                          <FileTextIcon />
                          {t('clone.text_label')}
                        </Button>
                      )}
                      {row.take && (
                        <>
                          <AudioPreviewButton
                            src={apiPath('/audio/' + encodeURIComponent(row.take.audio_path))}
                            source={'projects-take-' + row.take.id}
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void reuseTake(row.take!)}
                          >
                            {t('clone.history_reuse')}
                          </Button>
                        </>
                      )}
                      {row.export && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void reveal(row.export!)}
                        >
                          <FolderOpenIcon />
                          {t('clone.reveal')}
                        </Button>
                      )}
                      {row.render && (
                        <AudioPreviewButton
                          src={apiPath('/audio/' + encodeURIComponent(row.render.output))}
                          source={'projects-render-' + row.render.job_id}
                        />
                      )}
                      {row.projectKind && (
                        <>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={t('sidebar.rename') + ' ' + row.name}
                            disabled={locked}
                            onClick={() => {
                              setRename(row.name);
                              setConfirm({
                                id: row.id,
                                kind: row.projectKind!,
                                action: 'rename',
                              });
                            }}
                          >
                            <PencilIcon />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={t('common.delete') + ' ' + row.name}
                            disabled={locked}
                            onClick={() =>
                              setConfirm({
                                id: row.id,
                                kind: row.projectKind!,
                                action: 'delete',
                              })
                            }
                          >
                            <TrashIcon />
                          </Button>
                        </>
                      )}
                    </div>
                    {row.projectKind &&
                      confirm?.id === row.id &&
                      confirm.kind === row.projectKind && (
                        <div className="flex w-full flex-wrap items-center gap-3 rounded-lg bg-muted/40 p-3 text-sm">
                          {confirm.action === 'rename' ? (
                            <Input
                              className="min-w-48 flex-1"
                              autoFocus
                              aria-label={t('stories.projectName')}
                              value={rename}
                              onChange={(event) => setRename(event.target.value)}
                              disabled={locked}
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' && rename.trim()) void apply();
                                if (event.key === 'Escape') setConfirm(null);
                              }}
                            />
                          ) : (
                            <span className="flex-1">
                              {t(
                                confirm.action === 'delete'
                                  ? 'projectActions.delete'
                                  : 'projectActions.open',
                                { name: row.name },
                              )}
                            </span>
                          )}
                          <Button
                            variant={confirm.action === 'delete' ? 'destructive' : 'default'}
                            disabled={locked || (confirm.action === 'rename' && !rename.trim())}
                            onClick={() => void apply()}
                          >
                            {t(
                              confirm.action === 'rename'
                                ? 'sidebar.rename_save'
                                : 'common.confirm',
                            )}
                          </Button>
                          <Button variant="ghost" disabled={busy} onClick={() => setConfirm(null)}>
                            {t('common.cancel')}
                          </Button>
                        </div>
                      )}
                  </article>
                );
              })}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
