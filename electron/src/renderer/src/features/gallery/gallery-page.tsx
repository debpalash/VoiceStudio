import {
  AudioLinesIcon,
  ChevronRightIcon,
  Grid2X2Icon,
  ListFilterIcon,
  RotateCcwIcon,
  SearchIcon,
  SlidersHorizontalIcon,
  TagIcon,
} from 'lucide-react';
import { SecondarySidebar } from '@/components/workspace-sidebar';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { PipelineFailure } from '@/components/pipeline-failure';
import { CommunityView } from './community-view';
import { VoiceDestinations } from './voice-destinations';
import { placeLongformVoice } from './place-voice';
import type { Mode } from '@/features/longform/longform-session';
import { ImportsView } from './imports-view';
import { FACETS } from '@shared/components/gallery/facets';
import { FAVORITES_KEY, readFavorites, favoriteCatalogue } from './favorites';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { LoaderCircleIcon, StarIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { ProfileAvatar } from '@/components/profile-avatar';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import type { Profile } from '@/lib/api/types';
import type {
  ArchetypePage,
  ArchetypeCategory,
} from '@shared/api/archetypes-types';
import { mergeDescribedAttrs } from '@shared/utils/voiceInstruct';
import { STORAGE, readDraft } from '@/features/design/design-draft';
import { setCloneSetting } from '@/lib/store/clone-settings';

const FACET_LABELS: Record<string, string> = {
  gender: 'clone.cat_Gender',
  age: 'clone.cat_Age',
  pitch: 'clone.cat_Pitch',
  accent: 'clone.cat_AccentDialect',
  lang: 'clone.language',
};

export function GalleryPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [zone, setZone] = useState<'archetypes' | 'imports' | 'community'>('archetypes');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [facets, setFacets] = useState<Record<string, string>>({});
  const [favorites, setFavorites] = useState(readFavorites);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const toggleFavorite = (id: string) =>
    setFavorites((current) => {
      const next = current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id];
      try {
        localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
      } catch {
        /* In-memory fallback. */
      }
      return next;
    });
  const facet = (key: string, value: string) =>
    setFacets((current) => {
      const next = { ...current };
      if (next[key] === value) delete next[key];
      else next[key] = value;
      return next;
    });
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => () => operation.current?.abort(), []);
  const categories = useQuery({
    queryKey: ['archetype-categories'],
    queryFn: ({ signal }) => apiJson<ArchetypeCategory[]>('/archetypes/categories', { signal }),
    staleTime: 300000,
  });
  const catalogue = useInfiniteQuery({
    queryKey: ['archetypes', query, category, facets],
    enabled: zone === 'archetypes' && !favoritesOnly,
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      apiJson<ArchetypePage>(
        '/archetypes?' +
          new URLSearchParams({
            ...facets,
            q: query,
            use_case: category,
            offset: String(pageParam),
            limit: '60',
          }),
        { signal },
      ),
    getNextPageParam: (last) =>
      last.offset + last.items.length < last.total && last.items.length
        ? last.offset + last.items.length
        : undefined,
    staleTime: 300000,
  });
  const favoriteResults = useQuery({
    queryKey: ['archetypes-favorites', query, category, facets],
    queryFn: ({ signal }) => favoriteCatalogue({ ...facets, q: query, use_case: category }, signal),
    enabled: zone === 'archetypes' && favoritesOnly && favorites.length > 0,
    staleTime: 300000,
  });
  const useVoice = async (id: string, target?: Mode) => {
    if (operation.current) return;
    const active = new AbortController();
    operation.current = active;
    setSaving(id);
    setError(null);
    try {
      const result = await apiJson<{ profile_id: string }>(
        '/archetypes/' + encodeURIComponent(id) + '/use',
        { method: 'POST', signal: active.signal },
      );
      const profiles = await apiJson<Profile[]>('/profiles', {
        signal: active.signal,
      });
      client.setQueryData(queryKeys.profiles, profiles);
      if (active.signal.aborted) return;
      const profile = profiles.find((item) => item.id === result.profile_id);
      if (!profile) throw new Error('Saved profile missing');
      if (target) {
        placeLongformVoice(profile, target);
        await navigate({
          to: target === 'stories' ? '/stories' : '/audiobook',
        });
        return;
      }
      let attrs = {};
      try {
        attrs = JSON.parse(profile.vd_states || '{}');
      } catch {
        /* Legacy attributes use defaults. */
      }
      const draft = readDraft();
      localStorage.setItem(
        STORAGE,
        JSON.stringify({
          ...draft,
          attrs: mergeDescribedAttrs(attrs),
          seed: profile.seed ?? draft.seed,
        }),
      );
      setCloneSetting('language', profile.language || 'Auto');
      await navigate({ to: '/design' });
    } catch (cause) {
      if (!active.signal.aborted)
        setError(t('gallery.use_failed', { message: describeError(cause) }));
    } finally {
      if (!active.signal.aborted) setSaving(null);
      operation.current = null;
    }
  };
  const items = favoritesOnly
    ? (favoriteResults.data || []).filter((item) => favorites.includes(item.id))
    : catalogue.data?.pages.flatMap((page) => page.items) || [];
  const loading = favoritesOnly
    ? favorites.length > 0 && favoriteResults.isPending
    : catalogue.isPending;
  const requestError =
    catalogue.error || categories.error || (favoritesOnly ? favoriteResults.error : null);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('nav.gallery')}</h1>
      </WorkspaceHeader>
      <div className="flex shrink-0 gap-2 border-b border-border/50 px-5 py-2">
        {(['archetypes', 'community', 'imports'] as const).map((value) => (
          <Button
            key={value}
            size="sm"
            variant={zone === value ? 'secondary' : 'ghost'}
            aria-pressed={zone === value}
            disabled={saving !== null}
            onClick={() => setZone(value)}
          >
            {t('gallery.zone_' + value)}
          </Button>
        ))}
      </div>
      {zone === 'community' ? (
        <CommunityView favorites={favorites} toggleFavorite={toggleFavorite} />
      ) : zone === 'imports' ? (
        <ImportsView />
      ) : (
        <div className="flex min-h-0 flex-1 @max-[40rem]:flex-col">
          <SecondarySidebar
            title={t('nav.gallery')}
            icon={ListFilterIcon}
            variant="navigation"
            className="space-y-1"
          >
            <div className="relative mb-2">
              <SearchIcon
                aria-hidden="true"
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                type="search"
                className="h-9 pl-8"
                aria-label={t('common.search')}
                placeholder={t('common.search')}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <Button
              className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
              variant={!category ? 'secondary' : 'ghost'}
              aria-pressed={!category}
              onClick={() => setCategory('')}
            >
              <Grid2X2Icon />
              {t('gallery.all')}
            </Button>
            {categories.data?.map((item) => (
              <Button
                key={item.id}
                className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
                variant={category === item.id ? 'secondary' : 'ghost'}
                aria-pressed={category === item.id}
                onClick={() => setCategory(item.id)}
              >
                <TagIcon />
                {item.name}
              </Button>
            ))}
            <Button
              className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
              variant={favoritesOnly ? 'secondary' : 'ghost'}
              aria-pressed={favoritesOnly}
              onClick={() => setFavoritesOnly((value) => !value)}
            >
              <StarIcon />
              {t('gallery.favorites')}
            </Button>
            <div className="mt-3 flex items-center gap-2 border-t border-border/45 px-2 pt-3 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
              <SlidersHorizontalIcon className="size-3.5" />
              {t('gallery.filters')}
            </div>
            {Object.entries(FACETS).map(([key, values]) => (
              <details
                key={key}
                className="group rounded-lg border border-border/45 bg-background/25 px-2.5 py-2 text-xs transition-colors open:bg-background/40"
              >
                <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium">
                  <ChevronRightIcon className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" />
                  {t(FACET_LABELS[key])}
                  {facets[key] ? ': ' + facets[key] : ''}
                </summary>
                <div className="mt-2 flex flex-wrap gap-1">
                  {values.map((value) => (
                    <Button
                      key={value}
                      size="xs"
                      variant={facets[key] === value ? 'secondary' : 'ghost'}
                      aria-pressed={facets[key] === value}
                      onClick={() => facet(key, value)}
                    >
                      {value}
                    </Button>
                  ))}
                </div>
              </details>
            ))}
            <Button
              className="h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal [&_svg]:text-muted-foreground"
              variant="ghost"
              aria-pressed={facets.whisper === 'true'}
              onClick={() => facet('whisper', 'true')}
            >
              <AudioLinesIcon />
              {t('clone.opt_whisper')}
            </Button>
            <Button
              className="mt-2 h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 font-normal text-muted-foreground"
              variant="ghost"
              onClick={() => {
                setFacets({});
                setCategory('');
                setSearch('');
                setQuery('');
                setFavoritesOnly(false);
              }}
            >
              <RotateCcwIcon />
              {t('gallery.reset')}
            </Button>
          </SecondarySidebar>
          <section className="min-w-0 flex-1 overflow-y-auto p-6">
            <div className="mx-auto max-w-6xl space-y-5">
              <p className="text-sm text-muted-foreground">{t('gallery.subtitle')}</p>
              {(error || requestError) && (
                <PipelineFailure
                  fallback={error || describeError(requestError)}
                  action={
                    requestError ? (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => {
                          void categories.refetch();
                          if (favoritesOnly) void favoriteResults.refetch();
                          else void catalogue.refetch();
                        }}
                      >
                        {t('common.retry')}
                      </Button>
                    ) : undefined
                  }
                  onDismiss={error ? () => setError(null) : undefined}
                />
              )}
              {loading && <p role="status">{t('common.loading')}</p>}
              {!loading && !items.length && (
                <p className="py-10 text-center text-muted-foreground">{t('gallery.no_matches')}</p>
              )}
              <div className="grid grid-cols-[repeat(auto-fill,minmax(min(260px,100%),1fr))] gap-3">
                {items.map((item) => (
                  <article
                    key={item.id}
                    className="flex flex-col gap-3 rounded-xl border border-border/50 bg-card/40 p-4"
                  >
                    <div className="flex items-center gap-3">
                      <ProfileAvatar name={item.name} />
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={t('gallery.favorite') + ': ' + item.name}
                        aria-pressed={favorites.includes(item.id)}
                        onClick={() => toggleFavorite(item.id)}
                      >
                        <StarIcon
                          className={favorites.includes(item.id) ? 'fill-primary text-primary' : ''}
                        />
                      </Button>
                      <h2 className="min-w-0 truncate text-sm font-medium">{item.name}</h2>
                    </div>
                    <p className="flex-1 text-xs leading-5 text-muted-foreground">
                      {item.instruct}
                    </p>
                    <div className="flex items-center justify-between gap-2">
                      <AudioPreviewButton
                        src={apiPath('/archetypes/' + encodeURIComponent(item.id) + '/preview')}
                        fallbackSrc={apiPath(
                          '/archetypes/' + encodeURIComponent(item.id) + '/preview?local=true',
                        )}
                        source={'gallery-' + item.id}
                      />
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={Boolean(saving)}
                        onClick={() => void useVoice(item.id)}
                      >
                        {saving === item.id && <LoaderCircleIcon className="animate-spin" />}
                        {t(saving === item.id ? 'gallery.saving' : 'gallery.use_voice')}
                      </Button>
                      <VoiceDestinations
                        disabled={Boolean(saving)}
                        onChoose={(target) => void useVoice(item.id, target)}
                      />
                    </div>
                  </article>
                ))}
              </div>
              {!favoritesOnly && catalogue.hasNextPage && (
                <Button
                  variant="outline"
                  disabled={catalogue.isFetchingNextPage}
                  onClick={() => void catalogue.fetchNextPage()}
                >
                  {t('gallery.load_more')}
                </Button>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
