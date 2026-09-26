import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Play, Loader, ExternalLink, Plus, Star } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import SearchableSelect from './SearchableSelect';
import { ArchetypeIcon } from '../utils/archetypeIcons';
import { PRESETS } from '../utils/constants';
import { useArchetypes } from '../api/hooks';
import { autoProfileId } from '../utils/segments';
import { useArchetypeAsProfile } from '../api/archetypes';
import { useAppStore } from '../store';

/**
 * Shared voice picker (#22) — one searchable, grouped control used everywhere a
 * voice is chosen (Stories cast, Audiobook default, Dub segments). A thin
 * wrapper over {@link SearchableSelect}: it builds a group-ordered options array
 * and surfaces optional preview / gallery-jump / create adornments.
 *
 * It owns NO audio. It is a controlled component for its `value` (it only emits
 * a value string via `onChange`). It makes TWO scoped API calls — and only for
 * the gallery group (#1219): (1) it fetches `/archetypes` while its dropdown is
 * open so the gallery search box can reach the whole catalog, and (2) when the
 * user *picks* a gallery voice it materializes it into a real voice profile
 * (`useArchetypeAsProfile` → POST /archetypes/{id}/use) and emits the returned
 * **profile id** — never the archetype id, which no backend voice resolver can
 * use. Materialize-on-select keeps the value contract below intact: downstream
 * code and the backend only ever see a normal profile id.
 *
 * Value contract (identical to what every existing call site already sends to
 * the backend, so project data stays byte-compatible):
 *   '' (engine default) | '<profileId>' | 'preset:<id>' | 'auto:<slug>'
 *
 * Group order is fixed: default → fromVideo (dub only) → clone → designed →
 * gallery → preset. Clone-vs-designed splits on the runtime `.instruct` string
 * (matching VoicePreview/DubSegmentRow), NOT `profile.kind`.
 *
 * @param {string}   value            controlled value (see contract above)
 * @param {(v:string)=>void} onChange commits a new value
 * @param {Array}    [profiles=[]]    voice profiles ({id,name,instruct?})
 * @param {boolean}  [presets=false]  include the PRESETS character group
 * @param {boolean}  [gallery=true]   offer the Voice Gallery (archetype) group
 * @param {Object}   [speakerClones=null] dub from-video speakers ({name: ...})
 * @param {boolean}  [engineDefault=true] include the '' engine-default row
 * @param {string}   [defaultLabel]   overrides the '' row label (Stories "↳ Aria")
 * @param {(v:string)=>void} [onPreview]   render a preview button → calls this
 * @param {boolean}  [previewLoading=false] show a spinner + disable preview
 * @param {()=>void} [onJumpToGallery] render a gallery-jump button → calls this
 * @param {()=>void} [onCreateVoice]  render an inline "create voice" button
 * @param {string}   [recentsKey='']  persist recents under this key (real ids only)
 * @param {string}   [placeholder]    trigger placeholder when nothing resolves
 * @param {string}   [ariaLabel]      accessible label for the select trigger
 * @param {boolean}  [menuPortal=false] portal the dropdown to <body> (needed
 *   inside clipping ancestors: overflow:auto panels / react-window rows — #1220)
 */
// Adornment icon-button (preview / gallery / create). Layout + reset as
// utilities; :hover/:disabled states stay in VoiceSelector.css.
const VOICE_SELECTOR_BTN =
  'voice-selector__btn inline-flex items-center justify-center w-[26px] h-[26px] p-0 [border:1px_solid_var(--chrome-border,rgba(255,255,255,0.12))] rounded-[6px] bg-transparent text-[var(--chrome-fg-muted,#999)] cursor-pointer';

const ARCHETYPE_PREFIX = 'archetype:';

// Tiny local debounce so typing in the gallery search box doesn't fire a
// /archetypes request per keystroke. No new dependency.
function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

export default function VoiceSelector({
  value = '',
  onChange,
  profiles = [],
  presets = false,
  gallery = true,
  speakerClones = null,
  engineDefault = true,
  defaultLabel,
  onPreview,
  previewLoading = false,
  onJumpToGallery,
  onCreateVoice,
  recentsKey = '',
  placeholder,
  ariaLabel,
  disabled = false,
  size = 'md',
  buttonClassName,
  menuPortal = false,
  menuClassName = '',
}) {
  const { t } = useTranslation();

  // ── Gallery (archetype) state — only fetched while the dropdown is open ──
  const favoriteIds = useAppStore((s) => s.favoriteArchetypeIds);
  const [open, setOpen] = useState(false);
  const [rawQuery, setRawQuery] = useState('');
  const searchQuery = useDebounced(rawQuery.trim(), 200);
  const [materializing, setMaterializing] = useState(false);
  // Optimistic row for a voice we just materialized, so the trigger shows its
  // name immediately instead of a ghost "not found" flash while the real
  // profiles list catches up (the backend emits a `profiles` realtime event on
  // /use, which triggers loadProfiles app-wide — no prop plumbing needed).
  const [justMaterialized, setJustMaterialized] = useState(null);

  // With a query → search the whole catalog; without → a small featured page.
  const archFilters = useMemo(
    () => (searchQuery ? { q: searchQuery, limit: 40 } : { featured: true, limit: 60 }),
    [searchQuery],
  );
  const { data: archPage } = useArchetypes(archFilters, gallery && open);

  const galleryOptions = useMemo(() => {
    if (!gallery) return [];
    const items = archPage?.items || [];
    const favSet = new Set(favoriteIds);
    // Favorites first (within the loaded set), otherwise catalog order.
    const ordered = [...items].sort(
      (a, b) => (favSet.has(b.id) ? 1 : 0) - (favSet.has(a.id) ? 1 : 0),
    );
    return ordered.map((a) => ({
      value: `${ARCHETYPE_PREFIX}${a.id}`,
      label: a.name,
      group: 'gallery',
      groupLabel: t('voiceSelector.gallery'),
      archetypeIcon: a.icon,
      isFavorite: favSet.has(a.id),
    }));
  }, [gallery, archPage, favoriteIds, t]);

  const galleryNameById = useMemo(() => {
    const m = new Map();
    for (const a of archPage?.items || []) m.set(a.id, a.name);
    return m;
  }, [archPage]);

  const options = useMemo(() => {
    const list = [];

    // 1. engine-default sentinel — FIRST, no group header (groupLabel '').
    if (engineDefault) {
      list.push({
        value: '',
        label: defaultLabel || t('voiceSelector.engineDefault'),
        group: 'default',
        groupLabel: '',
      });
    }

    // 2. fromVideo (dub only) — slug rule byte-identical to DubSegmentRow.
    const speakers = speakerClones ? Object.keys(speakerClones) : [];
    for (const spk of speakers) {
      list.push({
        value: autoProfileId(spk),
        label: `🎤 ${spk}`,
        group: 'fromVideo',
        groupLabel: t('voiceSelector.fromVideo'),
      });
    }

    // 3 & 4. clone vs designed — split on runtime `.instruct` (not .kind).
    const clones = profiles.filter((p) => !p.instruct);
    const designed = profiles.filter((p) => !!p.instruct);
    for (const p of clones) {
      list.push({
        value: p.id,
        label: p.name?.trim() || p.id,
        group: 'clone',
        groupLabel: t('voiceSelector.clone'),
      });
    }
    for (const p of designed) {
      list.push({
        value: p.id,
        label: p.name?.trim() || p.id,
        group: 'designed',
        groupLabel: t('voiceSelector.designed'),
      });
    }
    // Optimistic just-materialized voice — shown as a designed row until the
    // real profiles list refetch includes it (deduped by value).
    if (justMaterialized && !profiles.some((p) => p.id === justMaterialized.id)) {
      list.push({
        value: justMaterialized.id,
        label: justMaterialized.name?.trim() || justMaterialized.id,
        group: 'designed',
        groupLabel: t('voiceSelector.designed'),
      });
    }

    // 5. gallery (archetype) group — materialize-on-select.
    for (const g of galleryOptions) list.push(g);

    // 6. presets.
    if (presets) {
      for (const p of PRESETS) {
        list.push({
          value: `preset:${p.id}`,
          label: p.name,
          group: 'preset',
          groupLabel: t('voiceSelector.presets'),
        });
      }
    }

    // Ghost: a real profile id is selected but the profile is gone (deleted but
    // still referenced by a track/segment/default). Render a human label so the
    // trigger isn't the raw id — but DON'T auto-clear (that mutates user data).
    const isSentinel =
      !value ||
      value.startsWith('preset:') ||
      value.startsWith('auto:') ||
      value.startsWith(ARCHETYPE_PREFIX);
    if (!isSentinel && !list.some((o) => o.value === value)) {
      list.push({
        value,
        label: t('voiceSelector.missingVoice'),
        group: 'clone',
        groupLabel: t('voiceSelector.clone'),
      });
    }

    return list;
  }, [
    profiles,
    presets,
    galleryOptions,
    justMaterialized,
    speakerClones,
    engineDefault,
    defaultLabel,
    value,
    t,
  ]);

  // Only real profile ids are worth recording as recents (never the archetype:
  // sentinel — the picked gallery value is transient, replaced by the profile id).
  const isRecentable = (v) =>
    !!v && !v.startsWith('preset:') && !v.startsWith('auto:') && !v.startsWith(ARCHETYPE_PREFIX);

  // Materialize-on-select: an `archetype:<id>` pick is turned into a real voice
  // profile before it ever reaches the parent, which only ever sees a profile id.
  const handleChange = useCallback(
    async (v) => {
      if (typeof v !== 'string' || !v.startsWith(ARCHETYPE_PREFIX)) {
        onChange?.(v);
        return;
      }
      const id = v.slice(ARCHETYPE_PREFIX.length);
      const name = galleryNameById.get(id);
      setMaterializing(true);
      try {
        // eslint-disable-next-line react-hooks/rules-of-hooks -- useArchetypeAsProfile is an API call, not a React hook
        const r = await useArchetypeAsProfile(id, name);
        setJustMaterialized({ id: r.profile_id, name: r.name });
        onChange?.(r.profile_id);
      } catch (e) {
        // Keep the previous value; tell the user it didn't take with an
        // actionable, non-technical message (the raw error goes to the console).
        console.error('[VoiceSelector] failed to add gallery voice', e);
        toast.error(t('voiceSelector.addVoiceFailed'));
      } finally {
        setMaterializing(false);
      }
    },
    [onChange, galleryNameById, t],
  );

  const renderOption = useCallback((o) => {
    if (o && o.archetypeIcon) {
      return (
        <span className="inline-flex items-center gap-[6px] min-w-0">
          <ArchetypeIcon name={o.archetypeIcon} size={13} />
          <span className="overflow-hidden text-ellipsis whitespace-nowrap">{o.label}</span>
          {o.isFavorite && (
            <Star size={9} className="text-[color:var(--accent)] shrink-0 fill-current" />
          )}
        </span>
      );
    }
    return o?.label ?? o?.value ?? '';
  }, []);

  return (
    <div className="voice-selector flex items-center gap-[6px] min-w-0">
      <SearchableSelect
        value={value}
        onChange={handleChange}
        options={options}
        renderGroupHeaders
        renderOption={renderOption}
        isRecentable={isRecentable}
        recentsKey={recentsKey}
        placeholder={
          materializing
            ? t('voiceSelector.addingVoice')
            : placeholder || t('voiceSelector.engineDefault')
        }
        disabled={disabled || materializing}
        size={size}
        buttonClassName={buttonClassName}
        ariaLabel={ariaLabel}
        menuPortal={menuPortal}
        menuClassName={menuClassName}
        onOpenChange={setOpen}
        onQueryChange={setRawQuery}
      />
      {(materializing || onPreview || onJumpToGallery || onCreateVoice) && (
        <div className="voice-selector__adornments inline-flex items-center gap-[2px] flex-[0_0_auto]">
          {materializing && (
            <span
              className="inline-flex items-center justify-center w-[26px] h-[26px]"
              role="status"
              aria-label={t('voiceSelector.addingVoice')}
              title={t('voiceSelector.addingVoice')}
            >
              <Loader size={13} className="voice-selector__spin" />
            </span>
          )}
          {onPreview && (
            <button
              type="button"
              className={VOICE_SELECTOR_BTN}
              onClick={() => onPreview(value)}
              disabled={previewLoading}
              aria-label={t('voiceSelector.preview')}
              title={t('voiceSelector.preview')}
            >
              {previewLoading ? (
                <Loader size={13} className="voice-selector__spin" />
              ) : (
                <Play size={13} />
              )}
            </button>
          )}
          {onJumpToGallery && (
            <button
              type="button"
              className={VOICE_SELECTOR_BTN}
              onClick={() => onJumpToGallery()}
              aria-label={t('voiceSelector.openGallery')}
              title={t('voiceSelector.openGallery')}
            >
              <ExternalLink size={13} />
            </button>
          )}
          {onCreateVoice && (
            <button
              type="button"
              className={VOICE_SELECTOR_BTN}
              onClick={() => onCreateVoice()}
              aria-label={t('voiceSelector.createVoice')}
              title={t('voiceSelector.createVoice')}
            >
              <Plus size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
