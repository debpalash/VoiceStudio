import React, { useState, useMemo, useEffect } from 'react';
import {
  Loader,
  Star,
  RotateCcw,
  Grid,
  List,
  SlidersHorizontal,
  Sparkles,
  SearchX,
  Search,
  X,
  User,
  Cake,
  AudioLines,
  Flag,
  Languages,
  VolumeX,
  LayoutGrid,
} from 'lucide-react';
import { Button, Input, Select, Segmented } from '../../ui';
import { useArchetypeCategories, useArchetypes } from '../../api/hooks';
import { titleCase, facetLabel, GALLERY_GRID } from './constants';
import ArchetypeCard from './ArchetypeCard';
import GallerySectionHeader from './GallerySectionHeader';

const BROWSE_PAGE = 60;
const SEARCH_DEBOUNCE_MS = 200;

// Tiny local debounce so typing in the search box doesn't fire a backend
// query per keystroke (same pattern as VoiceSelector's picker search).
function useDebounced(value, ms) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

// Facet vocabularies — values must match the backend taxonomy tokens exactly.
const FACETS = {
  gender: ['male', 'female'],
  age: ['child', 'teenager', 'young adult', 'middle-aged', 'elderly'],
  pitch: ['very low pitch', 'low pitch', 'moderate pitch', 'high pitch', 'very high pitch'],
  accent: [
    'american accent',
    'british accent',
    'australian accent',
    'canadian accent',
    'indian accent',
    'chinese accent',
    'japanese accent',
    'korean accent',
    'portuguese accent',
    'russian accent',
  ],
  // English + Chinese come from the generated catalog; the rest are curated
  // multilingual designed voices. Values must match the archetype `language`
  // field (a languages.json entry) exactly — that drives the backend filter.
  lang: [
    'English',
    'Chinese',
    'Spanish',
    'French',
    'German',
    'Italian',
    'Portuguese',
    'Russian',
    'Hindi',
    'Japanese',
    'Korean',
  ],
};

const hasActiveFilters = (f) => Object.values(f).some((v) => v !== null && v !== '');

// One icon per filter dimension — shared by the Filters panel selects and
// the active-filter pills so both read as one family.
const FACET_ICONS = {
  use_case: LayoutGrid,
  gender: User,
  age: Cake,
  pitch: AudioLines,
  accent: Flag,
  lang: Languages,
  whisper: VolumeX,
  q: Search,
  favOnly: Star,
};

const facetIconCls = 'shrink-0 text-[var(--chrome-fg-muted)]';

// ── Archetypes zone ─────────────────────────────────────────────────────────
export default function ArchetypesZone({
  t,
  filters,
  setFilter,
  resetFilters,
  favorites,
  toggleFavorite,
  viewMode,
  setViewMode,
  playingId,
  loadingPreviewId,
  onPreview,
  onUse,
  onDesign,
  onUseInStories,
  onUseAsAudiobookDefault,
  materializingId,
}) {
  const [favOnly, setFavOnly] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  // Free-text search over name/instruct (backend `q`) — the facet filters
  // alone can't reach a specific voice by name in a several-hundred catalog.
  const [rawQ, setRawQ] = useState('');
  const q = useDebounced(rawQ.trim(), SEARCH_DEBOUNCE_MS);
  useEffect(() => {
    setOffset(0);
  }, [filters, q]);

  const cleanFilters = useMemo(() => {
    const out = {};
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== null && v !== '') out[k] = v;
    });
    return out;
  }, [filters]);

  // Featured/browse exclusion mirrors the filter logic: once the user narrows
  // by facet OR by search text, the Featured strip hides and Browse includes
  // everything — otherwise curated-only languages would filter to empty.
  const showFeatured = !hasActiveFilters(filters) && !favOnly && !q;

  const categoriesQ = useArchetypeCategories();
  const featuredQ = useArchetypes({ featured: true, limit: 100 });
  const browseQ = useArchetypes({
    ...cleanFilters,
    ...(q ? { q } : {}),
    ...(showFeatured ? { featured: false } : {}),
    limit: BROWSE_PAGE,
    offset,
  });

  const categories = categoriesQ.data || [];
  const featured = featuredQ.data?.items || [];
  const browse = browseQ.data?.items || [];
  const total = browseQ.data?.total ?? 0;

  const favSet = useMemo(() => new Set(favorites), [favorites]);

  const categoryName = (id) => categories.find((c) => c.id === id)?.name || titleCase(id || '');
  // Active-filter pills: every applied narrowing as a removable chip, so the
  // bar says what it is doing without opening the Filters panel. Clearing a
  // pill clears just that dimension; Clear-all resets everything incl. search.
  const pills = [
    favOnly
      ? {
          key: 'favOnly',
          Icon: FACET_ICONS.favOnly,
          label: t('gallery.favorites', { defaultValue: 'Favorites' }),
          clear: () => setFavOnly(false),
        }
      : null,
    filters.use_case
      ? {
          key: 'use_case',
          Icon: FACET_ICONS.use_case,
          label: categoryName(filters.use_case),
          clear: () => setFilter('use_case', null),
        }
      : null,
    ...['gender', 'age', 'pitch', 'accent', 'lang'].map((dim) =>
      filters[dim]
        ? {
            key: dim,
            Icon: FACET_ICONS[dim],
            label: facetLabel(filters[dim]),
            clear: () => setFilter(dim, null),
          }
        : null,
    ),
    filters.whisper === true
      ? {
          key: 'whisper',
          Icon: FACET_ICONS.whisper,
          label: t('archetypes.facet_whisper', { defaultValue: 'Whisper' }),
          clear: () => setFilter('whisper', null),
        }
      : null,
    q ? { key: 'q', Icon: FACET_ICONS.q, label: `“${q}”`, clear: () => setRawQ('') } : null,
  ].filter(Boolean);
  const clearAll = () => {
    resetFilters();
    setFavOnly(false);
    setRawQ('');
  };
  const applyFav = (list) => (favOnly ? list.filter((a) => favSet.has(a.id)) : list);
  const advancedFilterCount = ['gender', 'age', 'pitch', 'accent', 'lang', 'whisper'].filter(
    (key) => filters[key] !== null && filters[key] !== '',
  ).length;

  // NOTE: no `key` here — React keys must be passed directly on the element,
  // not spread in (spreading a `key` prop triggers a dev warning + is ignored).
  const cardProps = (a) => ({
    a,
    t,
    viewMode,
    isFavorite: favSet.has(a.id),
    isPlaying: playingId === a.id,
    isLoadingPreview: loadingPreviewId === a.id,
    previewLocked: Boolean(loadingPreviewId),
    onPreview,
    onUse,
    onDesign,
    onUseInStories,
    onUseAsAudiobookDefault,
    onToggleFavorite: toggleFavorite,
    isMaterializing: materializingId === a.id,
    materializationLocked: Boolean(materializingId),
  });

  const facetToggle =
    'inline-flex items-center gap-[5px] h-[26px] box-border px-[9px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-[var(--chrome-hover-bg)] text-[var(--chrome-fg-muted)] text-[0.68rem] whitespace-nowrap cursor-pointer hover:text-[var(--chrome-fg)]';
  const gridClass = viewMode === 'grid' ? GALLERY_GRID : 'flex flex-col gap-[6px]';

  return (
    // data-testid: stable e2e hook — locale-independent, unlike the translated
    // aria-labels/headings inside (see e2e/gallery.spec.ts).
    <div data-testid="archetypes-zone" className="flex-1 min-h-0 flex flex-col overflow-y-auto">
      <div className="shrink-0 mb-[8px] pb-[8px] border-b border-transparent">
        <div className="flex items-center gap-[6px] min-w-0">
          <div className="relative min-w-[140px] flex-[1_1_200px]">
            <Search
              size={14}
              aria-hidden="true"
              className="pointer-events-none absolute left-[9px] top-1/2 -translate-y-1/2 text-[var(--chrome-fg-muted)]"
            />
            <Input
              size="sm"
              className="w-full pl-[30px] pr-[26px]"
              value={rawQ}
              onChange={(e) => setRawQ(e.target.value)}
              placeholder={t('common.search', { defaultValue: 'Search…' })}
              aria-label={t('common.search', { defaultValue: 'Search…' })}
            />
            {rawQ && (
              <button
                type="button"
                className="absolute right-[5px] top-1/2 flex h-[20px] w-[20px] -translate-y-1/2 items-center justify-center rounded-[6px] border border-transparent bg-transparent text-[var(--chrome-fg-muted)] transition-colors hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--color-fg)]"
                onClick={() => setRawQ('')}
                aria-label={t('common.clear', { defaultValue: 'Clear' })}
              >
                <X size={12} aria-hidden="true" />
              </button>
            )}
          </div>
          <span className="inline-flex min-w-0 shrink-0 items-center gap-[5px]">
            <LayoutGrid size={13} aria-hidden="true" className={facetIconCls} />
            <Select
              size="sm"
              className="w-auto min-w-[118px] max-w-[170px]"
              aria-label={t('gallery.zone_archetypes', { defaultValue: 'Archetypes' })}
              value={filters.use_case ?? ''}
              onChange={(e) => setFilter('use_case', e.target.value || null)}
            >
              <option value="">{t('gallery.all', { defaultValue: 'All' })}</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {t(`archetypes.use_${c.id}`, { defaultValue: c.name })}
                </option>
              ))}
            </Select>
          </span>
          <Button
            variant="ghost"
            size="sm"
            active={filtersOpen}
            leading={<SlidersHorizontal size={13} />}
            trailing={
              advancedFilterCount > 0 ? (
                <span className="min-w-[16px] rounded-full bg-[var(--accent)] px-[4px] py-px text-center text-[0.58rem] leading-[14px] text-white">
                  {advancedFilterCount}
                </span>
              ) : null
            }
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((open) => !open)}
          >
            {t('gallery.filters', { defaultValue: 'Filters' })}
          </Button>
          <button
            type="button"
            className={`inline-flex items-center gap-[5px] h-[26px] box-border px-[9px] rounded-[var(--chrome-radius-pill)] border border-transparent bg-transparent text-[0.68rem] whitespace-nowrap cursor-pointer transition-colors hover:bg-[var(--chrome-hover-bg)] ${
              favOnly
                ? 'text-[#fabd2f]'
                : 'text-[var(--chrome-fg-muted)] hover:text-[var(--chrome-fg)]'
            }`}
            aria-pressed={favOnly}
            onClick={() => setFavOnly((v) => !v)}
          >
            <Star size={12} fill={favOnly ? 'currentColor' : 'none'} aria-hidden="true" />{' '}
            {t('gallery.favorites', { defaultValue: 'Favorites' })}
          </button>
          <div className="ml-auto shrink-0">
            <Segmented
              size="xs"
              value={viewMode}
              onChange={setViewMode}
              items={[
                {
                  value: 'grid',
                  label: <Grid size={14} />,
                  title: t('library.card_grid'),
                },
                { value: 'list', label: <List size={14} />, title: t('library.list') },
              ]}
            />
          </div>
        </div>

        {filtersOpen ? (
          <div className="mt-[6px] flex items-center gap-[6px] overflow-x-auto pb-px [scrollbar-width:thin]">
            {['gender', 'age', 'pitch', 'accent', 'lang'].map((dim) => {
              const DimIcon = FACET_ICONS[dim];
              return (
                <span key={dim} className="inline-flex shrink-0 items-center gap-[5px]">
                  <DimIcon size={13} aria-hidden="true" className={facetIconCls} />
                  <Select
                    size="sm"
                    className="w-auto min-w-[88px] max-w-[124px]"
                    aria-label={t(`archetypes.facet_${dim}`, { defaultValue: titleCase(dim) })}
                    value={filters[dim] ?? ''}
                    onChange={(e) => setFilter(dim, e.target.value || null)}
                  >
                    <option value="">
                      {t(`archetypes.facet_${dim}`, { defaultValue: titleCase(dim) })}
                    </option>
                    {FACETS[dim].map((opt) => (
                      <option key={opt} value={opt}>
                        {facetLabel(opt)}
                      </option>
                    ))}
                  </Select>
                </span>
              );
            })}
            <label className={`${facetToggle} shrink-0`}>
              <VolumeX size={13} aria-hidden="true" className={facetIconCls} />
              <input
                type="checkbox"
                checked={filters.whisper === true}
                onChange={(e) => setFilter('whisper', e.target.checked ? true : null)}
              />
              {t('archetypes.facet_whisper', { defaultValue: 'Whisper' })}
            </label>
          </div>
        ) : null}

        {/* Active narrowings as removable pills + one Clear-all — the bar says
            what it is doing without opening the Filters panel. */}
        {pills.length > 0 && (
          <div className="mt-[6px] flex flex-wrap items-center gap-[5px]">
            {pills.map((pill) => (
              <span
                key={pill.key}
                className="inline-flex max-w-full items-center gap-[5px] rounded-[var(--chrome-radius-pill)] bg-[var(--chrome-accent-bg)] py-[2px] pl-[8px] pr-[5px] text-[0.66rem] text-[color:var(--chrome-accent)]"
              >
                <pill.Icon size={11} aria-hidden="true" className="shrink-0" />
                <span className="min-w-0 truncate">{pill.label}</span>
                <button
                  type="button"
                  className="flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded-full border border-transparent bg-transparent transition-colors hover:bg-[color-mix(in_srgb,var(--chrome-accent)_22%,transparent)]"
                  onClick={pill.clear}
                  aria-label={t('common.remove', {
                    defaultValue: 'Remove {{term}}',
                    term: pill.label,
                  })}
                >
                  <X size={10} aria-hidden="true" />
                </button>
              </span>
            ))}
            <button
              type="button"
              className="inline-flex shrink-0 cursor-pointer items-center gap-[4px] border border-transparent bg-transparent px-[6px] text-[0.66rem] text-[var(--chrome-fg-muted)] transition-colors hover:text-[var(--color-fg)]"
              onClick={clearAll}
              aria-label={t('gallery.reset', { defaultValue: 'Reset' })}
            >
              <RotateCcw size={11} aria-hidden="true" />
              {t('gallery.reset', { defaultValue: 'Reset' })}
            </button>
          </div>
        )}
      </div>

      {showFeatured && (
        <section className="mb-[16px]">
          <GallerySectionHeader
            icon={<Sparkles size={12} strokeWidth={1.5} color="#fabd2f" aria-hidden="true" />}
            title={t('archetypes.featured', { defaultValue: 'Featured' })}
          />
          <div className={gridClass}>
            {applyFav(featured).map((a) => (
              <ArchetypeCard key={a.id} {...cardProps(a)} />
            ))}
          </div>
        </section>
      )}

      <section className="mb-[14px]">
        <GallerySectionHeader
          title={t('archetypes.browse_all', { defaultValue: 'Browse all' })}
          count={total}
        />
        {browseQ.isLoading ? (
          <div className="flex items-center justify-center gap-[8px] p-[24px] text-[var(--text-secondary)]">
            <Loader className="spin" size={18} />
          </div>
        ) : (
          <>
            <div className={gridClass}>
              {applyFav(browse).map((a) => (
                <ArchetypeCard key={a.id} {...cardProps(a)} />
              ))}
            </div>
            {applyFav(browse).length === 0 && (
              <div className="flex flex-col items-center justify-center gap-[8px] px-[16px] py-[32px] text-center text-[var(--text-secondary)]">
                <SearchX size={20} strokeWidth={1.5} aria-hidden="true" />
                <span className="max-w-[300px] text-[0.78rem] leading-[1.6]">
                  {t('gallery.no_matches', { defaultValue: 'No voices match these filters.' })}
                </span>
              </div>
            )}
            {offset + BROWSE_PAGE < total && !favOnly && (
              <div className="flex justify-center py-[12px]">
                <Button
                  variant="ghost"
                  onClick={() => setOffset(offset + BROWSE_PAGE)}
                  disabled={browseQ.isFetching}
                >
                  {browseQ.isFetching ? <Loader className="spin" size={14} /> : null}
                  {t('gallery.load_more', { defaultValue: 'Load more' })}
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
