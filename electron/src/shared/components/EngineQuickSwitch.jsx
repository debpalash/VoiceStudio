import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronRight, Cpu } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { listLoadedModels } from '../api/system';
import { useEngines, useSelectEngine } from '../api/hooks';
import { notifyEngineSelected } from '../utils/engineSelectToast';
import { useAppStore } from '../store';
import { MENU_SURFACE } from './computeTarget';
import { engineDisplayName } from '../utils/engineDisplayName';
import DictationModelPicker from './DictationModelPicker';

/**
 * A compact TTS/ASR/LLM picker for chrome that needs to expose the active
 * engine without growing a second engine-management surface. The matrix stays
 * the detailed view; this lists only engines that are ready to be used.
 */
export default function EngineQuickSwitch({
  family = 'tts',
  className = '',
  shortcutTarget = false,
  // The footer chip opens upward (nothing below the last row on screen);
  // workspace-header chips must open downward or the popover clips off the
  // top of the viewport.
  dropUp = false,
  prominent = false,
  embedded = false,
}) {
  const { t } = useTranslation();
  const rootRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [switchError, setSwitchError] = useState('');
  const { data: engines } = useEngines();
  const selectMutation = useSelectEngine();
  const { data: residency } = useQuery({
    queryKey: ['loaded-models'],
    queryFn: listLoadedModels,
    staleTime: 10_000,
    retry: false,
    enabled: open || embedded,
  });

  const familyData = engines?.[family];
  const active = familyData?.backends?.find((engine) => engine.id === familyData.active);
  const available = useMemo(
    () => (familyData?.backends || []).filter((engine) => engine.available),
    [familyData],
  );
  const residentIds = useMemo(
    () =>
      new Set(
        (residency?.models || []).flatMap((model) => (model.engine_id ? [model.engine_id] : [])),
      ),
    [residency],
  );

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
    };
    const escape = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  // In-webview shortcut bridge. This stays a DOM event (not a Tauri global
  // shortcut), so every desktop and browser build behaves the same way.
  useEffect(() => {
    if (!shortcutTarget) return undefined;
    const show = () => {
      setSwitchError('');
      setOpen(true);
    };
    window.addEventListener('engine-quick-switch', show);
    return () => window.removeEventListener('engine-quick-switch', show);
  }, [shortcutTarget]);

  if (!active || available.length === 0) return null;

  const activeName = engineDisplayName(active.display_name);
  const locked = Boolean(familyData.env_override);
  const choose = async (backendId) => {
    if (backendId === familyData.active || locked) return;
    setSwitchError('');
    try {
      const result = await selectMutation.mutateAsync({ family, backendId });
      if (result.env_override) {
        setSwitchError(t('settings.llmp_env_override'));
        return;
      }
      notifyEngineSelected(result, t, family);
      setOpen(false);
    } catch (error) {
      setSwitchError(error?.message || t('engines.switch_failed'));
    }
  };

  return (
    <div
      className={`${embedded ? 'block w-full' : 'relative inline-flex shrink-0 items-center'} ${prominent ? 'self-start max-w-full' : ''} ${className}`}
      ref={rootRef}
    >
      {!embedded && (
        <button
          type="button"
          onClick={() => {
            setSwitchError('');
            setOpen((value) => !value);
          }}
          aria-haspopup="dialog"
          aria-expanded={open}
          title={t('engines.activeEngine', {
            family: family.toUpperCase(),
            engine: activeName,
          })}
          aria-label={t('engines.activeEngine', {
            family: family.toUpperCase(),
            engine: activeName,
          })}
          className={`inline-flex items-center gap-[5px] rounded-sm border-0 bg-transparent px-[7px] font-medium text-[color:var(--chrome-fg-muted)] transition-[background,color] hover:bg-[var(--chrome-hover-bg)] hover:text-[color:var(--chrome-fg)] ${prominent ? 'min-h-11 text-sm text-left' : 'h-[20px] text-[11px]'}`}
        >
          <Cpu size={13} aria-hidden="true" />
          <span className={prominent ? 'min-w-0 break-words' : 'max-w-[124px] truncate'}>
            {activeName}
          </span>
        </button>
      )}

      {(open || embedded) && (
        <div
          role={embedded ? 'group' : 'dialog'}
          aria-label={t('engines.engineCompatLabel', { family: family.toUpperCase() })}
          className={
            embedded
              ? 'flex w-full min-w-0 flex-col gap-1 py-2'
              : `absolute ${prominent ? 'left-0' : 'right-0'} z-[60] flex w-[272px] max-w-[calc(100vw-32px)] flex-col gap-[4px] p-[8px] ${
                  dropUp ? 'bottom-[calc(100%+8px)]' : 'top-[calc(100%+8px)]'
                } ${MENU_SURFACE}`
          }
        >
          {locked && (
            <p className="m-[4px] text-[11px] leading-[1.4] text-[color:var(--chrome-fg-muted)]">
              {t('settings.llmp_env_override')}
            </p>
          )}
          {available.map((engine) => {
            const isActive = engine.id === familyData.active;
            const warm = residentIds.has(engine.id);
            const displayName = engineDisplayName(engine.display_name);
            const parts = displayName.match(/^(.+?)\s*\((.*)\)$/);
            // The Sherpa engine is one engine but seven catalogue models; the
            // model choice lives right under its row so "which dictation
            // model?" is answerable from the same menu that shows the engine.
            const nestedPicker =
              family === 'asr' && engine.id === 'sherpa-onnx-asr' ? (
                <DictationModelPicker embedded={embedded} />
              ) : null;
            return (
              <React.Fragment key={engine.id}>
                <button
                  type="button"
                  disabled={isActive || locked || selectMutation.isPending}
                  onClick={() => choose(engine.id)}
                  className={`flex w-full items-center gap-2 rounded-md border-0 px-2 py-3 text-left text-xs text-[color:var(--chrome-fg)] hover:bg-[var(--chrome-hover-bg)] disabled:cursor-default ${embedded && isActive ? 'bg-[var(--chrome-accent-bg)]' : 'bg-transparent'}`}
                >
                  <span className="w-[12px] shrink-0">
                    {isActive && <Check size={12} aria-label={t('engines.active')} />}
                  </span>
                  <span
                    className={
                      embedded
                        ? 'min-w-0 flex-1 whitespace-normal break-words'
                        : 'min-w-0 flex-1 truncate'
                    }
                  >
                    {embedded && parts ? (
                      <>
                        <span className="block font-medium text-sm">{parts[1]}</span>
                        <span className="block mt-1 text-xs leading-relaxed text-[var(--chrome-fg-muted)]">
                          {parts[2]}
                        </span>
                      </>
                    ) : (
                      displayName
                    )}
                  </span>
                  <span className="shrink-0 text-[10px] text-[color:var(--chrome-fg-muted)]">
                    {warm
                      ? t('engines.inMemory')
                      : isActive
                        ? t('engines.active')
                        : embedded
                          ? ''
                          : t('engines.available')}
                  </span>
                </button>
                {nestedPicker}
              </React.Fragment>
            );
          })}
          {switchError && (
            <p
              role="alert"
              className="m-[4px] text-[11px] leading-[1.4] text-[color:var(--chrome-severity-err)]"
            >
              {switchError}
            </p>
          )}
          {!embedded && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                useAppStore.getState().openCatalogue({ family });
              }}
              className="mt-[3px] flex items-center gap-[3px] border-0 bg-transparent px-[7px] py-[5px] text-left text-[11px] text-[color:var(--chrome-fg-muted)] hover:text-[color:var(--chrome-fg)]"
            >
              {t('settings.engines')} <ChevronRight size={12} aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
