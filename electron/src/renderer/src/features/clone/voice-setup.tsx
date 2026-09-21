import { ProfileAvatar } from '@/components/profile-avatar';
import { ArrowLeftIcon, SearchIcon } from 'lucide-react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useProfiles } from '@/hooks/use-profiles';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Profile } from '@/lib/api/types';
import { selectCloneProfile } from '@/lib/store/reference';
import { ReferencePanel } from './reference-panel';

const VIRTUALIZE_ABOVE = 30;
const VOICE_ROW_HEIGHT = 64;

export function voiceGridPresentation(count: number): 'grid' | 'virtual' {
  return count > VIRTUALIZE_ABOVE ? 'virtual' : 'grid';
}

export function VoiceSetup({ onChosen, onBack }: { onChosen: () => void; onBack: () => void }) {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const [query, setQuery] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const eligibleVoices = useMemo(
    () =>
      (profiles.data ?? []).filter((profile) => profile.kind === 'clone' && profile.ref_audio_path),
    [profiles.data],
  );
  const voices = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return normalized
      ? eligibleVoices.filter((profile) => profile.name.toLocaleLowerCase().includes(normalized))
      : eligibleVoices;
  }, [eligibleVoices, query]);
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
  }, [query]);

  const chooseVoice = async (profile: Profile) => {
    selectCloneProfile(profile);
    onChosen();
  };

  const voiceButton = (profile: Profile) => (
    <Button
      key={profile.id}
      variant="outline"
      className="h-14 min-w-0 justify-start gap-2"
      onClick={() => void chooseVoice(profile)}
    >
      <ProfileAvatar name={profile.name} imageUrl={profile.image_url} />
      <span className="truncate">{profile.name}</span>
    </Button>
  );

  return (
    <section className="flex w-full flex-col gap-6">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('preferences.back')}
          title={t('preferences.back')}
          onClick={onBack}
        >
          <ArrowLeftIcon />
        </Button>
        <h2 className="text-xl font-semibold tracking-tight">{t('cloneFlow.choose_voice')}</h2>
      </div>
      {eligibleVoices.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm text-muted-foreground">{t('clone.saved_profiles')}</h3>
          {eligibleVoices.length > VIRTUALIZE_ABOVE && (
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={t('common.search')}
                placeholder={t('common.search')}
                className="pl-8"
              />
            </div>
          )}
          <div ref={scrollRef} className="max-h-56 overflow-y-auto">
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
                    {voices.slice(row.index * 2, row.index * 2 + 2).map(voiceButton)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">{voices.map(voiceButton)}</div>
            )}
          </div>
        </div>
      )}
      {profiles.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('preferences.loading')}
        </p>
      )}
      <ReferencePanel setup />
    </section>
  );
}
