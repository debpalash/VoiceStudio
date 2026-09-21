import { getBridge, isMac } from '@/components/bridge';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { ProfileAvatar } from '@/components/profile-avatar';
import { Button } from '@/components/ui/button';
import { useHistory } from '@/hooks/use-history';
import { useProfiles } from '@/hooks/use-profiles';
import { brandArtwork, brandIcon } from '@/lib/brand';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { setWorkspace, useWorkspace } from '@/lib/store/workspace';
import { selectCloneProfile } from '@/lib/store/reference';
import { openTake } from '@/lib/store/takes';
import { blankLongformDraft, editLongform } from '@/features/longform/longform-session';
import { projectLibrary, type LongformProject } from '@/features/longform/project-library';
import { openDubProject } from '@/features/dub/dub-session';
import type { DubProject } from '@/features/projects/project-format';
import { Link, useNavigate } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { apiJson } from '@/lib/api/client';
import { readDraft, restoreDesignProfile, writeDraft } from '@/features/design/design-draft';
import {
  ArrowRightIcon,
  AudioLinesIcon,
  BookOpenIcon,
  FilmIcon,
  FingerprintIcon,
  FolderOpenIcon,
  LibraryIcon,
  MicIcon,
  PanelLeftOpenIcon,
  WandSparklesIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { runRendererTask } from '@/lib/global-error-recovery';

interface Destination {
  to: string;
  label: string;
  description: string;
  Icon: LucideIcon;
  count?: number;
}

interface ExportRecord {
  id: string;
  filename: string;
  destination_path: string;
  mode?: string;
}

export function HomePage() {
  const { t } = useTranslation();
  const { libraryOpen } = useWorkspace();
  const navigate = useNavigate();
  const openSite = () => {
    const bridge = getBridge();
    const url =
      'https://voicestudio.sh/?utm_source=voicestudio&utm_medium=desktop&utm_campaign=home_banner';
    if (bridge) void bridge.files.openExternal(url);
    else window.open(url, '_blank', 'noopener,noreferrer');
  };
  const { data: profiles = [] } = useProfiles();
  const { data: history = [] } = useHistory();
  const { data: exports = [] } = useQuery({
    queryKey: ['export-history'],
    queryFn: ({ signal }) => apiJson<ExportRecord[]>('/export/history', { signal }),
    staleTime: 30_000,
  });
  const { data: dubProjects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: ({ signal }) => apiJson<DubProject[]>('/projects', { signal }),
  });
  const { data: longformProjects = [] } = useQuery({
    queryKey: ['longform-projects'],
    queryFn: projectLibrary.list,
  });
  const cloned = profiles.filter((profile) => profile.kind !== 'design');
  const designed = profiles.filter((profile) => profile.kind === 'design');
  const destinations: Destination[] = [
    {
      to: '/clone',
      label: t('nav.clone'),
      description: t('clone.reference_hint'),
      Icon: FingerprintIcon,
      count: cloned.length,
    },
    {
      to: '/design',
      label: t('designWorkspace.title'),
      description: t('clone.describe_hint'),
      Icon: WandSparklesIcon,
      count: designed.length,
    },
    {
      to: '/dub',
      label: t('dubWorkspace.title'),
      description: t('dub.supported_formats'),
      Icon: FilmIcon,
    },
    {
      to: '/stories',
      label: t('nav.stories'),
      description: t('stories.subtitle'),
      Icon: AudioLinesIcon,
    },
    {
      to: '/audiobook',
      label: t('audiobook.title'),
      description: t('audiobook.subtitle'),
      Icon: BookOpenIcon,
    },
    {
      to: '/gallery',
      label: t('nav.gallery'),
      description: t('gallery.subtitle'),
      Icon: LibraryIcon,
    },
    {
      to: '/transcriptions',
      label: t('nav.transcribe'),
      description: t('demo.dictation_lede'),
      Icon: MicIcon,
    },
    {
      to: '/tools',
      label: t('tools.title'),
      description: t('tools.desc'),
      Icon: WrenchIcon,
    },
  ];
  const recentProjects = [
    ...dubProjects.map((project) => ({
      kind: 'dub' as const,
      project,
      updatedAt:
        (project.updated_at || 0) < 1e12
          ? (project.updated_at || 0) * 1000
          : project.updated_at || 0,
    })),
    ...longformProjects.map((project) => ({
      kind: 'longform' as const,
      project,
      updatedAt: project.updatedAt,
    })),
  ]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, 4);
  const openProject = async (
    item: { kind: 'dub'; project: DubProject } | { kind: 'longform'; project: LongformProject },
  ) => {
    if (item.kind === 'dub') {
      const project = await apiJson<DubProject>('/projects/' + encodeURIComponent(item.project.id));
      if (openDubProject(project)) await navigate({ to: '/dub' });
      return;
    }
    editLongform(item.project.mode, {
      ...blankLongformDraft(),
      ...structuredClone(item.project.draft),
      projectId: item.project.id,
    });
    await navigate({
      to: item.project.mode === 'stories' ? '/stories' : '/audiobook',
    });
  };
  const useProfile = async (profile: (typeof profiles)[number]) => {
    if (profile.kind === 'design') {
      const current = readDraft();
      const restored = restoreDesignProfile(profile, current.seed);
      writeDraft({
        ...current,
        attrs: restored.attrs,
        seed: restored.seed,
        profileId: restored.profileId,
      });
      patchCloneSettings({ language: restored.language });
      await navigate({ to: '/design' });
      return;
    }
    selectCloneProfile(profile);
    await navigate({ to: '/clone' });
  };
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        {isMac() &&
          (libraryOpen ? (
            <img src={brandIcon} alt="" className="size-4" />
          ) : (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('clone.toggle_sidebar')}
              onClick={() => setWorkspace({ libraryOpen: true })}
            >
              <PanelLeftOpenIcon />
            </Button>
          ))}
        <h1 className="text-sm font-medium">{t('app.name')}</h1>
        <Link
          to="/projects"
          className="ml-auto inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
        >
          <FolderOpenIcon className="size-4" />
          {t('projects.title')}
        </Link>
      </WorkspaceHeader>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl px-6 py-7">
          <section className="relative isolate overflow-hidden rounded-3xl border border-border/50 bg-card/30 px-7 py-8">
            <img
              src={brandArtwork}
              alt=""
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 right-0 -z-10 h-full w-2/3 object-cover object-right opacity-35 [mask-image:linear-gradient(90deg,transparent,black)]"
            />
            <div className="max-w-xl">
              <div className="mb-4 flex size-11 items-center justify-center rounded-2xl bg-primary/10">
                <img src={brandIcon} alt="" className="size-7" />
              </div>
              <h2 className="text-3xl font-semibold tracking-[-0.035em]">{t('app.name')}</h2>
              <p className="mt-3 max-w-lg text-sm leading-6 text-muted-foreground">
                {t('app.tagline')}
                <button
                  type="button"
                  onClick={openSite}
                  className="ml-1 text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
                >
                  voicestudio.sh
                </button>
              </p>
            </div>
          </section>

          <section className="mt-6 grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-3">
            {destinations.map(({ to, label, description, Icon, count }) => (
              <Link
                key={to}
                to={to}
                className="group flex min-h-36 flex-col rounded-2xl border border-border/50 bg-card/25 p-4 outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Icon className="size-4" />
                  </div>
                  {count !== undefined && (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
                      {count}
                    </span>
                  )}
                </div>
                <h3 className="mt-4 flex items-center gap-2 text-sm font-semibold">
                  {label}
                  <ArrowRightIcon className="size-3.5 -translate-x-1 opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
                </h3>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                  {description}
                </p>
              </Link>
            ))}
          </section>

          {exports.length > 0 && (
            <section className="mt-8">
              <div className="mb-2 flex items-center justify-between px-1">
                <h2 className="flex items-center gap-2 text-sm font-medium">
                  <FolderOpenIcon className="size-4 text-muted-foreground" />
                  {t('projects.exports')}
                </h2>
                <Link
                  to="/projects"
                  className="text-xs text-muted-foreground hover:text-foreground"
                >
                  {t('projects.all')}
                </Link>
              </div>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-2">
                {exports.slice(0, 4).map((record) => (
                  <Link
                    key={record.id}
                    to="/projects"
                    className="group flex min-w-0 items-center gap-3 rounded-xl border border-border/50 bg-card/20 px-3 py-2.5 outline-none transition-colors hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <FolderOpenIcon className="size-4 shrink-0 text-primary" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {record.filename ||
                          record.destination_path.split(/[\\/]/).pop() ||
                          t('projects.export')}
                      </span>
                      {record.mode && (
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                          {record.mode}
                        </span>
                      )}
                    </span>
                    <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground opacity-60 transition-opacity group-hover:opacity-100" />
                  </Link>
                ))}
              </div>
            </section>
          )}

          {recentProjects.length > 0 && (
            <section className="mt-8">
              <div className="mb-2 flex items-center justify-between px-1">
                <h2 className="flex items-center gap-2 text-sm font-medium">
                  <FolderOpenIcon className="size-4 text-muted-foreground" />
                  {t('projects.title')}
                </h2>
                <Link
                  to="/projects"
                  className="text-xs text-muted-foreground hover:text-foreground"
                >
                  {t('projects.all')}
                </Link>
              </div>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-2">
                {recentProjects.map((item) => {
                  const Icon =
                    item.kind === 'dub'
                      ? FilmIcon
                      : item.project.mode === 'stories'
                        ? AudioLinesIcon
                        : BookOpenIcon;
                  const label =
                    item.kind === 'dub'
                      ? t('projects.dub_projects')
                      : t(item.project.mode === 'stories' ? 'nav.stories' : 'audiobook.title');
                  return (
                    <button
                      key={item.kind + ':' + item.project.id}
                      type="button"
                      className="group flex min-w-0 items-center gap-3 rounded-xl border border-border/50 bg-card/20 px-3 py-2.5 text-left outline-none transition-colors hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => void openProject(item)}
                    >
                      <Icon className="size-4 shrink-0 text-primary" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">
                          {item.project.name}
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                          {label}
                        </span>
                      </span>
                      <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground opacity-60 transition-opacity group-hover:opacity-100" />
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          {(profiles.length > 0 || history.length > 0) && (
            <section className="mt-8 grid gap-6 lg:grid-cols-2">
              {profiles.length > 0 && (
                <div>
                  <div className="mb-2 flex items-center justify-between px-1">
                    <h2 className="text-sm font-medium">{t('clone.saved_profiles')}</h2>
                    <Link
                      to="/projects"
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      {t('projects.all')}
                    </Link>
                  </div>
                  <div className="rounded-2xl border border-border/50 bg-card/20 p-2">
                    {profiles.slice(0, 4).map((profile) => (
                      <div
                        key={profile.id}
                        className="flex min-w-0 items-center gap-3 rounded-xl px-2.5 py-2 hover:bg-accent/50"
                      >
                        <ProfileAvatar name={profile.name} imageUrl={profile.image_url} />
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">
                          {profile.name}
                        </span>
                        <Button size="sm" variant="ghost" onClick={() => void useProfile(profile)}>
                          {t('clone.select_profile')}
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {history.length > 0 && (
                <div>
                  <div className="mb-2 flex items-center justify-between px-1">
                    <h2 className="text-sm font-medium">{t('clone.history_title')}</h2>
                    <Link
                      to="/projects"
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      {t('projects.all')}
                    </Link>
                  </div>
                  <div className="rounded-2xl border border-border/50 bg-card/20 p-2">
                    {history.slice(0, 4).map((take) => (
                      <button
                        key={take.id}
                        type="button"
                        className="flex w-full min-w-0 items-center gap-3 rounded-xl px-2.5 py-2 text-left hover:bg-accent/50"
                        onClick={() => {
                          openTake(take);
                          runRendererTask('Open recent voice take', () =>
                            navigate({ to: '/clone' }),
                          );
                        }}
                      >
                        <AudioLinesIcon className="size-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate text-sm">{take.text}</span>
                        <ArrowRightIcon className="size-3.5 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
