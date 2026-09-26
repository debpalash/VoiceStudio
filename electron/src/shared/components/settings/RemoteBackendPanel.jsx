/**
 * Settings → Sharing → Remote backend panel (parity program Wave 2.3).
 *
 * Point this app at a VoiceStudio backend running elsewhere (a GPU box over
 * Tailscale, a Docker deployment). Persists only the URL. A supplied master
 * credential is exchanged for a short-lived session and removed from the UI;
 * it never enters localStorage or an ordinary health request.
 *
 * Saving is guarded: the URL must be a parseable http(s):// URL (a typo'd
 * base would brick every API call after the reload), and saving a URL that
 * hasn't passed a connection test asks for confirmation first.
 *
 * Pairs with the backend's OMNIVOICE_API_KEY bearer gate; full recipe in
 * docs/remote-gpu.md.
 */
import React, { useState } from 'react';
import { Server } from 'lucide-react';
import toast from 'react-hot-toast';
import { Trans, useTranslation } from 'react-i18next';
import { LS_BACKEND_URL, LS_API_KEY, API } from '../../api/client';
import { clearAdminSession, exchangeApiKey, getAdminSession } from '../../api/authSession';
import { askConfirm } from '../../utils/dialog';
import { disableRemoteBackend, probeRemoteBackend } from '../../utils/remoteBackendProbe';
import { reloadAfterApplicationPersistence } from '../../utils/persistenceLifecycle';
import { SettingsSection, SettingRow, InfoHint, SettingsInput } from './primitives';
import { Button, Badge } from '../../ui';

const REMOTE_GPU_DOCS_URL = 'https://github.com/debpalash/VoiceStudio/blob/main/docs/remote-gpu.md';

/** A saved backend base must be a parseable absolute http(s) URL. */
export function isValidBackendUrl(value) {
  if (!value) return false;
  try {
    const u = new URL(value);
    return (
      (u.protocol === 'http:' || u.protocol === 'https:') &&
      !u.username &&
      !u.password &&
      !u.search &&
      !u.hash
    );
  } catch {
    return false;
  }
}

function storedBackendUrl() {
  try {
    return localStorage.getItem(LS_BACKEND_URL) || '';
  } catch {
    return '';
  }
}

function removeLegacyMaster() {
  try {
    localStorage.removeItem(LS_API_KEY);
  } catch {
    // A blocked storage API is equivalent to the legacy key not being usable.
  }
}

export default function RemoteBackendPanel({ reload = reloadAfterApplicationPersistence }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState(storedBackendUrl);
  const [key, setKey] = useState('');
  const [probe, setProbe] = useState(null); // {ok, detail, target}
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [authenticatedTarget, setAuthenticatedTarget] = useState(null);
  const [initialTarget] = useState(() => (storedBackendUrl() || API).trim().replace(/\/+$/, ''));
  const [restoredSessionTarget] = useState(() => getAdminSession(initialTarget)?.apiBase ?? null);
  const hasSavedRemote = Boolean(storedBackendUrl());

  const normalized = url.trim().replace(/\/+$/, '');

  const onTest = async () => {
    if (testing || saving) return;
    setTesting(true);
    setProbe(null);
    const target = normalized || API;
    const master = key.trim();
    // Retain the secret only in this in-flight stack frame. A pending legacy
    // master in localStorage is left alone: a mere connection test must not
    // consume the key the bootstrap migration still needs to retry.
    setKey('');
    try {
      const result = await probeRemoteBackend(target);
      if (!result.ok || !master) {
        setProbe(result);
        return;
      }
      try {
        await exchangeApiKey(master, { apiBase: target });
        setAuthenticatedTarget(target);
        setProbe(result);
      } catch (error) {
        setAuthenticatedTarget(null);
        setProbe({
          ok: false,
          kind: error?.status ? 'http' : 'network',
          status: error?.status,
          target,
        });
      }
    } finally {
      setTesting(false);
    }
  };

  const onSave = async () => {
    if (testing || saving) return;
    setSaving(true);
    const master = key.trim();
    setKey('');
    try {
      if (normalized) {
        if (!isValidBackendUrl(normalized)) {
          toast.error(
            t('settings.remote_backend_invalid_url', {
              defaultValue:
                'Enter a valid URL starting with http:// or https:// (e.g. http://gpu-box:3900).',
            }),
          );
          return;
        }
        // A wrong base bricks every API call after the reload — if this exact
        // URL hasn't passed a connection test, make the user confirm.
        const verified = probe?.ok && probe.target === normalized;
        if (!verified) {
          const go = await askConfirm(
            t('settings.remote_backend_confirm_unverified', {
              defaultValue:
                "This backend URL hasn't passed a connection test. Save it and reload anyway? " +
                "If it's wrong, the app can't reach any backend until you change it back here.",
            }),
            t('settings.remote_backend_confirm_title', { defaultValue: 'Use unverified backend?' }),
          );
          if (!go) return;
        }
        if (master) {
          try {
            await exchangeApiKey(master, { apiBase: normalized });
            setAuthenticatedTarget(normalized);
          } catch (error) {
            setAuthenticatedTarget(null);
            setProbe({
              ok: false,
              kind: error?.status ? 'http' : 'network',
              status: error?.status,
              target: normalized,
            });
            return;
          }
        } else if ((authenticatedTarget ?? restoredSessionTarget ?? initialTarget) !== normalized) {
          clearAdminSession();
        }
        localStorage.setItem(LS_BACKEND_URL, normalized);
      } else {
        // Disabling the remote backend is an explicit discard: the pending
        // legacy master goes with the connection it belonged to. Everywhere
        // else the durable key is consumed only by a successful exchange
        // (exchangeApiKey removes it), so an unreachable backend can't strand
        // the user by destroying their only copy.
        localStorage.removeItem(LS_BACKEND_URL);
        clearAdminSession();
        removeLegacyMaster();
      }
      // api/client.ts resolves the base once at module load.
      await reload();
    } catch (error) {
      toast.error(
        t('settings.save_failed', {
          message: error?.message || t('bootstrap.unknown_error'),
        }),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      icon={Server}
      title={t('settings.remote_backend_title', { defaultValue: 'Remote backend' })}
      description={t('settings.remote_backend_desc', {
        defaultValue:
          'Run inference on another machine; leave the URL empty for the local backend. ' +
          'Saving reloads the app to apply.',
      })}
      actions={
        <InfoHint learnMoreHref={REMOTE_GPU_DOCS_URL}>
          <Trans
            i18nKey="settings.remote_backend_hint"
            defaults="Start the backend on the other machine with <1>OMNIVOICE_API_KEY</1> set, reach it over your tailnet, and point this app at it."
            components={{ 1: <code /> }}
          />
        </InfoHint>
      }
    >
      <SettingRow
        stack
        title={t('settings.remote_backend_url', { defaultValue: 'Backend URL' })}
        control={
          <SettingsInput
            mono
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://gpu-box.tailnet.ts.net:3900"
            aria-label={t('settings.remote_backend_url', { defaultValue: 'Backend URL' })}
            data-testid="remote-backend-url"
          />
        }
      />
      <SettingRow
        stack
        title={t('settings.remote_backend_key', { defaultValue: 'API key' })}
        control={
          <SettingsInput
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="off"
            disabled={testing || saving}
            placeholder={t('settings.remote_backend_key_placeholder', {
              defaultValue: 'value of OMNIVOICE_API_KEY on the server',
            })}
            aria-label={t('settings.remote_backend_key', { defaultValue: 'API key' })}
            data-testid="remote-backend-key"
          />
        }
      />

      <div className="flex flex-wrap items-center gap-[var(--space-3)] min-w-0 max-w-full">
        <Button
          variant="subtle"
          size="sm"
          onClick={onTest}
          loading={testing}
          disabled={testing || saving}
          data-testid="remote-backend-test"
        >
          {t('settings.remote_backend_test', { defaultValue: 'Test connection' })}
        </Button>
        <Button
          variant="subtle"
          size="sm"
          onClick={onSave}
          loading={saving}
          disabled={testing || saving}
          data-testid="remote-backend-save"
        >
          {t('settings.remote_backend_save', { defaultValue: 'Save & reload' })}
        </Button>
        {hasSavedRemote && (
          <Button
            variant="subtle"
            size="sm"
            onClick={() => void disableRemoteBackend(reload)}
            data-testid="remote-backend-disable"
          >
            {t('settings.remote_backend_use_local')}
          </Button>
        )}
        {probe && (
          <Badge tone={probe.ok ? 'success' : 'danger'} dot role="status">
            {probe.ok
              ? t('settings.remote_backend_probe_ok', {
                  detail: probe.detail,
                  defaultValue: 'OK — {{detail}}',
                })
              : t('settings.remote_backend_probe_fail', {
                  detail: t(`settings.remote_backend_error_${probe.kind}`, {
                    status: probe.status,
                  }),
                  defaultValue: 'Failed — {{detail}}',
                })}
          </Badge>
        )}
      </div>
    </SettingsSection>
  );
}
