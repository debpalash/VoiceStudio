import { useTranslation } from 'react-i18next';
import { Switch } from '@/components/ui/switch';
import type { TranslationEngine } from '@/features/settings/translation-settings';
import { setDubTranslationOptions, type DubSession } from './dub-session';

type ToggleKey = 'autoGlossary' | 'reflectPass' | 'condenseSuggest';
interface ToggleRow {
  key: ToggleKey;
  label: string;
  help: string;
  fallback: boolean;
}

const LLM_ROWS: ToggleRow[] = [
  {
    key: 'autoGlossary',
    label: 'dub.auto_glossary_label',
    help: 'dub.auto_glossary_title',
    fallback: true,
  },
  { key: 'reflectPass', label: 'dub.reflect_label', help: 'dub.reflect_title', fallback: true },
];
const CONDENSE: ToggleRow = {
  key: 'condenseSuggest',
  label: 'dub.condense_label',
  help: 'dub.condense_title',
  fallback: false,
};

/** Engine translation refinements: one compact switch row each, help on one line. */
export function DubTranslationToggles({
  session,
  locked,
  provider,
}: {
  session: DubSession;
  locked: boolean;
  provider?: TranslationEngine;
}) {
  const { t } = useTranslation();
  const rows = provider?.id === 'openai' ? [...LLM_ROWS, CONDENSE] : [CONDENSE];
  return (
    <div className="space-y-1.5 pt-1">
      {rows.map((row) => (
        <label key={row.key} className="flex items-center justify-between gap-3 text-xs">
          <span className="min-w-0">
            <span className="block font-medium">{t(row.label)}</span>
            <span className="line-clamp-1 text-muted-foreground" title={t(row.help)}>
              {t(row.help)}
            </span>
          </span>
          <Switch
            checked={session[row.key] ?? row.fallback}
            disabled={locked}
            onCheckedChange={(checked) => setDubTranslationOptions({ [row.key]: checked })}
          />
        </label>
      ))}
    </div>
  );
}
