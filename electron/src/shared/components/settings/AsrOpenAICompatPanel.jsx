/**
 * Model Catalogue (ASR tab) → OpenAI-compatible remote ASR panel (#877).
 *
 * A path to Qwen3-ASR, a self-hosted FunASR/SenseVoice server, LM Studio /
 * llama.cpp-style local servers, or OpenAI's own Whisper API. Configures the
 * `openai-compat-asr` backend's base_url/model/api_key; the engine is then
 * activated with the "Use" button on its row in the ASR engine matrix right
 * above this panel (or pinned via `OMNIVOICE_ASR_BACKEND=openai-compat-asr`,
 * which always wins over the Settings pick). The engine re-reads this config
 * on every transcribe — no restart needed after saving.
 *
 * Endpoints (loopback-only):
 *   GET  /api/settings/asr-openai-compat       → {base_url, model, has_key}
 *   PUT  /api/settings/asr-openai-compat       body {base_url?, model?, api_key?}
 *        ('' clears api_key; omitted/null leaves it unchanged — never returned)
 *   POST /api/settings/asr-openai-compat/test  → {ok, status, latency_ms, …}
 *        Cheap GET {base_url}/models probe of the PERSISTED config. Test
 *        connection saves first (same stale-config contract as the LLM
 *        providers panel), so the probe always sees the just-typed values.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Mic, Plug } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch, apiPost } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';

export default function AsrOpenAICompatPanel({ onSaved = null }) {
  const { t } = useTranslation();
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [hasKey, setHasKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [testing, setTesting] = useState(false);
  // Verdict of the last connection probe ({ok, status, latency_ms, …}), or
  // null. Cleared on any edit so a stale green can't vouch for new values.
  const [testResult, setTestResult] = useState(null);
  // Last server-acknowledged values: the one Save button persists all three
  // fields, so it stays disabled until something actually differs (dirty) and
  // a successful save shows an explicit "Saved" confirmation.
  const [server, setServer] = useState({ base_url: '', model: '' });

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/asr-openai-compat');
      setBaseUrl(d?.base_url || '');
      setModel(d?.model || '');
      setHasKey(Boolean(d?.has_key));
      setApiKey(''); // the key is never returned — the field always starts blank
      setServer({ base_url: d?.base_url || '', model: d?.model || '' });
    } catch (e) {
      setError(e?.message || t('models.asrOpenAICompatLoadError'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const edit = (setter) => (e) => {
    setter(e.target.value);
    setTestResult(null);
  };

  // Returns true when the PUT (and state refresh) succeeded — Test connection
  // aborts on a failed save so it never probes stale stored config under a
  // save error (same contract as LLMProvidersPanel.runTest).
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/asr-openai-compat', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: baseUrl,
          model,
          // Only send api_key when the user actually typed something —
          // an untouched field must leave the stored key unchanged, not
          // clear it (the field is always blank on load, so "unchanged"
          // and "empty" would otherwise be indistinguishable).
          ...(apiKey ? { api_key: apiKey } : {}),
        }),
      });
      const d = await res.json();
      setBaseUrl(d.base_url || '');
      setModel(d.model || '');
      setHasKey(Boolean(d.has_key));
      setApiKey('');
      setServer({ base_url: d.base_url || '', model: d.model || '' });
      setSaved(true);
      // Tell the host (Model Catalogue) so the matrix refetches and the
      // engine's row can flip unavailable → available without a manual Refresh.
      onSaved?.();
      return true;
    } catch (e) {
      setError(e?.message || t('models.asrOpenAICompatSaveError'));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const dirty = baseUrl !== server.base_url || model !== server.model || apiKey !== '';

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      // Save first so the probe sees the just-typed URL/model/key. If the
      // save failed, stop: probing the stale stored config would contradict
      // the save error with a misleading green result.
      if (dirty && !(await save())) return;
      const res = await apiPost('/api/settings/asr-openai-compat/test');
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, status: 'request_failed', detail: e?.message });
    } finally {
      setTesting(false);
    }
  };

  // status → localized, actionable message. Unknown statuses (older/newer
  // backend) fall through to the generic failure line + raw detail on hover.
  const testMessage = (r) => {
    const ms = Math.round(r?.latency_ms || 0);
    switch (r?.status) {
      case 'ok':
        if (r.model_found === true)
          return t('models.asrOpenAICompatTestOkModelListed', { ms, model: server.model });
        if (r.model_found === false)
          return t('models.asrOpenAICompatTestOkModelMissing', { ms, model: server.model });
        return t('models.asrOpenAICompatTestOk', { ms });
      case 'ok_no_models':
        return t('models.asrOpenAICompatTestOkNoModels', { ms });
      case 'auth_failed':
        return t('models.asrOpenAICompatTestAuthFailed', { code: r.http_status });
      case 'http_error':
        return t('models.asrOpenAICompatTestHttpError', { code: r.http_status });
      case 'timeout':
        return t('models.asrOpenAICompatTestTimeout');
      case 'unreachable':
        return t('models.asrOpenAICompatTestUnreachable');
      case 'not_configured':
        return t('models.asrOpenAICompatTestNotConfigured');
      case 'invalid_url':
        return t('models.asrOpenAICompatTestInvalidUrl');
      default:
        return r?.detail || t('models.asrOpenAICompatTestFailed');
    }
  };

  return (
    <SettingsSection
      icon={Mic}
      title={t('models.asrOpenAICompatTitle')}
      description={t('models.asrOpenAICompatDescription')}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      <SettingRow
        stack
        title={t('models.asrOpenAICompatBaseUrlTitle')}
        hint={t('models.asrOpenAICompatBaseUrlHint')}
        control={
          <SettingsInput
            mono
            type="text"
            value={baseUrl}
            onChange={edit(setBaseUrl)}
            placeholder="http://localhost:8000/v1"
            data-testid="asr-openai-compat-base-url"
          />
        }
      />

      <SettingRow
        stack
        title={t('models.asrOpenAICompatModelTitle')}
        control={
          <SettingsInput
            mono
            type="text"
            value={model}
            onChange={edit(setModel)}
            placeholder="whisper-1"
            data-testid="asr-openai-compat-model"
          />
        }
      />

      <SettingRow
        stack
        title={t('models.asrOpenAICompatApiKeyTitle')}
        hint={
          hasKey ? t('models.asrOpenAICompatKeyConfigured') : t('models.asrOpenAICompatApiKeyHint')
        }
        control={
          <>
            <SettingsInput
              mono
              type="password"
              value={apiKey}
              onChange={edit(setApiKey)}
              placeholder={hasKey ? '••••••••' : t('models.asrOpenAICompatApiKeyOptional')}
              data-testid="asr-openai-compat-api-key"
            />
            <Button
              variant="subtle"
              size="sm"
              onClick={save}
              loading={saving}
              disabled={saving || testing || !dirty}
              data-testid="asr-openai-compat-save"
            >
              {t('common.save')}
            </Button>
            {saved && !dirty && !saving && (
              <span
                className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)]"
                role="status"
                data-testid="asr-openai-compat-saved"
              >
                {t('models.asrOpenAICompatSaved')}
              </span>
            )}
          </>
        }
      />

      <SettingRow
        stack
        title={t('models.asrOpenAICompatTestTitle')}
        hint={t('models.asrOpenAICompatTestHint')}
        control={
          <>
            <Button
              variant="subtle"
              size="sm"
              onClick={testConnection}
              loading={testing}
              disabled={testing || saving || !baseUrl.trim()}
              leading={!testing && <Plug size={11} />}
              data-testid="asr-openai-compat-test"
            >
              {testing ? t('models.asrOpenAICompatTesting') : t('models.asrOpenAICompatTest')}
            </Button>
            {testResult && !testing && (
              <span
                className={`text-[length:var(--text-xs)] ${
                  testResult.ok
                    ? 'text-[color:var(--chrome-severity-ok,#98971a)]'
                    : 'text-[color:var(--chrome-severity-err,#cc241d)]'
                }`}
                role="status"
                title={testResult.detail || undefined}
                data-testid="asr-openai-compat-test-result"
              >
                {testMessage(testResult)}
              </span>
            )}
          </>
        }
      />
    </SettingsSection>
  );
}
