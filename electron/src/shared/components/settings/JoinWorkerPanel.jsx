/**
 * Settings → System → Remote workers → "Lend this machine's GPU".
 *
 * The receiving half of enrollment, and the half that had no UI at all: a
 * machine could only become a worker by launching with OMNIVOICE_WORKER_MODE
 * and OMNIVOICE_WORKER_TOKEN in its environment. So the control plane happily
 * minted join codes that, on the other machine, had nowhere to go — the panel
 * said "paste it into VoiceStudio on the other machine" and there was nothing
 * to paste it into. This is that box.
 *
 * Sits under the same master toggle as the rest of Remote workers because "off
 * means off" is the feature's contract, and dialling out is exactly the kind of
 * traffic that promise covers.
 *
 * Two states worth keeping distinct, because the next action differs:
 *   • never joined — needs a code;
 *   • joined but stopped — needs a switch, not another code. The pinned
 *     certificate survives, so resuming asks for nothing.
 */
import React, { useState } from 'react';
import { HardDriveDownload, LogIn } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { SettingRow, SettingsSection, SettingsToggle } from './primitives';
import { Badge, Button } from '../../ui';

const REFRESH_MS = 5000;

export default function JoinWorkerPanel({ request }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);

  const { data } = useQuery({
    queryKey: ['workers', 'agent'],
    queryFn: () => request('/workers/agent'),
    refetchInterval: REFRESH_MS,
    refetchIntervalInBackground: false,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['workers'] });
  const joined = Boolean(data?.enrolled);
  const running = Boolean(data?.running);

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

  const join = () =>
    guarded(async () => {
      await request('/workers/agent/join', { method: 'POST', body: { token: code.trim() } });
      setCode('');
      toast.success(
        t('settings.worker_join_ok', { defaultValue: 'Joined. This machine is now taking work.' }),
      );
    });

  const setRunning = (next) =>
    guarded(() => request('/workers/agent/enabled', { method: 'POST', body: { enabled: next } }));

  return (
    <SettingsSection
      icon={HardDriveDownload}
      title={t('settings.worker_join_title', { defaultValue: "Lend this machine's GPU" })}
      description={t('settings.worker_join_desc', {
        defaultValue:
          'Let another copy of VoiceStudio send jobs to this machine. Paste the join code it showed you — or scan its QR with your phone and paste it here.',
      })}
      actions={
        joined ? (
          <Badge tone={running ? 'success' : 'neutral'} dot>
            {running
              ? t('settings.worker_join_working', { defaultValue: 'Working' })
              : t('settings.worker_join_stopped', { defaultValue: 'Stopped' })}
          </Badge>
        ) : null
      }
    >
      {joined && (
        <SettingRow
          title={t('settings.worker_join_take_work', { defaultValue: 'Take work from' })}
          subtitle={
            data?.endpoint ||
            t('settings.worker_join_no_endpoint', { defaultValue: 'No control plane remembered.' })
          }
          control={
            <SettingsToggle
              checked={running}
              disabled={busy || data?.env_pinned}
              onChange={setRunning}
            />
          }
        />
      )}

      {/* An environment-pinned worker is a deployment decision, not a user
          preference — saying so beats a toggle that snaps back. */}
      {data?.env_pinned && (
        <p className="m-0 text-xs opacity-60">
          {t('settings.worker_join_env', {
            defaultValue:
              'OMNIVOICE_WORKER_MODE is set in this machine’s environment, so it decides — change it there.',
          })}
        </p>
      )}

      <SettingRow
        title={
          joined
            ? t('settings.worker_join_rejoin', { defaultValue: 'Join a different one' })
            : t('settings.worker_join_code', { defaultValue: 'Join code' })
        }
        subtitle={t('settings.worker_join_code_hint', {
          defaultValue:
            'Single-use and expires in 15 minutes. Generate it on the machine that will send the work.',
        })}
        control={
          <div className="flex items-center gap-2">
            <input
              className="input w-72"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={t('settings.worker_join_placeholder', { defaultValue: 'ovw_…' })}
              name="worker-join-code"
              autoComplete="off"
              spellCheck={false}
              translate="no"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && code.trim() && !busy) join();
              }}
              aria-label={t('settings.worker_join_code', { defaultValue: 'Join code' })}
            />
            <Button
              variant="primary"
              size="sm"
              leading={<LogIn size={14} />}
              disabled={busy || !code.trim()}
              onClick={join}
            >
              {t('settings.worker_join', { defaultValue: 'Join' })}
            </Button>
          </div>
        }
      />

      {/* The join failure a user actually hits is "that code expired" — a bare
          toast disappears before they have read it, so it stays on the panel
          until the next attempt clears it. */}
      {data?.last_error && (
        <p role="alert" className="m-0 text-xs text-red-400">
          {data.last_error}
        </p>
      )}
    </SettingsSection>
  );
}
