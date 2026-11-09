import { useTranslation } from 'react-i18next';
import { useEngines } from '@/hooks/use-engines';
import { setCloneSetting, useCloneSettings } from '@/lib/store/clone-settings';
// @ts-expect-error Shared JSX component has no declaration file.
import SearchableSelect from '@shared/components/SearchableSelect';

/** Built-in voices of engines that ship presets instead of cloning (e.g. Supertonic-3, #2938). */
export function PresetVoicePicker({ disabled = false }: { disabled?: boolean }) {
  const { t } = useTranslation();
  const { activeTts } = useEngines();
  const settings = useCloneSettings();
  const voices = activeTts?.preset_voices ?? [];
  if (!voices.length) return null;
  const value = voices.includes(settings.presetVoice)
    ? settings.presetVoice
    : (activeTts?.default_voice ?? voices[0]);
  return (
    <label className="flex items-center gap-1.5 px-2 text-xs">
      <span className="text-muted-foreground">{t('presetVoice.label')}</span>
      <SearchableSelect
        options={voices.map((voice) => ({ value: voice, label: voice }))}
        value={value}
        onChange={(voice: string) => setCloneSetting('presetVoice', voice)}
        ariaLabel={t('presetVoice.label')}
        size="sm"
        buttonClassName="rounded-md border border-border/60 bg-background px-1.5 py-1 text-xs"
        menuPortal
        disabled={disabled}
      />
    </label>
  );
}
