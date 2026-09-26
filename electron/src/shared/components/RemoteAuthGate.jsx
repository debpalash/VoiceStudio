import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { API } from '../api/client';
import { exchangeApiKey } from '../api/authSession';
import { reloadAfterApplicationPersistence } from '../utils/persistenceLifecycle';

// On a remote device the backend can demand EITHER a LAN-share PIN
// (NetworkAccessMiddleware → "PIN required") OR an API key (BearerKeyMiddleware
// → "API key required") — both 401. client.ts reads the detail, decides which,
// and dispatches a single `ov:auth-required` CustomEvent carrying the mode; this
// gate listens for it and swaps the app tree for the matching entry form.
// `forceGate` / `forceMode` are test-only. PINs remain tab-scoped; an API master
// is immediately exchanged for a short-lived session and never persisted.
export default function RemoteAuthGate({
  children,
  forceGate = false,
  forceMode = 'pin',
  reload = reloadAfterApplicationPersistence,
}) {
  const { t } = useTranslation();
  const [gated, setGated] = useState(forceGate);
  const [mode, setMode] = useState(forceMode);
  const [value, setValue] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onRequired = (e) => {
      setMode(e.detail?.mode === 'apikey' ? 'apikey' : 'pin');
      setGated(true);
      setError(null);
      setValue('');
    };
    window.addEventListener('ov:auth-required', onRequired);
    return () => window.removeEventListener('ov:auth-required', onRequired);
  }, []);

  if (!gated) return children;

  const i18nKey = mode === 'apikey' ? 'remote_apikey_gate' : 'remote_gate';

  const submit = async (e) => {
    e.preventDefault();
    if (pendingRef.current) return;
    const v = value.trim();
    if (!v) return;
    setError(null);
    pendingRef.current = true;
    setPending(true);
    if (mode === 'pin') {
      try {
        sessionStorage.setItem('ov_pin', v);
        await reload();
      } catch {
        setError({ status: undefined });
      } finally {
        pendingRef.current = false;
        setPending(false);
      }
      return;
    }

    // Remove the secret from controlled UI state before awaiting the network.
    // On success, exchangeApiKey also deletes any durable value left by an
    // older release; a failed exchange leaves it for the bootstrap migration
    // to retry on the next launch.
    setValue('');
    try {
      await exchangeApiKey(v, { apiBase: API });
      await reload();
    } catch (exchangeError) {
      setError({ status: exchangeError?.status });
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  return (
    <div className="remote-auth-gate" role="dialog" aria-modal="true">
      <form onSubmit={submit} className="remote-auth-gate__card">
        <h2>{t(`${i18nKey}.title`)}</h2>
        <p>{t(`${i18nKey}.body`)}</p>
        <label htmlFor="ov-cred">{t(`${i18nKey}.label`)}</label>
        <input
          id="ov-cred"
          type={mode === 'apikey' ? 'password' : 'text'}
          inputMode={mode === 'apikey' ? undefined : 'numeric'}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
          disabled={pending}
          autoFocus
        />
        {error && (
          <p role="alert">
            {error.status
              ? t('settings.remote_backend_error_http', { status: error.status })
              : t('settings.remote_backend_error_network')}
          </p>
        )}
        <button type="submit" disabled={pending}>
          {t(`${i18nKey}.connect`)}
        </button>
      </form>
    </div>
  );
}
