import { useEffect, useId, useRef, useState } from 'react';
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
  WorkflowIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { setWorkspace } from '@/lib/store/workspace';
import { cn } from '@/lib/utils';
import './workspace-menu.css';

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
    | '/workflows'
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
  ['/workflows', 'workflows.title', WorkflowIcon],
  ['/projects', 'projects.title', FolderIcon],
  ['/tools', 'tools.title', WrenchIcon],
  ['/integrations', 'integrationCatalog.title', BlocksIcon],
];

const itemClass =
  'workspace-nav-item group relative isolate flex h-8 min-w-0 items-center overflow-hidden rounded-md text-[13px] text-sidebar-foreground/80 outline-none transition-[color,background-color,box-shadow,backdrop-filter] duration-150 before:pointer-events-none before:absolute before:inset-0 before:-z-10 before:rounded-[inherit] before:bg-gradient-to-r before:from-white/[0.07] before:via-white/[0.025] before:to-transparent before:opacity-0 before:transition-opacity before:duration-150 hover:bg-sidebar-accent/65 hover:text-sidebar-foreground hover:backdrop-blur-xl hover:shadow-[inset_0_1px_0_rgb(255_255_255/9%),0_6px_18px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 hover:before:opacity-100 focus-visible:ring-2 focus-visible:ring-ring';
const iconClass =
  'workspace-nav-icon size-4 shrink-0 text-muted-foreground transition-[color,filter] duration-150';

const iconMotion: Record<Destination[0], string> = {
  '/clone': 'pulse',
  '/design': 'tilt',
  '/personas': 'nudge',
  '/gallery': 'lift',
  '/stories': 'wave',
  '/audiobook': 'tilt',
  '/dub': 'tilt',
  '/batch': 'lift',
  '/transcriptions': 'nudge',
  '/workflows': 'pulse',
  '/projects': 'lift',
  '/tools': 'tilt',
  '/integrations': 'turn',
};

function NavigationLink({
  destination: [to, label, Icon, activate],
  compact = false,
  nested = false,
  flyout = false,
  onCurrentRoute,
}: {
  destination: Destination;
  compact?: boolean;
  nested?: boolean;
  flyout?: boolean;
  onCurrentRoute?: () => void;
}) {
  const { t } = useTranslation();
  const link = (
    <Link
      to={to}
      aria-label={compact ? t(label) : undefined}
      onClick={() => {
        activate?.();
        onCurrentRoute?.();
      }}
      className={cn(
        itemClass,
        compact
          ? 'h-10 justify-center px-0'
          : flyout
            ? 'h-10 gap-2 px-3 text-sm'
            : nested
              ? 'gap-2 px-2'
              : 'gap-2.5 px-2.5',
      )}
      activeProps={{
        className:
          'bg-sidebar-accent/80 text-sidebar-foreground shadow-sm ring-1 ring-inset ring-sidebar-border/60',
        'aria-current': 'page',
      }}
    >
      <Icon
        className={iconClass}
        data-tone={to.slice(1)}
        data-motion={iconMotion[to]}
        aria-hidden="true"
      />
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
  const groupTone =
    label === 'nav.stories'
      ? 'stories-group'
      : label === 'nav.dub'
        ? 'dubbing-group'
        : label.slice(4);
  const [expanded, setExpanded] = useState(active);
  const [popupOpen, setPopupOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelOpen = () => {
    if (openTimer.current !== null) clearTimeout(openTimer.current);
    openTimer.current = null;
  };
  const cancelClose = () => {
    if (closeTimer.current !== null) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setPopupOpen(false), 240);
  };
  const id = useId();
  useEffect(
    () => () => {
      cancelOpen();
      cancelClose();
    },
    [],
  );
  useEffect(() => {
    cancelOpen();
    cancelClose();
    setExpanded(active);
    setPopupOpen(false);
  }, [pathname, active]);
  const triggerClass = cn(
    itemClass,
    'w-full',
    compact ? 'h-10 justify-center' : 'gap-2.5 px-2.5 font-medium',
    active &&
      'bg-sidebar-accent/65 text-sidebar-foreground ring-1 ring-inset ring-sidebar-border/50',
  );
  if (compact)
    return (
      <Popover
        open={popupOpen}
        onOpenChange={(open) => {
          cancelOpen();
          cancelClose();
          setPopupOpen(open);
        }}
      >
        <PopoverTrigger
          ref={triggerRef}
          aria-label={t(label)}
          data-active={active}
          className={triggerClass}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowRight' && event.key !== 'ArrowDown') return;
            event.preventDefault();
            cancelOpen();
            setPopupOpen(true);
            requestAnimationFrame(() => popupRef.current?.querySelector('a')?.focus());
          }}
          onPointerEnter={(event) => {
            if (event.pointerType !== 'mouse') return;
            cancelClose();
            if (!popupOpen) {
              cancelOpen();
              openTimer.current = setTimeout(() => setPopupOpen(true), 90);
            }
          }}
          onPointerLeave={(event) => {
            if (event.pointerType === 'mouse') {
              cancelOpen();
              if (popupOpen) scheduleClose();
            }
          }}
          onClickCapture={(event) => {
            // A press on an already hover-open trigger should keep the flyout
            // available. Keyboard and touch presses retain Base UI's toggle.
            if (popupOpen && event.detail > 0) event.stopPropagation();
          }}
        >
          <Icon
            className={iconClass}
            data-tone={groupTone}
            data-motion={label === 'nav.stories' ? 'wave' : 'pulse'}
            aria-hidden="true"
          />
        </PopoverTrigger>
        <PopoverContent
          ref={popupRef}
          side="right"
          sideOffset={0}
          className="w-52 p-1.5 data-open:zoom-in-100 data-closed:zoom-out-100"
          onPointerEnter={cancelClose}
          onPointerLeave={(event) => {
            if (event.pointerType === 'mouse') scheduleClose();
          }}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowLeft') return;
            event.preventDefault();
            setPopupOpen(false);
            triggerRef.current?.focus();
          }}
        >
          <div className="px-3 pb-1 pt-1.5 text-xs font-medium text-muted-foreground">
            {t(label)}
          </div>
          <div>
            {children.map((destination) => (
              <NavigationLink
                key={destination[0]}
                destination={destination}
                flyout
                onCurrentRoute={pathname === destination[0] ? () => setPopupOpen(false) : undefined}
              />
            ))}
          </div>
        </PopoverContent>
      </Popover>
    );
  return (
    <div>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={id}
        data-active={active}
        onClick={() => setExpanded((value) => !value)}
        className={triggerClass}
      >
        <Icon
          className={iconClass}
          data-tone={groupTone}
          data-motion={label === 'nav.stories' ? 'wave' : 'pulse'}
          aria-hidden="true"
        />
        <span className="truncate">{t(label)}</span>
        <ChevronRightIcon
          aria-hidden="true"
          className={cn(
            'ml-auto size-3.5 shrink-0 text-muted-foreground/60 transition-[transform,color] duration-200 group-hover:text-muted-foreground motion-reduce:transition-none',
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
