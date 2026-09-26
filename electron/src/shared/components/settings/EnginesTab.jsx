import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { addBreadcrumb } from '../../utils/breadcrumbs';
import { useEngines, useSelectEngine } from '../../api/hooks';
import { notifyEngineSelected } from '../../utils/engineSelectToast';
import EngineCompatibilityMatrix from '../EngineCompatibilityMatrix';
import useModelDownloads from './models/useModelDownloads';
import AsrOpenAICompatPanel from './AsrOpenAICompatPanel';
import { SETTINGS_SECTION_SURFACE } from './primitives';

/** Model Catalogue: ONE section, one matrix, a TTS / ASR / LLM tab strip.
 *
 *  The page used to stack three pinned per-family matrices; with every row
 *  free to grow (wrapping names, stacked badges, inline failure prose) a
 *  single engine could fill a viewport and the ASR/LLM pickers lived below
 *  the fold. The matrix's family tab strip (Radix Segmented — roving
 *  tabindex + arrow keys, active engine named in each tab caption) now
 *  presents one family at a time instead, over compact fixed-height rows.
 *
 *  The mounted matrix reads the app-wide `/engines` cache and issues one
 *  `/model/loaded` probe. Switching tabs only re-slices that shared payload;
 *  selection and installs invalidate it for every consumer.
 *
 *  The ASR tab additionally mounts the OpenAI-compatible remote ASR config
 *  panel below the matrix — configure server URL / model / key, test the
 *  connection, then activate with the engine's own "Use" button. Saving in
 *  the panel bumps `configVersion`, which refetches the matrix so the
 *  engine's row flips unavailable → available without a manual Refresh. */
export default function EnginesTab({
  initialFamily = 'tts',
  onFamilyChange = null,
  catalogueLayout = false,
}) {
  const { t } = useTranslation();
  const [family, setFamily] = useState(initialFamily);
  const [configVersion, setConfigVersion] = useState(0);
  const enginesQuery = useEngines();
  // Catalog rows + install/remove flow for the per-engine weights list in the
  // detail panel. Residency is the matrix's own probe, so it is skipped here.
  const downloads = useModelDownloads({ residency: false });
  const selectMutation = useSelectEngine();
  const onAsrConfigSaved = useCallback(() => setConfigVersion((v) => v + 1), []);
  useEffect(() => setFamily(initialFamily), [initialFamily]);

  // Plan 02-04 / ENGINE-06 — engine selection is wired through the
  // matrix component's optional onSelect callback so the matrix doubles
  // as a picker. Keeps a single source of truth for the engine list +
  // its install / GPU / isolation state.
  //
  // Review mode (the staged-checkpoint nudges) moved to Settings → General.
  const onSelect = useCallback(
    // modelId is only ever set by mlx-audio's curated-model picker (#981) —
    // every other call site (the "Use" button) omits it.
    async (family, backendId, modelId) => {
      try {
        addBreadcrumb(`engine:${family}=${backendId}`);
        const r = await selectMutation.mutateAsync({ family, backendId, modelId });
        // Consume the routing echo: warn (not a bare success) when the pick
        // lands on a CPU fallback on this host. See notifyEngineSelected.
        notifyEngineSelected(r, t, family);
      } catch (e) {
        toast.error(e.message || t('engines.switch_failed'));
      }
    },
    [selectMutation, t],
  );

  return (
    <div
      className={
        catalogueLayout ? 'flex min-h-0 flex-col gap-[32px]' : 'flex h-full min-h-0 flex-col'
      }
    >
      <section
        className={
          catalogueLayout
            ? 'flex min-h-0 flex-col'
            : `${SETTINGS_SECTION_SURFACE} flex min-h-[500px] flex-1 flex-col`
        }
        data-slot="settings-section"
        aria-label={t('settings.engines')}
      >
        <EngineCompatibilityMatrix
          family={family}
          sharedEngines={enginesQuery}
          weights={downloads.models}
          downloads={downloads}
          onSelect={onSelect}
          onFamilyChange={(next) => {
            setFamily(next);
            onFamilyChange?.(next);
          }}
          reloadToken={configVersion}
          catalogueLayout={catalogueLayout}
        />
      </section>
      {family === 'asr' && <AsrOpenAICompatPanel onSaved={onAsrConfigSaved} />}
    </div>
  );
}
