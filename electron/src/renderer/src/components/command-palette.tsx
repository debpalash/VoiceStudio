import { useEffect, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { runRendererTask } from '@/lib/global-error-recovery';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useHistory } from '@/hooks/use-history';
import { useProfiles } from '@/hooks/use-profiles';
import { useGenerateClone } from '@/hooks/use-generate';
import { setWorkspace } from '@/lib/store/workspace';
import { openTake } from '@/lib/store/takes';
import { selectCloneProfile } from '@/lib/store/reference';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { cn } from '@/lib/utils';
import { Kbd } from '@/components/ui/kbd';
import { isMac } from '@/components/bridge';
import { KeyboardCheatsheet } from '@/components/keyboard-cheatsheet';
import {
  AudioLinesIcon,
  BookOpenIcon,
  FilmIcon,
  FolderIcon,
  HistoryIcon,
  HomeIcon,
  LayersIcon,
  LibraryIcon,
  MicIcon,
  PhoneCallIcon,
  PlayIcon,
  SettingsIcon,
  KeyboardIcon,
  UsersRoundIcon,
  WandSparklesIcon,
  WrenchIcon,
  XIcon,
  type LucideIcon,
} from 'lucide-react';
import {
  designDraftFromTake,
  readDraft,
  restoreDesignProfile,
  writeDraft,
} from '@/features/design/design-draft';

interface CommandItem {
  id: string;
  label: string;
  group: string;
  icon: LucideIcon;
  shortcut?: string;
  run: () => void | Promise<void>;
}

export function CommandPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const history = useHistory();
  const profiles = useProfiles();
  const generation = useGenerateClone();
  useEffect(() => {
    const show = () => {
      setQuery('');
      setActive(0);
      setOpen(true);
    };
    const key = (event: KeyboardEvent) => {
      if (event.isComposing || event.repeat) return;
      const target = event.target;
      const editing =
        target instanceof HTMLElement &&
        (target.matches('input, textarea') || target.isContentEditable);
      if (!editing && (event.key === '?' || (event.shiftKey && event.key === '/'))) {
        event.preventDefault();
        setOpen(false);
        setShortcutsOpen((value) => !value);
        return;
      }
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === 'k') {
        event.preventDefault();
        show();
      }
      if (event.key === ',') {
        event.preventDefault();
        runRendererTask('Keyboard navigation to settings', () => navigate({ to: '/settings' }));
      }
    };
    window.addEventListener('keydown', key);
    window.addEventListener('voicestudio:commands', show);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('voicestudio:commands', show);
    };
  }, [navigate]);
  const workspaceGroup = t('nav.workspaces');
  const actionGroup = t('nav.clone');
  const personaGroup = t('nav.persona');
  const takeGroup = t('clone.history_title');
  const modifier = isMac() ? '⌘' : 'Ctrl';
  const source: CommandItem[] = [
    {
      id: 'home',
      label: t('app.name'),
      group: workspaceGroup,
      icon: HomeIcon,
      run: () => navigate({ to: '/' }),
    },
    {
      id: 'clone',
      label: t('nav.clone'),
      group: workspaceGroup,
      icon: AudioLinesIcon,
      run: () => navigate({ to: '/clone' }),
    },
    {
      id: 'stories',
      label: t('nav.stories'),
      group: workspaceGroup,
      icon: AudioLinesIcon,
      run: () => navigate({ to: '/stories' }),
    },
    {
      id: 'dub',
      label: t('nav.dub'),
      group: workspaceGroup,
      icon: FilmIcon,
      run: () => navigate({ to: '/dub' }),
    },
    {
      id: 'batch',
      label: t('nav.batch_dub'),
      group: workspaceGroup,
      icon: LayersIcon,
      run: () => navigate({ to: '/batch' }),
    },
    {
      id: 'voices',
      label: `${t('nav.persona')} · ${t('nav.saved')}`,
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
      group: workspaceGroup,
      icon: LibraryIcon,
      run: () => navigate({ to: '/gallery' }),
    },
    {
      id: 'transcriptions',
      label: t('nav.transcribe'),
      group: workspaceGroup,
      icon: MicIcon,
      run: () => navigate({ to: '/transcriptions' }),
    },
    {
      id: 'calls',
      label: t('nav.calls'),
      group: workspaceGroup,
      icon: PhoneCallIcon,
      run: () => navigate({ to: '/calls' }),
    },
    {
      id: 'design',
      label: t('designWorkspace.title'),
      group: workspaceGroup,
      icon: WandSparklesIcon,
      run: () => navigate({ to: '/design' }),
    },
    {
      id: 'audiobook',
      label: t('audiobook.title'),
      group: workspaceGroup,
      icon: BookOpenIcon,
      run: () => navigate({ to: '/audiobook' }),
    },
    {
      id: 'projects',
      label: t('projects.title'),
      group: workspaceGroup,
      icon: FolderIcon,
      run: () => navigate({ to: '/projects' }),
    },
    {
      id: 'tools',
      label: t('tools.title'),
      group: workspaceGroup,
      icon: WrenchIcon,
      run: () => navigate({ to: '/tools' }),
    },
    {
      id: 'shortcuts',
      label: t('keyboard.title'),
      group: workspaceGroup,
      icon: KeyboardIcon,
      shortcut: '?',
      run: () => setShortcutsOpen(true),
    },
    {
      id: 'settings',
      label: t('nav.settings'),
      group: workspaceGroup,
      icon: SettingsIcon,
      shortcut: `${modifier} ,`,
      run: () => navigate({ to: '/settings' }),
    },
    {
      id: 'takes',
      label: t('clone.history_title'),
      group: workspaceGroup,
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
            label: t('clone.synthesize'),
            group: actionGroup,
            icon: PlayIcon,
            shortcut: `${modifier} ↵`,
            run: () => generation.generate(),
          },
        ]
      : generation.isGenerating
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
      },
    })),
    ...(history.data ?? [])
      .filter((item) => item.mode === 'clone' || item.mode === 'design')
      .map((item) => ({
        id: 'take-' + item.id,
        label: item.text,
        group: takeGroup,
        icon: item.mode === 'design' ? WandSparklesIcon : HistoryIcon,
        run: async () => {
          openTake(item);
          if (item.mode === 'design') {
            writeDraft(designDraftFromTake(item));
            await navigate({ to: '/design' });
          } else {
            await navigate({ to: '/clone' });
          }
        },
      })),
  ];
  const items = source
    .filter((item) => item.label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
    .slice(0, 30);
  const run = (index: number) => {
    const item = items[index];
    if (!item) return;
    setOpen(false);
    runRendererTask(`Command palette: ${item.id}`, item.run);
  };
  return (
    <>
      <Dialog open={open} onOpenChange={setOpen}>
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
              setActive(0);
            }}
            className="mr-8 h-10 w-[calc(100%-2rem)]"
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const next =
                  (active + (event.key === 'ArrowDown' ? 1 : -1) + items.length) %
                  Math.max(1, items.length);
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
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.shortcut && <Kbd>{item.shortcut}</Kbd>}
                  </button>
                </div>
              );
            })}
          </div>
          {!items.length && (
            <p className="p-4 text-center text-sm text-muted-foreground">
              {t('preferences.no_matches')}
            </p>
          )}
        </DialogContent>
      </Dialog>
      <KeyboardCheatsheet open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </>
  );
}
