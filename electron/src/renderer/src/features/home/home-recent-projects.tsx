import {
  ArrowRightIcon,
  AudioLinesIcon,
  BookOpenIcon,
  FilmIcon,
  FolderOpenIcon,
} from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import type { LongformProject } from '@/features/longform/project-library';
import type { DubProject } from '@/features/projects/project-format';

export type HomeRecentProject =
  | { kind: 'dub'; project: DubProject }
  | { kind: 'longform'; project: LongformProject };

export function HomeRecentProjects({
  items,
  onOpen,
}: {
  items: readonly HomeRecentProject[];
  onOpen: (item: HomeRecentProject) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  if (!items.length) return null;
  return (
    <section className="mt-8">
      <div className="mb-2 flex items-center justify-between px-1">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <FolderOpenIcon className="size-4 text-muted-foreground" />
          {t('projects.title')}
        </h2>
        <Link to="/projects" className="text-xs text-muted-foreground hover:text-foreground">
          {t('projects.all')}
        </Link>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-2">
        {items.map((item) => {
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
              onClick={() => void onOpen(item)}
            >
              <Icon className="size-4 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{item.project.name}</span>
                <span className="mt-0.5 block truncate text-xs text-muted-foreground">{label}</span>
              </span>
              <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground opacity-60 transition-opacity group-hover:opacity-100" />
            </button>
          );
        })}
      </div>
    </section>
  );
}
