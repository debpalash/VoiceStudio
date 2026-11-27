import { ArrowLeftIcon, CheckIcon, PencilIcon, SearchIcon } from 'lucide-react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useProfiles } from '@/hooks/use-profiles';
import { DEMO_PROFILE_ID } from '@/hooks/use-clone-demo';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Profile } from '@/lib/api/types';
import { useCloneSetting } from '@/lib/store/clone-settings';
import { selectCloneProfile } from '@/lib/store/reference';
import { setWorkspace } from '@/lib/store/workspace';
import { cn } from '@/lib/utils';
import { ProfilePhoto, useProfileImageSave } from './profile-photo';
import { ReferencePanel } from './reference-panel';
import { CloneDemoAction } from './clone-demo';

const VIRTUALIZE_ABOVE = 30;
const SEARCH_ABOVE = 4;
const VOICE_ROW_HEIGHT = 80;
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
  const { t } = useTranslation();
  const photo = useProfileImageSave(profile);
  const language = profile.language === 'Auto' ? null : profile.language;
  const hasFile = (event: DragEvent) => Array.from(event.dataTransfer.types).includes('Files');
  return (
    <div
      data-slot="voice-card"
      // The photo ignores pointer events so clicks reach the card; drops land on the whole card.
      onDragOver={(event) => hasFile(event) && event.preventDefault()}
      onDrop={(event) => {
        // The photo's own drop handler already took (and prevented) a drop on its badge.
        if (event.defaultPrevented || !hasFile(event)) return;
        event.preventDefault();
        const file = event.dataTransfer.files[0];
        if (file) void photo.save(file);
      }}
      className={cn(
        'group/card relative flex h-[72px] min-w-0 items-center gap-2 rounded-2xl border px-3 transition-colors hover:border-border hover:bg-muted/40 has-[[data-choose]:focus-visible]:ring-2 has-[[data-choose]:focus-visible]:ring-ring @sm:gap-3 @sm:px-4',
        current ? 'border-primary/60 bg-primary/5' : 'border-border/70 bg-card/50',
      )}
    >
      {/* The row selects the voice. Independent actions sit above the row button. */}
      <button
        type="button"
        data-choose
        aria-label={`${t('clone.select_profile')}: ${profile.name}`}
        aria-current={current || undefined}
        className="absolute inset-0 rounded-2xl outline-none"
        onClick={() => onChoose(profile)}
      />
      <ProfilePhoto
        name={profile.name}
        imageUrl={profile.image_url}
        busy={photo.busy}
        onFile={(file) => void photo.save(file)}
        label={`${t('cloneFlow.change_photo')}: ${profile.name}`}
        className="z-10 size-10 text-sm"
        badge
      />
      <span className="pointer-events-none min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-base font-semibold">{profile.name}</span>
          {current && (
            <CheckIcon
              className="size-3.5 shrink-0 text-primary"
              aria-label={t('cloneFlow.current')}
            />
          )}
        </span>
        {language && (
          <span className="mt-0.5 block truncate text-sm text-muted-foreground">{language}</span>
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
      {profile.id === DEMO_PROFILE_ID && <CloneDemoAction compact />}
      <span
        aria-hidden="true"
        className="pointer-events-none relative inline-flex min-h-10 max-w-[40%] items-center rounded-full bg-primary px-3 py-1.5 text-center text-sm leading-tight font-semibold text-primary-foreground @sm:px-4"
      >
        {t('clone.select_profile')}
      </span>
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
    count: presentation === 'virtual' ? voices.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => VOICE_ROW_HEIGHT,
    overscan: 3,
    getItemKey: (row) => voices[row].id,
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
    <section
      data-slot="voice-chooser"
      className="@container mx-auto flex w-full max-w-3xl flex-col gap-7"
    >
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('preferences.back')}
          title={t('preferences.back')}
          className="shrink-0"
          onClick={onBack}
        >
          <ArrowLeftIcon />
        </Button>
        <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('cloneFlow.choose_voice')}
        </h2>
      </div>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="me-auto text-lg font-semibold tracking-tight">
            {t('clone.saved_profiles')}
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
          {eligibleVoices.length === 0 ? (
            !profiles.isPending && (
              <p className="text-sm text-muted-foreground">{t('cloneFlow.choose_hint')}</p>
            )
          ) : voices.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t('preferences.no_matches')}
            </p>
          ) : presentation === 'virtual' ? (
            <div className="relative w-full" style={{ height: rows.getTotalSize() }}>
              {rows.getVirtualItems().map((row) => (
                <div
                  key={row.key}
                  className="absolute left-0 top-0 w-full pb-2"
                  style={{ transform: `translateY(${row.start}px)` }}
                >
                  {card(voices[row.index])}
                </div>
              ))}
            </div>
          ) : (
            <div className="grid gap-2">{voices.map(card)}</div>
          )}
        </div>
      </div>
      {profiles.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('preferences.loading')}
        </p>
      )}
      <section className="space-y-2">
        <h3 className="text-lg font-semibold tracking-tight">{t('cloneFlow.add_voice')}</h3>
        <ReferencePanel setup />
      </section>
    </section>
  );
}
