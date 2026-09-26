import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CheckIcon, CopyIcon, Link2Icon, LogOutIcon, Trash2Icon, WifiIcon } from 'lucide-react';
import type { TFunction } from 'i18next';
import { PipelineFailure } from '@/components/pipeline-failure';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { apiJson, describeError } from '@/lib/api/client';
import { isImeComposing } from '@/lib/ime';
import { SettingsRow, SettingsSection } from './settings-layout';

interface InboundKey {
  key_id: string;
  label: string;
  last_seen_at?: number;
  revoked: boolean;
}

interface InboundSession {
  session_id: string;
  label: string;
  peer: string;
  tasks_run: number;
}

interface InboundConnection {
  endpoint: string;
  connected: boolean;
  last_error?: string;
}

interface InboundState {
  enabled: boolean;
  running: boolean;
  bind: string;
  port: number;
  exposed: boolean;
  startup_error?: string | null;
  keys: InboundKey[];
  sessions: InboundSession[];
  connections: InboundConnection[];
}

interface IssuedAccess {
  key_id: string;
  label: string;
  connection_string: string;
}

function relativeSeen(seconds: number, t: TFunction): string {
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - seconds) / 60));
  if (minutes < 1) return t('settings.inbound_just_now');
  if (minutes < 60) return t('settings.inbound_minutes_ago', { count: minutes });
  return t('settings.inbound_hours_ago', { count: Math.round(minutes / 60) });
}

function InboundSecret({ issued, onDone }: { issued: IssuedAccess; onDone: () => void }) {
  const { t } = useTranslation();
  const [qr, setQr] = useState('');
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  useEffect(() => {
    let active = true;
    void QRCode.toDataURL(issued.connection_string)
      .then((url) => active && setQr(url))
      .catch(() => {
        if (active) setQr('');
      });
    return () => {
      active = false;
    };
  }, [issued.connection_string]);
  return (
    <div className="m-4 grid gap-4 rounded-xl border border-primary/25 bg-primary/[0.06] p-4 @xl:grid-cols-[1fr_auto]">
      <div className="min-w-0 space-y-3">
        <div>
          <p className="text-sm font-medium">
            {t('settings.inbound_shown_once', { label: issued.label })}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('settings.inbound_shown_once_warning')}
          </p>
        </div>
        <code className="block max-h-28 overflow-auto break-all rounded-lg bg-background/70 p-3 text-xs">
          {issued.connection_string}
        </code>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(issued.connection_string);
                setCopied(true);
                setCopyError(false);
              } catch {
                setCopied(false);
                setCopyError(true);
              }
            }}
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
            {t(copied ? 'settings.workers_copied' : 'settings.workers_copy')}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDone}>
            {t('settings.workers_secret_done')}
          </Button>
          {copyError && (
            <span className="text-xs text-destructive">{t('settings.workers_copy_failed')}</span>
          )}
        </div>
      </div>
      {qr && (
        <img
          src={qr}
          alt={t('settings.workers_qr_alt')}
          className="size-36 rounded-lg bg-white p-2"
        />
      )}
    </div>
  );
}

export function InboundWorkersSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [bind, setBind] = useState('');
  const [label, setLabel] = useState('');
  const [connectionString, setConnectionString] = useState('');
  const [issued, setIssued] = useState<IssuedAccess | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['workers', 'inbound'],
    queryFn: ({ signal }) => apiJson<InboundState>('/workers/inbound', { signal }),
    refetchInterval: 5000,
  });
  const state = query.data;
  const keys = state?.keys.filter((key) => !key.revoked) ?? [];
  const act = async (work: () => Promise<unknown>, after?: () => void) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await work();
      after?.();
      setConfirmRevoke(null);
      await client.invalidateQueries({ queryKey: ['workers'] });
    } catch (error) {
      setError(describeError(error));
    } finally {
      setBusy(false);
    }
  };
  const toggle = (enabled: boolean, nextBind = '') =>
    act(
      () =>
        apiJson('/workers/inbound/enabled', {
          method: 'POST',
          body: JSON.stringify({ enabled, ...(nextBind ? { bind: nextBind } : {}) }),
        }),
      () => {
        if (!enabled) setIssued(null);
        if (nextBind) setBind('');
      },
    );

  return (
    <>
      <SettingsSection icon={WifiIcon} title={t('settings.inbound_title')}>
        <SettingsRow
          id="inbound-enabled"
          title={t('settings.inbound_enable')}
          description={t('settings.inbound_enable_hint')}
        >
          <Switch
            checked={Boolean(state?.enabled)}
            disabled={!state || busy}
            aria-label={t('settings.inbound_enable')}
            onCheckedChange={(enabled) => void toggle(enabled)}
          />
        </SettingsRow>
        {state?.startup_error && (
          <div className="p-4">
            <PipelineFailure fallback={state.startup_error} />
          </div>
        )}
        {state?.enabled && (
          <>
            <SettingsRow
              id="inbound-bind"
              title={t('settings.inbound_bind')}
              description={
                state.exposed
                  ? t('settings.inbound_bind_exposed', {
                      address: `${state.bind}:${state.port}`,
                    })
                  : t('settings.inbound_bind_local')
              }
            >
              <Input
                value={bind}
                className="w-48 font-mono"
                placeholder={state.bind || '127.0.0.1'}
                name="inbound-bind-address"
                autoComplete="off"
                spellCheck={false}
                aria-label={t('settings.inbound_bind')}
                disabled={busy}
                onChange={(event) => setBind(event.target.value)}
              />
              <Button
                size="sm"
                variant="outline"
                disabled={busy || !bind.trim()}
                onClick={() => void toggle(true, bind.trim())}
              >
                {t('settings.inbound_bind_apply')}
              </Button>
              {state.exposed && (
                <Badge variant="destructive">{t('settings.inbound_exposed_badge')}</Badge>
              )}
            </SettingsRow>
            <SettingsRow
              id="inbound-add-person"
              title={t('settings.inbound_add_person')}
              description={t('settings.inbound_add_person_hint')}
            >
              <Input
                value={label}
                className="w-48"
                placeholder={t('settings.inbound_label_placeholder')}
                name="inbound-person-label"
                autoComplete="off"
                aria-label={t('settings.inbound_label')}
                disabled={busy}
                onChange={(event) => setLabel(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !busy && !isImeComposing(event)) {
                    void act(
                      async () => {
                        const result = await apiJson<IssuedAccess>('/workers/inbound/keys', {
                          method: 'POST',
                          body: JSON.stringify({ label: label.trim() }),
                        });
                        setIssued(result);
                      },
                      () => setLabel(''),
                    );
                  }
                }}
              />
              <Button
                size="sm"
                disabled={busy}
                onClick={() =>
                  void act(
                    async () => {
                      const result = await apiJson<IssuedAccess>('/workers/inbound/keys', {
                        method: 'POST',
                        body: JSON.stringify({ label: label.trim() }),
                      });
                      setIssued(result);
                    },
                    () => setLabel(''),
                  )
                }
              >
                {t('settings.inbound_create')}
              </Button>
            </SettingsRow>
            {issued && <InboundSecret issued={issued} onDone={() => setIssued(null)} />}
            {keys.length > 0 && (
              <div className="px-4 py-3">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t('settings.inbound_people')}
                </p>
                <div className="space-y-1">
                  {keys.map((key) => (
                    <div
                      key={key.key_id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-2 hover:bg-muted/40"
                    >
                      <span className="min-w-0 truncate text-sm">
                        {key.label}
                        {key.last_seen_at ? (
                          <span className="text-muted-foreground">
                            {' · '}
                            {t('settings.inbound_last_seen', {
                              when: relativeSeen(key.last_seen_at, t),
                            })}
                          </span>
                        ) : null}
                      </span>
                      {confirmRevoke === key.key_id ? (
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <span className="max-w-72 text-xs text-muted-foreground">
                            {t('settings.inbound_revoke_confirm', { label: key.label })}
                          </span>
                          <Button
                            size="sm"
                            variant="destructive"
                            disabled={busy}
                            onClick={() =>
                              void act(() =>
                                apiJson(`/workers/inbound/keys/${encodeURIComponent(key.key_id)}`, {
                                  method: 'DELETE',
                                }),
                              )
                            }
                          >
                            {t('settings.inbound_revoke')}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmRevoke(null)}>
                            {t('common.cancel')}
                          </Button>
                        </div>
                      ) : (
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          disabled={busy}
                          aria-label={t('settings.inbound_revoke')}
                          onClick={() => setConfirmRevoke(key.key_id)}
                        >
                          <Trash2Icon />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="px-4 py-3">
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {t('settings.inbound_connected_now')}
              </p>
              {state.sessions.length ? (
                <div className="space-y-1">
                  {state.sessions.map((session) => (
                    <div
                      key={session.session_id}
                      className="flex items-center justify-between gap-3 rounded-lg px-2 py-2 hover:bg-muted/40"
                    >
                      <span className="min-w-0 truncate text-sm">
                        {session.label}
                        <span className="text-muted-foreground">
                          {' · '}
                          {session.peer}
                          {' · '}
                          {t('settings.inbound_jobs_run', { count: session.tasks_run })}
                        </span>
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() =>
                          void act(() =>
                            apiJson(
                              `/workers/inbound/sessions/${encodeURIComponent(session.session_id)}/disconnect`,
                              { method: 'POST' },
                            ),
                          )
                        }
                      >
                        <LogOutIcon />
                        {t('settings.inbound_disconnect')}
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">{t('settings.inbound_nobody')}</p>
              )}
            </div>
          </>
        )}
        {(error || query.isError) && (
          <div className="p-4">
            <PipelineFailure
              fallback={error || describeError(query.error)}
              onDismiss={error ? () => setError('') : undefined}
              action={
                query.isError ? (
                  <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>
                    {t('common.retry')}
                  </Button>
                ) : undefined
              }
            />
          </div>
        )}
      </SettingsSection>

      <SettingsSection icon={Link2Icon} title={t('settings.inbound_connect_title')}>
        <SettingsRow
          id="inbound-connection"
          title={t('settings.inbound_connect_row')}
          description={t('settings.inbound_connect_hint')}
        >
          <Input
            value={connectionString}
            type="password"
            className="min-w-72 font-mono"
            name="inbound-connection-string"
            autoComplete="off"
            spellCheck={false}
            translate="no"
            placeholder={t('settings.inbound_connect_placeholder')}
            aria-label={t('settings.inbound_connect_row')}
            disabled={busy}
            onChange={(event) => setConnectionString(event.target.value)}
          />
          <Button
            size="sm"
            disabled={busy || !connectionString.trim()}
            onClick={() =>
              void act(
                () =>
                  apiJson('/workers/inbound/connections', {
                    method: 'POST',
                    body: JSON.stringify({ connection_string: connectionString.trim() }),
                  }),
                () => setConnectionString(''),
              )
            }
          >
            {t('settings.inbound_connect')}
          </Button>
        </SettingsRow>
        {state?.connections.map((connection) => (
          <div
            key={connection.endpoint}
            className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
          >
            <span className="min-w-0 truncate font-mono text-xs">
              {connection.endpoint}
              <span className="font-sans text-muted-foreground">
                {' · '}
                {connection.connected
                  ? t('settings.inbound_connected')
                  : connection.last_error || t('settings.inbound_connecting')}
              </span>
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() =>
                void act(() =>
                  apiJson(
                    `/workers/inbound/connections/${encodeURIComponent(connection.endpoint)}`,
                    { method: 'DELETE' },
                  ),
                )
              }
            >
              <Trash2Icon />
              {t('settings.inbound_forget')}
            </Button>
          </div>
        ))}
      </SettingsSection>
    </>
  );
}
