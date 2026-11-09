import { ProjectHandoff } from './project-handoff';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { PlayInDockButton } from '@/components/now-playing-dock';
import { takePlaybackKind } from '@/lib/audio/playback-kind';
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
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
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
  SearchIcon,
  TrashIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { useDeleteProfile } from '@/hooks/use-profiles';
import { deleteHistoryItem } from '@/lib/api/history';
import { Input } from '@/components/ui/input';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { runRendererTask } from '@/lib/global-error-recovery';
import { isImeComposing } from '@/lib/ime';
import { useProfiles } from '@/hooks/use-profiles';
import { useHistory } from '@/hooks/use-history';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import type { HistoryItem, Profile } from '@/lib/api/types';
import {
  loadTranscriptions,
  removeTranscription,
  subscribeTranscriptions,
  type TranscriptEntry,
} from '@shared/utils/transcriptionsStore';
import { preferredTranscript } from '@shared/utils/transcriptionFormat';
import {
  useDubSession,
  openDubProject,
  attachDubProject,
  detachDubProject,
} from '../dub/dub-session';
import type { DubProject } from './project-format';
import { RenderDisclosure, renderRecipe, type RenderRecord } from './render-details';

import {
  LIBRARY_KINDS,
  countByFilter,
  matchesFilter,
  type Filter,
  type LibraryKind,
} from './library-filter';

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
  const deleteProfile = useDeleteProfile();
  const history = useHistory();
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>(loadTranscriptions);
  const [rename, setRename] = useState('');
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [view, setView] = useState<'grid' | 'list'>('list');
  const [confirm, setConfirm] = useState<{
    id: string;
    kind: 'dub' | Mode;
    action: 'open' | 'rename';
  } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState<LibraryRow[] | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletedCount, setDeletedCount] = useState(0);
  const cancelDelete = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<string | null>(null);
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
  const apply = () =>
    act(async () => {
      if (!confirm) return;
      if (confirm.kind !== 'dub') {
        if (longformSession.state.active) throw new Error('Longform work is active');
        if (confirm.action === 'rename') await projectLibrary.rename(confirm.id, rename);
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
        if (confirm.action === 'rename') {
          await apiJson(path, {
            method: 'PATCH',
            body: JSON.stringify({ name: rename.trim() }),
          });
          if (session.project?.id === confirm.id) {
            attachDubProject(session, {
              ...session.project,
              name: rename.trim(),
            });
          }
        } else {
          const project = await apiJson<DubProject>(path);
          if (!openDubProject(project)) throw new Error('Dubbing work is active');
          await navigate({ to: '/dub' });
        }
      }
      setConfirm(null);
    });

  const requestDelete = (targets: LibraryRow[]) => {
    if (locked || !targets.length) return;
    setConfirm(null);
    setDeleteError(null);
    setDeleting(targets);
  };
  const deleteRows = async () => {
    if (pending.current || locked || !deleting?.length) return;
    pending.current = true;
    setBusy(true);
    setDeleteError(null);
    const failed: LibraryRow[] = [];
    const removed = new Set<string>();
    const messages: string[] = [];
    try {
      for (const row of deleting) {
        try {
          const id = encodeURIComponent(row.id);
          if (row.projectKind === 'dub') {
            await apiJson('/projects/' + id, { method: 'DELETE' });
            detachDubProject(row.id);
          } else if (row.projectKind) {
            await projectLibrary.remove(row.id);
            for (const mode of ['stories', 'audiobook'] as const) {
              if (longformSession.state.drafts[mode].projectId === row.id)
                editLongform(mode, { projectId: null });
            }
          } else if (row.profile) await deleteProfile.mutateAsync(row.id);
          else if (row.take) await deleteHistoryItem(row.id);
          else if (row.transcript) removeTranscription(row.transcript.id);
          else if (row.export) await apiJson('/export/history/' + id, { method: 'DELETE' });
          else if (row.render) await apiJson('/longform/jobs/' + id, { method: 'DELETE' });
          removed.add(row.key);
        } catch (error) {
          failed.push(row);
          messages.push(row.name + ': ' + describeError(error));
        }
      }
      setSelected((current) => new Set([...current].filter((key) => !removed.has(key))));
      setDeletedCount(removed.size);
      setDeleting(failed.length ? failed : null);
      setDeleteError(messages.length ? messages.join('\n') : null);
      await Promise.all(
        [
          'projects',
          'longform-projects',
          'profiles',
          'history',
          'export-history',
          'longform-jobs',
        ].map((key) => client.invalidateQueries({ queryKey: [key] })),
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

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
        // The type label already says Stories or Audiobooks; no second copy of it.
        subtitle: '',
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
        subtitle: renderRecipe(render),
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

  const counts = useMemo(() => countByFilter(rows.map((row) => row.kind)), [rows]);
  const visible = rows.filter((row) => {
    if (!matchesFilter(row.kind, filter)) return false;
    const term = search.trim().toLocaleLowerCase();
    return !term || `${row.name} ${row.subtitle}`.toLocaleLowerCase().includes(term);
  });
  const selectedRows = visible.filter((row) => selected.has(row.key));
  const filterItems: {
    id: Filter;
    label: string;
    icon: typeof FolderOpenIcon;
  }[] = [
    { id: 'all', label: t('projects.all_projects'), icon: FolderOpenIcon },
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
      // Never carry the previous voice's transcript onto the reused take's voice.
      refText: profiles.data?.find((profile) => profile.id === take.profile_id)?.ref_text ?? '',
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
        <div className="ms-auto">
          <ProjectHandoff
            disabled={locked}
            selection={
              selectedRows.length === 1 && selectedRows[0].projectKind
                ? {
                    id: selectedRows[0].id,
                    kind: selectedRows[0].projectKind,
                    name: selectedRows[0].name,
                  }
                : undefined
            }
          />
        </div>
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
              <Fragment key={item.id}>
                {item.id === LIBRARY_KINDS[0] && (
                  <p className="px-2.5 pt-3 pb-1 text-[11px] font-medium text-muted-foreground">
                    {t('projects.library_group')}
                  </p>
                )}
                <Button
                  className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
                  variant={filter === item.id ? 'secondary' : 'ghost'}
                  aria-pressed={filter === item.id}
                  onClick={() => {
                    setFilter(item.id);
                    setSelected(new Set());
                  }}
                >
                  <Icon />
                  <span className="min-w-0 flex-1 truncate text-left">{item.label}</span>
                  <span className="rounded-md bg-background/45 px-1.5 py-0.5 text-[11px] tabular-nums text-muted-foreground">
                    {counts[item.id]}
                  </span>
                </Button>
              </Fragment>
            );
          })}
        </SecondarySidebar>
        <section className="min-w-0 flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-6xl space-y-5">
            <div className="flex items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="search"
                  className="pl-9"
                  aria-label={t('projects.search_placeholder')}
                  placeholder={t('projects.search_placeholder')}
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setSelected(new Set());
                  }}
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
            {visible.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={locked}
                  onClick={() => setSelected(new Set(visible.map((row) => row.key)))}
                >
                  {t('projects.select_visible')}
                </Button>
                {selectedRows.length > 0 && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={locked}
                      onClick={() => setSelected(new Set())}
                    >
                      {t('common.clear')}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={locked}
                      onClick={() => requestDelete(selectedRows)}
                    >
                      <TrashIcon />
                      {t('projects.delete_selected', { count: selectedRows.length })}
                    </Button>
                  </>
                )}
              </div>
            )}
            {deletedCount > 0 && (
              <p role="status" className="text-sm text-muted-foreground">
                {t('projects.deleted_count', { count: deletedCount })}
              </p>
            )}
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
                      <input
                        type="checkbox"
                        className="size-4 shrink-0 accent-primary"
                        disabled={locked}
                        aria-label={t('projects.select_item', { name: row.name })}
                        checked={selected.has(row.key)}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setSelected((current) => {
                            const next = new Set(current);
                            if (checked) next.add(row.key);
                            else next.delete(row.key);
                            return next;
                          });
                        }}
                      />
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
                        {row.render ? <RenderDisclosure render={row.render} /> : null}
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
                          <PlayInDockButton
                            src={apiPath('/audio/' + encodeURIComponent(row.take.audio_path))}
                            source={'take-' + row.take.id}
                            title={row.name}
                            kind={takePlaybackKind(row.take.mode)}
                            duration={row.take.duration_seconds}
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
                        <PlayInDockButton
                          src={apiPath('/audio/' + encodeURIComponent(row.render.output))}
                          source={'projects-render-' + row.render.job_id}
                          title={row.name}
                          kind={row.render.type === 'story' ? 'story' : 'audiobook'}
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
                        </>
                      )}
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t('common.delete') + ' ' + row.name}
                        disabled={locked}
                        onClick={() => requestDelete([row])}
                      >
                        <TrashIcon />
                      </Button>
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
                                if (isImeComposing(event)) return;
                                if (event.key === 'Enter' && rename.trim()) void apply();
                                if (event.key === 'Escape') setConfirm(null);
                              }}
                            />
                          ) : (
                            <span className="flex-1">
                              {t('projectActions.open', { name: row.name })}
                            </span>
                          )}
                          <Button
                            variant="default"
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
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleting(null);
        }}
      >
        <DialogContent
          showCloseButton={false}
          initialFocus={cancelDelete}
          className="sm:max-w-lg"
          style={{ background: 'var(--popover)' }}
        >
          <DialogHeader>
            <DialogTitle>
              {t('projects.delete_selected', { count: deleting?.length ?? 0 })}
            </DialogTitle>
            <DialogDescription>{t('projects.delete_warning')}</DialogDescription>
          </DialogHeader>
          <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">
            {deleting?.map((row) => (
              <li key={row.key} className="break-words">
                {row.name}
              </li>
            ))}
          </ul>
          {deleteError && (
            <p
              role="alert"
              className="max-h-32 overflow-y-auto whitespace-pre-wrap text-sm text-destructive"
            >
              {deleteError}
            </p>
          )}
          <DialogFooter>
            <Button
              ref={cancelDelete}
              variant="ghost"
              disabled={busy}
              onClick={() => setDeleting(null)}
            >
              {t('common.cancel')}
            </Button>
            <Button variant="destructive" disabled={locked} onClick={() => void deleteRows()}>
              <TrashIcon />
              {busy ? t('common.loading') : t(deleteError ? 'common.retry' : 'common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
