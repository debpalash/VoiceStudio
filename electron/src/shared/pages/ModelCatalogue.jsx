/**
 * ModelCatalogue — one page, one axis.
 *
 * Reads top-down the way a user thinks about it:
 *   1. SetupSummary — what speech, transcription, dictation and the language
 *      model use right now, one line each, with a Change button.
 *   2. The engine list for ONE family (TTS / ASR / LLM), the same tested
 *      EngineCompatibilityMatrix the Engines pane hosted; Change on a summary
 *      row switches this family and scrolls here.
 *   3. Weights no engine owns (pipeline weights such as speaker diarisation)
 *      for that family. Every other weight is listed under its engine in the
 *      detail panel. LLM engines bring their own weights, so nothing renders.
 *   4. One storage line pointing at Settings → Storage.
 *
 * The previous Engines | Models pane switch put the same decision on two axes
 * (family tabs inside one pane, role sections inside the other), duplicated
 * dictation in both, and parked storage stats, the HF token and the voice
 * preview toggle on the model list. Those now live where they belong
 * (Settings → Storage / Credentials), and there is no pane to pick.
 *
 * `pendingCatalogueFamily` is the one-shot deep-link hand-off (mirrors
 * Settings' `pendingSettingsTab`): a caller names a family and navigates
 * here; this page consumes it once and clears it so a later plain visit
 * reopens the last family.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Boxes } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../store';
import { useModels } from '../api/hooks';
import SetupSummary from '../components/catalogue/SetupSummary';
import EnginesTab from '../components/settings/EnginesTab';
import ModelStoreTab from '../components/settings/ModelStoreTab';
import { fmtBytes } from '../components/settings/models/format';

/** Persisted across visits so the workspace reopens where you left it. */
const FAMILY_KEY = 'omnivoice.catalogue.engine-family';
const FAMILIES = ['tts', 'asr', 'llm'];
/** Families whose engines load catalogue weights (LLM engines bring their own). */
const WEIGHT_FAMILIES = ['tts', 'asr'];

function readStoredFamily() {
  try {
    const stored = localStorage.getItem(FAMILY_KEY);
    return FAMILIES.includes(stored) ? stored : 'tts';
  } catch {
    return 'tts';
  }
}

export default function ModelCatalogue() {
  const { t } = useTranslation();
  const pendingCatalogueFamily = useAppStore((s) => s.pendingCatalogueFamily);
  const setPendingCatalogueFamily = useAppStore((s) => s.setPendingCatalogueFamily);
  const openSettingsTab = useAppStore((s) => s.openSettingsTab);
  // Seed from the deep-link so the first paint is already the requested
  // family — seeding from storage and correcting in an effect would flash.
  const [family, setFamilyRaw] = useState(() =>
    FAMILIES.includes(pendingCatalogueFamily) ? pendingCatalogueFamily : readStoredFamily(),
  );

  const setFamily = useCallback((next) => {
    setFamilyRaw(next);
    try {
      localStorage.setItem(FAMILY_KEY, next);
    } catch {
      /* private mode / quota — the family still switches */
    }
  }, []);

  // Consume the one-shot deep-link (including a repeat request for the
  // family we're already on, which must still clear).
  useEffect(() => {
    if (!pendingCatalogueFamily) return;
    if (FAMILIES.includes(pendingCatalogueFamily)) setFamily(pendingCatalogueFamily);
    setPendingCatalogueFamily(null);
  }, [pendingCatalogueFamily, setPendingCatalogueFamily, setFamily]);

  const enginesRef = useRef(null);
  const changeFamily = useCallback(
    (next) => {
      setFamily(next);
      enginesRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    },
    [setFamily],
  );

  const { data: models } = useModels();
  const cacheDir = models?.hf_cache_dir?.replace(/^\/Users\/[^/]+/, '~');

  return (
    // Same container-query shell as Settings: the app is zoom-scaled, so
    // viewport media queries fire at the wrong logical width under Tauri.
    <div
      className="h-full min-h-0 w-full overflow-y-auto overscroll-contain bg-[var(--chrome-bg)] [container-type:inline-size] [container-name:catalogue-shell]"
      data-testid="model-catalogue"
    >
      <div className="mx-auto box-border w-full max-w-[1500px] px-[34px] pb-[56px] pt-[34px] font-sans @max-[900px]/catalogue-shell:px-[24px] @max-[900px]/catalogue-shell:pb-[36px] @max-[900px]/catalogue-shell:pt-[26px] @max-[560px]/catalogue-shell:px-[14px]">
        <header className="mb-[22px] flex items-center gap-[12px]">
          <span
            className="inline-flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[12px] bg-[color-mix(in_srgb,var(--chrome-accent)_11%,transparent)] text-[color:var(--chrome-accent)]"
            aria-hidden="true"
          >
            <Boxes size={17} strokeWidth={1.6} />
          </span>
          <h1 className="m-0 min-w-0 [font-family:var(--font-serif)] text-[2rem] font-normal leading-none tracking-[-0.025em] text-[color:var(--chrome-fg)] @max-[560px]/catalogue-shell:text-[1.55rem]">
            {t('catalogue.title')}
          </h1>
        </header>

        <SetupSummary onChange={changeFamily} />

        {/* Full-bleed: engine rows and the weights table are wide, data-dense
            surfaces — a reading-width column would only add horizontal
            scrolling inside them. */}
        <section
          ref={enginesRef}
          data-testid="catalogue-engines"
          className="min-w-0 scroll-mt-[24px] [&>*:first-child]:mt-0"
        >
          <EnginesTab initialFamily={family} onFamilyChange={setFamily} catalogueLayout />
        </section>

        {/* Always mounted: ModelStoreTab owns the download-progress SSE state
            (progress, errors with Retry/Dismiss), which would be lost if a
            family switch unmounted it mid-download. LLM merely hides it. */}
        <section
          data-testid="catalogue-weights"
          aria-label={t('catalogue.other_weights')}
          className="mt-[40px] min-w-0"
          hidden={!WEIGHT_FAMILIES.includes(family)}
        >
          <ModelStoreTab family={family} title={t('catalogue.other_weights')} />
        </section>

        <footer
          data-testid="catalogue-storage"
          className="mt-[40px] flex flex-wrap items-center gap-x-[10px] gap-y-[4px] border-t border-border pt-[14px] font-mono text-[11px] text-muted-foreground"
        >
          {models && (
            <>
              <span className="text-foreground">
                {fmtBytes(models.total_installed_bytes) || '0 B'}
              </span>
              {models.disk_free_gb != null && (
                <>
                  <span aria-hidden="true">·</span>
                  <span title={t('models.disk_free_title')}>
                    {t('models.disk_free', { size: `${models.disk_free_gb} GB` })}
                  </span>
                </>
              )}
              {cacheDir && (
                <>
                  <span aria-hidden="true">·</span>
                  <code className="truncate" title={models.hf_cache_dir}>
                    {cacheDir}
                  </code>
                </>
              )}
            </>
          )}
          <button
            type="button"
            className="ml-auto cursor-pointer border-0 bg-transparent p-0 font-mono text-[11px] text-accent hover:underline"
            onClick={() => openSettingsTab('storage')}
            data-testid="catalogue-storage-link"
          >
            {t('catalogue.storage_link')} →
          </button>
        </footer>
      </div>
    </div>
  );
}
