import { useTranslation } from 'react-i18next';
import { Trash2Icon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { SettingsRow } from './settings-layout';
import type { PronunciationEntry } from './pronunciation-io';

export function PronunciationEntryRow({
  entry,
  busy,
  onToggle,
  onRemove,
}: {
  entry: PronunciationEntry;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const scope = entry.scope || entry.language;
  return (
    <SettingsRow
      id={'pronunciation-' + entry.id}
      title={entry.term + ' → ' + (entry.replacement || '—')}
      description={
        t('pronunciation.type_' + entry.type) +
        ' · ' +
        (scope === '*' ? t('pronunciation.global') : scope)
      }
    >
      <Switch
        checked={entry.enabled}
        disabled={busy}
        aria-label={t('pronunciation.enable_entry', { term: entry.term })}
        onCheckedChange={onToggle}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={busy}
        aria-label={t('pronunciation.remove', { term: entry.term })}
        onClick={onRemove}
      >
        <Trash2Icon />
      </Button>
    </SettingsRow>
  );
}
