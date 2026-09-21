import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { SendIcon, StarIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { patchCloneSettings } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import { getBridge } from '@/components/bridge';
import { STORAGE, readDraft } from '@/features/design/design-draft';
import {
  instructToVdStates,
  mergeDescribedAttrs,
} from '../../../../../../frontend/src/utils/voiceInstruct';
import type {
  CommunityItem,
  CommunityPage,
} from '../../../../../../frontend/src/api/community-types';
import type { Profile } from '@/lib/api/types';
import type { Mode } from '@/features/longform/longform-session';
import { placeLongformVoice } from './place-voice';
import { VoiceDestinations } from './voice-destinations';
export const communityKey = (item: CommunityItem) =>
  'community:' + (item._source_repo || item.source || 'default') + ':' + item.id;
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
  const catalogue = useInfiniteQuery({
    queryKey: ['community-gallery'],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      apiJson<CommunityPage>(
        '/community/items?' + new URLSearchParams({ offset: String(pageParam), limit: '100' }),
        { signal },
      ),
    getNextPageParam: (page) =>
      page.items.length && page.offset + page.items.length < page.total
        ? page.offset + page.items.length
        : undefined,
    staleTime: 300000,
  });
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
  const items = catalogue.data?.pages.flatMap((page) => page.items) || [];
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
            {t('gallery.community_empty')}
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
              <p className="text-xs text-muted-foreground">
                {[item.author, item.license, item.language].filter(Boolean).join(' \u00b7 ')}
              </p>
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
