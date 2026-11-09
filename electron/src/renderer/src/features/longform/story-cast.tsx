import { useTranslation } from 'react-i18next';
import { FingerprintIcon, PlusIcon, TrashIcon, UsersIcon } from 'lucide-react';
import { ChipPopover, ChoiceList } from './story-chips';
import { SettingsSection } from './settings-section';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Draft } from './longform-session';

interface Props {
  draft: Draft;
  profiles: { id: string; name: string }[];
  disabled: boolean;
  onChange: (patch: Partial<Draft>) => void;
}

export function StoryCast({ draft, profiles, disabled, onChange }: Props) {
  const { t } = useTranslation();
  return (
    <SettingsSection
      title={t('stories.cast')}
      icon={UsersIcon}
      meta={draft.cast.length || undefined}
    >
      {!draft.cast.length && (
        <p className="text-xs text-muted-foreground">{t('stories.castEmpty')}</p>
      )}
      {draft.cast.map((character) => {
        const voiceName = character.profileId
          ? (profiles.find((p) => p.id === character.profileId)?.name ??
            t('modelSettings.unavailable'))
          : t('stories.defaultVoice');
        return (
          <div key={character.id} data-slot="cast-member" className="flex items-center gap-1">
            <Input
              aria-label={t('stories.characterName')}
              className="h-8 min-w-0 flex-1"
              value={character.name}
              disabled={disabled}
              onChange={(e) =>
                onChange({
                  cast: draft.cast.map((c) =>
                    c.id === character.id ? { ...c, name: e.target.value } : c,
                  ),
                })
              }
            />
            <ChipPopover
              label={t('stories.voiceChip', { name: voiceName })}
              icon={FingerprintIcon}
              tone={character.profileId ? 'default' : 'muted'}
              disabled={disabled}
              className="h-8 max-w-32 px-2"
              content={(close) => (
                <ChoiceList
                  label={t('stories.voiceChip', { name: character.name })}
                  options={[{ id: null, name: t('stories.defaultVoice') }, ...profiles]}
                  value={character.profileId}
                  disabled={disabled}
                  onSelect={(profileId) => {
                    onChange({
                      cast: draft.cast.map((c) =>
                        c.id === character.id ? { ...c, profileId } : c,
                      ),
                    });
                    close();
                  }}
                />
              )}
            >
              {voiceName}
            </ChipPopover>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t('stories.removeCharacter')}
              title={t('stories.removeCharacter')}
              disabled={disabled}
              onClick={() =>
                onChange({
                  cast: draft.cast.filter((c) => c.id !== character.id),
                  lines: draft.lines.map((line) =>
                    line.character === character.id ? { ...line, character: undefined } : line,
                  ),
                })
              }
            >
              <TrashIcon />
            </Button>
          </div>
        );
      })}
      <Button
        variant="ghost"
        size="sm"
        disabled={disabled}
        onClick={() =>
          onChange({
            cast: [
              ...draft.cast,
              {
                id: crypto.randomUUID(),
                name: t('stories.character'),
                profileId: null,
              },
            ],
          })
        }
      >
        <PlusIcon />
        {t('stories.addCharacter')}
      </Button>
    </SettingsSection>
  );
}
