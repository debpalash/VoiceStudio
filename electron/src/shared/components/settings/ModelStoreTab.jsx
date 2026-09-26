import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { makeModelColumns } from './models/columns';
import { FAMILY_SECTIONS, groupModels, modelSectionKey } from './models/sections';
import useModelDownloads from './models/useModelDownloads';
import ModelSection from './models/ModelSection';

/** Catalog rows no engine claims (models.yaml `engines:` empty or absent). */
export function unownedModels(models) {
  return (models || []).filter((m) => !Array.isArray(m.engines) || m.engines.length === 0);
}

/**
 * Model store — the downloadable weights that belong to no single engine
 * (pipeline weights such as speaker diarisation), grouped by capability with
 * install / reinstall / delete per row and platform-incompatible rows behind
 * a per-section toggle. Everything an engine owns is listed under that
 * engine in the catalogue's detail panel instead (EngineWeights); with
 * `family` set this renders only that family's leftovers, with `family`
 * null every unowned row. Renders nothing at all when there is nothing to
 * show, so hosts can mount it unconditionally.
 */
export default function ModelStoreTab({ family = null, title = null, ownedToo = false }) {
  const { t } = useTranslation();
  const downloads = useModelDownloads();
  const { data, loading, speedRef, getRowRuntime, getResidency } = downloads;

  const MODEL_ROLE_LABEL = useMemo(
    () => ({
      all: t('models.role_all'),
      tts: t('models.role_tts'),
      asr: t('models.role_asr'),
      diarisation: t('models.role_diarisation'),
      diarization: t('models.role_diarisation'),
      llm: t('models.role_llm'),
      other: t('models.role_other'),
    }),
    [t],
  );
  const MODEL_SECTION_LABEL = useMemo(
    () => ({
      tts: t('models.section_tts'),
      asr: t('models.section_asr'),
      dictation: t('models.section_dictation'),
      diarisation: t('models.section_diarisation'),
      other: t('models.section_other'),
    }),
    [t],
  );

  const rows = useMemo(() => {
    const models = ownedToo ? data?.models || [] : unownedModels(data?.models);
    const keep = family ? FAMILY_SECTIONS[family] || [] : null;
    return keep ? models.filter((m) => keep.includes(modelSectionKey(m))) : models;
  }, [data, family, ownedToo]);
  const sections = useMemo(() => groupModels(rows, ''), [rows]);

  const columns = useMemo(
    () =>
      makeModelColumns({
        t,
        getRowRuntime,
        speedRef,
        MODEL_ROLE_LABEL,
        onInstall: downloads.onInstall,
        onDelete: downloads.onDelete,
        onReinstall: downloads.onReinstall,
        onCancel: downloads.onCancel,
        onDismissError: downloads.onDismissError,
        getResidency,
        onUnload: downloads.onUnload,
      }),
    [t, getRowRuntime, speedRef, MODEL_ROLE_LABEL, downloads, getResidency],
  );

  if (loading && !data) {
    return (
      <div className="px-[2px] py-[24px] font-sans text-sm text-muted-foreground">
        {t('common.loading')}
      </div>
    );
  }
  if (!data || sections.length === 0) return null;

  return (
    <section className="flex min-h-0 flex-col font-sans" data-testid="model-list-panel">
      {title && (
        <h2 className="m-0 mb-[14px] px-[2px] text-[length:var(--text-lg)] font-semibold text-foreground">
          {title}
        </h2>
      )}
      <div className="min-h-0 flex-1" data-testid="model-list-area">
        {sections.map((group) => (
          <ModelSection
            key={group.key}
            sectionKey={group.key}
            title={MODEL_SECTION_LABEL[group.key] || group.key}
            group={group}
            columns={columns}
            getRowRuntime={getRowRuntime}
            t={t}
          />
        ))}
      </div>
    </section>
  );
}
