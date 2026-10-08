import { ArrowLeftIcon, CheckIcon, PencilIcon, PlusIcon, SearchIcon } from 'lucide-react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useProfiles } from '@/hooks/use-profiles';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Profile } from '@/lib/api/types';
import { useCloneSetting } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import { setWorkspace } from '@/lib/store/workspace';
import { cn } from '@/lib/utils';
import { ProfilePhoto, useProfileImageSave } from './profile-photo';
import { ReferencePanel } from './reference-panel';

const VIRTUALIZE_ABOVE = 30;
const SEARCH_ABOVE = 4;
const VOICE_ROW_HEIGHT = 76;
const SORT_KEY = 'voicestudio.voice-picker-sort';
type Sort = 'recent' | 'name';

export function voiceGridPresentation(count: number): 'grid' | 'virtual' {
  return count > VIRTUALIZE_ABOVE ? 'virtual' : 'grid';
}

function createdAt(profile: Profile): number {
  const value = profile.created_at;
  if (typeof value === 'number') return value * 1000;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function readSort(): Sort {
  try {
    return localStorage.getItem(SORT_KEY) === 'name' ? 'name' : 'recent';
  } catch {
    return 'recent';
  }
}

function VoiceCard({
  profile,
  current,
  onChoose,
}: {
  profile: Profile;
  current: boolean;
  onChoose: (profile: Profile) => void;
}) {
  const { t, i18n } = useTranslation();
  const photo = useProfileImageSave(profile);
  const created = createdAt(profile);
  const meta = [
    profile.language && profile.language !== 'Auto' ? profile.language : null,
    created
      ? new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' }).format(created)
      : null,
  ].filter(Boolean);
  return (
    <div
      data-slot="voice-card"
      className={cn(
        'group/card relative flex h-[68px] min-w-0 items-center gap-3 rounded-xl border bg-card/40 px-3 transition-[background-color,border-color,box-shadow] duration-150 hover:border-border hover:bg-muted/40 has-[[data-choose]:focus-visible]:ring-2 has-[[data-choose]:focus-visible]:ring-ring',
        current ? 'border-primary/60 bg-primary/5' : 'border-border/60',
      )}
    >
      {/* Covers the card so the whole surface chooses the voice; photo and edit sit above it. */}
      <button
        type="button"
        data-choose
        aria-label={profile.name}
        aria-current={current || undefined}
        className="absolute inset-0 rounded-xl outline-none"
        onClick={() => onChoose(profile)}
      />
      <ProfilePhoto
        name={profile.name}
        imageUrl={profile.image_url}
        busy={photo.busy}
        onFile={(file) => void photo.save(file)}
        label={`${t('cloneFlow.change_photo')}: ${profile.name}`}
        className="z-10 size-11 text-sm"
      />
      <span className="pointer-events-none min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{profile.name}</span>
          {current && (
            <CheckIcon
              className="size-3.5 shrink-0 text-primary"
              aria-label={t('cloneFlow.current')}
            />
          )}
        </span>
        {meta.length > 0 && (
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {meta.join(' · ')}
          </span>
        )}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`${t('clone.edit_voice')}: ${profile.name}`}
        title={t('clone.edit_voice')}
        className="z-10 opacity-0 transition-opacity group-focus-within/card:opacity-100 group-hover/card:opacity-100 focus-visible:opacity-100 max-md:opacity-100 motion-reduce:transition-none"
        onClick={() => setWorkspace({ editingProfileId: profile.id, panel: null })}
      >
        <PencilIcon />
      </Button>
    </div>
  );
}

export function VoiceSetup({ onChosen, onBack }: { onChosen: () => void; onBack: () => void }) {
  const { t, i18n } = useTranslation();
  const profiles = useProfiles();
  const selectedId = useCloneSetting('selectedProfileId');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<Sort>(readSort);
  const scrollRef = useRef<HTMLDivElement>(null);
  const eligibleVoices = useMemo(
    () =>
      (profiles.data ?? []).filter((profile) => profile.kind === 'clone' && profile.ref_audio_path),
    [profiles.data],
  );
  const voices = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const matches = normalized
      ? eligibleVoices.filter((profile) => profile.name.toLocaleLowerCase().includes(normalized))
      : eligibleVoices;
    const collator = new Intl.Collator(i18n.language, { sensitivity: 'base', numeric: true });
    return [...matches].sort((a, b) =>
      sort === 'name' ? collator.compare(a.name, b.name) : createdAt(b) - createdAt(a),
    );
  }, [eligibleVoices, query, sort, i18n.language]);
  const presentation = voiceGridPresentation(voices.length);
  const rows = useVirtualizer({
    count: presentation === 'virtual' ? Math.ceil(voices.length / 2) : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => VOICE_ROW_HEIGHT,
    overscan: 3,
    getItemKey: (row) => `${voices[row * 2]?.id}:${voices[row * 2 + 1]?.id ?? ''}`,
  });

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [query, sort]);

  const chooseSort = (next: Sort) => {
    setSort(next);
    try {
      localStorage.setItem(SORT_KEY, next);
    } catch {
      // Sorting still applies for this session.
    }
  };
  const chooseVoice = (profile: Profile) => {
    selectCloneProfile(profile);
    onChosen();
  };
  const card = (profile: Profile) => (
    <VoiceCard
      key={profile.id}
      profile={profile}
      current={profile.id === selectedId}
      onChoose={chooseVoice}
    />
  );

  return (
    <section className="flex w-full flex-col gap-7">
      <div className="flex items-start gap-3">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('preferences.back')}
          title={t('preferences.back')}
          className="mt-0.5"
          onClick={onBack}
        >
          <ArrowLeftIcon />
        </Button>
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight">{t('cloneFlow.choose_voice')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('cloneFlow.choose_hint')}</p>
        </div>
      </div>
      {eligibleVoices.length > 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="me-auto flex items-center gap-2 text-sm font-medium">
              {t('clone.saved_profiles')}
              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground tabular-nums">
                {eligibleVoices.length}
              </span>
            </h3>
            {eligibleVoices.length > SEARCH_ABOVE && (
              <div className="relative w-full sm:w-56">
                <SearchIcon className="pointer-events-none absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label={t('common.search')}
                  placeholder={t('common.search')}
                  className="h-8 ps-8 text-sm"
                />
              </div>
            )}
            {eligibleVoices.length > 1 && (
              <div
                role="radiogroup"
                aria-label={t('cloneFlow.sort_label')}
                className="flex items-center gap-0.5 rounded-lg bg-muted/40 p-0.5 ring-1 ring-inset ring-border/50"
              >
                {(['recent', 'name'] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={sort === value}
                    onClick={() => chooseSort(value)}
                    className={cn(
                      'h-7 rounded-md px-2.5 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
                      sort === value
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {t(value === 'recent' ? 'cloneFlow.sort_recent' : 'cloneFlow.sort_name')}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div ref={scrollRef} className="-m-1 max-h-[19rem] overflow-y-auto p-1">
            {voices.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {t('preferences.no_matches')}
              </p>
            ) : presentation === 'virtual' ? (
              <div className="relative w-full" style={{ height: rows.getTotalSize() }}>
                {rows.getVirtualItems().map((row) => (
                  <div
                    key={row.key}
                    className="absolute left-0 top-0 grid w-full grid-cols-2 gap-2 pb-2"
                    style={{ transform: `translateY(${row.start}px)` }}
                  >
                    {voices.slice(row.index * 2, row.index * 2 + 2).map(card)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))] gap-2">
                {voices.map(card)}
              </div>
            )}
          </div>
        </div>
      )}
      {profiles.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('preferences.loading')}
        </p>
      )}
      <div className="space-y-3">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <PlusIcon className="size-4 text-muted-foreground" aria-hidden="true" />
          {t('cloneFlow.add_voice')}
        </h3>
        <ReferencePanel setup />
      </div>
    </section>
  );
}
