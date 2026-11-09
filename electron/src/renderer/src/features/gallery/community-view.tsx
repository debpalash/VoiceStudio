import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { keepPreviousData, useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { LanguagesIcon, SearchIcon, SendIcon, StarIcon } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { runRendererTask } from '@/lib/global-error-recovery';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import { getBridge } from '@/components/bridge';
import { STORAGE, readDraft } from '@/features/design/design-draft';
import { instructToVdStates, mergeDescribedAttrs } from '@shared/utils/voiceInstruct';
import type { CommunityItem, CommunityPage } from '@shared/api/community-types';
import type { Profile } from '@/lib/api/types';
import type { Mode } from '@/features/longform/longform-session';
import { placeLongformVoice } from './place-voice';
import { VoiceDestinations } from './voice-destinations';
export const communityKey = (item: CommunityItem) =>
  'community:' + (item._source_repo || item.source || 'default') + ':' + item.id;

const ALL = '*';

/**
 * One card per voice: the same voice can arrive from two catalogue sources or
 * across overlapping pages, and listing it twice reads as two different voices.
 */
export function uniqueCommunityItems(items: readonly CommunityItem[]): CommunityItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const keys = [
      communityKey(item),
      `${item.type}:${item.name.trim().toLocaleLowerCase()}:${(item.instruct || item.audio?.ref_text || '').trim()}`,
    ];
    if (keys.some((key) => seen.has(key))) return false;
    for (const key of keys) seen.add(key);
    return true;
  });
}

/** The same search the Archetypes list offers: name, description, author or language. */
export function matchesCommunitySearch(item: CommunityItem, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [item.name, item.instruct, item.audio?.ref_text, item.author, item.language]
    .filter((value): value is string => typeof value === 'string')
    .some((value) => value.toLocaleLowerCase().includes(needle));
}
const NEW_FOR_MS = 30 * 24 * 60 * 60 * 1000;
const GITHUB_HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;

/** Joined the gallery in the last 30 days (``added_at`` is a validated ISO date). */
export function isNewItem(item: CommunityItem, now = Date.now()): boolean {
  if (typeof item.added_at !== 'string') return false;
  const added = Date.parse(item.added_at + 'T00:00:00Z');
  return Number.isFinite(added) && added <= now && now - added < NEW_FOR_MS;
}

/** Profile URL for a community author who credited themselves by GitHub handle. */
export function authorProfileUrl(author?: unknown): string | null {
  // Remote data: an older backend may still pass a non-string through.
  if (typeof author !== 'string' || !GITHUB_HANDLE.test(author)) return null;
  if (author.toLowerCase() === 'voicestudio') return null;
  return 'https://github.com/' + author;
}

function openAuthor(event: MouseEvent<HTMLAnchorElement>) {
  const bridge = getBridge();
  if (!bridge) return;
  event.preventDefault();
  const href = event.currentTarget.href;
  runRendererTask('Open gallery author profile', () => bridge.files.openExternal(href));
}
export function CommunityView({
  favorites,
  toggleFavorite,
}: {
  favorites: string[];
  toggleFavorite(id: string): void;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [pack, setPack] = useState(ALL);
  const [language, setLanguage] = useState(ALL);
  const [search, setSearch] = useState('');
  const catalogue = useInfiniteQuery({
    queryKey: ['community-gallery', pack, language],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ offset: String(pageParam), limit: '100' });
      if (pack !== ALL) params.set('pack', pack);
      if (language !== ALL) params.set('lang', language);
      return apiJson<CommunityPage>('/community/items?' + params, { signal });
    },
    getNextPageParam: (page) =>
      page.items.length && page.offset + page.items.length < page.total
        ? page.offset + page.items.length
        : undefined,
    placeholderData: keepPreviousData,
    staleTime: 300000,
  });
  const facets = catalogue.data?.pages[0];
  const packs = facets?.packs || [];
  const languages = facets?.languages || [];
  const languageItems = [
    { value: ALL, label: t('gallery.community_all_languages') },
    ...languages.map((value) => ({ value, label: value })),
  ];
  const design = async (item: CommunityItem) => {
    try {
      localStorage.setItem(
        STORAGE,
        JSON.stringify({
          ...readDraft(),
          attrs: mergeDescribedAttrs(instructToVdStates(item.instruct || '')),
        }),
      );
      patchCloneSettings({
        language: item.language || 'Auto',
        selectedProfileId: null,
      });
      await navigate({ to: '/design' });
    } catch (cause) {
      setError(describeError(cause));
    }
  };
  const use = async (item: CommunityItem, target?: Mode) => {
    if (active.current) return;
    active.current = true;
    setBusy(communityKey(item));
    setError(null);
    try {
      const result = await apiJson<{ profile_id: string }>(
        '/community/items/' +
          encodeURIComponent(item.id) +
          '/use?' +
          new URLSearchParams({ name: item.name }),
        { method: 'POST' },
      );
      const profiles = await apiJson<Profile[]>('/profiles');
      client.setQueryData(queryKeys.profiles, profiles);
      if (!mounted.current) return;
      const profile = profiles.find((value) => value.id === result.profile_id);
      if (!profile) throw new Error('Profile missing');
      if (target) {
        placeLongformVoice(profile, target);
        await navigate({
          to: target === 'stories' ? '/stories' : '/audiobook',
        });
      } else if (item.type === 'preset') {
        let attrs = instructToVdStates(item.instruct || '');
        try {
          attrs = JSON.parse(profile.vd_states || '{}');
        } catch {
          /* legacy preset */
        }
        localStorage.setItem(
          STORAGE,
          JSON.stringify({
            ...readDraft(),
            attrs: mergeDescribedAttrs(attrs),
            seed: profile.seed ?? readDraft().seed,
          }),
        );
        patchCloneSettings({
          language: profile.language || 'Auto',
          selectedProfileId: null,
        });
        await navigate({ to: '/design' });
      } else {
        selectCloneProfile(profile);
        await navigate({ to: '/clone' });
      }
    } catch (cause) {
      if (mounted.current) setError(t('gallery.use_failed', { message: describeError(cause) }));
    } finally {
      active.current = false;
      if (mounted.current) setBusy(null);
    }
  };
  const submit = async (type: 'preset' | 'voice') => {
    setError(null);
    try {
      const { url } = await apiJson<{ url: string }>('/community/submit-url?type=' + type);
      const bridge = getBridge();
      if (bridge) await bridge.files.openExternal(url);
      else window.open(url, '_blank', 'noopener,noreferrer');
    } catch (cause) {
      setError(describeError(cause));
    }
  };
  const items = uniqueCommunityItems(
    catalogue.data?.pages.flatMap((page) => page.items) || [],
  ).filter((item) => matchesCommunitySearch(item, search));
  return (
    <section className="min-h-0 flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-6xl space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <p className="min-w-64 flex-1 text-sm leading-6 text-muted-foreground">
            {t('gallery.community_explainer')}
          </p>
          <Button variant="ghost" size="sm" onClick={() => void submit('preset')}>
            <SendIcon />
            {t('gallery.submit_preset')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void submit('voice')}>
            <SendIcon />
            {t('gallery.submit_voice')}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-48 flex-1 sm:max-w-72">
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
          {packs.length > 0 && (
            <ToggleGroup
              aria-label={t('gallery.community_packs')}
              value={[pack]}
              onValueChange={(values) => setPack(values[0] || ALL)}
              className="flex-wrap"
            >
              <ToggleGroupItem value={ALL}>{t('gallery.community_all_packs')}</ToggleGroupItem>
              {packs.map((entry) => (
                <ToggleGroupItem key={entry.id} value={entry.id} title={entry.description}>
                  {entry.name}
                  <span className="text-muted-foreground tabular-nums">{entry.count}</span>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
          {languages.length > 1 && (
            <Select
              items={languageItems}
              value={language}
              onValueChange={(value) => setLanguage(String(value ?? ALL))}
            >
              <SelectTrigger aria-label={t('gallery.community_language')} className="min-w-44">
                <LanguagesIcon className="size-4 text-muted-foreground" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {languageItems.map(({ value, label }) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
        {(error || catalogue.isError) && (
          <PipelineFailure
            fallback={error || describeError(catalogue.error)}
            action={
              catalogue.isError ? (
                <Button variant="ghost" size="xs" onClick={() => void catalogue.refetch()}>
                  {t('common.retry')}
                </Button>
              ) : undefined
            }
            onDismiss={error ? () => setError(null) : undefined}
          />
        )}
        {catalogue.isPending && <p role="status">{t('common.loading')}</p>}
        {!catalogue.isPending && !items.length && (
          <p className="py-12 text-center text-sm text-muted-foreground">
            {t(search.trim() ? 'gallery.no_matches' : 'gallery.community_empty')}
          </p>
        )}
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <article
              key={communityKey(item)}
              className="flex flex-col gap-3 rounded-xl border border-border/50 p-4"
            >
              <div className="flex items-center gap-2">
                <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{item.name}</h2>
                {isNewItem(item) && <Badge variant="secondary">{t('gallery.community_new')}</Badge>}
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t('gallery.favorites') + ': ' + item.name}
                  aria-pressed={favorites.includes(communityKey(item))}
                  onClick={() => toggleFavorite(communityKey(item))}
                >
                  <StarIcon
                    className={
                      favorites.includes(communityKey(item)) ? 'fill-primary text-primary' : ''
                    }
                  />
                </Button>
              </div>
              <p className="flex-1 text-xs leading-5 text-muted-foreground">
                {item.instruct || item.audio?.ref_text}
              </p>
              <CreditLine item={item} />

              <div className="flex flex-wrap items-center gap-2">
                <AudioPreviewButton
                  src={apiPath('/community/items/' + encodeURIComponent(item.id) + '/preview')}
                  fallbackSrc={
                    item.type === 'preset'
                      ? apiPath(
                          '/community/items/' + encodeURIComponent(item.id) + '/preview?local=true',
                        )
                      : undefined
                  }
                  source={communityKey(item)}
                />
                {item.type === 'preset' && item.instruct && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={Boolean(busy)}
                    onClick={() => void design(item)}
                  >
                    {t('gallery.open_designer')}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={Boolean(busy)}
                  onClick={() => void use(item)}
                >
                  {t(busy === communityKey(item) ? 'gallery.saving' : 'gallery.use_voice')}
                </Button>
                <VoiceDestinations
                  disabled={Boolean(busy)}
                  onChoose={(target) => void use(item, target)}
                />
              </div>
            </article>
          ))}
        </div>
        {catalogue.hasNextPage && (
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
  );
}

function CreditLine({ item }: { item: CommunityItem }) {
  const { t } = useTranslation();
  const profile = authorProfileUrl(item.author);
  const rest = [item.license, item.language].filter(Boolean).join(' · ');
  return (
    <p className="text-xs text-muted-foreground">
      {profile ? (
        <a
          href={profile}
          target="_blank"
          rel="noopener noreferrer"
          onClick={openAuthor}
          aria-label={t('gallery.community_author_profile', { author: item.author })}
          className="text-primary underline-offset-2 hover:underline"
        >
          {item.author}
        </a>
      ) : (
        item.author
      )}
      {item.author && rest ? ' · ' : ''}
      {rest}
    </p>
  );
}
