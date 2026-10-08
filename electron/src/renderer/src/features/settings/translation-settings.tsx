import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { LanguagesIcon } from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckIcon } from 'lucide-react';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { SettingsSection, SettingsRow } from './settings-layout';
import { SettingsActionError } from './settings-action-error';
import { toast } from 'sonner';
import {
  engineSelectionFeedback,
  type EngineSelectionResult,
} from '@/lib/engine-selection-feedback';
export interface TranslationEngine {
  id: string;
  display_name: string;
  category: 'online' | 'offline' | 'llm';
  installed: boolean;
  configured?: boolean;
  configured_via?: string | null;
  ready?: boolean;
  needs_key: boolean;
  pip_package: string | null;
  availability_reason?: string | null;
}
export function useTranslationEngines() {
  const status = useBackendStatus();
  return useQuery({
    enabled: isBackendReachable(status.stage),
    queryKey: ['translation-engines'],
    queryFn: () =>
      apiJson<{
        active: string;
        sandboxed: boolean;
        engines: TranslationEngine[];
      }>('/engines/translation'),
    staleTime: 30_000,
  });
}
export function TranslationSettings() {
  const { t } = useTranslation();
  const query = useTranslationEngines();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const act = async (id: string, install = false) => {
    setBusy(true);
    setFailed(null);
    try {
      const result = await apiJson<EngineSelectionResult>(
        install
          ? `/engines/translation/${encodeURIComponent(id)}/install`
          : '/engines/translation/select',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          ...(!install ? { body: JSON.stringify({ engine_id: id }) } : {}),
        },
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: ['translation-engines'] }),
        client.invalidateQueries({ queryKey: ['performance-profile'] }),
      ]);
      if (!install) {
        const feedback = engineSelectionFeedback(result, 'translation');
        toast.success(t(feedback.key, feedback.values));
      }
    } catch (error) {
      setFailed(describeError(error));
    } finally {
      setBusy(false);
    }
  };
  const ready = (engine: TranslationEngine) => engine.ready ?? engine.installed;
  const availableEngines = query.data?.engines.filter(ready);
  const unavailableEngines = query.data?.engines.filter((engine) => !ready(engine));
  const renderEngine = (engine: TranslationEngine) => (
    <SettingsRow
      key={engine.id}
      id={'translation-' + engine.id}
      title={engine.display_name}
      description={[
        t('modelMaintenance.' + engine.category),
        ready(engine) ? engine.configured_via : engine.availability_reason,
      ]
        .filter(Boolean)
        .join(' · ')}
    >
      {!engine.installed && !query.data?.sandboxed && engine.pip_package && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void act(engine.id, true)}
        >
          {t('modelMaintenance.install')}
        </Button>
      )}
      {engine.installed && engine.configured === false && engine.id === 'openai' && (
        <Link
          to="/settings/models/$family"
          params={{ family: 'llm' }}
          className="text-sm text-primary hover:underline"
        >
          {t('settings.llm_providers')}
        </Link>
      )}
      {engine.installed &&
        engine.configured === false &&
        ['deepl', 'microsoft'].includes(engine.id) && (
          <Link to="/settings/credentials" className="text-sm text-primary hover:underline">
            {t('settings.credentials')}
          </Link>
        )}
      {ready(engine) && (
        <Button
          size="sm"
          variant={query.data?.active === engine.id ? 'secondary' : 'outline'}
          disabled={busy || query.data?.active === engine.id}
          aria-pressed={query.data?.active === engine.id}
          onClick={() => void act(engine.id)}
        >
          {query.data?.active === engine.id && <CheckIcon />}
          {t(query.data?.active === engine.id ? 'modelSettings.selected' : 'modelSettings.select')}
        </Button>
      )}
    </SettingsRow>
  );
  return (
    <SettingsSection icon={LanguagesIcon} title={t('engineSidebar.translation')}>
      {query.isError ? (
        <SettingsActionError
          className="m-4"
          title={t('modelSettings.failed')}
          detail={describeError(query.error)}
          action={
            <Button size="xs" variant="ghost" onClick={() => void query.refetch()}>
              {t('backend.retry')}
            </Button>
          }
        />
      ) : query.isPending ? (
        <p role="status" className="p-4 text-sm">
          {t('preferences.loading')}
        </p>
      ) : (
        <>
          {availableEngines?.map(renderEngine)}
          {unavailableEngines && unavailableEngines.length > 0 && (
            <div>
              <div className="flex items-center gap-2 px-4 py-3 text-sm font-medium text-muted-foreground">
                {t('modelSettings.unavailable')}
                <span className="text-xs tabular-nums">{unavailableEngines.length}</span>
              </div>
              <div className="border-t border-border/50 [&>*+*]:border-t [&>*+*]:border-border/50">
                {unavailableEngines.map(renderEngine)}
              </div>
            </div>
          )}
        </>
      )}
      {failed && (
        <SettingsActionError
          className="m-4"
          title={t('modelSettings.failed')}
          detail={failed}
          onDismiss={() => setFailed(null)}
        />
      )}
    </SettingsSection>
  );
}
