import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { PhoneCallIcon, ShieldAlertIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ApiError, apiFetch, apiJson, describeError } from '@/lib/api/client';
import { useProfiles } from '@/hooks/use-profiles';
import { useEngines } from '@/hooks/use-engines';
import './twilio-setup.css';

export const TWILIO_DOCS =
  'https://github.com/debpalash/VoiceStudio/blob/main/docs/integrations/twilio.md';
const STATE_PATH = '/api/integrations/twilio/state';
const QUERY_KEY = ['integrations', 'twilio'] as const;
const DEFAULT = '__default__';

export interface TwilioCall {
  call: string;
  started_at: number;
  ended_at: number | null;
  outcome: string;
  audio_seconds: number;
}

export interface TwilioState {
  enabled: boolean;
  account_sid: string;
  has_auth_token: boolean;
  public_base_url: string;
  voice_id: string;
  engine: string;
  language: string;
  greeting: string;
  webhook_url: string;
  missing: string[];
  listener: { running: boolean; port: number | null; tunnel_target: string | null };
  calls: { active: number; max_concurrent: number; recent: TwilioCall[] };
  limits: { max_greeting_chars: number };
}

interface Form {
  account_sid: string;
  auth_token: string;
  public_base_url: string;
  voice_id: string;
  engine: string;
  greeting: string;
}

const OUTCOMES = new Set([
  'completed',
  'in_progress',
  'caller_hung_up',
  'busy',
  'rejected_signature',
  'rejected_stream',
  'time_limit',
  'synthesis_failed',
  'engine_unavailable',
]);
const FIELDS = new Set(['account_sid', 'auth_token', 'public_base_url', 'greeting']);

function formFrom(state: TwilioState): Form {
  return {
    account_sid: state.account_sid,
    auth_token: '',
    public_base_url: state.public_base_url,
    voice_id: state.voice_id,
    engine: state.engine,
    greeting: state.greeting,
  };
}

/** Twilio phone calls: all traffic stays local until the user enables it. */
export function TwilioSetup() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const state = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => apiJson<TwilioState>(STATE_PATH, { signal }),
    refetchInterval: (query) => (query.state.data?.enabled ? 5000 : false),
  });
  const profiles = useProfiles();
  const engines = useEngines();
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const previewRef = useRef<string | null>(null);

  useEffect(() => {
    if (state.data && form === null) setForm(formFrom(state.data));
  }, [state.data, form]);
  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    [],
  );

  if (!state.data || !form) {
    return (
      <section className="twilio-setup space-y-3" aria-busy={state.isLoading}>
        <h4>{t('twilioIntegration.title')}</h4>
        {state.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {describeError(state.error)}{' '}
            <Button size="sm" variant="ghost" onClick={() => void state.refetch()}>
              {t('common.retry')}
            </Button>
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
        )}
      </section>
    );
  }
  const server = state.data;
  const set = (key: keyof Form, value: string) => {
    setForm((current) => (current ? { ...current, [key]: value } : current));
    setError('');
  };
  const explain = (reason: unknown) => {
    const detail =
      reason instanceof ApiError && reason.payload && typeof reason.payload.detail === 'object'
        ? (reason.payload.detail as { code?: string; missing?: string[] })
        : null;
    if (detail?.code === 'incomplete' && Array.isArray(detail.missing)) {
      const fields = detail.missing
        .filter((field) => FIELDS.has(field))
        .map((field) => t(`twilioIntegration.field_${field}`));
      return t('twilioIntegration.incomplete', { fields: fields.join(', ') });
    }
    return describeError(reason);
  };
  const body = (extra: Record<string, unknown> = {}) =>
    JSON.stringify({
      account_sid: form.account_sid,
      public_base_url: form.public_base_url,
      voice_id: form.voice_id,
      engine: form.engine,
      greeting: form.greeting,
      ...(form.auth_token ? { auth_token: form.auth_token } : {}),
      ...extra,
    });
  const submit = async (extra: Record<string, unknown> = {}, onlyExtra = false) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const next = await apiJson<TwilioState>('/api/integrations/twilio/config', {
        method: 'PUT',
        body: onlyExtra ? JSON.stringify(extra) : body(extra),
      });
      queryClient.setQueryData(QUERY_KEY, next);
      if (!onlyExtra) setForm(formFrom(next));
      // A removed token must not come back from an unsaved draft on the next Save.
      else if ('auth_token' in extra)
        setForm((current) => (current ? { ...current, auth_token: '' } : current));
      toast.success(t('nav.saved'));
    } catch (reason) {
      setError(explain(reason));
      await state.refetch();
    } finally {
      setBusy(false);
    }
  };
  const testLocally = async () => {
    if (testing) return;
    setTesting(true);
    setError('');
    try {
      const res = await apiFetch('/api/integrations/twilio/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: form.greeting,
          voice_id: form.voice_id,
          engine: form.engine,
        }),
      });
      const url = URL.createObjectURL(await res.blob());
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
      previewRef.current = url;
      setPreview(url);
    } catch (reason) {
      setError(explain(reason));
    } finally {
      setTesting(false);
    }
  };
  const copyWebhook = async () => {
    try {
      await navigator.clipboard.writeText(server.webhook_url);
      toast.success(t('transcriptions.copied'));
    } catch {
      toast.error(t('transcriptions.copy_failed'));
    }
  };
  const ttsBackends = (engines.data?.tts?.backends ?? []).filter((backend) => backend.available);
  const time = new Intl.DateTimeFormat(i18n.language, { timeStyle: 'short', dateStyle: 'short' });

  return (
    <section className="twilio-setup space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h4 className="flex items-center gap-2">
            <PhoneCallIcon className="size-4" aria-hidden />
            {t('twilioIntegration.title')}
          </h4>
          <p className="text-sm text-muted-foreground">{t('twilioIntegration.intro')}</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span aria-hidden>{t('twilioIntegration.enable')}</span>
          <Switch
            aria-label={t('twilioIntegration.enable')}
            checked={server.enabled}
            disabled={busy}
            onCheckedChange={(enabled) =>
              // Turning off must always work, even with an invalid draft: send
              // only the switch. Turning on saves the form with it.
              void (enabled ? submit({ enabled }) : submit({ enabled: false }, true))
            }
          />
        </div>
      </div>
      <p className="twilio-warning flex gap-2 text-xs">
        <ShieldAlertIcon className="size-4 shrink-0" aria-hidden />
        {t('twilioIntegration.exposureWarning')}
      </p>

      {/* Read-only while a configuration update is in flight, so the
          response can never overwrite edits made meanwhile. */}
      <fieldset className="twilio-grid" disabled={busy}>
        <label>
          <span>{t('twilioIntegration.accountSid')}</span>
          <Input
            value={form.account_sid}
            autoComplete="off"
            spellCheck={false}
            placeholder="AC…"
            onChange={(event) => set('account_sid', event.target.value)}
          />
        </label>
        <label>
          <span>{t('twilioIntegration.authToken')}</span>
          <Input
            type="password"
            value={form.auth_token}
            autoComplete="off"
            placeholder={server.has_auth_token ? t('twilioIntegration.authTokenSaved') : ''}
            onChange={(event) => set('auth_token', event.target.value)}
          />
          <small>{t('twilioIntegration.authTokenHint')}</small>
        </label>
        <label className="twilio-wide">
          <span>{t('twilioIntegration.publicUrl')}</span>
          <Input
            value={form.public_base_url}
            spellCheck={false}
            placeholder="https://example.trycloudflare.com"
            onChange={(event) => set('public_base_url', event.target.value)}
          />
          <small>{t('twilioIntegration.publicUrlHint')}</small>
        </label>
        <label>
          <span>{t('twilioIntegration.voice')}</span>
          <Select
            value={form.voice_id || DEFAULT}
            onValueChange={(value) => set('voice_id', value === DEFAULT ? '' : String(value))}
          >
            <SelectTrigger aria-label={t('twilioIntegration.voice')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={DEFAULT}>{t('twilioIntegration.engineDefault')}</SelectItem>
              {(profiles.data ?? []).map((profile) => (
                <SelectItem key={profile.id} value={profile.id}>
                  {profile.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label>
          <span>{t('twilioIntegration.engine')}</span>
          <Select
            value={form.engine || DEFAULT}
            onValueChange={(value) => set('engine', value === DEFAULT ? '' : String(value))}
          >
            <SelectTrigger aria-label={t('twilioIntegration.engine')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={DEFAULT}>{t('twilioIntegration.activeEngine')}</SelectItem>
              {ttsBackends.map((backend) => (
                <SelectItem key={backend.id} value={backend.id}>
                  {backend.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="twilio-wide">
          <span>{t('twilioIntegration.greeting')}</span>
          <Textarea
            value={form.greeting}
            rows={3}
            maxLength={server.limits.max_greeting_chars}
            placeholder={t('twilioIntegration.greetingPlaceholder')}
            onChange={(event) => set('greeting', event.target.value)}
          />
        </label>
      </fieldset>

      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={busy} aria-busy={busy} onClick={() => void submit()}>
          {t('common.save')}
        </Button>
        <Button
          variant="outline"
          disabled={testing || !form.greeting.trim()}
          aria-busy={testing}
          onClick={() => void testLocally()}
        >
          {testing ? t('twilioIntegration.testing') : t('twilioIntegration.test')}
        </Button>
        {server.has_auth_token && (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() =>
              // Only the removal (never the draft form), and calls go off with
              // it: they cannot be answered without the token.
              void submit({ auth_token: '', ...(server.enabled ? { enabled: false } : {}) }, true)
            }
          >
            {t('twilioIntegration.clearToken')}
          </Button>
        )}
        <a href={TWILIO_DOCS} target="_blank" rel="noopener noreferrer">
          {t('common.learn_more')}
        </a>
      </div>
      <p className="text-xs text-muted-foreground">{t('twilioIntegration.testHint')}</p>
      {preview && (
        <audio controls src={preview} aria-label={t('twilioIntegration.test')} className="w-full" />
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      {server.webhook_url && (
        <div className="space-y-1">
          <h4 className="text-xs font-medium">{t('twilioIntegration.webhookUrl')}</h4>
          <div className="flex flex-wrap items-center gap-2">
            <code className="twilio-code">{server.webhook_url}</code>
            <Button size="sm" variant="outline" onClick={() => void copyWebhook()}>
              {t('transcriptions.copy')}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t('twilioIntegration.webhookHint')}</p>
        </div>
      )}
      <div className="space-y-1">
        <h4 className="text-xs font-medium">{t('twilioIntegration.tunnelTarget')}</h4>
        <p className="text-sm" role="status">
          <span
            className={server.listener.running ? 'twilio-dot is-on' : 'twilio-dot'}
            aria-hidden
          />
          {server.listener.running && server.listener.tunnel_target
            ? t('twilioIntegration.listening', { target: server.listener.tunnel_target })
            : t('twilioIntegration.listenerOff')}
        </p>
        <p className="text-xs text-muted-foreground">{t('twilioIntegration.tunnelHint')}</p>
      </div>

      <div className="space-y-2">
        <h4 className="text-xs font-medium">
          {t('twilioIntegration.recentCalls')}{' '}
          <span className="text-muted-foreground">
            {t('twilioIntegration.activeCalls', {
              active: server.calls.active,
              max: server.calls.max_concurrent,
            })}
          </span>
        </h4>
        {server.calls.recent.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('twilioIntegration.noCalls')}</p>
        ) : (
          <ul className="twilio-calls">
            {server.calls.recent.map((call, index) => (
              <li key={`${call.started_at}-${index}`}>
                <span>{time.format(new Date(call.started_at * 1000))}</span>
                <span>{call.call}</span>
                <span data-outcome={call.outcome}>
                  {t(
                    `twilioIntegration.outcome_${OUTCOMES.has(call.outcome) ? call.outcome : 'error'}`,
                  )}
                </span>
                <span>{t('twilioIntegration.audioSeconds', { seconds: call.audio_seconds })}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
