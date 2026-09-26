import { useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { apiJson } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { SettingsSection, SettingsRow } from './settings-layout';
import { refineFailureNoteKey } from '@shared/components/settings/refineStatus';
const flags = ['auto', 'smart_cleanup', 'self_correction', 'preserve_technical'] as const;
type Config = Record<(typeof flags)[number], boolean> & {
  llm_ready: boolean;
  last_refine_status?: { ok?: boolean; reason?: string };
};
export function RefinementSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const query = useQuery({
    queryKey: ['dictation-refinement'],
    queryFn: ({ signal }) => apiJson<Config>('/api/settings/dictation-refinement', { signal }),
  });
  const update = async (key: (typeof flags)[number], value: boolean) => {
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      const config = await apiJson<Config>('/api/settings/dictation-refinement', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
      client.setQueryData(['dictation-refinement'], config);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const note = refineFailureNoteKey(query.data?.last_refine_status);
  return (
    <SettingsSection title={t('settings.llmskills_dictation_refinement_name')}>
      {query.isPending && (
        <p className="p-4" role="status">
          {t('common.loading')}
        </p>
      )}
      {query.isError && (
        <Button variant="outline" onClick={() => void query.refetch()}>
          {t('backend.retry')}
        </Button>
      )}
      {error && (
        <p role="alert" className="p-4 text-sm text-destructive">
          {t('common.error')}
        </p>
      )}
      {note && (
        <p role="status" className="p-4 text-sm text-muted-foreground">
          {t(note)}
        </p>
      )}
      {query.data && !query.data.llm_ready && (
        <p className="p-4 text-sm text-muted-foreground">{t('settings.llmskills_needs_setup')}</p>
      )}
      <div className="px-4 py-2">
        <Link
          to="/settings/models/$family"
          params={{ family: 'llm' }}
          className="text-sm text-primary hover:underline"
        >
          {t('settings.llm_providers')}
        </Link>
      </div>
      {query.data &&
        flags.map((key) => (
          <SettingsRow
            key={key}
            id={'refine-' + key}
            title={t(
              key === 'auto'
                ? 'settings.llmskills_dictation_refinement_name'
                : 'dictation.flag_' + key,
            )}
            description={
              key === 'auto' ? t('settings.llmskills_dictation_refinement_desc') : undefined
            }
          >
            <Switch
              aria-label={t(
                key === 'auto'
                  ? 'settings.llmskills_dictation_refinement_name'
                  : 'dictation.flag_' + key,
              )}
              checked={query.data[key]}
              disabled={busy || (key !== 'auto' && !query.data.auto)}
              onCheckedChange={(value) => void update(key, value)}
            />
          </SettingsRow>
        ))}
    </SettingsSection>
  );
}
