import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { apiJson, describeError } from '@/lib/api/client';
import { useModelCatalogue } from '../model-catalogue-query';
import { familyIcons } from '../model-family';
import { SettingsRow } from '../settings-layout';
import { SettingsActionError } from '../settings-action-error';
import { familyModels, unclaimedModels } from './linking';
import { useModelOps } from './model-ops';
import { SelectButton, modelFamilyItem } from './model-line';
import { ActiveBadge, StatusBadge } from './badges';
import { FamilyList } from './family-list';
import type { FamilyItem } from './family-row';
import { useSelection } from './use-engine-select';

interface DictationModel {
  id: string;
  label: string;
  installed: boolean;
  repo_id: string;
}

const DICTATION_KEYS = [
  'settings-dictation',
  'sidebar-dictation',
  'model-library-dictation-prefs',
  'dictation-shortcut-prefs',
  'transcription-readiness',
  'performance-profile',
  'loaded-models',
  'engines',
  'sidebar-model-status',
] as const;

/** Settings -> Models -> Dictation: the on/off switch plus one model list. */
export function DictationList() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const catalogue = useModelCatalogue();
  const ops = useModelOps();
  const selection = useSelection('dictation', DICTATION_KEYS);
  const [toggling, setToggling] = useState(false);
  const [toggleFailed, setToggleFailed] = useState<string | null>(null);
  const dictation = useQuery({
    queryKey: ['settings-dictation'],
    queryFn: async () => {
      const [prefs, list] = await Promise.all([
        apiJson<{ model_id: string; enabled: boolean }>('/dictation/prefs'),
        apiJson<{ engine_available: boolean; models: DictationModel[] }>('/dictation/models'),
      ]);
      return { ...prefs, ...list };
    },
  });
  const data = dictation.data;
  const select = (id: string) =>
    selection.run(async () => {
      await apiJson('/dictation/prefs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: id, enabled: true }),
      });
      return { active: id };
    });
  const setEnabled = async (enabled: boolean) => {
    setToggling(true);
    setToggleFailed(null);
    try {
      await apiJson('/dictation/prefs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      await Promise.all(DICTATION_KEYS.map((key) => client.invalidateQueries({ queryKey: [key] })));
    } catch (error) {
      setToggleFailed(describeError(error));
    } finally {
      setToggling(false);
    }
  };

  const models = familyModels(catalogue.data?.models, 'dictation');
  const selectFor = (id: string, installed: boolean, active: boolean) =>
    installed && data?.engine_available ? (
      <SelectButton active={active} disabled={selection.busy} onSelect={() => void select(id)} />
    ) : undefined;
  let items: FamilyItem[] | undefined;
  if (data) {
    const claimed = models.filter((model) =>
      data.models.some(
        (entry) => entry.id === model.dictation_id || entry.repo_id === model.repo_id,
      ),
    );
    items = [
      ...data.models.map((entry): FamilyItem => {
        const active = data.model_id === entry.id;
        const model = claimed.find(
          (item) => item.dictation_id === entry.id || item.repo_id === entry.repo_id,
        );
        const installed = model?.installed ?? entry.installed;
        if (model) {
          const item = modelFamilyItem({
            model,
            ops,
            active,
            select: selectFor(entry.id, installed, active),
            summary: entry.repo_id,
          });
          return {
            ...item,
            key: 'dictation:' + entry.id,
            name: entry.label,
            badges: (
              <>
                {item.badges}
                {!data.engine_available && (
                  <StatusBadge tone="muted">{t('modelSettings.unavailable')}</StatusBadge>
                )}
              </>
            ),
          };
        }
        return {
          key: 'dictation:' + entry.id,
          anchorId: 'engine-' + entry.id,
          name: entry.label,
          summary: entry.repo_id,
          badges: active ? <ActiveBadge /> : undefined,
          actions: selectFor(entry.id, installed, active) ?? (
            <StatusBadge tone="muted">{t('modelSettings.unavailable')}</StatusBadge>
          ),
          search: [entry.label, entry.id, entry.repo_id],
          rank: { active, ready: installed, recommended: false },
        };
      }),
      ...unclaimedModels(models, claimed).map((model) => {
        const active = data.model_id === model.dictation_id;
        return modelFamilyItem({
          model,
          ops,
          active,
          select: model.dictation_id
            ? selectFor(model.dictation_id, model.installed, active)
            : undefined,
        });
      }),
    ];
  }
  const canEnable =
    Boolean(data?.engine_available) &&
    Boolean(data?.models.some((model) => model.id === data.model_id && model.installed));
  return (
    <>
      <FamilyList
        title={t('engineSidebar.dictation')}
        icon={familyIcons.dictation}
        items={items}
        header={
          <SettingsRow id="dictation-enabled" title={t('modelSettings.dictation_enabled')}>
            <Switch
              aria-labelledby="dictation-enabled"
              checked={Boolean(data?.enabled)}
              disabled={toggling || !data || (!data.enabled && !canEnable)}
              onCheckedChange={(value) => void setEnabled(value)}
            />
          </SettingsRow>
        }
        error={
          dictation.isError ? (
            <SettingsActionError
              className="m-4"
              title={t('modelSettings.failed')}
              detail={describeError(dictation.error)}
              action={
                <Button size="xs" variant="ghost" onClick={() => void dictation.refetch()}>
                  {t('backend.retry')}
                </Button>
              }
            />
          ) : undefined
        }
        footer={
          (selection.failed || toggleFailed) && (
            <SettingsActionError
              className="m-4"
              title={t('modelSettings.failed')}
              detail={selection.failed || toggleFailed || ''}
              onDismiss={() => {
                selection.clearFailure();
                setToggleFailed(null);
              }}
            />
          )
        }
      />
      {ops.confirmDialog}
    </>
  );
}
