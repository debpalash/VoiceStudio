import { useBackendStatus } from '@/hooks/use-backend-status';
import { isBackendReachable } from '@shared/utils/backendStage';
import { DownloadIcon, LanguagesIcon } from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiJson, describeError } from '@/lib/api/client';
import { Button, buttonVariants } from '@/components/ui/button';
import { SettingsActionError } from './settings-action-error';
import type { EngineSelectionResult } from '@/lib/engine-selection-feedback';
import { useModelCatalogue } from './model-catalogue-query';
import { ModelLicense } from './model-license';
import {
  familyModels,
  modelSearchText,
  modelsForEngine,
  unclaimedModels,
} from './model-list/linking';
import { useModelOps } from './model-list/model-ops';
import { describeJob } from './model-list/model-job';
import { ModelActions } from './model-list/model-actions';
import { ModelFailure, ModelGatedAccess } from './model-list/model-notices';
import { SelectButton, modelFamilyItem } from './model-list/model-line';
import { ActiveBadge, RecommendedBadge, StatusBadge } from './model-list/badges';
import { FamilyList } from './model-list/family-list';
import type { FamilyItem } from './model-list/family-row';
import { useSelection } from './model-list/use-engine-select';
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
  const catalogue = useModelCatalogue();
  const ops = useModelOps();
  const client = useQueryClient();
  const selection = useSelection('translation', ['translation-engines', 'performance-profile']);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installFailed, setInstallFailed] = useState<string | null>(null);
  const install = async (id: string) => {
    setInstalling(id);
    setInstallFailed(null);
    try {
      await apiJson(`/engines/translation/${encodeURIComponent(id)}/install`, { method: 'POST' });
      await Promise.all(
        ['translation-engines', 'performance-profile'].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    } catch (error) {
      setInstallFailed(describeError(error));
    } finally {
      setInstalling(null);
    }
  };
  const select = (id: string) =>
    selection.run(() =>
      apiJson<EngineSelectionResult>('/engines/translation/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ engine_id: id }),
      }),
    );
  const ready = (engine: TranslationEngine) => engine.ready ?? engine.installed;
  const models = familyModels(catalogue.data?.models, 'translation');
  const busy = selection.busy || installing !== null;
  const engineItem = (engine: TranslationEngine): FamilyItem => {
    const active = query.data?.active === engine.id;
    const linked = modelsForEngine(engine.id, models);
    const model = linked[0];
    const info = model ? describeJob(model, ops) : undefined;
    const canInstall = !engine.installed && !query.data?.sandboxed && Boolean(engine.pip_package);
    const configure =
      engine.installed && engine.configured === false
        ? engine.id === 'openai'
          ? 'llm'
          : ['deepl', 'microsoft'].includes(engine.id)
            ? 'credentials'
            : null
        : null;
    const selectButton = ready(engine) ? (
      <SelectButton active={active} disabled={busy} onSelect={() => void select(engine.id)} />
    ) : null;
    return {
      key: 'translation:' + engine.id,
      anchorId: 'translation-' + engine.id,
      name: engine.display_name,
      summary: [
        t('modelMaintenance.' + engine.category),
        ready(engine) ? engine.configured_via : engine.availability_reason,
      ]
        .filter(Boolean)
        .join(' · '),
      badges: (
        <>
          {active && <ActiveBadge />}
          {model?.curated && <RecommendedBadge />}
          {model && (
            <ModelLicense
              repoId={model.repo_id}
              label={model.label}
              info={model.license_info}
              acceptance={model.license_acceptance}
            />
          )}
          {!ready(engine) && (
            <StatusBadge tone="muted">{t('modelSettings.unavailable')}</StatusBadge>
          )}
        </>
      ),
      actions: (
        <>
          {canInstall && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void install(engine.id)}
            >
              <DownloadIcon />
              {t(
                installing === engine.id
                  ? 'modelMaintenance.installing'
                  : 'modelMaintenance.install',
              )}
            </Button>
          )}
          {configure === 'llm' && (
            <Link
              to="/settings/models/$family"
              params={{ family: 'llm' }}
              className={buttonVariants({ variant: 'outline', size: 'sm' })}
            >
              {t('settings.llm_providers')}
            </Link>
          )}
          {configure === 'credentials' && (
            <Link
              to="/settings/credentials"
              className={buttonVariants({ variant: 'outline', size: 'sm' })}
            >
              {t('settings.credentials')}
            </Link>
          )}
          {model && info ? (
            <>
              <ModelActions
                model={model}
                ops={ops}
                info={info}
                select={model.installed ? selectButton : undefined}
              />
              {!model.installed && selectButton}
            </>
          ) : (
            selectButton
          )}
        </>
      ),
      notices:
        model && info && ((model.gated && !model.installed) || info.failed) ? (
          <>
            <ModelGatedAccess model={model} ops={ops} info={info} />
            <ModelFailure model={model} ops={ops} info={info} />
          </>
        ) : undefined,
      search: [engine.display_name, engine.id, engine.availability_reason],
      childSearch: linked.flatMap(modelSearchText),
      rank: { active, ready: ready(engine), recommended: Boolean(model?.curated) },
    };
  };
  const engines = query.data?.engines;
  const items = engines && [
    ...engines.map(engineItem),
    ...unclaimedModels(
      models,
      engines.flatMap((engine) => modelsForEngine(engine.id, models)),
    ).map((model) => modelFamilyItem({ model, ops })),
  ];
  const failure = selection.failed || installFailed;
  return (
    <>
      <FamilyList
        title={t('engineSidebar.translation')}
        icon={LanguagesIcon}
        items={items}
        error={
          query.isError ? (
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
          ) : undefined
        }
        footer={
          failure && (
            <SettingsActionError
              className="m-4"
              title={t('modelSettings.failed')}
              detail={failure}
              onDismiss={() => {
                selection.clearFailure();
                setInstallFailed(null);
              }}
            />
          )
        }
      />
      {ops.confirmDialog}
    </>
  );
}
