/**
 * Footer compute control — where the next job runs, one click from anywhere.
 *
 * Remote workers was reachable only through Settings → System → Remote
 * workers, four clicks from the workspace, which is the wrong distance for a
 * decision people make several times an hour ("my desktop is free now", "the
 * laptop is on battery, keep it here"). This is the same choice as the
 * header's <GpuTarget/>, plus the two controls that decision usually needs
 * next: the master switch, and a join code to add a machine.
 *
 * It shows the RESOLVED answer, not the stored choice — see GpuTarget for why
 * that distinction matters — and it is deliberately absent when the feature is
 * off and no machine has ever been enrolled: a permanently visible chip for a
 * feature the user has not opted into is exactly the chrome noise this bar has
 * to stay clear of.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Cpu, Plus, SlidersHorizontal } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAppStore } from '../store';
import {
  FELL_BACK_TEXT,
  MENU_ITEM,
  MENU_SURFACE,
  StatusDot,
  latencyLabel,
  workersRequest as request,
} from './computeTarget';
import { SettingsToggle } from './settings/primitives';
import OneTimeSecret from './OneTimeSecret';

const IDLE_REFRESH_MS = 5000;

export default function ComputeQuickSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState(null);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef(null);

  // Shares both cache keys with the Settings panel and the header picker, so
  // opening this does not add a third poller for the same two answers.
  const { data: snapshot } = useQuery({
    queryKey: ['workers'],
    queryFn: () => request('/workers'),
    refetchInterval: (query) => (query.state?.data?.running ? IDLE_REFRESH_MS : false),
    refetchIntervalInBackground: false,
    retry: false,
  });
  const { data: target } = useQuery({
    queryKey: ['workers', 'target', ''],
    queryFn: () => request('/workers/target'),
    refetchInterval: IDLE_REFRESH_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });

  // Click-away. The popover is inline (not portalled) because the footer is
  // the last row on screen and nothing clips it.
  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const escape = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const enabled = Boolean(snapshot?.enabled);
  const workers = snapshot?.workers || [];
  const targets = target?.targets || [];
  const chosen = target?.target || 'local';
  const active = target?.active;

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['workers'] });

  const guarded = async (fn) => {
    setBusy(true);
    try {
      await fn();
      refresh();
    } catch (e) {
      toast.error(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const setEnabled = (next) =>
    guarded(async () => {
      await request('/workers/enabled', { method: 'POST', body: { enabled: next } });
      if (!next) setToken(null);
    });

  const choose = (id) =>
    guarded(() => request('/workers/target', { method: 'POST', body: { target: id } }));

  const addMachine = () =>
    guarded(async () => {
      setToken(
        await request('/workers/enrollments', { method: 'POST', body: { ttl_seconds: 900 } }),
      );
    });

  const openSettings = useCallback(() => {
    setOpen(false);
    useAppStore.getState().openSettingsTab?.('workers');
  }, []);

  // Never opted in and nothing enrolled → no chip at all.
  if (!enabled && workers.length === 0) return null;

  const localLabel = t('gpu.local', { defaultValue: 'Local' });
  const label = active?.remote ? active.label : localLabel;
  const activeTarget = active?.remote
    ? targets.find((x) => x.id === active.worker_id)
    : targets.find((x) => x.is_local);
  // Chose a worker, work is running here anyway — the dot has to report the
  // machine you picked, or a green dot beside "Local" hides that it is down.
  const fellBack = enabled && !active?.remote && chosen !== 'local';
  const dotStatus = fellBack
    ? targets.find((x) => x.id === chosen)?.status || 'offline'
    : activeTarget?.status || 'ready';

  return (
    <div className="relative inline-flex shrink-0 items-center" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={active?.reason || t('gpu.picker', { defaultValue: 'Where jobs run' })}
        aria-label={t('compute.quick_settings', { defaultValue: 'Compute — where jobs run' })}
        className={
          'inline-flex items-center gap-[5px] h-[20px] px-[8px] rounded-sm border-0 cursor-pointer ' +
          'text-[11px] font-medium [font-family:inherit] [transition:background_0.1s,color_0.1s] ' +
          (open
            ? 'bg-[var(--chrome-hover-bg)] [color:var(--chrome-fg)]'
            : 'bg-transparent [color:var(--color-fg-muted)] hover:bg-[var(--chrome-hover-bg)] hover:[color:var(--chrome-fg)]')
        }
      >
        <Cpu size={14} aria-hidden="true" />
        <StatusDot status={enabled ? dotStatus : 'offline'} />
        <span className={fellBack ? FELL_BACK_TEXT : undefined}>{label}</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label={t('compute.quick_settings', { defaultValue: 'Compute — where jobs run' })}
          className={`absolute bottom-[calc(100%+8px)] right-0 z-[60] flex w-[272px] flex-col gap-[8px] p-[12px] ${MENU_SURFACE}`}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase [letter-spacing:0.06em] opacity-60">
              {t('compute.title', { defaultValue: 'Where jobs run' })}
            </span>
            {/* The app's switch, not a raw checkbox: this is the same control
                as the panel's master toggle and has to read as the same thing. */}
            <span className="flex items-center gap-[6px] text-[11px] opacity-80">
              {t('compute.remote', { defaultValue: 'Remote' })}
              <SettingsToggle
                checked={enabled}
                disabled={busy}
                onChange={setEnabled}
                aria-label={t('settings.workers_enable', { defaultValue: 'Use remote workers' })}
              />
            </span>
          </div>

          {!enabled ? (
            <p className="m-0 text-[11px] leading-[1.5] opacity-60">
              {t('compute.off_hint', {
                defaultValue: 'Everything runs on this machine. Turn Remote on to use another.',
              })}
            </p>
          ) : (
            <>
              <div className="flex flex-col gap-[2px]">
                {(targets.length
                  ? targets
                  : [{ id: 'local', label: localLabel, is_local: true }]
                ).map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    disabled={busy}
                    onClick={() => choose(item.id)}
                    className={`flex w-full items-center gap-2 rounded border-0 bg-transparent px-[6px] py-[5px] text-left text-[11px] cursor-pointer hover:bg-white/5 ${
                      item.available === false ? 'opacity-60' : ''
                    }`}
                  >
                    <span className="w-[12px] shrink-0">
                      {item.id === chosen ? <Check size={12} /> : null}
                    </span>
                    {item.is_local ? (
                      <span className="w-[6px]" />
                    ) : (
                      <StatusDot status={item.status} />
                    )}
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {/* Load while it is working, latency while it is idle —
                        the number that tells you whether to pick it. */}
                    {!item.is_local && (
                      <span className="shrink-0 tabular-nums opacity-60">
                        {item.active_tasks > 0
                          ? `${item.active_tasks}/${item.max_tasks}`
                          : latencyLabel(item)}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {active?.reason && fellBack && (
                <p className={`m-0 text-[11px] leading-[1.4] ${FELL_BACK_TEXT}`}>{active.reason}</p>
              )}

              {token ? (
                <OneTimeSecret
                  value={token.token}
                  expiresAt={token.expires_at}
                  qrSize={96}
                  onDone={() => setToken(null)}
                  headline={t('compute.token_once', {
                    defaultValue: 'Scan or paste this on the other machine. Shown once.',
                  })}
                />
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={addMachine}
                  className={`${MENU_ITEM} text-[11px] opacity-80 hover:opacity-100`}
                >
                  <Plus size={12} aria-hidden="true" />
                  {t('compute.add_machine', { defaultValue: 'Add a machine' })}
                </button>
              )}
            </>
          )}

          <button
            type="button"
            onClick={openSettings}
            className={`${MENU_ITEM} text-[11px] opacity-70 hover:opacity-100`}
          >
            <SlidersHorizontal size={12} aria-hidden="true" />
            {t('compute.manage', { defaultValue: 'Remote worker settings' })}
          </button>
        </div>
      )}
    </div>
  );
}
