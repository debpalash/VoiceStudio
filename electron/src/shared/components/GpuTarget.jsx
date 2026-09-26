/**
 * Header GPU picker — where the next job runs.
 *
 * Exactly one target is active at a time: this machine, or one worker you
 * enrolled. Other connected workers are standby and receive nothing.
 *
 * The badge shows the **resolved** answer, not the stored choice, and those
 * differ in the case that matters: you picked your desktop, your desktop went
 * to sleep, and the work is now running locally. Showing the choice there
 * would be a lie every time it mattered most — so the chip reads "Local" with
 * the reason underneath, while the menu still shows your desktop as selected.
 *
 * `Local` has no rename control. It is not a machine — it is this machine —
 * and there is nothing to name.
 *
 * The answer is also per operation, because a worker is not remote for
 * everything: work reaches a worker only where this side has a producer for
 * it, and those are ported one at a time. Without that, the badge would read
 * "gpu2 ● ready" on the Dub, Audiobook and Transcripts tabs while 100% of
 * that work runs here — the same lie the resolved-answer rule exists to
 * prevent, in a place the user cannot even see it happen.
 */
import React, { useCallback, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Cpu, Check, ChevronDown } from 'lucide-react';
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

// Two cadences: a slow tick that just keeps status honest, and a fast one
// while work is in flight so the task count reads as live rather than lagging
// several seconds behind the thing the user is watching.
const IDLE_REFRESH_MS = 5000;
const BUSY_REFRESH_MS = 1000;

/**
 * What the workspace in front of the user actually submits.
 *
 * A workspace that submits no GPU job of its own — the launchpad, Settings,
 * the gallery, OmniDrive — maps to no operation, which asks about the target
 * itself rather than about one job. That is exactly what the menu wants when
 * you open it from anywhere to pick a machine.
 *
 * Ids are the control plane's (`worker/routing.py`), not the UI's: `mode` is
 * a navigation id and these are units of work, so `studio` and the legacy
 * `clone`/`design` modes all submit `tts`.
 */
const OP_BY_MODE = {
  generate: 'tts',
  studio: 'tts',
  clone: 'tts',
  design: 'tts',
  dub: 'dub',
  audiobook: 'audiobook',
  stories: 'longform',
  transcriptions: 'asr',
  dictation: 'dictation',
};

export default function GpuTarget() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  // The header's status row is `overflow-hidden`, which clips an absolutely
  // positioned menu — the entries below the first simply vanish. Rendering
  // into a portal and positioning from the button's rect escapes the clip.
  const buttonRef = useRef(null);
  const [anchor, setAnchor] = useState(null);

  const toggle = useCallback(() => {
    setOpen((wasOpen) => {
      if (!wasOpen && buttonRef.current) {
        const rect = buttonRef.current.getBoundingClientRect();
        setAnchor({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
      }
      return !wasOpen;
    });
  }, []);

  // The surface being rendered, not the machine — see OP_BY_MODE. Part of the
  // query key so switching tabs re-resolves instead of showing the previous
  // tab's answer until the next poll.
  const op = OP_BY_MODE[useAppStore((s) => s.mode)] || '';

  const { data } = useQuery({
    queryKey: ['workers', 'target', op],
    queryFn: () => request(op ? `/workers/target?op=${encodeURIComponent(op)}` : '/workers/target'),
    refetchInterval: (query) => {
      const t = (query.state?.data?.targets || []).find((x) => x.id === query.state?.data?.target);
      return t && t.active_tasks > 0 ? BUSY_REFRESH_MS : IDLE_REFRESH_MS;
    },
    refetchIntervalInBackground: false,
    retry: false,
  });

  const targets = data?.targets || [];
  const active = data?.active;
  const chosen = data?.target || 'local';

  // Nothing to choose between: with no worker enrolled, a GPU picker is just
  // clutter on a feature the user has not opted into.
  if (targets.length <= 1) return null;

  const chosenTarget = targets.find((x) => x.id === chosen);
  const activeTarget = active?.remote
    ? targets.find((x) => x.id === active.worker_id)
    : targets.find((x) => x.is_local);
  const label = active?.remote ? active.label : t('gpu.local', { defaultValue: 'Local' });

  // What a worker can be sent at all. Absent (an older control plane, or a
  // response that predates op-awareness) means "don't claim anything" — the
  // coverage line and the unported reason simply do not render.
  const remoteOps = data?.remote_operations || [];
  const opLabel = (id) => t(`gpu.ops.${id}`, { defaultValue: id });
  // Chosen a worker, on a surface nothing would ever send it. Not a failure
  // and not the worker's fault, so it is deliberately NOT `fellBack`: no
  // amber, because there is nothing wrong to warn about.
  const opIsLocalOnly =
    Boolean(op) && chosen !== 'local' && remoteOps.length > 0 && !remoteOps.includes(op);
  const coverage = remoteOps.length
    ? t('gpu.coverage', {
        ops: remoteOps.map(opLabel).join(', '),
        defaultValue: '{{ops}} only',
      })
    : '';
  const reason = opIsLocalOnly
    ? op === 'dictation'
      ? t('gpu.dictationLocal', { defaultValue: 'Dictation always runs on this machine' })
      : t('gpu.opLocal', {
          op: opLabel(op),
          defaultValue: 'Local — {{op}} does not run remotely yet',
        })
    : active?.reason || '';
  const fellBack = !active?.remote && chosen !== 'local' && !opIsLocalOnly;
  // When the chosen worker is unreachable the work runs here, but the DOT must
  // report the worker's state — a green dot beside "Local" would hide that
  // the machine you picked is down. Same on an unported surface: the machine
  // is fine, and the reason line is what says why it is idle.
  const dotStatus =
    fellBack || opIsLocalOnly ? chosenTarget?.status || 'offline' : activeTarget?.status || 'ready';
  const chipLatency = latencyLabel(active?.remote ? activeTarget : null);

  const choose = async (id) => {
    setOpen(false);
    try {
      const next = await request('/workers/target', { method: 'POST', body: { target: id } });
      // POST answers for the target as a whole. Writing that into an
      // op-scoped cache entry would paint "gpu2 ● ready" on a tab whose work
      // is local until the next poll corrected it — so it seeds the cache
      // only where the two questions are the same one, and the invalidate
      // below refreshes the rest.
      if (!op) queryClient.setQueryData(['workers', 'target', ''], next);
      queryClient.invalidateQueries({ queryKey: ['workers'] });
    } catch (e) {
      toast.error(e?.message || String(e));
    }
  };

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        title={reason || undefined}
        aria-label={t('gpu.picker', { defaultValue: 'Where jobs run' })}
        className="inline-flex items-center gap-1.5 rounded-[5px] border-0 bg-transparent px-2 py-1 text-xs [font-family:inherit] [color:inherit] cursor-pointer opacity-75 hover:bg-[color-mix(in_srgb,var(--chrome-fg)_8%,transparent)] hover:opacity-100"
      >
        <Cpu size={13} />
        <StatusDot status={dotStatus} />
        <span className={fellBack ? FELL_BACK_TEXT : undefined}>{label}</span>
        {chipLatency && <span className="opacity-60">{chipLatency}</span>}
        <ChevronDown size={11} />
      </button>

      {open &&
        anchor &&
        createPortal(
          <>
            <div className="fixed inset-0 z-[9998]" onClick={() => setOpen(false)} />
            <div
              data-slot="gpu-target-menu"
              style={{ top: anchor.top, right: anchor.right }}
              className={`fixed z-[9999] min-w-[240px] ${MENU_SURFACE}`}
            >
              {targets.map((target) => (
                // Any enrolled worker is selectable, including an offline one:
                // you pick your desktop and then go and switch it on. Routing
                // already falls back locally with a reason until it answers, so
                // forbidding the choice would only prevent setting it up.
                <button
                  key={target.id}
                  type="button"
                  onClick={() => choose(target.id)}
                  className={`${MENU_ITEM} text-xs ${target.available ? '' : 'opacity-60'}`}
                >
                  <span className="w-3">{target.id === chosen ? <Check size={12} /> : null}</span>
                  {target.is_local ? (
                    <span className="w-[6px]" />
                  ) : (
                    <StatusDot status={target.status} />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate">{target.label}</span>
                      {latencyLabel(target) && (
                        <span className="shrink-0 opacity-60">{latencyLabel(target)}</span>
                      )}
                    </span>
                    {/* The address disambiguates two machines a user named
                      similarly; the detail says why one cannot be picked; the
                      task count is what makes "busy" mean something; the
                      coverage is what stops the entry from implying the
                      machine takes everything. Coverage is dropped while the
                      worker is unusable — "offline · TTS only" answers a
                      question the user is not asking yet. */}
                    {!target.is_local && (
                      <span className="block truncate opacity-60">
                        {target.detail
                          ? target.detail
                          : [
                              target.active_tasks > 0
                                ? `${target.endpoint} · ${target.active_tasks}/${target.max_tasks} ${t(
                                    'gpu.tasks',
                                    { defaultValue: 'tasks' },
                                  )}`
                                : target.endpoint,
                              coverage,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                      </span>
                    )}
                  </span>
                </button>
              ))}
              {reason && (fellBack || opIsLocalOnly) && (
                <p
                  className={`m-0 px-2 py-1 text-[11px] ${fellBack ? FELL_BACK_TEXT : 'opacity-60'}`}
                >
                  {reason}
                </p>
              )}
            </div>
          </>,
          document.body,
        )}
    </div>
  );
}
