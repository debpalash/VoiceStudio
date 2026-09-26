import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRoundIcon } from 'lucide-react';
import { exchangeApiKey } from '../../../../../frontend/src/api/authSession';
import { absoluteApiBase, ApiError, apiFetch } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

type AuthMode = 'apikey' | 'pin';

export function WebAuthGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [checked, setChecked] = useState(!__WEB_DEPLOYMENT__);
  const [mode, setMode] = useState<AuthMode | null>(null);
  const [value, setValue] = useState('');
  const [pending, setPending] = useState(false);
  const [errorStatus, setErrorStatus] = useState<number | null | undefined>();
  const submitting = useRef(false);

  useEffect(() => {
    if (!__WEB_DEPLOYMENT__) return;
    const required = (event: Event) => {
      const detail = (event as CustomEvent<{ mode?: string }>).detail;
      setMode(detail?.mode === 'apikey' ? 'apikey' : 'pin');
      setChecked(true);
      setValue('');
      setErrorStatus(undefined);
    };
    window.addEventListener('ov:auth-required', required);
    void apiFetch('/engines')
      .then(() => setChecked(true))
      .catch((error) => {
        if (!(error instanceof ApiError) || (error.status !== 401 && error.status !== 403)) {
          setChecked(true);
        }
      });
    return () => window.removeEventListener('ov:auth-required', required);
  }, []);

  if (!checked) {
    return (
      <div className="flex h-full min-h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        {t('preferences.loading')}
      </div>
    );
  }
  if (!mode) return <>{children}</>;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const credential = value.trim();
    if (!credential || submitting.current) return;
    submitting.current = true;
    setPending(true);
    setErrorStatus(undefined);
    try {
      if (mode === 'apikey') {
        setValue('');
        await exchangeApiKey(credential, { apiBase: absoluteApiBase() });
      } else {
        sessionStorage.setItem('ov_pin', credential);
      }
      window.location.reload();
    } catch (error) {
      setErrorStatus((error as { status?: number }).status ?? null);
    } finally {
      submitting.current = false;
      setPending(false);
    }
  };

  const apiKey = mode === 'apikey';
  return (
    <div
      className="flex h-full min-h-screen items-center justify-center bg-background p-6"
      role="dialog"
      aria-modal="true"
    >
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-2 flex size-9 items-center justify-center rounded-md bg-primary/10 text-primary">
            <KeyRoundIcon className="size-5" />
          </div>
          <CardTitle>
            {t(apiKey ? 'settings.remote_backend_title' : 'network.share_on_network')}
          </CardTitle>
          <CardDescription>
            {t(apiKey ? 'settings.remote_backend_key_placeholder' : 'network.share_confirm_hint')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-3" onSubmit={submit}>
            <label className="block space-y-1.5 text-xs font-medium" htmlFor="web-auth-credential">
              <span>{t(apiKey ? 'settings.remote_backend_key' : 'network.pin')}</span>
              <Input
                id="web-auth-credential"
                type={apiKey ? 'password' : 'text'}
                inputMode={apiKey ? undefined : 'numeric'}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                autoComplete="off"
                disabled={pending}
                autoFocus
              />
            </label>
            {errorStatus !== undefined && (
              <p className="text-xs text-destructive" role="alert">
                {errorStatus
                  ? t('settings.remote_backend_error_http', { status: errorStatus })
                  : t('settings.remote_backend_error_network')}
              </p>
            )}
            <Button className="w-full" type="submit" disabled={pending || !value.trim()}>
              {t('settings.inbound_connect')}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
