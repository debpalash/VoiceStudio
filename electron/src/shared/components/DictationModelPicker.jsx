import React, { useEffect, useState } from 'react';
import { Check, Download, Loader } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { apiJson } from '../api/client';
import { useInstallModel } from '../api/hooks';
import { useAppStore } from '../store';

function fmtSize(sizeGb) {
  if (sizeGb == null) return '';
  if (sizeGb < 1) return `${Math.round(sizeGb * 1000)} MB`;
  return `${sizeGb.toFixed(sizeGb < 10 ? 1 : 0)} GB`;
}

/**
 * The sherpa-onnx dictation model list, nested under the "Sherpa-ONNX
 * dictation" row of the Transcription engine picker. The engine row switches
 * the *engine*; this switches which of the seven catalogue models that engine
 * (and the live-dictation hotkey) loads — the same `dictation.model_id` pref
 * the Settings → Voice panel writes, so both surfaces always agree.
 *
 * Picking a model that isn't downloaded yet persists the choice and starts
 * the download in one click (mirrors VoicePanel.onPick): the user gets the
 * model the moment it lands rather than a second "now install it" step.
 */
export default function DictationModelPicker({ embedded = false }) {
  const { t } = useTranslation();
  const modelId = useAppStore((s) => s.dictationModelId);
  const setModelId = useAppStore((s) => s.setDictationModelId);
  const loadPrefs = useAppStore((s) => s.loadDictationPrefs);
  const dictationLoaded = useAppStore((s) => s.dictationLoaded);
  const installMutation = useInstallModel();
  const [installing, setInstalling] = useState(() => new Set());

  useEffect(() => {
    if (!dictationLoaded) loadPrefs();
  }, [dictationLoaded, loadPrefs]);

  const { data, refetch } = useQuery({
    queryKey: ['dictation-models'],
    queryFn: () => apiJson('/dictation/models'),
    staleTime: 10_000,
    retry: false,
    // While a download runs, poll so the "installed" state flips without a
    // reopen — the quick menu has no SSE progress stream of its own.
    refetchInterval: installing.size > 0 ? 2_000 : false,
  });
  const models = Array.isArray(data?.models) ? data.models : [];
  if (models.length === 0) return null;

  const selectedId = models.some((m) => m.id === modelId)
    ? modelId
    : (models.find((m) => m.recommended) || models[0]).id;

  const pick = async (model) => {
    if (model.id !== selectedId) setModelId(model.id);
    if (model.installed || installing.has(model.repo_id)) return;
    setInstalling((prev) => new Set(prev).add(model.repo_id));
    try {
      await installMutation.mutateAsync(model.repo_id);
      await refetch();
    } catch {
      // The install surface (Settings → Voice / Model Catalogue) reports the
      // failure with full detail; here the row simply returns to "download".
    } finally {
      setInstalling((prev) => {
        const next = new Set(prev);
        next.delete(model.repo_id);
        return next;
      });
    }
  };

  return (
    <div
      role="group"
      aria-label={t('engines.dictation_model')}
      title={t('engines.dictation_model_hint')}
      data-testid="dictation-model-picker"
      className={`ml-[20px] flex flex-col border-l border-transparent pl-1 ${embedded ? 'mb-2' : 'mb-1'}`}
    >
      <p className="m-0 px-2 pb-[2px] pt-[2px] text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--chrome-fg-muted)]">
        {t('engines.dictation_model')}
      </p>
      {models.map((m) => {
        const isSel = m.id === selectedId;
        const busy = installing.has(m.repo_id);
        const desc = t(`voicePanel.model_desc.${m.id}`, { defaultValue: m.languages || '' });
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => pick(m)}
            disabled={busy}
            aria-pressed={isSel}
            data-testid={`dictation-model-${m.id}`}
            title={m.installed ? desc : `${desc} — ${t('engines.dictation_not_installed')}`}
            className={`flex w-full items-center gap-2 rounded-md border-0 px-2 py-[5px] text-left text-xs text-[color:var(--chrome-fg)] hover:bg-[var(--chrome-hover-bg)] disabled:cursor-default ${isSel ? 'bg-[var(--chrome-accent-bg)]' : 'bg-transparent'}`}
          >
            <span className="w-[12px] shrink-0">
              {isSel && <Check size={12} aria-label={t('engines.active')} />}
            </span>
            <span className="min-w-0 flex-1 truncate">
              <span className="font-medium">{m.label}</span>
              <span className="ml-[6px] text-[10px] text-[var(--chrome-fg-muted)]">
                {m.tag === 'streaming'
                  ? t('voicePanel.badge_streaming')
                  : t('voicePanel.badge_offline')}
                {m.recommended && ` · ${t('voicePanel.badge_recommended')}`}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-1 text-[10px] tabular-nums text-[color:var(--chrome-fg-muted)]">
              {fmtSize(m.size_gb)}
              {busy ? (
                <Loader
                  size={12}
                  className="animate-spin"
                  aria-label={t('voicePanel.downloading')}
                />
              ) : m.installed ? null : (
                <Download size={12} aria-hidden="true" />
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
