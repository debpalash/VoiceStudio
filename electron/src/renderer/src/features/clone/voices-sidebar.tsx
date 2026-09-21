import { useNavigate } from '@tanstack/react-router';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { ProfileAvatar } from '@/components/profile-avatar';
import { setWorkspace } from '@/lib/store/workspace';
import { openTake, reuseTake, useSelectedTake } from '@/lib/store/takes';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useQueryClient } from '@tanstack/react-query';
import {
  FingerprintIcon,
  PencilIcon,
  HistoryIcon,
  Redo2Icon,
  SearchIcon,
  StarIcon,
  Trash2Icon,
  WandSparklesIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { formatClock } from '@/components/waveform-player';
import {
  useClearHistory,
  useDeleteHistoryItem,
  useHistory,
  useToggleStarred,
} from '@/hooks/use-history';
import { useDeleteProfile, useProfiles } from '@/hooks/use-profiles';
import { audioUrl, profileAudioUrl } from '@/lib/api/client';
import type { HistoryItem, Profile } from '@/lib/api/types';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import { queryKeys } from '@/lib/query';
import { cn } from '@/lib/utils';
import { runRendererTask } from '@/lib/global-error-recovery';
import { ConfirmDialog } from './confirm-dialog';
import { displayTitle, formatRelative } from './format';
import {
  DESIGN_DRAFT_EVENT,
  designDraftFromTake,
  readDraft,
  restoreDesignProfile,
  writeDraft,
} from '@/features/design/design-draft';
import { useTtsReadiness, type TtsReadinessBlocker } from '@/hooks/use-tts-readiness';

const VIRTUALIZE_ABOVE = 30;

export function profileListPresentation(
  count: number,
  library: boolean,
): 'grid' | 'list' | 'virtual' {
  if (library) return 'grid';
  return count > VIRTUALIZE_ABOVE ? 'virtual' : 'list';
}

// ── Saved voices ────────────────────────────────────────────────────────────

function PreviewButton({
  profile,
  ttsBlocker,
  disabledLabel,
}: {
  profile: Profile;
  ttsBlocker: TtsReadinessBlocker;
  disabledLabel: string;
}) {
  const client = useQueryClient();
  const pendingIdentity =
    profile.kind === 'design' && !profile.ref_audio_path && !profile.locked_audio_path;
  return (
    <AudioPreviewButton
      src={profileAudioUrl(profile.id)}
      source={'profile-' + profile.id}
      activity={pendingIdentity ? 'synthesis' : undefined}
      disabled={pendingIdentity && Boolean(ttsBlocker)}
      disabledLabel={disabledLabel}
      onReady={
        pendingIdentity
          ? () => void client.invalidateQueries({ queryKey: queryKeys.profiles })
          : undefined
      }
    />
  );
}

export function SavedVoices({
  onSelected,
  library = false,
  scrollRef,
}: {
  onSelected?: () => void;
  library?: boolean;
  scrollRef?: RefObject<HTMLDivElement | null>;
}) {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const profiles = useProfiles();
  const ttsBlocker = useTtsReadiness();
  const deleteProfile = useDeleteProfile();
  const selectedProfileId = useCloneSetting('selectedProfileId');
  const [selectedDesignId, setSelectedDesignId] = useState(() => readDraft().profileId);
  const [pendingDelete, setPendingDelete] = useState<Profile | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<'all' | Profile['kind']>('all');

  const allItems = profiles.data ?? [];
  const items = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return allItems.filter(
      (profile) =>
        (kind === 'all' || profile.kind === kind) &&
        (!normalized ||
          `${profile.name} ${profile.instruct ?? ''} ${profile.ref_text ?? ''}`
            .toLocaleLowerCase()
            .includes(normalized)),
    );
  }, [allItems, kind, query]);
  const showLibraryTools = library || allItems.length > 4;
  const hasBothKinds =
    allItems.some((profile) => profile.kind === 'clone') &&
    allItems.some((profile) => profile.kind === 'design');
  const presentation = profileListPresentation(items.length, library);
  const virtualizer = useVirtualizer({
    count: presentation === 'virtual' ? items.length : 0,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => 38,
    overscan: 6,
    getItemKey: (index) => items[index]?.id ?? index,
  });

  useEffect(() => {
    const sync = (event: Event) =>
      setSelectedDesignId(
        (event as CustomEvent<ReturnType<typeof readDraft>>).detail?.profileId ??
          readDraft().profileId,
      );
    window.addEventListener(DESIGN_DRAFT_EVENT, sync);
    return () => window.removeEventListener(DESIGN_DRAFT_EVENT, sync);
  }, []);

  const select = async (profile: Profile) => {
    if (profile.kind === 'design') {
      const current = readDraft();
      const restored = restoreDesignProfile(profile, current.seed);
      writeDraft({
        ...current,
        attrs: restored.attrs,
        seed: restored.seed,
        profileId: restored.profileId,
      });
      setCloneSetting('language', restored.language);
      onSelected?.();
      runRendererTask('Reuse take in voice design', () => navigate({ to: '/design' }));
      return;
    }
    selectCloneProfile(profile);
    onSelected?.();
    runRendererTask('Reuse take in voice cloning', () => navigate({ to: '/clone' }));
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    try {
      await deleteProfile.mutateAsync(pendingDelete.id);
    } catch {
      // The shared profile mutation reports the error and keeps this action retryable.
    }
  };

  if (profiles.isPending)
    return (
      <p role="status" className="p-4 text-sm text-muted-foreground">
        {t('preferences.loading')}
      </p>
    );
  if (profiles.isError)
    return (
      <div role="alert" className="p-4 text-sm">
        <p>{profiles.error.message}</p>
        <Button variant="ghost" onClick={() => void profiles.refetch()}>
          {t('backend.retry')}
        </Button>
      </div>
    );
  if (allItems.length === 0) {
    return (
      <div className="mx-auto flex min-h-72 max-w-md flex-col items-center justify-center px-6 py-10 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <FingerprintIcon className="size-5" aria-hidden="true" />
        </div>
        <p className="text-[length:var(--text-label)] leading-relaxed text-muted-foreground">
          {t('clone.no_profiles')}
        </p>
        {library ? (
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <Button
              onClick={() =>
                runRendererTask('Open voice cloning', () => navigate({ to: '/clone' }))
              }
            >
              <FingerprintIcon />
              {t('clone.title')}
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                runRendererTask('Open voice design', () => navigate({ to: '/design' }))
              }
            >
              <WandSparklesIcon />
              {t('designWorkspace.title')}
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <>
      {showLibraryTools ? (
        <div className="sticky top-0 z-20 space-y-2 bg-sidebar/95 px-2 pt-1 pb-2 backdrop-blur-xl">
          <div className="relative">
            <SearchIcon
              className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label={t('common.search')}
              placeholder={t('common.search')}
              className="h-8 bg-sidebar-accent/25 pl-8"
            />
          </div>
          {hasBothKinds ? (
            <ToggleGroup
              value={[kind]}
              onValueChange={(value) => {
                const next = value[0];
                if (next === 'all' || next === 'clone' || next === 'design') setKind(next);
              }}
              size="sm"
              aria-label={t('clone.saved_profiles')}
            >
              <ToggleGroupItem value="all">{t('clone.history_all')}</ToggleGroupItem>
              <ToggleGroupItem value="clone" aria-label={t('clone.title')} title={t('clone.title')}>
                <FingerprintIcon />
              </ToggleGroupItem>
              <ToggleGroupItem
                value="design"
                aria-label={t('designWorkspace.title')}
                title={t('designWorkspace.title')}
              >
                <WandSparklesIcon />
              </ToggleGroupItem>
            </ToggleGroup>
          ) : null}
        </div>
      ) : null}
      {items.length === 0 ? (
        <p className="px-4 py-8 text-center text-[length:var(--text-label)] text-muted-foreground">
          {t('common.no_matches')}
        </p>
      ) : (
        <ul
          className={
            presentation === 'grid'
              ? 'grid grid-cols-[repeat(auto-fill,minmax(min(100%,260px),1fr))] gap-3 p-2'
              : presentation === 'virtual'
                ? 'relative w-full py-1'
                : 'flex flex-col gap-0.5 px-2 py-1'
          }
          style={
            presentation === 'virtual' ? { height: virtualizer.getTotalSize() + 8 } : undefined
          }
        >
          {(presentation === 'virtual'
            ? virtualizer.getVirtualItems().map((row) => ({
                profile: items[row.index]!,
                row,
              }))
            : items.map((profile) => ({ profile, row: null }))
          ).map(({ profile, row }) => {
            const design = profile.kind === 'design';
            const active = profile.id === (design ? selectedDesignId : selectedProfileId);
            return (
              <li
                key={profile.id}
                className={cn(
                  'group/profile relative flex items-center gap-2 rounded-lg px-2 transition-[background-color,box-shadow,backdrop-filter] duration-150 hover:bg-sidebar-accent/70 hover:shadow-[inset_0_1px_0_rgb(255_255_255/8%),0_6px_18px_rgb(0_0_0/10%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/60 hover:backdrop-blur-xl',
                  library ? 'py-3' : 'h-9',
                  row && 'absolute right-2 left-2 top-0',
                  active &&
                    'bg-sidebar-accent/80 shadow-sm ring-1 ring-inset ring-sidebar-border/60',
                )}
                style={row ? { transform: `translateY(${row.start + 4}px)` } : undefined}
              >
                <button
                  type="button"
                  className="absolute inset-y-0 left-0 right-24 z-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  title={profile.name + ' · ' + formatRelative(profile.created_at, i18n.language)}
                  aria-label={profile.name}
                  aria-pressed={active}
                  onClick={() => void select(profile)}
                />
                <ProfileAvatar
                  name={profile.name}
                  imageUrl={profile.image_url}
                  className={cn('pointer-events-none shrink-0', library ? 'size-9' : 'size-6')}
                />
                <div className="pointer-events-none min-w-0 flex-1">
                  <p className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                    <span className="truncate">{profile.name}</span>
                    <span
                      className="shrink-0 text-muted-foreground"
                      title={t(design ? 'projects.designed_voice' : 'projects.cloned_voice')}
                    >
                      {design ? (
                        <WandSparklesIcon className="size-3" aria-hidden="true" />
                      ) : (
                        <FingerprintIcon className="size-3" aria-hidden="true" />
                      )}
                    </span>
                  </p>
                  {library && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {formatRelative(profile.created_at, i18n.language)}
                    </p>
                  )}
                </div>
                <div className="absolute right-2 z-20 flex items-center gap-0.5 rounded-md bg-sidebar-accent/90 p-0.5 opacity-0 shadow-sm ring-1 ring-sidebar-border/50 backdrop-blur-xl transition-opacity duration-150 group-hover/profile:opacity-100 group-focus-within/profile:opacity-100">
                  {profile.ref_audio_path ||
                  profile.locked_audio_path ||
                  profile.kind === 'design' ? (
                    <PreviewButton
                      profile={profile}
                      ttsBlocker={ttsBlocker}
                      disabledLabel={t('engines.none_ready_title')}
                    />
                  ) : null}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t('paneActions.edit')}
                    title={t('paneActions.edit')}
                    onClick={() => {
                      openTake(null);
                      setWorkspace({
                        editingProfileId: profile.id,
                        panel: null,
                      });
                      if (!library)
                        runRendererTask('Use saved voice in cloning', () =>
                          navigate({ to: '/clone' }),
                        );
                    }}
                  >
                    <PencilIcon />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="text-muted-foreground hover:text-destructive"
                    onClick={() => setPendingDelete(profile)}
                    aria-label={t('clone.delete_profile')}
                  >
                    <Trash2Icon />
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title={t('clone.delete_profile')}
        description={t('clone.delete_profile_confirm', {
          name: pendingDelete?.name ?? '',
        })}
        confirmLabel={t('common.delete')}
        onConfirm={confirmDelete}
      />
    </>
  );
}

// ── Recent takes ────────────────────────────────────────────────────────────

interface TakeRowProps {
  item: HistoryItem;
  onStar: (item: HistoryItem) => void;
  onReuse: (item: HistoryItem) => void | Promise<void>;
  onDelete: (item: HistoryItem) => void;
}

function TakeRow({ item, onStar, onReuse, onDelete }: TakeRowProps) {
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const starred = Boolean(item.starred);
  const selected = useSelectedTake();
  const isDesign = item.mode === 'design';
  const workspaceLabel = isDesign ? t('designWorkspace.title') : t('clone.title');
  const reopen = async () => {
    await onReuse(item);
    await navigate({ to: isDesign ? '/design' : '/clone' });
  };
  return (
    <div
      className={cn(
        'group relative flex h-9 min-w-0 items-center rounded-lg px-2 transition-[background-color,box-shadow,backdrop-filter] duration-150 hover:bg-sidebar-accent/60 hover:shadow-[inset_0_1px_0_rgb(255_255_255/7%),0_6px_18px_rgb(0_0_0/9%)] hover:ring-1 hover:ring-inset hover:ring-sidebar-border/50 hover:backdrop-blur-xl',
        selected?.id === item.id && 'bg-sidebar-accent',
      )}
    >
      <button
        type="button"
        aria-pressed={selected?.id === item.id}
        onClick={() => {
          if (isDesign) {
            openTake(item);
            runRendererTask('Open design take', () => navigate({ to: '/design' }));
          } else {
            openTake(item);
            runRendererTask('Open cloned take', () => navigate({ to: '/clone' }));
          }
        }}
        className="flex h-full min-w-0 flex-1 items-center gap-2 rounded text-left text-[length:var(--text-label)] outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title={[
          item.text,
          workspaceLabel,
          formatClock(item.duration_seconds ?? 0),
          formatRelative(item.created_at, i18n.language),
        ].join(' · ')}
      >
        {starred ? (
          <StarIcon className="size-3.5 shrink-0 fill-current text-warning" />
        ) : (
          <HistoryIcon className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="truncate">{displayTitle(item.text)}</span>
      </button>
      <div className="pointer-events-none absolute right-1 z-10 flex items-center gap-0.5 rounded-md bg-sidebar-accent/95 p-0.5 opacity-0 backdrop-blur-xl transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
        {item.audio_path && (
          <AudioPreviewButton src={audioUrl(item.audio_path)} source={'take-' + item.id} />
        )}
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onStar(item)}
          aria-pressed={starred}
          aria-label={starred ? t('clone.history_unstar') : t('clone.history_star')}
          className={cn(starred && 'text-warning hover:text-warning')}
        >
          <StarIcon className={cn(starred && 'fill-current')} />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={t('clone.history_reuse')}
          title={t('clone.history_reuse')}
          onClick={() => void reopen()}
        >
          <Redo2Icon data-icon="inline-start" />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          className="text-muted-foreground hover:text-destructive"
          onClick={() => onDelete(item)}
          aria-label={t('clone.history_delete')}
        >
          <Trash2Icon />
        </Button>
      </div>
    </div>
  );
}

interface VirtualTakesProps {
  items: HistoryItem[];
  scrollRef: RefObject<HTMLDivElement | null>;
  rowProps: Omit<TakeRowProps, 'item'>;
}

function VirtualTakes({ items, scrollRef, rowProps }: VirtualTakesProps) {
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 42,
    overscan: 6,
    getItemKey: (index) => items[index]?.id ?? index,
  });
  return (
    <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
      {virtualizer.getVirtualItems().map((row) => {
        const item = items[row.index];
        if (!item) return null;
        return (
          <div
            key={row.key}
            ref={virtualizer.measureElement}
            data-index={row.index}
            className="absolute top-0 left-0 w-full px-2 pb-1.5"
            style={{ transform: `translateY(${row.start}px)` }}
          >
            <TakeRow item={item} {...rowProps} />
          </div>
        );
      })}
    </div>
  );
}

interface RecentTakesProps {
  scrollRef: RefObject<HTMLDivElement | null>;
}

function RecentTakes({ scrollRef }: RecentTakesProps) {
  const { t } = useTranslation();
  const history = useHistory();
  const deleteItem = useDeleteHistoryItem();
  const clearHistory = useClearHistory();
  const toggleStarred = useToggleStarred();
  const [filter, setFilter] = useState<'all' | 'clone' | 'design' | 'starred'>('all');
  const [confirmClear, setConfirmClear] = useState(false);

  const items = useMemo(() => {
    const all = (history.data ?? []).filter(
      (item) => !item.mode || item.mode === 'clone' || item.mode === 'design',
    );
    if (filter === 'starred') return all.filter((item) => Boolean(item.starred));
    if (filter === 'clone') return all.filter((item) => !item.mode || item.mode === 'clone');
    if (filter === 'design') return all.filter((item) => item.mode === 'design');
    return all;
  }, [history.data, filter]);

  const rowProps: Omit<TakeRowProps, 'item'> = {
    onStar: (item) => toggleStarred.mutate({ id: item.id, starred: !item.starred }),
    onReuse: async (item) => {
      if (item.mode === 'design') {
        writeDraft(designDraftFromTake(item));
        setCloneSetting('language', item.language || 'Auto');
        openTake(null);
        return;
      }
      await reuseTake(item);
    },
    onDelete: (item) => deleteItem.mutate(item.id),
  };

  return (
    <>
      <div className="flex items-center justify-between gap-2 px-3 pt-2 pb-1">
        <ToggleGroup
          value={[filter]}
          onValueChange={(value) => {
            const next = value[0];
            if (next === 'all' || next === 'clone' || next === 'design' || next === 'starred')
              setFilter(next);
          }}
          size="sm"
          aria-label={t('clone.history_title')}
        >
          <ToggleGroupItem value="all">{t('clone.history_all')}</ToggleGroupItem>
          <ToggleGroupItem value="clone" aria-label={t('clone.title')} title={t('clone.title')}>
            <FingerprintIcon />
          </ToggleGroupItem>
          <ToggleGroupItem
            value="design"
            aria-label={t('designWorkspace.title')}
            title={t('designWorkspace.title')}
          >
            <WandSparklesIcon />
          </ToggleGroupItem>
          <ToggleGroupItem
            value="starred"
            aria-label={t('clone.history_starred')}
            title={t('clone.history_starred')}
          >
            <StarIcon />
          </ToggleGroupItem>
        </ToggleGroup>
        {(history.data?.length ?? 0) > 0 ? (
          <Button
            variant="ghost"
            size="xs"
            className="text-muted-foreground hover:text-destructive"
            onClick={() => setConfirmClear(true)}
          >
            <Trash2Icon data-icon="inline-start" />
            {t('clone.history_clear')}
          </Button>
        ) : null}
      </div>
      {history.isPending ? (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t('preferences.loading')}
        </p>
      ) : history.isError ? (
        <div role="alert" className="p-4 text-sm">
          <p>{history.error.message}</p>
          <Button variant="ghost" onClick={() => void history.refetch()}>
            {t('backend.retry')}
          </Button>
        </div>
      ) : items.length === 0 ? (
        <p className="px-4 py-8 text-center text-[length:var(--text-label)] text-muted-foreground">
          {t('clone.history_empty')}
        </p>
      ) : items.length > VIRTUALIZE_ABOVE ? (
        <VirtualTakes items={items} scrollRef={scrollRef} rowProps={rowProps} />
      ) : (
        <div className="flex flex-col gap-1.5 p-2">
          {items.map((item) => (
            <TakeRow key={item.id} item={item} {...rowProps} />
          ))}
        </div>
      )}
      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title={t('clone.history_clear')}
        description={t('clone.history_clear_confirm')}
        confirmLabel={t('common.delete')}
        onConfirm={async () => {
          await clearHistory.mutateAsync();
        }}
      />
    </>
  );
}

// ── Sidebar ─────────────────────────────────────────────────────────────────

export function VoicesSidebar({
  initialTab = 'voices',
  onVoiceSelected,
}: {
  initialTab?: 'voices' | 'takes';
  onVoiceSelected?: () => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<'voices' | 'takes'>(initialTab);
  const scrollRef = useRef<HTMLDivElement>(null);

  return (
    <Card
      size="sm"
      className="@container/library flex min-h-0 min-w-0 flex-1 flex-col rounded-none bg-transparent py-0 ring-0"
    >
      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (value === 'voices' || value === 'takes') setTab(value);
          setWorkspace({ libraryTab: value });
        }}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="px-2 py-2">
          <TabsList className="flex w-full min-w-0 gap-1 bg-transparent p-0">
            <TabsTrigger
              value="voices"
              title={t('clone.saved_profiles')}
              aria-label={t('clone.saved_profiles')}
              className="min-w-0 flex-none data-active:flex-1 @[300px]/library:flex-1 rounded-lg text-sidebar-foreground/60 hover:bg-sidebar-accent/50 data-active:bg-[var(--sidebar-row-active)] data-active:text-sidebar-foreground dark:data-active:border-transparent dark:data-active:bg-[var(--sidebar-row-active)] dark:data-active:text-sidebar-foreground"
            >
              <FingerprintIcon data-icon="inline-start" />
              <span
                className={cn(
                  'min-w-0 truncate',
                  tab !== 'voices' && 'hidden @[300px]/library:inline',
                )}
              >
                {t('clone.saved_profiles')}
              </span>
            </TabsTrigger>
            <TabsTrigger
              value="takes"
              title={t('clone.history_title')}
              aria-label={t('clone.history_title')}
              className="min-w-0 flex-none data-active:flex-1 @[300px]/library:flex-1 rounded-lg text-sidebar-foreground/60 hover:bg-sidebar-accent/50 data-active:bg-[var(--sidebar-row-active)] data-active:text-sidebar-foreground dark:data-active:border-transparent dark:data-active:bg-[var(--sidebar-row-active)] dark:data-active:text-sidebar-foreground"
            >
              <HistoryIcon data-icon="inline-start" />
              <span
                className={cn(
                  'min-w-0 truncate',
                  tab !== 'takes' && 'hidden @[300px]/library:inline',
                )}
              >
                {t('clone.history_title')}
              </span>
            </TabsTrigger>
          </TabsList>
        </div>
        <div
          ref={scrollRef}
          className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain [scrollbar-width:thin]"
        >
          <TabsContent value="voices">
            <SavedVoices onSelected={onVoiceSelected} scrollRef={scrollRef} />
          </TabsContent>
          <TabsContent value="takes">
            <RecentTakes scrollRef={scrollRef} />
          </TabsContent>
        </div>
      </Tabs>
    </Card>
  );
}
