import React, { useMemo } from 'react';
import { Loader, Send, Store, CloudOff } from 'lucide-react';
import { useCommunityItems } from '../../api/hooks';
import { communitySubmitUrl } from '../../api/community';
import { openExternal } from '../../api/external';
import ArchetypeCard from './ArchetypeCard';
import GallerySectionHeader from './GallerySectionHeader';
import { GALLERY_GRID } from './constants';

const itemKey = (item) => `community:${item._source_repo || item.source || 'default'}:${item.id}`;

// ── Community zone (marketplace) ─────────────────────────────────────────────
export default function CommunityZone({
  t,
  playingId,
  loadingPreviewId,
  favorites,
  toggleFavorite,
  onPreview,
  flash,
  onDesign,
  onUse,
  onUseInStories,
  onUseAsAudiobookDefault,
  materializingId,
}) {
  const itemsQ = useCommunityItems({ limit: 100 });
  const items = itemsQ.data?.items || [];
  const favSet = useMemo(() => new Set(favorites), [favorites]);

  const submit = async (type) => {
    try {
      const { url } = await communitySubmitUrl(type);
      await openExternal(url);
    } catch {
      flash(t('gallery.submit_failed', { defaultValue: 'Could not open the submission form.' }));
    }
  };

  const submitBtn =
    'inline-flex items-center gap-[5px] px-[10px] py-[6px] border border-transparent bg-transparent text-[var(--text-secondary)] rounded-[var(--chrome-radius-pill)] text-[0.7rem] cursor-pointer transition-colors hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--color-fg)]';

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-y-auto">
      <div className="mb-[10px] flex shrink-0 flex-wrap items-center justify-between gap-x-[12px] gap-y-[8px]">
        <p className="m-0 min-w-0 flex-1 text-[0.72rem] leading-[1.5] text-[var(--text-secondary)]">
          {t('gallery.community_explainer', {
            defaultValue:
              'Designed presets and recorded voices shared by the community, loaded from the omnivoice-gallery.',
          })}
        </p>
        <div className="flex shrink-0 gap-[6px]">
          <button className={submitBtn} onClick={() => submit('preset')}>
            <Send size={13} /> {t('gallery.submit_preset', { defaultValue: 'Submit a preset' })}
          </button>
          <button className={submitBtn} onClick={() => submit('voice')}>
            <Send size={13} /> {t('gallery.submit_voice', { defaultValue: 'Submit a voice' })}
          </button>
        </div>
      </div>

      {itemsQ.isLoading ? (
        <div className="flex items-center justify-center gap-[8px] p-[24px] text-[var(--text-secondary)]">
          <Loader className="spin" size={18} />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-[8px] px-[16px] py-[32px] text-center text-[var(--text-secondary)]">
          <CloudOff size={20} strokeWidth={1.5} aria-hidden="true" />
          <span className="max-w-[340px] text-[0.78rem] leading-[1.6]">
            {t('gallery.community_empty', {
              defaultValue:
                'No community voices loaded yet — connect to the internet and reopen, or be the first to submit one.',
            })}
          </span>
        </div>
      ) : (
        <>
          <GallerySectionHeader
            icon={<Store size={12} strokeWidth={1.5} aria-hidden="true" />}
            title={t('gallery.zone_community', { defaultValue: 'Community' })}
            count={items.length}
          />
          <div className={GALLERY_GRID}>
            {items.map((it) => (
              <ArchetypeCard
                key={itemKey(it)}
                a={it}
                t={t}
                favoriteId={itemKey(it)}
                isFavorite={favSet.has(itemKey(it))}
                isPlaying={playingId === itemKey(it)}
                isLoadingPreview={loadingPreviewId === itemKey(it)}
                previewLocked={Boolean(loadingPreviewId)}
                onToggleFavorite={toggleFavorite}
                onPreview={onPreview}
                onUse={onUse}
                onDesign={it.type === 'preset' && it.instruct ? onDesign : null}
                onUseInStories={onUseInStories}
                onUseAsAudiobookDefault={onUseAsAudiobookDefault}
                isMaterializing={materializingId === it.id}
                materializationLocked={Boolean(materializingId)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
