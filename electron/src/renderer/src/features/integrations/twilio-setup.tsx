import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  ArrowRightIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleDashedIcon,
  PhoneCallIcon,
  PlayIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
} from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
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
import { getBridge } from '@/components/bridge';
import { IntegrationDetailColumns } from './integration-detail-layout';
import type { IntegrationPanelProps } from './setup-registry';
import {
  CopyField,
  DisabledReason,
  Field,
  StatusPill,
  StepCard,
  focusStep,
  type OverallStatus,
  type StepStatus,
} from './twilio-parts';
import './twilio-setup.css';

export const TWILIO_DOCS =
  'https://github.com/debpalash/VoiceStudio/blob/main/docs/integrations/twilio.md';
const STATE_PATH = '/api/integrations/twilio/state';
const QUERY_KEY = ['integrations', 'twilio'] as const;
/**
 * Calls backend (docs/integrations/calls.md), feature-detected: a 404 means
 * "not in this build". `/calls/settings` carries `from_number`,
 * `inbound_mode` and `disclosure_template`; `/calls/readiness` returns
 * `[{ id, ok, detail }]`.
 */
export const CALLS_SETTINGS_PATH = '/calls/settings';
export const CALLS_READINESS_PATH = '/calls/readiness';
/** Select sentinel for "no explicit choice"; never shown, always labelled. */
const DEFAULT = '__default__';
const DEFAULT_GATEWAY_PORT = 3950;
const E164 = /^\+[1-9]\d{6,14}$/;
const ACCOUNT_SID = /^AC[0-9a-fA-F]{32}$/;

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
  listener: {
    running: boolean;
    port: number | null;
    host?: string;
    preferred_port?: number;
    tunnel_target: string | null;
  };
  calls: { active: number; max_concurrent: number; recent: TwilioCall[] };
  limits: { max_greeting_chars: number };
}

export interface CallsSettings {
  from_number?: string;
  inbound_mode?: string;
  disclosure_template?: string;
}

export interface ReadinessItem {
  id: string;
  ok: boolean;
  detail?: string;
}

interface Draft {
  account_sid: string;
  auth_token: string;
  replacing_token: boolean;
  public_base_url: string;
  voice_id: string;
  engine: string;
  greeting: string;
}

interface CallsDraft {
  from_number: string;
  inbound_mode: string;
  disclosure: string;
}

type Step = 'account' | 'tunnel' | 'number' | 'voice';
type Busy = Step | 'enable' | 'remove' | 'check' | 'preview' | null;

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
const ANSWERED = new Set([
  'completed',
  'in_progress',
  'caller_hung_up',
  'time_limit',
  'synthesis_failed',
  'engine_unavailable',
]);
const ERROR_CODES = new Set([
  'invalid_account_sid',
  'invalid_public_url',
  'greeting_too_long',
  'missing_greeting',
  'listener_failed',
]);
const FIELDS = new Set(['account_sid', 'auth_token', 'public_base_url', 'greeting']);
const CHECKS = new Set(['credentials', 'tunnel', 'number', 'llm', 'asr', 'voice']);
const CHECK_STEP: Record<string, Step> = {
  credentials: 'account',
  tunnel: 'tunnel',
  number: 'number',
  voice: 'voice',
};

function draftFrom(state: TwilioState): Draft {
  return {
    account_sid: state.account_sid,
    auth_token: '',
    replacing_token: false,
    public_base_url: state.public_base_url,
    voice_id: state.voice_id,
    engine: state.engine,
    greeting: state.greeting,
  };
}

/** GET a feature-detected endpoint: null when this backend does not have it. */
async function optionalJson<T>(path: string, signal: AbortSignal): Promise<T | null> {
  try {
    return await apiJson<T>(path, { signal });
  } catch (reason) {
    if (reason instanceof ApiError && (reason.status === 404 || reason.status === 405)) return null;
    throw reason;
  }
}

/** The local address the tunnel must forward to, exact even while calls are off. */
export function tunnelTarget(listener: TwilioState['listener']): string {
  if (listener.tunnel_target) return listener.tunnel_target;
  const raw = listener.host ?? '';
  const host = !raw || raw === '0.0.0.0' || raw === '::' ? '127.0.0.1' : raw;
  const shown = host.includes(':') ? `[${host}]` : host;
  return `http://${shown}:${listener.preferred_port ?? DEFAULT_GATEWAY_PORT}`;
}

type Os = 'darwin' | 'win32' | 'linux';
function currentOs(): Os {
  const platform = getBridge()?.app.platform;
  if (platform === 'darwin' || platform === 'win32') return platform;
  if (platform) return 'linux';
  const agent = typeof navigator === 'undefined' ? '' : navigator.userAgent;
  return /Mac/i.test(agent) ? 'darwin' : /Win/i.test(agent) ? 'win32' : 'linux';
}

const INSTALL: Record<'cloudflared' | 'ngrok', Partial<Record<Os, string>>> = {
  cloudflared: {
    darwin: 'brew install cloudflared',
    win32: 'winget install --id Cloudflare.cloudflared',
  },
  ngrok: {
    darwin: 'brew install ngrok',
    win32: 'winget install --id ngrok.ngrok',
    linux: 'sudo snap install ngrok',
  },
};
const INSTALL_DOCS = {
  cloudflared:
    'https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/',
  ngrok: 'https://ngrok.com/download',
};

export function tunnelCommand(tool: 'cloudflared' | 'ngrok', target: string) {
  // ngrok takes host:port; cloudflared takes the full origin.
  return tool === 'cloudflared'
    ? `cloudflared tunnel --url ${target}`
    : `ngrok http ${target.replace(/^http:\/\//, '')}`;
}

/** Twilio phone calls: everything stays local until the user turns calls on. */
export function TwilioSetup({ hero, rail }: IntegrationPanelProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const router = useRouter();
  const state = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => apiJson<TwilioState>(STATE_PATH, { signal }),
    refetchInterval: (query) => (query.state.data?.enabled ? 5000 : false),
  });
  const enabled = Boolean(state.data?.enabled);
  const callsSettings = useQuery({
    queryKey: ['calls', 'settings'],
    queryFn: ({ signal }) => optionalJson<CallsSettings>(CALLS_SETTINGS_PATH, signal),
    retry: false,
    staleTime: 60_000,
  });
  const readiness = useQuery({
    queryKey: ['calls', 'readiness'],
    queryFn: ({ signal }) =>
      optionalJson<ReadinessItem[] | { checklist?: ReadinessItem[] }>(CALLS_READINESS_PATH, signal),
    retry: false,
    refetchInterval: enabled ? 5000 : false,
  });
  const profiles = useProfiles();
  const engines = useEngines();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [callsDraft, setCallsDraft] = useState<CallsDraft | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [errors, setErrors] = useState<Partial<Record<Step | 'enable' | 'preview', string>>>({});
  const [tool, setTool] = useState<'cloudflared' | 'ngrok'>('cloudflared');
  const [checked, setChecked] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const previewRef = useRef<string | null>(null);

  useEffect(() => {
    if (state.data && draft === null) setDraft(draftFrom(state.data));
  }, [state.data, draft]);
  useEffect(() => {
    if (callsSettings.data && callsDraft === null)
      setCallsDraft({
        from_number: callsSettings.data.from_number ?? '',
        inbound_mode: callsSettings.data.inbound_mode ?? 'greeting',
        // Prefilled, so what is shown is exactly what gets saved.
        disclosure:
          callsSettings.data.disclosure_template || t('twilioIntegration.disclosureDefault'),
      });
  }, [callsSettings.data, callsDraft, t]);
  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    [],
  );

  if (!state.data || !draft) {
    return (
      <>
        {hero({})}
        <section className="twilio-loading" aria-busy={state.isLoading}>
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
      </>
    );
  }

  const server = state.data;
  const calls = callsSettings.data ?? null;
  const callsRoute = Boolean(
    (router.routesByPath as Record<string, unknown> | undefined)?.['/calls'],
  );
  const set = (key: keyof Draft, value: string | boolean) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  const setCalls = (key: keyof CallsDraft, value: string) =>
    setCallsDraft((current) => (current ? { ...current, [key]: value } : current));
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
    if (detail?.code && ERROR_CODES.has(detail.code))
      return t(`twilioIntegration.error_${detail.code}`);
    return describeError(reason);
  };
  const fail = (key: Step | 'enable' | 'preview', message: string) =>
    setErrors((current) => ({ ...current, [key]: message }));
  const clear = (key: Step | 'enable' | 'preview') =>
    setErrors((current) => ({ ...current, [key]: undefined }));

  /** PUT only this step's fields; other steps keep their unsaved drafts. */
  const save = async (
    step: Step | 'enable' | 'remove',
    body: Record<string, unknown>,
    reset: (keyof Draft)[],
  ) => {
    if (busy) return false;
    const key = step === 'remove' ? 'account' : step;
    setBusy(step);
    clear(key);
    try {
      const next = await apiJson<TwilioState>('/api/integrations/twilio/config', {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      queryClient.setQueryData(QUERY_KEY, next);
      setDraft((current) => {
        if (!current) return current;
        const fresh = draftFrom(next);
        const merged = { ...current };
        for (const field of reset) (merged as Record<string, unknown>)[field] = fresh[field];
        return merged;
      });
      void readiness.refetch();
      toast.success(t('nav.saved'));
      return true;
    } catch (reason) {
      fail(key, explain(reason));
      await state.refetch();
      return false;
    } finally {
      setBusy(null);
    }
  };
  const saveCalls = async (step: Step, body: CallsSettings) => {
    const next = await apiJson<CallsSettings>(CALLS_SETTINGS_PATH, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
    queryClient.setQueryData(['calls', 'settings'], next);
    void readiness.refetch();
    return step;
  };

  // ── Derived status ──────────────────────────────────────────────────────
  const credentialsDone = Boolean(server.account_sid && server.has_auth_token);
  const tunnelDone = Boolean(server.public_base_url);
  // Only a call that passed the signature check proves the webhook points
  // here with the current token; rejected or busy attempts do not.
  const answered = server.calls.recent.some((call) => ANSWERED.has(call.outcome));
  // In agent mode the call agent answers, so no greeting is needed.
  const agentAnswers = callsSettings.data?.inbound_mode === 'agent';
  const voiceDone = Boolean(server.greeting) || agentAnswers;
  const steps: Record<Step, StepStatus> = {
    account: credentialsDone ? 'done' : 'todo',
    tunnel: tunnelDone ? 'done' : 'todo',
    number: !server.webhook_url ? 'blocked' : answered ? 'done' : 'todo',
    voice: voiceDone ? 'done' : 'todo',
  };
  const overall: OverallStatus = server.enabled
    ? server.listener.running
      ? 'live'
      : 'attention'
    : server.missing.length === 0
      ? 'ready'
      : 'setup';
  const firstOpen = (['account', 'tunnel', 'number', 'voice'] as Step[]).find(
    (step) => steps[step] !== 'done' && !(step === 'number' && steps.number === 'todo'),
  );
  const derivedChecklist: ReadinessItem[] = [
    { id: 'credentials', ok: credentialsDone },
    { id: 'tunnel', ok: tunnelDone },
    { id: 'number', ok: answered },
    { id: 'voice', ok: voiceDone },
  ];
  const served = Array.isArray(readiness.data) ? readiness.data : readiness.data?.checklist;
  const checklist = (served ?? derivedChecklist).filter((item) => CHECKS.has(item.id));

  const toggleCalls = (on: boolean) =>
    void save('enable', { enabled: on }, []).then((ok) => {
      if (!ok && on) focusStep(firstOpen ?? 'account');
    });
  const primaryAction: ReactNode =
    overall === 'setup' ? (
      <Button onClick={() => focusStep(firstOpen ?? 'account')}>
        {t('twilioIntegration.continueSetup')}
        <ArrowRightIcon aria-hidden />
      </Button>
    ) : overall === 'ready' ? (
      <Button
        disabled={busy !== null}
        aria-busy={busy === 'enable'}
        onClick={() => toggleCalls(true)}
      >
        <PhoneCallIcon aria-hidden />
        {t('twilioIntegration.turnOn')}
      </Button>
    ) : (
      <Button
        variant="outline"
        disabled={busy !== null}
        aria-busy={busy === 'enable'}
        onClick={() => toggleCalls(false)}
      >
        {t('twilioIntegration.turnOff')}
      </Button>
    );

  // ── Step 1: account ─────────────────────────────────────────────────────
  const sidInvalid = Boolean(draft.account_sid) && !ACCOUNT_SID.test(draft.account_sid.trim());
  const tokenEditing = !server.has_auth_token || draft.replacing_token;
  const accountStep = (
    <StepCard
      id="account"
      index={1}
      title={t('twilioIntegration.stepAccount')}
      description={t('twilioIntegration.stepAccountHint')}
      disabled={busy !== null}
      status={steps.account}
      footer={
        <>
          <Button
            disabled={busy !== null}
            aria-busy={busy === 'account'}
            onClick={() =>
              void save(
                'account',
                {
                  account_sid: draft.account_sid,
                  ...(tokenEditing && draft.auth_token ? { auth_token: draft.auth_token } : {}),
                },
                ['account_sid', 'auth_token', 'replacing_token'],
              )
            }
          >
            {t('twilioIntegration.saveAndCheck')}
          </Button>
          <small>{t('twilioIntegration.checkHint')}</small>
        </>
      }
    >
      <div className="twilio-fields">
        <Field
          label={t('twilioIntegration.accountSid')}
          htmlFor="twilio-sid"
          hint={sidInvalid ? t('twilioIntegration.error_invalid_account_sid') : undefined}
        >
          <Input
            id="twilio-sid"
            value={draft.account_sid}
            autoComplete="off"
            spellCheck={false}
            placeholder="AC…"
            aria-invalid={sidInvalid || undefined}
            aria-describedby={sidInvalid ? 'twilio-sid-hint' : undefined}
            onChange={(event) => set('account_sid', event.target.value)}
          />
        </Field>
        {tokenEditing ? (
          <Field
            label={t('twilioIntegration.authToken')}
            htmlFor="twilio-token"
            hint={t('twilioIntegration.authTokenHint')}
          >
            <div className="twilio-inline">
              <Input
                id="twilio-token"
                type="password"
                value={draft.auth_token}
                autoComplete="off"
                aria-describedby="twilio-token-hint"
                onChange={(event) => set('auth_token', event.target.value)}
              />
              {server.has_auth_token && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    set('auth_token', '');
                    set('replacing_token', false);
                  }}
                >
                  {t('common.cancel')}
                </Button>
              )}
            </div>
          </Field>
        ) : (
          <div className="twilio-field">
            <span className="twilio-field-label">{t('twilioIntegration.authToken')}</span>
            <div className="twilio-secret">
              <ShieldCheckIcon aria-hidden />
              <span>{t('twilioIntegration.authTokenStored')}</span>
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() => set('replacing_token', true)}
              >
                {t('twilioIntegration.replace')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                aria-busy={busy === 'remove'}
                onClick={() =>
                  // Only the removal (never a draft), and calls go off with it:
                  // they cannot be answered without the token.
                  void save(
                    'remove',
                    { auth_token: '', ...(server.enabled ? { enabled: false } : {}) },
                    ['auth_token', 'replacing_token'],
                  )
                }
              >
                {t('twilioIntegration.remove')}
              </Button>
            </div>
          </div>
        )}
      </div>
      {errors.account && (
        <p role="alert" className="twilio-error">
          {errors.account}
        </p>
      )}
    </StepCard>
  );

  // ── Step 2: tunnel ──────────────────────────────────────────────────────
  const target = tunnelTarget(server.listener);
  const os = currentOs();
  const install = INSTALL[tool][os];
  const listenerMessage = server.listener.running
    ? t('twilioIntegration.listening', { target })
    : server.enabled
      ? t('twilioIntegration.listenerFailed')
      : t('twilioIntegration.listenerOffHint');
  const tunnelStep = (
    <StepCard
      id="tunnel"
      index={2}
      title={t('twilioIntegration.stepTunnel')}
      description={t('twilioIntegration.stepTunnelHint')}
      disabled={busy !== null}
      status={steps.tunnel}
      footer={
        <Button
          disabled={busy !== null}
          aria-busy={busy === 'tunnel'}
          onClick={() =>
            void save('tunnel', { public_base_url: draft.public_base_url }, ['public_base_url'])
          }
        >
          {t('common.save')}
        </Button>
      }
    >
      <div className="twilio-tool">
        <ToggleGroup
          value={[tool]}
          onValueChange={(value) => {
            const next = value[0];
            if (next === 'cloudflared' || next === 'ngrok') setTool(next);
          }}
          variant="outline"
          size="sm"
          aria-label={t('twilioIntegration.tunnelTool')}
        >
          <ToggleGroupItem value="cloudflared">cloudflared</ToggleGroupItem>
          <ToggleGroupItem value="ngrok">ngrok</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <ol className="twilio-substeps">
        <li>
          {install ? (
            <CopyField label={t('twilioIntegration.installTool', { tool })} value={install} />
          ) : (
            <p>
              {t('twilioIntegration.installFrom', { tool })}{' '}
              <a href={INSTALL_DOCS[tool]} target="_blank" rel="noopener noreferrer">
                {INSTALL_DOCS[tool].replace(/^https:\/\//, '')}
              </a>
            </p>
          )}
        </li>
        <li>
          <CopyField
            label={t('twilioIntegration.runTunnel')}
            value={tunnelCommand(tool, target)}
            hint={t('twilioIntegration.tunnelHint')}
          />
        </li>
        <li>
          <Field
            label={t('twilioIntegration.publicUrl')}
            htmlFor="twilio-public-url"
            hint={t('twilioIntegration.publicUrlHint', { tool })}
          >
            <Input
              id="twilio-public-url"
              value={draft.public_base_url}
              spellCheck={false}
              inputMode="url"
              placeholder={
                tool === 'cloudflared'
                  ? 'https://example.trycloudflare.com'
                  : 'https://example.ngrok-free.app'
              }
              aria-describedby="twilio-public-url-hint"
              onChange={(event) => set('public_base_url', event.target.value)}
            />
          </Field>
        </li>
      </ol>
      <div className="twilio-check" role="status">
        <span className={server.listener.running ? 'twilio-dot is-on' : 'twilio-dot'} aria-hidden />
        <span>
          {checked || server.listener.running || server.enabled
            ? listenerMessage
            : t('twilioIntegration.checkPrompt')}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={busy !== null}
          aria-busy={busy === 'check'}
          onClick={async () => {
            setBusy('check');
            try {
              await state.refetch();
              setChecked(true);
            } finally {
              setBusy(null);
            }
          }}
        >
          <RefreshCwIcon aria-hidden />
          {t('twilioIntegration.check')}
        </Button>
      </div>
      <p className="twilio-note">{t('twilioIntegration.exposureWarning')}</p>
      {errors.tunnel && (
        <p role="alert" className="twilio-error">
          {errors.tunnel}
        </p>
      )}
    </StepCard>
  );

  // ── Step 3: phone number ────────────────────────────────────────────────
  const numberSupported = Boolean(calls && 'from_number' in calls && callsDraft);
  const numberInvalid =
    numberSupported && Boolean(callsDraft?.from_number) && !E164.test(callsDraft!.from_number);
  const numberStep = (
    <StepCard
      id="number"
      index={3}
      title={t('twilioIntegration.stepNumber')}
      description={t('twilioIntegration.stepNumberHint')}
      disabled={busy !== null}
      status={steps.number}
      footer={
        numberSupported ? (
          <Button
            disabled={busy !== null || numberInvalid}
            aria-busy={busy === 'number'}
            onClick={async () => {
              setBusy('number');
              clear('number');
              try {
                await saveCalls('number', { from_number: callsDraft!.from_number.trim() });
                toast.success(t('nav.saved'));
              } catch (reason) {
                fail('number', explain(reason));
              } finally {
                setBusy(null);
              }
            }}
          >
            {t('common.save')}
          </Button>
        ) : undefined
      }
    >
      {numberSupported && (
        <Field
          label={t('twilioIntegration.phoneNumber')}
          htmlFor="twilio-number"
          hint={
            numberInvalid
              ? t('twilioIntegration.phoneNumberInvalid')
              : t('twilioIntegration.phoneNumberHint')
          }
        >
          <Input
            id="twilio-number"
            type="tel"
            value={callsDraft!.from_number}
            placeholder="+15551234567"
            autoComplete="off"
            aria-invalid={numberInvalid || undefined}
            aria-describedby="twilio-number-hint"
            onChange={(event) => setCalls('from_number', event.target.value)}
          />
        </Field>
      )}
      {server.webhook_url ? (
        <>
          <CopyField label={t('twilioIntegration.webhookUrl')} value={server.webhook_url} />
          <ol className="twilio-console-steps">
            <li>{t('twilioIntegration.consoleOpen')}</li>
            <li>{t('twilioIntegration.consoleVoice')}</li>
            <li>{t('twilioIntegration.consolePaste')}</li>
          </ol>
        </>
      ) : (
        <DisabledReason id="twilio-number-reason">
          {t('twilioIntegration.webhookNeedsTunnel')}
        </DisabledReason>
      )}
      {errors.number && (
        <p role="alert" className="twilio-error">
          {errors.number}
        </p>
      )}
    </StepCard>
  );

  // ── Step 4: voice & behaviour ───────────────────────────────────────────
  const activeEngine = engines.activeTts?.display_name ?? engines.data?.tts?.active ?? '';
  const voiceItems = [
    { value: DEFAULT, label: t('twilioIntegration.defaultVoice') },
    ...(profiles.data ?? []).map((profile) => ({ value: profile.id, label: profile.name })),
  ];
  const ownVoices = new Set(
    (profiles.data ?? [])
      .filter((profile) => Boolean(profile.verified_own_voice) || profile.kind === 'design')
      .map((profile) => profile.id),
  );
  const ttsBackends = (engines.data?.tts?.backends ?? []).filter((backend) => backend.available);
  const engineItems = [
    {
      value: DEFAULT,
      label: activeEngine
        ? t('twilioIntegration.activeEngineNamed', { name: activeEngine })
        : t('twilioIntegration.activeEngine'),
    },
    ...ttsBackends.map((backend) => ({ value: backend.id, label: backend.display_name })),
  ];
  const agentSupported = Boolean(calls && 'inbound_mode' in calls && callsDraft);
  const disclosureSupported = Boolean(calls && 'disclosure_template' in calls && callsDraft);
  const defaultDisclosure = t('twilioIntegration.disclosureDefault');
  const hasDisclosure = draft.greeting.includes(defaultDisclosure);
  const voiceStep = (
    <StepCard
      id="voice"
      index={4}
      title={t('twilioIntegration.stepVoice')}
      description={t('twilioIntegration.stepVoiceHint')}
      disabled={busy !== null}
      status={steps.voice}
      footer={
        <Button
          disabled={busy !== null}
          aria-busy={busy === 'voice'}
          onClick={async () => {
            const saved = await save(
              'voice',
              { voice_id: draft.voice_id, engine: draft.engine, greeting: draft.greeting },
              ['voice_id', 'engine', 'greeting'],
            );
            if (saved && callsDraft && (agentSupported || disclosureSupported)) {
              try {
                await saveCalls('voice', {
                  ...(agentSupported ? { inbound_mode: callsDraft.inbound_mode } : {}),
                  ...(disclosureSupported ? { disclosure_template: callsDraft.disclosure } : {}),
                });
              } catch (reason) {
                fail('voice', explain(reason));
              }
            }
          }}
        >
          {t('common.save')}
        </Button>
      }
    >
      <div className="twilio-fields twilio-fields-pair">
        <Field
          label={t('twilioIntegration.voice')}
          htmlFor="twilio-voice"
          hint={t('twilioIntegration.voiceCallHint')}
        >
          <Select
            items={voiceItems}
            value={draft.voice_id || DEFAULT}
            onValueChange={(value) => set('voice_id', value === DEFAULT ? '' : String(value))}
          >
            <SelectTrigger
              id="twilio-voice"
              className="w-full"
              aria-describedby="twilio-voice-hint"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {voiceItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                  {ownVoices.has(item.value) && (
                    <span className="twilio-own">{t('twilioIntegration.canCall')}</span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t('twilioIntegration.engine')} htmlFor="twilio-engine">
          <Select
            items={engineItems}
            value={draft.engine || DEFAULT}
            onValueChange={(value) => set('engine', value === DEFAULT ? '' : String(value))}
          >
            <SelectTrigger id="twilio-engine" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {engineItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
      <div className="twilio-field">
        <span id="twilio-mode-label" className="twilio-field-label">
          {t('twilioIntegration.inboundMode')}
        </span>
        <ToggleGroup
          value={[agentSupported ? callsDraft!.inbound_mode : 'greeting']}
          onValueChange={(value) => {
            const next = value[0];
            if (agentSupported && (next === 'greeting' || next === 'agent'))
              setCalls('inbound_mode', next);
          }}
          variant="outline"
          size="sm"
          aria-labelledby="twilio-mode-label"
          aria-describedby={agentSupported ? undefined : 'twilio-mode-reason'}
        >
          <ToggleGroupItem value="greeting">{t('twilioIntegration.modeGreeting')}</ToggleGroupItem>
          <ToggleGroupItem value="agent" disabled={!agentSupported}>
            {t('twilioIntegration.modeAgent')}
          </ToggleGroupItem>
        </ToggleGroup>
        {!agentSupported && (
          <DisabledReason id="twilio-mode-reason">
            {t('twilioIntegration.modeAgentSoon')}
          </DisabledReason>
        )}
      </div>
      <Field
        label={t('twilioIntegration.greeting')}
        htmlFor="twilio-greeting"
        hint={t('twilioIntegration.greetingCount', {
          length: draft.greeting.length,
          max: server.limits.max_greeting_chars,
        })}
        wide
      >
        <Textarea
          id="twilio-greeting"
          value={draft.greeting}
          rows={3}
          maxLength={server.limits.max_greeting_chars}
          placeholder={t('twilioIntegration.greetingPlaceholder')}
          aria-describedby="twilio-greeting-hint"
          onChange={(event) => set('greeting', event.target.value)}
        />
      </Field>
      <div className="twilio-field">
        {disclosureSupported ? (
          <>
            <label htmlFor="twilio-disclosure" className="twilio-field-label">
              {t('twilioIntegration.disclosure')}
            </label>
            <Textarea
              id="twilio-disclosure"
              rows={2}
              value={callsDraft!.disclosure}
              aria-describedby="twilio-disclosure-hint"
              onChange={(event) => setCalls('disclosure', event.target.value)}
            />
          </>
        ) : (
          <div className="twilio-disclosure">
            <span className="twilio-field-label">{t('twilioIntegration.disclosure')}</span>
            <q>{defaultDisclosure}</q>
            <Button
              size="sm"
              variant="outline"
              disabled={hasDisclosure}
              aria-describedby="twilio-disclosure-hint"
              onClick={() =>
                set(
                  'greeting',
                  `${defaultDisclosure} ${draft.greeting}`
                    .trim()
                    .slice(0, server.limits.max_greeting_chars),
                )
              }
            >
              {hasDisclosure
                ? t('twilioIntegration.disclosureAdded')
                : t('twilioIntegration.disclosureAdd')}
            </Button>
          </div>
        )}
        <small id="twilio-disclosure-hint">{t('twilioIntegration.disclosureNote')}</small>
      </div>
      {errors.voice && (
        <p role="alert" className="twilio-error">
          {errors.voice}
        </p>
      )}
    </StepCard>
  );

  // ── Rail: checklist, test, recent calls ────────────────────────────────
  const testLocally = async () => {
    if (busy) return;
    setBusy('preview');
    clear('preview');
    try {
      const res = await apiFetch('/api/integrations/twilio/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: draft.greeting,
          voice_id: draft.voice_id,
          engine: draft.engine,
        }),
      });
      const url = URL.createObjectURL(await res.blob());
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
      previewRef.current = url;
      setPreview(url);
    } catch (reason) {
      fail('preview', explain(reason));
    } finally {
      setBusy(null);
    }
  };
  const previewReason = !draft.greeting.trim() ? t('twilioIntegration.previewNeedsGreeting') : '';
  const time = new Intl.DateTimeFormat(i18n.language, { timeStyle: 'short', dateStyle: 'short' });

  const checklistCard = (
    <section
      className="integration-detail-panel twilio-rail-card"
      aria-labelledby="twilio-checklist"
    >
      <h3 id="twilio-checklist">{t('twilioIntegration.checklist')}</h3>
      <ul className="twilio-checklist">
        {checklist.map((item) => {
          const step = CHECK_STEP[item.id];
          const label = t(`twilioIntegration.check_${item.id}`);
          const content = (
            <>
              {item.ok ? (
                <CheckCircle2Icon className="is-ok" aria-hidden />
              ) : (
                <CircleDashedIcon aria-hidden />
              )}
              <span>
                <span>{label}</span>
                <span className="sr-only">
                  {item.ok ? t('twilioIntegration.step_done') : t('twilioIntegration.step_todo')}
                </span>
                {item.detail && <small>{item.detail}</small>}
              </span>
            </>
          );
          return (
            <li key={item.id} data-ok={item.ok}>
              {step ? (
                <button type="button" onClick={() => focusStep(step)}>
                  {content}
                </button>
              ) : (
                <div>{content}</div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );

  const testCard = (
    <section className="integration-detail-panel twilio-rail-card" aria-labelledby="twilio-test">
      <h3 id="twilio-test">{t('twilioIntegration.stepTest')}</h3>
      <p className="twilio-note">{t('twilioIntegration.testHint')}</p>
      <Button
        variant="outline"
        className="w-full"
        disabled={busy !== null || Boolean(previewReason)}
        aria-busy={busy === 'preview'}
        aria-describedby={previewReason ? 'twilio-preview-reason' : undefined}
        onClick={() => void testLocally()}
      >
        <PlayIcon aria-hidden />
        {busy === 'preview' ? t('twilioIntegration.testing') : t('twilioIntegration.test')}
      </Button>
      {previewReason && <DisabledReason id="twilio-preview-reason">{previewReason}</DisabledReason>}
      {preview && (
        <audio controls src={preview} aria-label={t('twilioIntegration.test')} className="w-full" />
      )}
      {errors.preview && (
        <p role="alert" className="twilio-error">
          {errors.preview}
        </p>
      )}
      {callsRoute ? (
        <a href="#/calls" className={buttonVariants({ variant: 'ghost', className: 'w-full' })}>
          {t('twilioIntegration.openCalls')}
          <ArrowRightIcon aria-hidden />
        </a>
      ) : (
        <>
          <Button
            variant="ghost"
            className="w-full"
            disabled
            aria-describedby="twilio-callme-reason"
          >
            <PhoneCallIcon aria-hidden />
            {t('twilioIntegration.callMe')}
          </Button>
          <DisabledReason id="twilio-callme-reason">
            {t('twilioIntegration.callMeSoon')}
          </DisabledReason>
        </>
      )}
    </section>
  );

  const callsCard = (
    <section className="integration-detail-panel twilio-rail-card" aria-labelledby="twilio-calls">
      <h3 id="twilio-calls" className="twilio-calls-title">
        {t('twilioIntegration.recentCalls')}
        <span>
          {t('twilioIntegration.activeCalls', {
            active: server.calls.active,
            max: server.calls.max_concurrent,
          })}
        </span>
      </h3>
      {server.calls.recent.length === 0 ? (
        <p className="twilio-note">{t('twilioIntegration.noCalls')}</p>
      ) : (
        <ul className="twilio-calls">
          {server.calls.recent.map((call, index) => (
            <li key={`${call.started_at}-${index}`}>
              <span>
                {time.format(new Date(call.started_at * 1000))} · {call.call}
              </span>
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
    </section>
  );

  const howItWorks = (
    <Collapsible className="twilio-how">
      <CollapsibleTrigger className="twilio-how-trigger">
        {t('twilioIntegration.howItWorks')}
        <ChevronDownIcon aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent className="twilio-how-body">
        <ol className="twilio-flow" aria-label={t('twilioIntegration.flowLabel')}>
          <li>
            <span>{t('twilioIntegration.flowTwilio')}</span>
          </li>
          <li>
            <span>{t('twilioIntegration.flowTunnel')}</span>
          </li>
          <li>
            <span>{t('twilioIntegration.flowGateway', { target })}</span>
          </li>
          <li>
            <span>{t('twilioIntegration.flowStudio')}</span>
          </li>
        </ol>
        <ul className="twilio-security">
          <li>{t('twilioIntegration.securitySigned')}</li>
          <li>{t('twilioIntegration.securityTokens')}</li>
          <li>{t('twilioIntegration.securityIsolation')}</li>
          <li>{t('twilioIntegration.securitySecret')}</li>
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );

  return (
    <>
      {hero({ status: <StatusPill status={overall} />, action: primaryAction })}
      {errors.enable && (
        <p role="alert" className="twilio-error twilio-page-error">
          {errors.enable}
        </p>
      )}
      <IntegrationDetailColumns
        main={
          <div className="twilio-steps">
            {accountStep}
            {tunnelStep}
            {numberStep}
            {voiceStep}
            {howItWorks}
          </div>
        }
        railTop={
          <>
            {checklistCard}
            {testCard}
            {callsCard}
          </>
        }
        railBottom={rail}
      />
    </>
  );
}
