/**
 * Settings → Capture → Voice panel.
 *
 * The home of the live-dictation controls (the "Voice" card in the screenshot):
 *
 *   1. Enable Voice Dictation — master toggle. Subtitle shows the REAL
 *      effective dictation shortcut (same live source as HotkeyTab); changing it still
 *      lives in HotkeyTab below.
 *   2. Dictation Mode — Toggle / Hold segmented control. Toggle = press once to
 *      start, again to stop. Hold = dictate while the key is held.
 *   3. Speech Model — a dropdown of the seven sherpa-onnx dictation models. The
 *      collapsed control shows the selected model's name + description; expanded,
 *      each row shows offline/streaming + recommended badges, the size, a
 *      one-line description, a checkmark on the selected one, and a trash icon to
 *      delete an installed model. Not-installed models show a Download action and
 *      inline download progress while installing.
 *
 * Prefs are the backend `dictation.*` namespace (GET/POST /dictation/prefs),
 * mirrored into the zustand store; model install/delete/progress REUSE the
 * model-store endpoints + SSE stream the ModelStoreTab already drives, so a
 * model installed here shows installed there and vice-versa.
 *
 * Cross-platform: every control behaves identically on macOS / Windows / Linux.
 * The shortcut read no-ops gracefully in the web UI (no Tauri) and falls back to
 * the documented Ctrl/Cmd+Shift+Space default label.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Mic, Check, Download, Trash2, ChevronDown, Loader, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-hot-toast';
import { useAppStore } from '../../store';
import { apiJson } from '../../api/client';
import { useInstallModel, useDeleteModel } from '../../api/hooks';
import { setupDownloadStreamUrl } from '../../api/setup';
import { isTauri as _isTauri } from '../../utils/media';
import { Badge, Progress, Segmented } from '../../ui';
import { useEffectiveDictationShortcut } from '../../hooks/useEffectiveDictationShortcut';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';

/** Native confirm dialog in Tauri, window.confirm in the web UI. Mirrors the
 * `askConfirm` helper in Settings.jsx (kept local to avoid coupling). */
async function askConfirm(message, title = 'Confirm') {
  if (_isTauri) {
    try {
      const { ask } = await import('@tauri-apps/plugin-dialog');
      return ask(message, { title, kind: 'warning' });
    } catch {
      /* dialog plugin unavailable — fall through */
    }
  }
  return Promise.resolve(typeof window !== 'undefined' ? window.confirm(message) : true);
}

/** Format a model's download size for display (e.g. "180 MB", "1.2 GB"). */
function fmtSize(sizeGb) {
  if (sizeGb == null) return '';
  if (sizeGb < 1) return `${Math.round(sizeGb * 1000)} MB`;
  return `${sizeGb.toFixed(sizeGb < 10 ? 1 : 0)} GB`;
}

export default function VoicePanel() {
  const { t } = useTranslation();

  const enabled = useAppStore((s) => s.dictationEnabled);
  const setEnabled = useAppStore((s) => s.setDictationEnabled);
  const mode = useAppStore((s) => s.dictationMode);
  const setMode = useAppStore((s) => s.setDictationMode);
  const modelId = useAppStore((s) => s.dictationModelId);
  const setModelId = useAppStore((s) => s.setDictationModelId);
  const loadPrefs = useAppStore((s) => s.loadDictationPrefs);

  const [models, setModels] = useState([]);
  const [engineAvailable, setEngineAvailable] = useState(true);
  const [engineReason, setEngineReason] = useState(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const { info: shortcut } = useEffectiveDictationShortcut(_isTauri);

  // Per-repo download runtime, keyed by repo_id, driven by the shared SSE
  // progress stream (same events ModelStoreTab consumes):
  //   { [repo_id]: { phase, pct } }
  const [rowState, setRowState] = useState({});
  const esRef = useRef(null);
  const dropdownRef = useRef(null);

  const installMutation = useInstallModel();
  const deleteMutation = useDeleteModel();

  // Hydrate prefs from the backend on mount (write-through setters keep them in
  // sync after that).
  useEffect(() => {
    loadPrefs();
  }, [loadPrefs]);

  const loadModels = React.useCallback(async () => {
    try {
      const data = await apiJson('/dictation/models');
      setModels(Array.isArray(data?.models) ? data.models : []);
      setEngineAvailable(data?.engine_available !== false);
      setEngineReason(data?.engine_reason || null);
    } catch {
      setModels([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  // Subscribe to the model-store SSE so install progress shows inline here too.
  // We only track the aggregate percent + lifecycle phase per repo — the full
  // per-file accounting lives in ModelStoreTab; here a single bar is enough.
  useEffect(() => {
    const es = new EventSource(setupDownloadStreamUrl());
    esRef.current = es;
    es.onmessage = (evt) => {
      try {
        const ev = JSON.parse(evt.data);
        if (!ev?.repo_id) return;
        setRowState((prev) => {
          const cur = prev[ev.repo_id] || {};
          if (ev.phase === 'install_start')
            return { ...prev, [ev.repo_id]: { phase: 'active', pct: 0 } };
          if (ev.phase === 'install_done')
            return { ...prev, [ev.repo_id]: { phase: 'install_done', pct: 100 } };
          if (ev.phase === 'install_error')
            return { ...prev, [ev.repo_id]: { phase: 'install_error', error: ev.error } };
          if (ev.phase === 'install_cancelled')
            return { ...prev, [ev.repo_id]: { phase: 'install_cancelled' } };
          if (ev.phase === 'delete_done')
            return { ...prev, [ev.repo_id]: { phase: 'delete_done' } };
          if (ev.phase === 'aggregate') {
            const total = ev.total_bytes || 0;
            const bytePct = total > 0 ? (ev.bytes_done / total) * 100 : 0;
            const ft = ev.files_total || 0;
            const filePct = ft > 0 ? ((ev.files_done || 0) / ft) * 100 : 0;
            return { ...prev, [ev.repo_id]: { phase: 'active', pct: Math.max(bytePct, filePct) } };
          }
          // Plain per-file tqdm event: approximate with its pct if no aggregate yet.
          if (ev.pct != null && cur.phase !== 'active') {
            return { ...prev, [ev.repo_id]: { phase: 'active', pct: ev.pct } };
          }
          return prev;
        });
      } catch {
        /* keepalive */
      }
    };
    return () => es.close();
  }, []);

  // When an install/delete terminates, refresh the model list so the installed
  // flag flips, then clear the terminal row so it reverts to the list state.
  useEffect(() => {
    const term = Object.entries(rowState).find(([, s]) =>
      ['install_done', 'delete_done', 'install_error', 'install_cancelled'].includes(s.phase),
    );
    if (!term) return;
    const id = setTimeout(() => {
      loadModels();
      setRowState((prev) => {
        const n = { ...prev };
        delete n[term[0]];
        return n;
      });
    }, 800);
    return () => clearTimeout(id);
  }, [rowState, loadModels]);

  // Close the dropdown on outside-click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const selected = useMemo(
    () => models.find((m) => m.id === modelId) || models.find((m) => m.recommended) || models[0],
    [models, modelId],
  );

  const onInstall = async (repoId) => {
    setRowState((p) => ({ ...p, [repoId]: { phase: 'active', pct: 0 } }));
    try {
      await installMutation.mutateAsync(repoId);
    } catch (e) {
      toast.error(e?.message || String(e));
      setRowState((p) => {
        const n = { ...p };
        delete n[repoId];
        return n;
      });
    }
  };

  const onDelete = async (model) => {
    const ok = await askConfirm(
      t('voicePanel.delete_confirm', { label: model.label }),
      t('voicePanel.delete_confirm_title'),
    );
    if (!ok) return;
    setRowState((p) => ({ ...p, [model.repo_id]: { phase: 'deleting' } }));
    try {
      await deleteMutation.mutateAsync(model.repo_id);
    } catch (e) {
      toast.error(e?.message || String(e));
      setRowState((p) => {
        const n = { ...p };
        delete n[model.repo_id];
        return n;
      });
    }
  };

  // Picking a model: write the pref. If it isn't installed yet, also kick off
  // the download — the user gets the model the moment it lands, and the live
  // socket will pick it up via the persisted pref.
  const onPick = (model) => {
    setModelId(model.id);
    setOpen(false);
    if (!model.installed && !rowState[model.repo_id]) {
      onInstall(model.repo_id);
    }
  };

  // Human-readable, i18n-keyed description per model id (one line each). Falls
  // back to the backend `languages` string when a key is missing.
  const modelDesc = (m) => t(`voicePanel.model_desc.${m.id}`, { defaultValue: m.languages || '' });

  const shortcutLabel = shortcut.display;

  return (
    <SettingsSection
      className="voicepanel"
      icon={Mic}
      accent="var(--chrome-accent)"
      title={t('voicePanel.title')}
      description={t('voicePanel.subtitle')}
    >
      {!engineAvailable && (
        <div
          className="mb-[var(--space-4)] flex items-center gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] [border:1px_solid_color-mix(in_srgb,var(--chrome-severity-warn)_32%,transparent)] bg-[color-mix(in_srgb,var(--chrome-severity-warn)_12%,transparent)] px-[var(--space-4)] py-[var(--space-3)] text-[length:var(--text-sm)] text-[var(--chrome-severity-warn)]"
          role="alert"
        >
          <AlertTriangle size={13} />
          <span>{engineReason || t('voicePanel.engine_unavailable')}</span>
        </div>
      )}

      {/* 1 — Enable Voice Dictation */}
      <SettingRow
        title={t('voicePanel.enable_label')}
        subtitle={t('voicePanel.enable_sub', { shortcut: shortcutLabel })}
        control={
          <SettingsToggle
            checked={enabled}
            onChange={setEnabled}
            aria-label={t('voicePanel.enable_label')}
          />
        }
      />

      {/* 2 — Dictation Mode */}
      <SettingRow
        title={t('voicePanel.mode_label')}
        subtitle={t('voicePanel.mode_sub')}
        control={
          <Segmented
            size="sm"
            value={mode}
            onChange={(v) => setMode(v)}
            items={[
              { value: 'toggle', label: t('voicePanel.mode_toggle') },
              { value: 'hold', label: t('voicePanel.mode_hold') },
            ]}
          />
        }
      />

      {/* 3 — Speech Model */}
      <SettingRow
        className="voicepanel__row--model"
        align="start"
        title={t('voicePanel.model_label')}
        subtitle={selected ? modelDesc(selected) : t('voicePanel.model_sub')}
        control={
          <div
            className="relative w-auto min-w-[200px] max-w-[280px] flex-none text-left"
            ref={dropdownRef}
          >
            <button
              type="button"
              className="flex w-full cursor-pointer items-center justify-between gap-[8px] rounded-[var(--chrome-radius-pill)] [border:1px_solid_var(--chrome-border,rgba(255,255,255,0.1))] bg-[var(--chrome-input-bg,rgba(255,255,255,0.04))] px-[var(--space-4)] py-[var(--space-3)] text-left text-[length:var(--text-md)] text-[var(--chrome-fg)] disabled:cursor-not-allowed disabled:opacity-60"
              aria-haspopup="listbox"
              aria-expanded={open}
              disabled={loading || models.length === 0}
              onClick={() => setOpen((o) => !o)}
              data-testid="dictation-model-trigger"
            >
              <span className="truncate">{selected ? selected.label : t('common.loading')}</span>
              <ChevronDown size={14} className={`voicepanel__dd-chev ${open ? 'is-open' : ''}`} />
            </button>

            {open && (
              <ul
                className="absolute right-0 top-[calc(100%_+_6px)] z-40 m-0 max-h-[380px] w-[360px] max-w-[80vw] list-none overflow-y-auto rounded-[10px] [border:1px_solid_var(--chrome-border,rgba(255,255,255,0.1))] bg-[var(--chrome-menu-bg,#1d1d22)] p-[5px] shadow-[0_12px_32px_rgba(0,0,0,0.45)]"
                role="listbox"
                aria-label={t('voicePanel.model_label')}
              >
                {models.map((m) => {
                  const rs = rowState[m.repo_id];
                  const installing = rs && rs.phase === 'active';
                  const deleting = rs && rs.phase === 'deleting';
                  const isSel = m.id === selected?.id;
                  return (
                    <li
                      key={m.id}
                      className={`flex items-start gap-[4px] rounded-[8px] hover:bg-[var(--chrome-hover-bg,rgba(255,255,255,0.08))] ${
                        isSel ? 'bg-[var(--chrome-hover-bg,rgba(255,255,255,0.06))]' : ''
                      }`}
                      role="option"
                      aria-selected={isSel}
                    >
                      <button
                        type="button"
                        className="flex min-w-0 flex-[1_1_auto] cursor-pointer items-start gap-[8px] border-0 bg-transparent pb-[9px] pl-[8px] pr-[4px] pt-[9px] text-left text-[var(--chrome-fg)]"
                        onClick={() => onPick(m)}
                        data-testid={`dictation-model-${m.id}`}
                      >
                        <span className="mt-[1px] w-[16px] flex-none text-[var(--chrome-accent,#83a598)]">
                          {isSel && <Check size={14} />}
                        </span>
                        <span className="flex min-w-0 flex-col gap-[4px]">
                          <span className="flex flex-wrap items-center gap-[6px]">
                            <span className="text-[0.86rem] font-semibold">{m.label}</span>
                            <Badge tone="neutral" size="xs">
                              {m.tag === 'streaming'
                                ? t('voicePanel.badge_streaming')
                                : t('voicePanel.badge_offline')}
                            </Badge>
                            {m.recommended && (
                              <Badge tone="success" size="xs">
                                {t('voicePanel.badge_recommended')}
                              </Badge>
                            )}
                            <span className="ml-auto text-[0.74rem] text-[var(--chrome-fg-muted)]">
                              {fmtSize(m.size_gb)}
                            </span>
                          </span>
                          <span className="text-[0.76rem] leading-[1.4] text-[var(--chrome-fg-muted)]">
                            {modelDesc(m)}
                          </span>
                          {installing && (
                            <span className="voicepanel__dd-progress">
                              <Progress value={rs.pct ?? null} tone="brand" size="xs" />
                              <span className="flex-none text-[0.72rem] text-[var(--chrome-fg-muted)]">
                                {rs.pct != null && rs.pct > 0
                                  ? t('voicePanel.downloading_pct', { pct: Math.round(rs.pct) })
                                  : t('voicePanel.downloading')}
                              </span>
                            </span>
                          )}
                        </span>
                      </button>

                      {/* Right-edge affordance: download (not installed), spinner
                          (installing/deleting), or delete (installed). */}
                      <span className="flex flex-none items-center pb-0 pl-0 pr-[6px] pt-[9px]">
                        {installing || deleting ? (
                          <Loader size={14} className="voicepanel__spin" />
                        ) : m.installed ? (
                          <button
                            type="button"
                            className="inline-flex h-[26px] w-[26px] cursor-pointer items-center justify-center rounded-[6px] border-0 bg-transparent p-0 text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg,rgba(255,255,255,0.1))] hover:text-[var(--chrome-fg)]"
                            title={t('voicePanel.delete_model')}
                            aria-label={t('voicePanel.delete_model')}
                            onClick={(e) => {
                              e.stopPropagation();
                              onDelete(m);
                            }}
                            data-testid={`dictation-delete-${m.id}`}
                          >
                            <Trash2 size={13} />
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="inline-flex h-[26px] w-[26px] cursor-pointer items-center justify-center rounded-[6px] border-0 bg-transparent p-0 text-[var(--chrome-fg-muted)] hover:bg-[var(--chrome-hover-bg,rgba(255,255,255,0.1))] hover:text-[var(--chrome-fg)]"
                            title={t('voicePanel.download_model')}
                            aria-label={t('voicePanel.download_model')}
                            onClick={(e) => {
                              e.stopPropagation();
                              onInstall(m.repo_id);
                            }}
                            data-testid={`dictation-install-${m.id}`}
                          >
                            <Download size={13} />
                          </button>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        }
      />
    </SettingsSection>
  );
}
