/**
 * Settings → System → Remote workers.
 *
 * Run inference on your other machines. Its own System entry rather than a
 * section under Sharing, because the direction is opposite: everything in
 * Sharing is about letting something else reach THIS machine, while this
 * sends work OUT to machines you own and brings the results back.
 *
 * Also distinct from the Remote backend panel under Sharing: that one points
 * this app at a backend running elsewhere, so the work and the data both live
 * there. This keeps the app here and hands out individual tasks.
 *
 * Two rules the UI must not soften, because they are the feature's contract:
 *
 *   • Off means off. With the toggle off there is no listening socket, no
 *     certificate, and no background loop — the app is what it was before.
 *   • Every worker is consented to individually. Audio, reference voices, and
 *     text leave this machine for a worker, so "I trust my desktop" is not
 *     "I trust whatever else gets added later".
 *
 * The enrollment token is shown exactly once. Only its hash is stored, so
 * there is no way to display it again — that is the point, not a limitation.
 * It is handed over as a QR as well as text (see <OneTimeSecret/>), because
 * the machine that has to receive it is by definition not the machine holding
 * the clipboard.
 *
 * The list is a device list, not a table of settings: each row answers "can I
 * use this machine right now, and if not why" — status, address, latency, how
 * loaded it is — and carries the actions for that one machine. Approval lives
 * here too: a worker that connected but has never been approved is inert, and
 * a panel that only labels that state without offering the yes is a dead end.
 */
import React, { useState } from 'react';
import { Cpu, Check, Trash2, PlayCircle, Pencil, ShieldCheck } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '../../api/client';
import { askConfirm } from '../../utils/dialog';
import OneTimeSecret from '../OneTimeSecret';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';
import { Button, Badge } from '../../ui';
import InboundNodePanel from './InboundNodePanel';
import JoinWorkerPanel from './JoinWorkerPanel';

const REFRESH_MS = 5000;

/**
 * `apiFetch` deliberately returns the raw Response and sets no Content-Type —
 * it preserves the call shape so FormData posts keep working. Every JSON
 * caller therefore has to say so itself and parse the body, and a non-2xx is
 * NOT an exception, so an unchecked call fails silently.
 *
 * This wrapper does all three in one place, and surfaces FastAPI's `detail`
 * so the user sees "Remote workers are turned off…" instead of "500".
 */
async function request(path, { body, ...opts } = {}) {
  const res = await apiFetch(path, {
    ...opts,
    ...(body === undefined
      ? {}
      : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload?.detail;
    const error = new Error(
      typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `HTTP ${res.status}`,
    );
    error.isServerMessage = typeof detail === 'string' && detail.trim().length > 0;
    throw error;
  }
  return payload;
}

/** Latency only means something for a machine across a network. */
function latencyLabel(worker) {
  const ms = worker.connected ? worker.latency_ms : 0;
  // 0 is "not measured yet", not "instantaneous" — say nothing rather than
  // claim a suspiciously perfect link.
  if (!ms) return '';
  return ms < 1 ? '<1 ms' : `${Math.round(ms)} ms`;
}

function relativeSeen(seconds, t) {
  if (!seconds) return '';
  const mins = Math.max(0, Math.round(Date.now() / 1000 - seconds) / 60);
  if (mins < 1) return t('settings.workers_seen_now', { defaultValue: 'just now' });
  if (mins < 60)
    return t('settings.workers_seen_min', {
      defaultValue: '{{count}}m ago',
      count: Math.round(mins),
    });
  return t('settings.workers_seen_hr', {
    defaultValue: '{{count}}h ago',
    count: Math.round(mins / 60),
  });
}

export default function WorkersPanel() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [token, setToken] = useState(null);
  const [busy, setBusy] = useState(false);

  const { data } = useQuery({
    queryKey: ['workers'],
    queryFn: () => request('/workers'),
    // Only poll once the feature is on: a disabled panel should not generate
    // background traffic every five seconds forever.
    refetchInterval: (query) => (query.state?.data?.running ? REFRESH_MS : false),
    refetchIntervalInBackground: false,
  });

  const enabled = Boolean(data?.enabled);
  const workers = data?.workers || [];
  const online = workers.filter((w) => w.connected && w.enabled).length;

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['workers'] });

  /** Every mutation here is the same shape: run it, refresh, surface why not. */
  const guarded = async (fn) => {
    try {
      await fn();
      refresh();
    } catch (e) {
      toast.error(e?.message || String(e));
    }
  };

  const setEnabled = async (next) => {
    setBusy(true);
    await guarded(async () => {
      await request('/workers/enabled', { method: 'POST', body: { enabled: next } });
      if (!next) setToken(null);
    });
    setBusy(false);
  };

  const createToken = async () => {
    setBusy(true);
    try {
      setToken(
        await request('/workers/enrollments', { method: 'POST', body: { ttl_seconds: 900 } }),
      );
    } catch (e) {
      toast.error(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const removeWorker = async (worker) => {
    const ok = await askConfirm(
      t('settings.workers_remove_confirm', {
        name: worker.name,
        defaultValue:
          'Remove {{name}}? Its key is revoked, so it cannot reconnect without a new token.',
      }),
      t('settings.workers_remove_title', { defaultValue: 'Remove worker?' }),
    );
    if (!ok) return;
    await guarded(() => request(`/workers/${worker.id}`, { method: 'DELETE' }));
  };

  const resumeWorker = (worker) =>
    guarded(() => request(`/workers/${worker.id}/resume`, { method: 'POST' }));

  const approveWorker = (worker) =>
    guarded(() => request(`/workers/${worker.id}/consent`, { method: 'POST' }));

  const renameWorker = (worker, name) => {
    const trimmed = (name || '').trim();
    // An empty name would leave the row labelled by its key id, which is not
    // something a user can recognise — treat it as "keep the current name".
    if (!trimmed || trimmed === worker.name) return undefined;
    return guarded(() =>
      request(`/workers/${worker.id}`, { method: 'PATCH', body: { name: trimmed } }),
    );
  };

  const toggleWorker = (worker) =>
    guarded(() =>
      request(`/workers/${worker.id}`, { method: 'PATCH', body: { enabled: !worker.enabled } }),
    );

  return (
    <>
      <SettingsSection
        icon={Cpu}
        title={t('settings.workers_title', { defaultValue: 'Remote workers' })}
        description={t('settings.workers_desc', {
          defaultValue:
            'Send individual jobs to GPUs on your other machines. Results come back here. Nothing is sent until you add a worker and approve it.',
        })}
        actions={
          enabled && data?.running ? (
            <Badge tone={online > 0 ? 'success' : 'neutral'} dot>
              {online > 0
                ? t('settings.workers_summary_online', {
                    defaultValue: '{{count}} online',
                    count: online,
                  })
                : t('settings.workers_summary_none', { defaultValue: 'Nobody connected' })}
            </Badge>
          ) : null
        }
      >
        <SettingRow
          title={t('settings.workers_enable', { defaultValue: 'Use remote workers' })}
          subtitle={t('settings.workers_enable_hint', {
            defaultValue:
              'While this is off, no connection is accepted and nothing leaves this machine.',
          })}
          control={<SettingsToggle checked={enabled} disabled={busy} onChange={setEnabled} />}
        />

        {enabled && !data?.running && data?.startup_error && (
          <p role="alert" className="rounded-lg bg-red-500/5 p-3 text-sm text-red-300">
            {t('settings.workers_port_conflict', {
              defaultValue:
                'Remote workers are unavailable because another VoiceStudio instance is already accepting them on this port. Close the other instance, or set OMNIVOICE_WORKER_PORT to a different port and restart VoiceStudio.',
            })}
          </p>
        )}

        {enabled && data?.running && (
          <>
            <SettingRow
              mono
              title={t('settings.workers_endpoint', { defaultValue: 'Workers connect to' })}
              subtitle={t('settings.workers_endpoint_hint', {
                defaultValue:
                  'A worker has to be able to reach this address. On different networks, a VPN such as Tailscale is the reliable way.',
              })}
              control={<code>{data?.endpoint || '—'}</code>}
            />

            <SettingRow
              title={t('settings.workers_add', { defaultValue: 'Add a worker' })}
              subtitle={t('settings.workers_add_hint_qr', {
                defaultValue:
                  'Generate a token, then scan the QR from the other machine or paste the code into its Remote workers settings.',
              })}
              control={
                <Button variant="primary" onClick={createToken} disabled={busy}>
                  {t('settings.workers_new_token', { defaultValue: 'Generate token' })}
                </Button>
              }
            />

            {token && (
              <OneTimeSecret
                value={token.token}
                expiresAt={token.expires_at}
                onDone={() => setToken(null)}
                headline={t('settings.workers_token_once', {
                  defaultValue:
                    'Copy this now — it is shown only once, works only once, and expires in 15 minutes.',
                })}
                note={t('settings.workers_token_qr_hint', {
                  defaultValue:
                    'On the other machine: Settings → System → Remote workers → Join, then scan or paste.',
                })}
              />
            )}

            {workers.length === 0 ? (
              <EmptyWorkers />
            ) : (
              <ul className="m-0 list-none divide-y divide-white/5 p-0">
                {workers.map((w) => (
                  <WorkerRow
                    key={w.id}
                    worker={w}
                    onRemove={() => removeWorker(w)}
                    onResume={() => resumeWorker(w)}
                    onToggle={() => toggleWorker(w)}
                    onApprove={() => approveWorker(w)}
                    onRename={(name) => renameWorker(w, name)}
                  />
                ))}
              </ul>
            )}
          </>
        )}
      </SettingsSection>
      {/* The other direction: this machine accepting connections, and the
        machines this one dials. Kept in the same System entry because a user
        looking for "use my other GPU" should find both ways in one place, and
        behind the same master toggle because "off means off" is this feature's
        contract — a second switch that stayed live under it would be exactly
        the surprise that promise exists to prevent. Headless nodes that only
        lend a GPU set OMNIVOICE_INBOUND_NODE and never see this panel. */}
      {enabled && <JoinWorkerPanel request={request} />}
      {enabled && <InboundNodePanel request={request} />}
    </>
  );
}

/**
 * The empty state carries the three steps rather than one sentence, because
 * "no workers yet" is the moment the user needs the instructions — not the
 * moment to send them to the docs.
 */
function EmptyWorkers() {
  const { t } = useTranslation();
  const steps = [
    t('settings.workers_step_1', {
      defaultValue: 'Install VoiceStudio on the machine with the GPU.',
    }),
    t('settings.workers_step_2', { defaultValue: 'Generate a token above.' }),
    t('settings.workers_step_3', {
      defaultValue: 'Scan the QR there, or paste the code into its Remote workers settings.',
    }),
  ];
  return (
    <div className="py-3">
      <p className="m-0 text-sm opacity-70">
        {t('settings.workers_none', {
          defaultValue: 'No workers yet. Generate a token to add your first one.',
        })}
      </p>
      <ol className="mt-2 mb-0 list-none space-y-1.5 p-0">
        {steps.map((step, i) => (
          <li key={step} className="flex items-start gap-2 text-xs opacity-70">
            <span className="mt-[1px] inline-flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded-full bg-white/8 text-[10px] tabular-nums">
              {i + 1}
            </span>
            <span>{step}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// Disable / Remove are housekeeping: present for every row forever, needed
// rarely. They fade in on hover or when anything inside takes focus — never
// display:none, so they stay reachable by keyboard and to assistive tech.
const SECONDARY_ACTIONS =
  'flex items-center gap-1.5 opacity-0 transition-opacity duration-150 ' +
  'group-hover:opacity-100 focus-within:opacity-100';

/** Filled proportion of a worker's task slots, for the load meter. */
function loadFraction(worker) {
  const active = worker.active_tasks ?? 0;
  const slots = active + (worker.available_slots ?? 0);
  return slots > 0 ? Math.min(1, active / slots) : 0;
}

export function WorkerRow({
  worker,
  onRemove,
  onResume,
  onToggle,
  onApprove = () => {},
  onRename = () => {},
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(worker.name);
  const paused = (worker.breakers || []).length > 0;
  const approved = worker.consent_granted !== false;

  const commit = () => {
    setEditing(false);
    onRename(draft);
  };
  const status = !worker.enabled
    ? t('settings.workers_status_disabled', { defaultValue: 'Disabled' })
    : paused
      ? t('settings.workers_status_paused', { defaultValue: 'Paused' })
      : worker.connected
        ? t('settings.workers_status_online', { defaultValue: 'Online' })
        : t('settings.workers_status_offline', { defaultValue: 'Offline' });
  const healthy = worker.connected && worker.enabled && !paused;

  // The second line is the machine's identity and freshness: which box this
  // actually is, and whether what the row says is current. Latency and load
  // live on the right of the first line, where the eye already is.
  const address = worker.address || worker.endpoint || worker.host || '';
  const seen = !worker.connected ? relativeSeen(worker.last_seen_at, t) : '';
  const meta = [
    address,
    seen && t('settings.workers_last_seen', { defaultValue: 'last seen {{when}}', when: seen }),
    (worker.resident_models || []).slice(0, 3).join(', '),
  ].filter(Boolean);

  return (
    <li className="group flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {/* The dot rides with the name, not with the row: aligned to the
              row's centre it lands beside the address line and reads as a
              bullet for the wrong sentence. */}
          <span
            aria-hidden="true"
            className={`h-[7px] w-[7px] shrink-0 rounded-full ${
              !worker.enabled
                ? 'bg-white/25'
                : paused
                  ? 'bg-amber-400'
                  : worker.connected
                    ? 'bg-emerald-400'
                    : 'bg-red-400'
            }`}
          />
          {editing ? (
            <input
              autoFocus
              aria-label={t('settings.workers_rename', { defaultValue: 'Rename worker' })}
              className="min-w-0 flex-1 rounded bg-black/20 px-2 py-0.5 text-sm"
              value={draft}
              maxLength={120}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commit();
                if (e.key === 'Escape') {
                  setDraft(worker.name);
                  setEditing(false);
                }
              }}
            />
          ) : (
            <>
              <span className="truncate font-medium">{worker.name}</span>
              <button
                type="button"
                className="border-0 bg-transparent p-0 opacity-50 cursor-pointer hover:opacity-100"
                aria-label={t('settings.workers_rename', { defaultValue: 'Rename worker' })}
                onClick={() => {
                  setDraft(worker.name);
                  setEditing(true);
                }}
              >
                <Pencil size={12} />
              </button>
            </>
          )}
          <Badge tone={healthy ? 'success' : paused ? 'warn' : 'neutral'}>{status}</Badge>
          {!approved && (
            <Badge tone="warn">
              {t('settings.workers_needs_consent', { defaultValue: 'Not approved' })}
            </Badge>
          )}
          {latencyLabel(worker) && (
            <span className="text-[11px] tabular-nums opacity-60">{latencyLabel(worker)}</span>
          )}
        </div>

        {meta.length > 0 && (
          <p className="mt-0.5 m-0 truncate font-mono text-[11px] opacity-50">{meta.join(' · ')}</p>
        )}

        {worker.connected && (
          <div className="mt-1 flex items-center gap-2">
            {/* A meter says "busy" at a glance in a way a fraction never does,
                but the fraction stays — it is the number the user quotes when
                something is queued. */}
            <span
              className="h-[3px] w-[64px] overflow-hidden rounded-full bg-white/10"
              aria-hidden="true"
            >
              <span
                className="block h-full rounded-full bg-current opacity-70"
                style={{ width: `${Math.round(loadFraction(worker) * 100)}%` }}
              />
            </span>
            <span className="text-xs opacity-70">
              {t('settings.workers_load', {
                active: worker.active_tasks ?? 0,
                slots: (worker.active_tasks ?? 0) + (worker.available_slots ?? 0),
                defaultValue: 'Tasks {{active}} / {{slots}}',
              })}
            </span>
          </div>
        )}

        {/* The breaker summary is written to be understood: "paused after 3
            failures, retrying in 60s" is actionable in a way that a
            reliability percentage never is. */}
        {paused && (
          <p className="mt-0.5 m-0 text-xs text-amber-400">{worker.breakers[0].summary}</p>
        )}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        {/* Approval is the one action the panel used to name without offering.
            A worker can connect, sit there labelled "Not approved", and never
            be usable — so the yes belongs on the row that raises it. Approve
            and Resume stay visible because they are the row's whole point when
            they appear; the housekeeping pair fades in with the row so four
            machines do not read as twelve buttons. */}
        {!approved && (
          <Button size="sm" leading={<ShieldCheck size={14} />} onClick={onApprove}>
            {t('settings.workers_approve', { defaultValue: 'Approve' })}
          </Button>
        )}
        {paused && (
          <Button variant="ghost" size="sm" leading={<PlayCircle size={14} />} onClick={onResume}>
            {t('settings.workers_resume', { defaultValue: 'Resume' })}
          </Button>
        )}
        <div className={SECONDARY_ACTIONS}>
          <Button
            variant="ghost"
            size="sm"
            leading={worker.enabled ? null : <Check size={14} />}
            onClick={onToggle}
          >
            {worker.enabled
              ? t('settings.workers_disable', { defaultValue: 'Disable' })
              : t('settings.workers_enable_one', { defaultValue: 'Enable' })}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-[color:var(--color-danger)]"
            leading={<Trash2 size={14} />}
            onClick={onRemove}
          >
            {t('settings.workers_remove', { defaultValue: 'Remove' })}
          </Button>
        </div>
      </div>
    </li>
  );
}
