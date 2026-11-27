import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { runRendererTask } from '@/lib/global-error-recovery';
import { isImeComposing } from '@/lib/ime';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useHistory } from '@/hooks/use-history';
import { useProfiles } from '@/hooks/use-profiles';
import { useGenerateClone } from '@/hooks/use-generate';
import { setWorkspace } from '@/lib/store/workspace';
import { openTake } from '@/lib/store/takes';
import { selectCloneProfile } from '@/lib/store/reference';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { SETTINGS_ENTRIES } from '@/features/settings/settings-index';
import { dubProjectsQuery, longformProjectsQuery } from '@/features/projects/project-queries';
import { cn } from '@/lib/utils';
import { Kbd } from '@/components/ui/kbd';
import { isMac } from '@/components/bridge';
import { KeyboardCheatsheet } from '@/components/keyboard-cheatsheet';
import { useTheme } from '@/hooks/use-theme';
import { formatRelative } from '@/features/clone/format';
import { matchingCommands, recentTakes, takeLength, type CommandItem } from './command-palette-items';
import { CommandPaletteSearchStatus } from './command-palette-search-status';
import {
  AudioLinesIcon,
  BlocksIcon,
  BookOpenIcon,
  BoxesIcon,
  CrownIcon,
  FilmIcon,
  FolderIcon,
  HistoryIcon,
  HomeIcon,
  LayersIcon,
  LibraryIcon,
  ListPlusIcon,
  MicIcon,
  MoonIcon,
  PhoneCallIcon,
  PlayIcon,
  SettingsIcon,
  SunIcon,
  KeyboardIcon,
  UsersRoundIcon,
  WandSparklesIcon,
  WorkflowIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react';
import {
  readDraft,
  replaceRecipe,
  restoreDesignProfile,
  writeDraft,
} from '@/features/design/design-draft';

export interface CommandPaletteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  shortcutsOpen: boolean;
  onShortcutsOpenChange: (open: boolean) => void;
  openNonce: number;
}

export function CommandPaletteDialog({
  open,
  onOpenChange,
  shortcutsOpen,
  onShortcutsOpenChange,
  openNonce,
}: CommandPaletteDialogProps) {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const currentProjectSearch = useLocation({
    select: ({ pathname, search }) => (pathname === '/projects' ? search.q : undefined),
  });
  const [query, setQuery] = useState('');
  const needle = query.trim();
  const dubs = useQuery({ ...dubProjectsQuery, enabled: open && Boolean(needle) });
  const books = useQuery({ ...longformProjectsQuery, enabled: open && Boolean(needle) });
  // The highlight follows the command, not its position, so results arriving
  // (a saved profile, history) cannot shift Enter onto a different command.
  // Undefined selects the first result by default. Null requires an explicit selection.
  const [activeId, setActiveId] = useState<string | null>();
  const history = useHistory();
  const profiles = useProfiles();
  const generation = useGenerateClone();
  useEffect(() => {
    setQuery('');
    setActiveId(undefined);
  }, [openNonce]);
  const workspaceGroup = t('nav.workspaces');
  const actionGroup = t('nav.clone');
  const personaGroup = t('nav.persona');
  const takeGroup = t('clone.history_title');
  const modifier = isMac() ? '⌘' : 'Ctrl';
  const source: CommandItem[] = [
    {
      id: 'home',
      label: t('app.name'),
      keywords: [t('nav.home'), 'home'],
      group: workspaceGroup,
      icon: HomeIcon,
      run: () => navigate({ to: '/' }),
    },
    {
      id: 'clone',
      label: t('nav.clone'),
      keywords: [t('nav.clone_short'), 'clone', 'cloning'],
      group: workspaceGroup,
      icon: AudioLinesIcon,
      run: () => navigate({ to: '/clone' }),
    },
    {
      id: 'stories',
      label: t('nav.stories'),
      keywords: [t('stories.newStory'), t('projects.title'), 'story', 'new project'],
      group: workspaceGroup,
      icon: AudioLinesIcon,
      run: () => navigate({ to: '/stories' }),
    },
    {
      id: 'dub',
      label: t('nav.dub'),
      keywords: [t('dubWorkspace.title'), 'dubbing', 'video', 'translate'],
      group: workspaceGroup,
      icon: FilmIcon,
      run: () => navigate({ to: '/dub' }),
    },
    {
      id: 'batch',
      label: t('nav.batch_dub'),
      keywords: ['batch', 'queue'],
      group: workspaceGroup,
      icon: LayersIcon,
      run: () => navigate({ to: '/batch' }),
    },
    {
      id: 'voices',
      label: `${t('nav.persona')} · ${t('nav.saved')}`,
      keywords: ['voices', 'profiles', 'saved', 'personas'],
      group: workspaceGroup,
      icon: UsersRoundIcon,
      run: () => {
        setWorkspace({ libraryOpen: true, libraryTab: 'voices' });
        return navigate({ to: '/personas' });
      },
    },
    {
      id: 'gallery',
      label: `${t('nav.persona')} · ${t('nav.gallery')}`,
      keywords: ['gallery', 'community', 'presets'],
      group: workspaceGroup,
      icon: LibraryIcon,
      run: () => navigate({ to: '/gallery' }),
    },
    {
      id: 'transcriptions',
      label: t('nav.transcribe'),
      keywords: [t('nav.dictation'), 'transcribe', 'dictation', 'speech to text'],
      group: workspaceGroup,
      icon: MicIcon,
      run: () => navigate({ to: '/transcriptions' }),
    },
    {
      id: 'workflows',
      label: t('workflows.title'),
      group: workspaceGroup,
      icon: WorkflowIcon,
      keywords: ['workflow', 'automation'],
      run: () => navigate({ to: '/workflows' }),
    },
    {
      id: 'calls',
      label: t('calls.title'),
      group: workspaceGroup,
      icon: PhoneCallIcon,
      keywords: ['calls', 'phone'],
      run: () => navigate({ to: '/calls' }),
    },
    {
      id: 'design',
      label: t('designWorkspace.title'),
      keywords: [t('nav.design'), 'design', 'voice design'],
      group: workspaceGroup,
      icon: WandSparklesIcon,
      run: () => navigate({ to: '/design' }),
    },
    {
      id: 'audiobook',
      label: t('audiobook.title'),
      keywords: ['audiobook', 'book', 'new project'],
      group: workspaceGroup,
      icon: BookOpenIcon,
      run: () => navigate({ to: '/audiobook' }),
    },
    {
      id: 'projects',
      label: t('projects.title'),
      group: workspaceGroup,
      keywords: ['projects', 'new project'],
      icon: FolderIcon,
      run: () => navigate({ to: '/projects' }),
    },
    {
      id: 'tools',
      label: t('tools.title'),
      group: workspaceGroup,
      icon: WrenchIcon,
      keywords: ['tools', 'compare', 'convert'],
      run: () => navigate({ to: '/tools' }),
    },
    {
      id: 'integrations',
      label: t('integrationCatalog.title'),
      group: workspaceGroup,
      icon: BlocksIcon,
      keywords: ['integrations', 'mcp', 'n8n', 'twilio', 'api'],
      run: () => navigate({ to: '/integrations' }),
    },
    {
      id: 'pro',
      label: t('proPage.title'),
      group: workspaceGroup,
      icon: CrownIcon,
      keywords: ['pro', 'upgrade', 'license'],
      run: () => navigate({ to: '/pro' }),
    },
    {
      id: 'models',
      label: t('modelSettings.models'),
      group: workspaceGroup,
      icon: BoxesIcon,
      keywords: [t('nav.settings'), 'models', 'engines', 'download'],
      run: () => navigate({ to: '/settings/models' }),
    },
    {
      id: 'theme',
      label: theme === 'dark' ? t('app.switch_to_light') : t('app.switch_to_dark'),
      group: workspaceGroup,
      icon: theme === 'dark' ? SunIcon : MoonIcon,
      keywords: [t('preferences.theme'), 'theme', 'dark', 'light', 'mode'],
      run: () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    },
    {
      id: 'shortcuts',
      label: t('keyboard.title'),
      keywords: ['shortcuts', 'keyboard'],
      group: workspaceGroup,
      icon: KeyboardIcon,
      shortcut: '?',
      run: () => onShortcutsOpenChange(true),
    },
    {
      id: 'settings',
      label: t('nav.settings'),
      group: workspaceGroup,
      icon: SettingsIcon,
      shortcut: `${modifier} ,`,
      keywords: ['settings', 'preferences'],
      run: () => navigate({ to: '/settings' }),
    },
    {
      id: 'takes',
      label: t('clone.history_title'),
      group: workspaceGroup,
      keywords: ['history', 'takes'],
      icon: HistoryIcon,
      run: () => {
        setWorkspace({ libraryOpen: true, libraryTab: 'takes' });
        return navigate({ to: '/clone' });
      },
    },
    ...(generation.canGenerate
      ? [
          {
            id: 'generate',
            // While a take renders, a new one joins the queue.
            label: generation.isGenerating ? t('generationQueue.add') : t('clone.synthesize'),
            group: actionGroup,
            icon: generation.isGenerating ? ListPlusIcon : PlayIcon,
            shortcut: `${modifier} ↵`,
            run: () => generation.generate(),
          },
        ]
      : []),
    ...(generation.isGenerating
      ? [
          {
            id: 'cancel',
            label: t('clone.cancel_generation'),
            group: actionGroup,
            icon: XIcon,
            run: () => generation.cancel(),
          },
        ]
      : []),
    ...(profiles.data ?? []).map((profile) => ({
      id: 'voice-' + profile.id,
      label: profile.name,
      group: personaGroup,
      icon: profile.kind === 'design' ? WandSparklesIcon : UsersRoundIcon,
      run: async () => {
        if (generation.isGenerating) return;
        if (profile.kind === 'design') {
          const current = readDraft();
          const restored = restoreDesignProfile(profile, current.seed);
          writeDraft(
            replaceRecipe(current, {
              attrs: restored.attrs,
              seed: restored.seed,
              profileId: restored.profileId,
              description: restored.description,
              picks: restored.picks,
              mapped: restored.mapped,
            }),
          );
          patchCloneSettings({ language: restored.language });
          await navigate({ to: '/design' });
          return;
        }
        selectCloneProfile(profile);
        await navigate({ to: '/clone' });
      },
    })),
    ...recentTakes(history.data ?? [], needle ? Infinity : undefined).map((item): CommandItem => ({
      id: 'take-' + item.id,
      label: item.text,
      detail: [formatRelative(item.created_at, i18n.language), takeLength(item.duration_seconds)]
        .filter(Boolean)
        .join(' · '),
      group: takeGroup,
      icon: item.mode === 'design' ? WandSparklesIcon : HistoryIcon,
      run: async () => {
        openTake(item);
        if (item.mode === 'design') {
          await navigate({ to: '/design' });
        } else {
          await navigate({ to: '/clone' });
        }
      },
    })),
  ];
  const projectItems: CommandItem[] = needle
    ? [
        ...(dubs.data ?? []).map(({ id, name }) => ({ id: `dub-${id}`, name, kind: 'nav.dub' })),
        ...(books.data ?? []).map(({ id, name, mode }) => ({
          id: `${mode}-${id}`,
          name,
          kind: mode === 'stories' ? 'nav.stories' : 'audiobook.title',
        })),
      ].map((project) => ({
        id: `project-${project.id}`,
        label: project.name,
        detail: t(project.kind),
        keywords: [t('projects.title'), t(project.kind)],
        group: t('projects.title'),
        icon: FolderIcon,
        run: () =>
          navigate({
            to: '/projects',
            search: { q: project.name },
            replace: project.name === currentProjectSearch,
            // A new selection must reach the mounted page even when the URL is identical.
            state: (previous) => ({ ...previous, projectSearchSelection: crypto.randomUUID() }),
          }),
      }))
    : [];
  // Settings pages are searchable but stay out of the default list.
  const settingsItems: CommandItem[] = needle
    ? SETTINGS_ENTRIES.map((entry) => ({
        id: `settings-${entry.label}`,
        label: t(entry.label),
        group: t('nav.settings'),
        icon: SettingsIcon,
        run: () => navigate({ to: entry.to }),
      }))
    : [];
  const items = matchingCommands([...source, ...settingsItems], projectItems, query);
  // If late results displace an explicit selection, Enter must wait for a new one.
  const active = activeId === undefined ? 0 : items.findIndex((item) => item.id === activeId);
  if (typeof activeId === 'string' && active < 0) setActiveId(null);
  const setActive = (index: number) => setActiveId(items[index]?.id ?? null);
  const run = (index: number) => {
    const item = items[index];
    if (!item) return;
    onOpenChange(false);
    runRendererTask(`Command palette: ${item.id}`, item.run);
  };
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-xl p-3" aria-describedby={undefined}>
          <DialogTitle className="sr-only">{t('preferences.search')}</DialogTitle>
          <Input
            autoFocus
            role="combobox"
            aria-expanded={open}
            aria-controls="command-results"
            aria-activedescendant={items[active] ? `command-${items[active].id}` : undefined}
            aria-label={t('preferences.search')}
            placeholder={t('preferences.search')}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveId(undefined);
            }}
            className="mr-8 h-10 w-[calc(100%-2rem)]"
            onKeyDown={(event) => {
              if (isImeComposing(event)) return;
              if (items.length && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
                event.preventDefault();
                const forward = event.key === 'ArrowDown';
                const next =
                  active < 0
                    ? (forward ? 0 : items.length - 1)
                    : (active + (forward ? 1 : -1) + items.length) % items.length;
                setActive(next);
                document
                  .getElementById(`command-${items[next]?.id}`)
                  ?.scrollIntoView({ block: 'nearest' });
              }
              if (event.key === 'Enter') {
                event.preventDefault();
                run(active);
              }
            }}
          />
          <div
            id="command-results"
            role="listbox"
            aria-label={t('preferences.search')}
            className="max-h-80 overflow-y-auto"
          >
            {items.map((item, index) => {
              const Icon = item.icon;
              const startsGroup = index === 0 || items[index - 1]?.group !== item.group;
              return (
                <div key={item.id} role="presentation">
                  {startsGroup && (
                    <p className="px-3 pt-3 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground">
                      {item.group}
                    </p>
                  )}
                  <button
                    id={`command-${item.id}`}
                    role="option"
                    aria-selected={active === index}
                    tabIndex={-1}
                    className={cn(
                      'group flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm outline-none transition-[background-color,box-shadow,transform]',
                      active === index
                        ? 'bg-accent text-accent-foreground shadow-sm ring-1 ring-inset ring-border/60'
                        : 'hover:bg-accent/60',
                    )}
                    onMouseMove={() => setActive(index)}
                    onClick={() => run(index)}
                  >
                    <Icon className="size-4 shrink-0 text-muted-foreground transition-colors duration-150 group-hover:text-foreground" />
                    <span className="min-w-0 flex-1 truncate" title={item.label}>
                      {item.label}
                    </span>
                    {item.detail && (
                      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                        {item.detail}
                      </span>
                    )}
                    {item.shortcut && <Kbd>{item.shortcut}</Kbd>}
                  </button>
                </div>
              );
            })}
          </div>
          <CommandPaletteSearchStatus
            hasResults={items.length > 0}
            queries={[history, profiles]}
            projectQueries={needle ? [dubs, books] : []}
          />
        </DialogContent>
      </Dialog>
      <KeyboardCheatsheet open={shortcutsOpen} onOpenChange={onShortcutsOpenChange} />
    </>
  );
}
