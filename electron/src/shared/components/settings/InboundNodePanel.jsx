/**
 * Settings → System → Remote workers → the "other direction" half.
 *
 * The default setup has the GPU machine dial this app, which is right when the
 * machine is yours alone — but it connects to exactly one app, so sharing a GPU
 * means editing its settings, restarting, and disconnecting whoever had it.
 * This is the arrangement where the GPU machine listens instead and several
 * people connect to it at once.
 *
 * Two things here are the feature's contract and must not be softened:
 *
 *   • The connection string is shown exactly once. Only its hash is stored,
 *     so it cannot be displayed again — that is the point, not a limitation.
 *   • Every connection uses TLS and pins the node certificate carried in the
 *     one-time connection string. There is no plaintext fallback.
 */
import React, { useState } from 'react';
import { Link2, LogOut, Trash2, Wifi } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { askConfirm } from '../../utils/dialog';
import OneTimeSecret from '../OneTimeSecret';
import { SettingRow, SettingsSection, SettingsToggle } from './primitives';
import { Badge, Button } from '../../ui';

const REFRESH_MS = 5000;

function failureMessage(error, t) {
  if (error?.isServerMessage && typeof error.message === 'string' && error.message.trim()) {
    return error.message;
  }
  return t('settings.inbound_request_failed', {
    defaultValue:
      'Could not reach that GPU machine. Check that it is online and accepting connections, then try again.',
  });
}

function relative(seconds, t) {
  if (!seconds) return '';
  const mins = Math.max(0, Math.round((Date.now() / 1000 - seconds) / 60));
  if (mins < 1) return t('settings.inbound_just_now', { defaultValue: 'just now' });
  if (mins < 60)
    return t('settings.inbound_minutes_ago', { defaultValue: '{{count}}m ago', count: mins });
  return t('settings.inbound_hours_ago', {
    defaultValue: '{{count}}h ago',
    count: Math.round(mins / 60),
  });
}

export default function InboundNodePanel({ request }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [issued, setIssued] = useState(null);
  const [paste, setPaste] = useState('');
  const [label, setLabel] = useState('');
  const [bind, setBind] = useState('');
  const [busy, setBusy] = useState(false);

  const { data } = useQuery({
    queryKey: ['workers', 'inbound'],
    queryFn: () => request('/workers/inbound'),
    refetchInterval: REFRESH_MS,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['workers'] });
  const keys = (data?.keys || []).filter((k) => !k.revoked);
  const sessions = data?.sessions || [];
  const connections = data?.connections || [];

  const guarded = async (fn) => {
    setBusy(true);
    try {
      await fn();
      refresh();
    } catch (err) {
      toast.error(failureMessage(err, t));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (next) =>
    guarded(async () => {
      await request('/workers/inbound/enabled', {
        method: 'POST',
        body: { enabled: next, bind: bind || undefined },
      });
      if (!next) setIssued(null);
    });

  const issue = () =>
    guarded(async () => {
      const result = await request('/workers/inbound/keys', {
        method: 'POST',
        body: { label: label.trim() },
      });
      setIssued(result);
      setLabel('');
    });

  const revoke = (key) =>
    guarded(async () => {
      const ok = await askConfirm(
        t('settings.inbound_revoke_confirm', {
          defaultValue:
            'Remove access for {{label}}? Their connection string stops working immediately. Everyone else stays connected.',
          label: key.label,
        }),
        t('settings.inbound_revoke_title', { defaultValue: 'Remove access?' }),
      );
      if (!ok) return;
      await request(`/workers/inbound/keys/${key.key_id}`, { method: 'DELETE' });
    });

  const disconnect = (session) =>
    guarded(() =>
      request(`/workers/inbound/sessions/${session.session_id}/disconnect`, { method: 'POST' }),
    );

  const connect = () =>
    guarded(async () => {
      await request('/workers/inbound/connections', {
        method: 'POST',
        body: { connection_string: paste.trim() },
      });
      setPaste('');
    });

  const forget = (row) =>
    guarded(() =>
      request(`/workers/inbound/connections/${encodeURIComponent(row.endpoint)}`, {
        method: 'DELETE',
      }),
    );

  return (
    <>
      <SettingsSection
        icon={Wifi}
        title={t('settings.inbound_title', { defaultValue: 'Share this GPU with other people' })}
        description={t('settings.inbound_desc', {
          defaultValue:
            'Let other machines connect to this one so several people can share its GPU. Every connection is encrypted and pinned to this machine’s certificate.',
        })}
      >
        <SettingRow
          title={t('settings.inbound_enable', { defaultValue: 'Accept connections' })}
          subtitle={t('settings.inbound_enable_hint', {
            defaultValue:
              'Other people connect to this machine instead of it connecting to them. Nobody gets in without a connection string you create.',
          })}
          control={<SettingsToggle checked={!!data?.enabled} disabled={busy} onChange={toggle} />}
        />

        {data?.startup_error ? (
          <p className="text-sm text-red-500" aria-live="polite">
            {data.startup_error}
          </p>
        ) : null}

        {data?.enabled ? (
          <>
            <SettingRow
              title={t('settings.inbound_bind', { defaultValue: 'Reachable from' })}
              subtitle={
                data?.exposed
                  ? t('settings.inbound_bind_exposed', {
                      defaultValue:
                        'Anyone who can reach {{address}} can connect with a valid connection string. TLS encrypts the session and pins this machine’s certificate; keep each string private and remove access when it is no longer needed.',
                      address: `${data?.bind}:${data?.port}`,
                    })
                  : t('settings.inbound_bind_local', {
                      defaultValue:
                        'Only this machine can reach it. Enter your network address to let other machines connect.',
                    })
              }
              control={
                <div className="flex items-center gap-2">
                  <input
                    className="input w-44"
                    value={bind}
                    placeholder={data?.bind || '127.0.0.1'}
                    onChange={(e) => setBind(e.target.value)}
                    name="inbound-bind-address"
                    autoComplete="off"
                    spellCheck={false}
                    aria-label={t('settings.inbound_bind', { defaultValue: 'Reachable from' })}
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy || !bind}
                    onClick={() => toggle(true)}
                  >
                    {t('settings.inbound_bind_apply', { defaultValue: 'Apply' })}
                  </Button>
                  {data?.exposed ? (
                    <Badge tone="warn">
                      {t('settings.inbound_exposed_badge', { defaultValue: 'On your network' })}
                    </Badge>
                  ) : null}
                </div>
              }
            />

            <SettingRow
              title={t('settings.inbound_add_person', { defaultValue: 'Add a person' })}
              subtitle={t('settings.inbound_add_person_hint', {
                defaultValue:
                  'Creates a connection string for one person. Give each person their own, so removing one does not disconnect everybody.',
              })}
              control={
                <div className="flex items-center gap-2">
                  <input
                    className="input w-44"
                    value={label}
                    placeholder={t('settings.inbound_label_placeholder', {
                      defaultValue: "Alice's laptop",
                    })}
                    onChange={(e) => setLabel(e.target.value)}
                    name="inbound-person-label"
                    autoComplete="off"
                    aria-label={t('settings.inbound_label', { defaultValue: 'Name' })}
                  />
                  <Button size="sm" disabled={busy} onClick={issue}>
                    {t('settings.inbound_create', { defaultValue: 'Create' })}
                  </Button>
                </div>
              }
            />

            {issued ? (
              <OneTimeSecret
                value={issued.connection_string}
                onDone={() => setIssued(null)}
                headline={t('settings.inbound_shown_once', {
                  defaultValue: 'Copy this now — it is not shown again. Give it only to {{label}}.',
                  label: issued.label,
                })}
                note={t('settings.inbound_shown_once_warning', {
                  defaultValue:
                    'Treat it like a password and send it privately. Its certificate fingerprint pins the encrypted connection to this GPU machine.',
                })}
              />
            ) : null}

            {keys.length ? (
              <div className="space-y-1">
                <p className="text-xs uppercase opacity-60">
                  {t('settings.inbound_people', { defaultValue: 'People with access' })}
                </p>
                {keys.map((key) => (
                  <div key={key.key_id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">
                      {key.label}
                      {key.last_seen_at ? (
                        <span className="opacity-60">
                          {' · '}
                          {t('settings.inbound_last_seen', {
                            defaultValue: 'last used {{when}}',
                            when: relative(key.last_seen_at, t),
                          })}
                        </span>
                      ) : null}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      leading={<Trash2 size={14} />}
                      disabled={busy}
                      onClick={() => revoke(key)}
                    >
                      {t('settings.inbound_revoke', { defaultValue: 'Remove' })}
                    </Button>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="space-y-1">
              <p className="text-xs uppercase opacity-60">
                {t('settings.inbound_connected_now', { defaultValue: 'Connected right now' })}
              </p>
              {sessions.length === 0 ? (
                <p className="text-sm opacity-60">
                  {t('settings.inbound_nobody', { defaultValue: 'Nobody is connected.' })}
                </p>
              ) : (
                sessions.map((session) => (
                  <div
                    key={session.session_id}
                    className="flex items-center justify-between gap-2 text-sm"
                  >
                    <span className="truncate">
                      {session.label}
                      <span className="opacity-60">
                        {' · '}
                        {session.peer}
                        {' · '}
                        {t('settings.inbound_jobs_run', {
                          defaultValue: '{{count}} jobs',
                          count: session.tasks_run,
                        })}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      leading={<LogOut size={14} />}
                      disabled={busy}
                      onClick={() => disconnect(session)}
                    >
                      {t('settings.inbound_disconnect', { defaultValue: 'Disconnect' })}
                    </Button>
                  </div>
                ))
              )}
            </div>
          </>
        ) : null}
      </SettingsSection>

      <SettingsSection
        icon={Link2}
        title={t('settings.inbound_connect_title', { defaultValue: 'Connect to a GPU machine' })}
        description={t('settings.inbound_connect_desc', {
          defaultValue:
            'Paste the connection string someone gave you. The machine has to be accepting connections for this to work.',
        })}
      >
        <SettingRow
          title={t('settings.inbound_connect_row', { defaultValue: 'Connection string' })}
          subtitle={t('settings.inbound_connect_hint', {
            defaultValue:
              'Starts with ovnode:// and includes the key, address, port and certificate fingerprint.',
          })}
          control={
            <div className="flex items-center gap-2">
              <input
                className="input w-72"
                value={paste}
                placeholder={t('settings.inbound_connect_placeholder', {
                  defaultValue: 'ovnode://…@192.168.0.110:7444?fingerprint=…',
                })}
                onChange={(e) => setPaste(e.target.value)}
                name="inbound-connection-string"
                autoComplete="off"
                spellCheck={false}
                translate="no"
                aria-label={t('settings.inbound_connect_row', {
                  defaultValue: 'Connection string',
                })}
              />
              <Button size="sm" disabled={busy || !paste.trim()} onClick={connect}>
                {t('settings.inbound_connect', { defaultValue: 'Connect' })}
              </Button>
            </div>
          }
        />

        {connections.map((row) => (
          <div key={row.endpoint} className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate">
              {row.endpoint}
              <span className="opacity-60">
                {' · '}
                {row.connected
                  ? t('settings.inbound_connected', { defaultValue: 'connected' })
                  : row.last_error ||
                    t('settings.inbound_connecting', { defaultValue: 'connecting…' })}
              </span>
            </span>
            <Button
              size="sm"
              variant="ghost"
              leading={<Trash2 size={14} />}
              disabled={busy}
              onClick={() => forget(row)}
            >
              {t('settings.inbound_forget', { defaultValue: 'Remove' })}
            </Button>
          </div>
        ))}
      </SettingsSection>
    </>
  );
}
