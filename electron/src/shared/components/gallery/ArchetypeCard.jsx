import React from 'react';
import { BookOpen, Ellipsis, Headphones, Loader, Play, Star, UserPlus, Wand2 } from 'lucide-react';
import { Menu } from '../../ui';
import {
  ArchetypeAvatar,
  AccentFlag,
  NowPlaying,
  USE_CASE_COLOR,
} from '../../utils/archetypeIcons';
import { facetLabel } from './constants';

// ── Archetype card ───────────────────────────────────────────────────────────
export default function ArchetypeCard({
  a,
  t,
  isFavorite,
  isPlaying,
  isLoadingPreview,
  onPreview,
  onUse,
  onDesign,
  onToggleFavorite,
  onUseInStories,
  onUseAsAudiobookDefault,
  favoriteId = a.id,
  previewLocked = false,
  isMaterializing = false,
  materializationLocked = false,
}) {
  const color = USE_CASE_COLOR[a.use_case] || '#83a598';
  const sub = [a.facets.gender, a.facets.age, a.facets.pitch]
    .filter(Boolean)
    .map(facetLabel)
    .join(' · ');
  const dialect =
    a.attrs?.ChineseDialect && a.attrs.ChineseDialect !== 'Auto' ? a.attrs.ChineseDialect : null;
  const accentLabel = a.facets.accent
    ? facetLabel(a.facets.accent)
    : dialect || (a.language === 'Chinese' ? 'Chinese' : null);
  const hasChips = Boolean(accentLabel || a.facets.whisper);

  const cardBase =
    'group relative flex min-h-[168px] flex-col gap-[9px] p-[14px] rounded-[12px] ' +
    'border border-transparent bg-[color-mix(in_srgb,var(--chrome-fg)_3%,transparent)] ' +
    'transition-[transform,box-shadow,background-color] duration-150 ' +
    'hover:-translate-y-px ' +
    'hover:bg-[color-mix(in_srgb,var(--chrome-fg)_5.5%,transparent)] hover:shadow-[0_8px_24px_rgba(0,0,0,0.32)] ' +
    'motion-reduce:transition-none motion-reduce:hover:translate-y-0';
  const cardState = isPlaying
    ? 'bg-[color-mix(in_srgb,var(--card-accent)_9%,transparent)] shadow-[0_0_0_1px_var(--card-accent),0_6px_22px_rgba(0,0,0,0.4)]'
    : '';

  // No Tailwind preflight in this app (theme + utilities only) — every native
  // <button> carries the OS face (grey fill, outset border) unless reset, so
  // each ghost control repeats the border-transparent + bg-transparent reset.
  const iconBtn =
    'inline-flex items-center justify-center w-[28px] h-[28px] flex-shrink-0 rounded-[8px] border border-transparent bg-transparent text-[var(--color-fg-muted)] cursor-pointer transition-[opacity,color,background-color] duration-150 hover:bg-[var(--chrome-hover-bg)] disabled:cursor-not-allowed disabled:opacity-30';

  const designerBtn = onDesign ? (
    <button
      type="button"
      className={`${iconBtn} opacity-50 group-hover:opacity-100 focus-visible:opacity-100 hover:text-[var(--card-accent)]`}
      onClick={() => onDesign(a)}
      disabled={materializationLocked}
      title={t('gallery.open_designer', { defaultValue: 'Open in Designer' })}
      aria-label={t('gallery.open_designer', { defaultValue: 'Open in Designer' })}
    >
      <Wand2 size={14} aria-hidden="true" />
    </button>
  ) : null;
  const moreBtn =
    onUseInStories || onUseAsAudiobookDefault ? (
      <Menu
        placement="bottom-end"
        disabled={materializationLocked}
        items={[
          onUseInStories
            ? {
                id: 'stories',
                icon: BookOpen,
                label: t('gallery.use_in_stories', { defaultValue: 'Use in Stories' }),
                onSelect: () => onUseInStories(a),
              }
            : null,
          onUseAsAudiobookDefault
            ? {
                id: 'audiobook',
                icon: Headphones,
                label: t('gallery.set_audiobook_default', {
                  defaultValue: 'Set as Audiobook default',
                }),
                onSelect: () => onUseAsAudiobookDefault(a),
              }
            : null,
        ].filter(Boolean)}
      >
        <button
          type="button"
          className={`${iconBtn} opacity-50 group-hover:opacity-100 focus-visible:opacity-100 hover:text-[var(--card-accent)]`}
          aria-label={t('gallery.more_actions', { defaultValue: 'More actions' })}
          title={t('gallery.more_actions', { defaultValue: 'More actions' })}
        >
          <Ellipsis size={15} aria-hidden="true" />
        </button>
      </Menu>
    ) : null;

  return (
    <div
      data-testid="gallery-persona-card"
      className={`${cardBase} ${cardState}`}
      style={{ '--card-accent': color }}
    >
      {/* Header — the name is the focal point; metadata recedes (smaller, muted).
          Icon actions cluster top-right so the bottom row fits Preview + Use. */}
      <div className="flex items-center gap-[10px]">
        <ArchetypeAvatar item={a} size={44} />
        <div className="flex-1 min-w-0">
          <div
            className="text-[0.82rem] font-semibold leading-tight text-[var(--color-fg)] truncate"
            title={a.name}
          >
            {a.name}
          </div>
          {sub && (
            <div
              className="text-[0.66rem] text-[var(--color-fg-muted)] mt-[3px] truncate"
              title={sub}
            >
              {sub}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-[2px]">
          {designerBtn}
          {moreBtn}
          <button
            type="button"
            className={`flex items-center justify-center w-[28px] h-[28px] flex-shrink-0 rounded-[8px] border border-transparent bg-transparent cursor-pointer transition-[color,background-color,opacity] hover:bg-[var(--chrome-hover-bg)] ${
              isFavorite
                ? 'text-[#fabd2f]'
                : 'text-[var(--color-fg-subtle)] opacity-70 group-hover:opacity-100 hover:text-[#fabd2f]'
            }`}
            onClick={() => onToggleFavorite(favoriteId)}
            title={t('gallery.favorite', { defaultValue: 'Favorite' })}
            aria-label={t('gallery.favorite', { defaultValue: 'Favorite' })}
            aria-pressed={isFavorite}
          >
            <Star size={15} fill={isFavorite ? 'currentColor' : 'none'} aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Chips only render when present — no empty reserved row. Cards without
          chips stay compact; the grid stretches each row to equal height so the
          `mt-auto` action row still bottom-aligns across the grid. */}
      {hasChips && (
        <div className="flex flex-wrap items-center gap-[5px]">
          {accentLabel && (
            <span className="inline-flex items-center gap-[5px] pl-[5px] pr-[8px] py-[2px] rounded-[7px] bg-[var(--color-bg-elev-2)] text-[var(--color-fg-muted)] text-[0.64rem] leading-[1.6]">
              <AccentFlag accent={a.facets.accent} lang={a.language} size={14} />
              {accentLabel}
            </span>
          )}
          {a.facets.whisper && (
            <span className="inline-flex items-center gap-[5px] px-[8px] py-[2px] rounded-[7px] bg-[var(--color-bg-elev-2)] text-[var(--color-fg-muted)] text-[0.64rem] leading-[1.6]">
              {t('archetypes.facet_whisper', { defaultValue: 'Whisper' })}
            </span>
          )}
        </div>
      )}

      {/* Actions — quiet Preview (ghost, token hover), confident accent Use voice
          (tinted → solid accent with inverse text). Use voice never wraps: the
          icon cluster lives in the header, so this row always fits. */}
      <div className="mt-auto flex items-center gap-[6px] pt-[9px]">
        <button
          type="button"
          className="inline-flex shrink-0 items-center gap-[6px] border border-transparent bg-transparent px-[9px] py-[6px] rounded-[6px] text-[var(--color-fg-muted)] text-[0.68rem] cursor-pointer transition-colors hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--color-fg)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onPreview(a)}
          disabled={previewLocked}
          aria-busy={isLoadingPreview}
          title={t('gallery.preview', { defaultValue: 'Preview' })}
        >
          {isLoadingPreview ? (
            <Loader className="spin" size={15} aria-hidden="true" />
          ) : isPlaying ? (
            <NowPlaying color={color} />
          ) : (
            <Play size={15} aria-hidden="true" />
          )}
          <span>{t('gallery.preview', { defaultValue: 'Preview' })}</span>
        </button>
        <button
          type="button"
          className="flex-1 inline-flex min-w-0 items-center justify-center gap-[6px] whitespace-nowrap border border-transparent px-[10px] py-[6px] rounded-[6px] bg-[color-mix(in_srgb,var(--card-accent)_13%,transparent)] text-[var(--card-accent)] text-[0.7rem] font-semibold cursor-pointer transition-colors hover:bg-[var(--card-accent)] hover:text-[var(--color-fg-inverse)] focus-visible:bg-[var(--card-accent)] focus-visible:text-[var(--color-fg-inverse)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onUse(a)}
          disabled={materializationLocked}
          aria-busy={isMaterializing}
        >
          {isMaterializing ? (
            <Loader className="spin" size={14} aria-hidden="true" />
          ) : (
            <UserPlus size={14} aria-hidden="true" />
          )}{' '}
          {t('gallery.use_voice', { defaultValue: 'Use voice' })}
        </button>
      </div>
    </div>
  );
}
