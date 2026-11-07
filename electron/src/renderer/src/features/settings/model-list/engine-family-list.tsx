import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Button } from '@/components/ui/button';
import { engineFamilyState, useEngines } from '@/hooks/use-engines';
import { describeError } from '@/lib/api/client';
import type { EngineBackend, EngineFamilyState } from '@/lib/api/types';
import { EngineInstall } from '../engine-install';
import { AUDIOCPP_TTS_ENGINES, AudioCppRuntimeInstall } from '../audiocpp-runtime-install';
import { EngineLicense, licensedEngine } from '../engine-license';
import { EngineHealth } from '../engine-health';
import { ModelLicense } from '../model-license';
import { useModelCatalogue, type CatalogueModel } from '../model-catalogue-query';
import { familyIcons } from '../model-family';
import { SettingsActionError } from '../settings-action-error';
import {
  FASTER_WHISPER_ENGINES,
  familyModels,
  modelSearchText,
  modelsForEngine,
  selectableAsrModel,
  unclaimedModels,
} from './linking';
import { useModelOps, type ModelOps } from './model-ops';
import { describeJob } from './model-job';
import { ModelActions } from './model-actions';
import { ModelFailure, ModelGatedAccess } from './model-notices';
import { ModelLine, SelectButton, modelFamilyItem } from './model-line';
import { DeviceChip, RecommendedBadge, StatusBadge, ActiveBadge } from './badges';
import { EngineDetails, ModelCount } from './engine-details';
import { FamilyList } from './family-list';
import type { FamilyItem } from './family-row';
import { useEngineSelect } from './use-engine-select';
import { GGUF_ENGINE, GgufQuantPicker } from './gguf-quant-picker';
import { asrSetupHint, engineUnavailableReason } from './engine-guidance';
import { LocalModelPath } from './local-model-path';

type Family = 'tts' | 'asr';

interface Context {
  family: Family;
  state: EngineFamilyState;
  models: CatalogueModel[];
  ops: ModelOps;
  select: ReturnType<typeof useEngineSelect>;
  t: TFunction;
}

/** The model id `/engines/select` accepts for this engine, if it takes one. */
function modelChoice(ctx: Context, engine: EngineBackend, model: CatalogueModel) {
  const curated = engine.curated_models?.find((choice) => choice.repo_id === model.repo_id);
  if (curated) return { id: curated.key, keys: [curated.key, curated.repo_id] };
  if (
    ctx.family === 'asr' &&
    FASTER_WHISPER_ENGINES.has(engine.id) &&
    selectableAsrModel(model.repo_id)
  )
    return { id: model.repo_id, keys: [model.repo_id] };
  return null;
}

function engineSummary(ctx: Context, engine: EngineBackend, active: boolean) {
  if (engine.local_install_required && !engine.available)
    return ctx.t('engines.localInstallRequired');
  if (!engine.available)
    return (
      engineUnavailableReason(engine.reason, ctx.t) ||
      (ctx.family === 'asr' ? asrSetupHint(engine, ctx.t) : engine.install_hint) ||
      engine.hint ||
      undefined
    );
  if (active && ctx.state.active_model) {
    const model = ctx.models.find(
      (item) =>
        item.repo_id === ctx.state.active_model ||
        engine.curated_models?.some(
          (choice) => choice.key === ctx.state.active_model && choice.repo_id === item.repo_id,
        ),
    );
    return model?.label ?? ctx.state.active_model;
  }
  return engine.hint || undefined;
}

function engineItem(ctx: Context, engine: EngineBackend): FamilyItem {
  const { t, ops, select } = ctx;
  const active = ctx.state.active === engine.id;
  const linked = modelsForEngine(engine.id, ctx.models, engine.curated_models);
  const single = linked.length === 1 ? linked[0] : undefined;
  const licenseId = licensedEngine(engine.id, engine.license_required, engine.license_accepted);
  const engineSelect = engine.available ? (
    <SelectButton
      active={active}
      disabled={select.busy}
      onSelect={() => void select.select(engine.id)}
    />
  ) : null;
  const setup: ReactNode = engine.available ? null : licenseId ? (
    <EngineLicense id={licenseId} name={engine.display_name} />
  ) : engine.one_click_install ? (
    <EngineInstall id={engine.id} />
  ) : AUDIOCPP_TTS_ENGINES.has(engine.id) ? (
    <AudioCppRuntimeInstall />
  ) : null;
  const singleInfo = single ? describeJob(single, ops) : undefined;
  const fallbackNote =
    engine.available && engine.routing_status === 'cpu_fallback' ? engine.routing_reason : null;
  const setupHint =
    !engine.local_install_required && !engine.setup_snippet && !engine.docs_url
      ? ctx.family === 'asr'
        ? asrSetupHint(engine, t)
        : engine.install_hint
      : undefined;
  const details = (
    <EngineDetails
      detail={engine.available ? fallbackNote : setupHint || engineSummary(ctx, engine, active)}
      snippet={engine.available ? null : engine.setup_snippet}
      docsUrl={engine.docs_url}
      tools={
        <>
          {engine.id === GGUF_ENGINE && engine.available && <GgufQuantPicker />}
          <LocalModelPath family={ctx.family} engineId={engine.id} />
          <EngineHealth
            id={engine.id}
            available={engine.available}
            isolationMode={engine.isolation_mode}
          />
        </>
      }
    />
  );
  const hosted =
    linked.length > 1
      ? linked.map((model) => {
          const choice = modelChoice(ctx, engine, model);
          const modelActive =
            active && Boolean(choice?.keys.includes(ctx.state.active_model ?? ''));
          return (
            <ModelLine
              key={model.repo_id}
              model={model}
              ops={ops}
              active={modelActive}
              anchorId={`engine-${engine.id}-model-${model.repo_id.replaceAll('/', '-')}`}
              select={
                choice && engine.available ? (
                  <SelectButton
                    active={modelActive}
                    disabled={select.busy}
                    onSelect={() => void select.select(engine.id, choice.id)}
                  />
                ) : undefined
              }
            />
          );
        })
      : null;
  return {
    key: 'engine:' + engine.id,
    anchorId: 'engine-' + engine.id,
    name: engine.display_name,
    summary: engineSummary(ctx, engine, active),
    badges: (
      <>
        {active && <ActiveBadge />}
        {linked.some((model) => model.curated) && <RecommendedBadge />}
        {single && (
          <ModelLicense
            repoId={single.repo_id}
            label={single.label}
            info={single.license_info}
            acceptance={single.license_acceptance}
          />
        )}
        {!engine.available && (
          <StatusBadge tone="muted" title={engineUnavailableReason(engine.reason, t) || undefined}>
            {t('modelSettings.unavailable')}
          </StatusBadge>
        )}
      </>
    ),
    meta: (
      <>
        {linked.length > 1 && (
          <ModelCount
            installed={linked.filter((model) => model.installed).length}
            total={linked.length}
          />
        )}
        <DeviceChip
          device={engine.effective_device}
          title={engine.routing_reason || engine.routing_status}
        />
      </>
    ),
    actions: (
      <>
        {setup}
        {single && singleInfo ? (
          <>
            <ModelActions
              model={single}
              ops={ops}
              info={singleInfo}
              select={single.installed ? engineSelect : undefined}
            />
            {!single.installed && engineSelect}
          </>
        ) : (
          engineSelect
        )}
      </>
    ),
    notices:
      single && singleInfo && ((single.gated && !single.installed) || singleInfo.failed) ? (
        <>
          <ModelGatedAccess model={single} ops={ops} info={singleInfo} />
          <ModelFailure model={single} ops={ops} info={singleInfo} />
        </>
      ) : undefined,
    body: (
      <>
        {hosted}
        {details}
      </>
    ),
    defaultOpen: active && linked.length > 1,
    search: [engine.display_name, engine.id, engine.hint, engine.reason],
    childSearch: linked.flatMap(modelSearchText),
    rank: {
      active,
      ready: engine.available,
      recommended: linked.some((model) => model.curated || model.required),
    },
    folded:
      !engine.available &&
      !engine.one_click_install &&
      !licenseId &&
      linked.length > 0 &&
      linked.every((model) => model.supported === false),
  };
}

function standaloneItem(ctx: Context, model: CatalogueModel): FamilyItem {
  const activeBackend = ctx.state.active;
  const selectable = ctx.family === 'asr' && selectableAsrModel(model.repo_id);
  const active = ctx.state.active_model === model.repo_id;
  return modelFamilyItem({
    model,
    ops: ctx.ops,
    active,
    select: selectable ? (
      <SelectButton
        active={active}
        disabled={ctx.select.busy}
        onSelect={() =>
          void ctx.select.select(
            activeBackend === 'faster-whisper-isolated' ? activeBackend : 'faster-whisper',
            model.repo_id,
          )
        }
      />
    ) : undefined,
  });
}

/** Settings -> Models -> TTS / ASR: engines with their models, in one list. */
export function EngineFamilyList({ family, footer }: { family: Family; footer?: ReactNode }) {
  const { t } = useTranslation();
  const engines = useEngines();
  const catalogue = useModelCatalogue();
  const ops = useModelOps();
  const select = useEngineSelect(family);
  // A payload without this family lists no engines; its models still show.
  const state =
    engineFamilyState(engines.data, family) ??
    (engines.data ? { active: null, backends: [] } : undefined);
  const models = familyModels(catalogue.data?.models, family);
  let items: FamilyItem[] | undefined;
  if (state) {
    const ctx: Context = { family, state, models, ops, select, t };
    const claimed = state.backends.flatMap((engine) =>
      modelsForEngine(engine.id, models, engine.curated_models),
    );
    items = [
      ...state.backends.map((engine) => engineItem(ctx, engine)),
      ...unclaimedModels(models, claimed).map((model) => standaloneItem(ctx, model)),
    ];
  }
  return (
    <>
      <FamilyList
        title={t('engineSidebar.' + family)}
        icon={familyIcons[family]}
        items={items}
        error={
          engines.isError ? (
            <SettingsActionError
              className="m-4"
              title={t('modelSettings.failed')}
              detail={describeError(engines.error)}
              action={
                <Button size="xs" variant="ghost" onClick={() => engines.retry()}>
                  {t('backend.retry')}
                </Button>
              }
            />
          ) : undefined
        }
        footer={
          <>
            {select.failed && (
              <SettingsActionError
                className="m-4"
                title={t('modelSettings.failed')}
                detail={select.failed}
                onDismiss={select.clearFailure}
              />
            )}
            {ops.failed && (
              <p role="alert" className="px-4 py-3 text-sm text-destructive">
                {t('modelMaintenance.failed')}
              </p>
            )}
            {footer}
          </>
        }
      />
      {ops.confirmDialog}
    </>
  );
}
