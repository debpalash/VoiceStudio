import { useEffect, useId, useState } from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { Link, useRouterState } from '@tanstack/react-router';
import {
  AudioLinesIcon,
  FingerprintIcon,
  BookOpenIcon,
  FolderIcon,
  WrenchIcon,
  LayersIcon,
  LibraryIcon,
  FilmIcon,
  WandSparklesIcon,
  MicIcon,
  UsersRoundIcon,
  ChevronRightIcon,
  BlocksIcon,
  PhoneCallIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { setWorkspace } from '@/lib/store/workspace';
import { cn } from '@/lib/utils';

type Destination = readonly [
  to:
    | '/clone'
    | '/stories'
    | '/audiobook'
    | '/projects'
    | '/tools'
    | '/batch'
    | '/gallery'
    | '/dub'
    | '/design'
    | '/transcriptions'
    | '/personas'
    | '/calls'
    | '/integrations',
  label: string,
  icon: typeof AudioLinesIcon,
  activate?: () => void,
];

const openSaved = () => setWorkspace({ libraryOpen: true, libraryTab: 'voices' });

const voiceDestinations: Destination[] = [
  ['/clone', 'nav.clone_short', FingerprintIcon, openSaved],
  ['/design', 'designWorkspace.title', WandSparklesIcon],
  ['/personas', 'nav.saved', UsersRoundIcon, openSaved],
  ['/gallery', 'nav.gallery', LibraryIcon],
];
const storyDestinations: Destination[] = [
  ['/stories', 'nav.stories', AudioLinesIcon],
  ['/audiobook', 'audiobook.title', BookOpenIcon],
];
const dubDestinations: Destination[] = [
  ['/dub', 'dubWorkspace.title', FilmIcon],
  ['/batch', 'nav.batch_dub', LayersIcon],
];
const laterDestinations: Destination[] = [
  ['/transcriptions', 'nav.transcribe', MicIcon],
  ['/calls', 'nav.calls', PhoneCallIcon],
  ['/projects', 'projects.title', FolderIcon],
  ['/tools', 'tools.title', WrenchIcon],
  ['/integrations', 'integrationCatalog.title', BlocksIcon],
];

const itemClass =
  'group relative isolate flex h-8 min-w-0 items-center overflow-hidden rounded-md text-sm text-sidebar-foreground/80 outline-none transition-[color,background-color,box-shadow,backdrop-filter] duration-150 before:pointer-events-none before:absolute before:inset-0 before:-z-10 before:rounded-[inherit] before:bg-gradient-to-r before:from-white/[0.07] before:via-white/[0.025] before:to-transparent before:opacity-0 before:transition-opacity before:duration-150 hover:bg-sidebar-accent/65 hover:text-sidebar-foreground hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/9%),0_6px_18px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 hover:before:opacity-100 focus-visible:ring-2 focus-visible:ring-ring';
const iconClass =
  'size-4 shrink-0 text-muted-foreground transition-[color,filter] duration-150 group-hover:text-sidebar-foreground group-hover:drop-shadow-[0_1px_3px_rgb(0_0_0/22%)]';

function NavigationLink({
  destination: [to, label, Icon, activate],
  compact = false,
  nested = false,
}: {
  destination: Destination;
  compact?: boolean;
  nested?: boolean;
}) {
  const { t } = useTranslation();
  const link = (
    <Link
      to={to}
      aria-label={compact ? t(label) : undefined}
      onClick={activate}
      className={cn(
        itemClass,
        compact ? 'justify-center px-0' : nested ? 'h-7 gap-2 px-2 text-xs' : 'gap-2.5 px-2.5',
      )}
      activeProps={{
        className:
          'bg-sidebar-accent/80 text-sidebar-foreground shadow-sm ring-1 ring-inset ring-sidebar-border/60',
        'aria-current': 'page',
      }}
    >
      <Icon className={cn(iconClass, nested && 'size-3.5')} aria-hidden="true" />
      {!compact && <span className="truncate">{t(label)}</span>}
    </Link>
  );
  if (!compact) return link;
  return (
    <Tooltip>
      <TooltipTrigger render={link} />
      <TooltipContent side="right">{t(label)}</TooltipContent>
    </Tooltip>
  );
}

function NavigationGroup({
  label,
  icon: Icon,
  children,
  compact,
  pathname,
}: {
  label: string;
  icon: typeof AudioLinesIcon;
  children: Destination[];
  compact: boolean;
  pathname: string;
}) {
  const { t } = useTranslation();
  const active = children.some(([to]) => pathname === to || pathname.startsWith(to + '/'));
  const [expanded, setExpanded] = useState(active);
  const [popupOpen, setPopupOpen] = useState(false);
  const id = useId();
  useEffect(() => {
    setExpanded(active);
    setPopupOpen(false);
  }, [pathname, active]);
  const triggerClass = cn(
    itemClass,
    'w-full',
    compact ? 'justify-center' : 'gap-2.5 px-2.5 font-medium',
    active &&
      'bg-sidebar-accent/65 text-sidebar-foreground ring-1 ring-inset ring-sidebar-border/50',
  );
  if (compact)
    return (
      <Popover open={popupOpen} onOpenChange={setPopupOpen}>
        <PopoverTrigger aria-label={t(label)} className={triggerClass}>
          <Icon className={iconClass} aria-hidden="true" />
        </PopoverTrigger>
        <PopoverContent side="right" className="w-52 p-2">
          <div className="px-2 pb-2 pt-1 text-xs font-medium text-muted-foreground">{t(label)}</div>
          <div onClick={() => setPopupOpen(false)}>
            {children.map((destination) => (
              <NavigationLink key={destination[0]} destination={destination} />
            ))}
          </div>
        </PopoverContent>
      </Popover>
    );
  return (
    <div className="py-0.5">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setExpanded((value) => !value)}
        className={triggerClass}
      >
        <Icon className={iconClass} aria-hidden="true" />
        <span className="truncate">{t(label)}</span>
        <ChevronRightIcon
          aria-hidden="true"
          className={cn(
            'ml-auto size-3.5 shrink-0 transition-transform duration-200 motion-reduce:transition-none',
            expanded && 'rotate-90',
          )}
        />
      </button>
      <div
        id={id}
        aria-hidden={!expanded}
        inert={!expanded}
        className={cn(
          'grid transition-[grid-template-rows,opacity] duration-200 motion-reduce:transition-none',
          expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
      >
        <div className="overflow-hidden">
          <div className="ml-[1.08rem] mt-0.5 space-y-0.5 border-l border-sidebar-border/60 pl-2">
            {children.map((destination) => (
              <NavigationLink key={destination[0]} destination={destination} nested />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function WorkspaceNavigation({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  return (
    <nav
      data-slot="workspace-navigation"
      aria-label={t('nav.workspaces')}
      className={cn(
        'min-h-0 overflow-y-auto overscroll-contain py-2 [scrollbar-width:none]',
        compact ? 'space-y-0.5 px-1.5' : 'shrink-0 space-y-0.5 px-3',
      )}
    >
      <NavigationGroup
        label="nav.voice"
        icon={FingerprintIcon}
        children={voiceDestinations}
        compact={compact}
        pathname={pathname}
      />
      <NavigationGroup
        label="nav.stories"
        icon={AudioLinesIcon}
        children={storyDestinations}
        compact={compact}
        pathname={pathname}
      />
      <NavigationGroup
        label="nav.dub"
        icon={FilmIcon}
        children={dubDestinations}
        compact={compact}
        pathname={pathname}
      />
      {laterDestinations.map((destination) => (
        <NavigationLink key={destination[0]} destination={destination} compact={compact} />
      ))}
    </nav>
  );
}
