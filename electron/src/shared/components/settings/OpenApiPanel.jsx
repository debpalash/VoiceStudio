import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Braces, Copy, ExternalLink, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { API, apiFetch } from '../../api/client';
import { getAdminSession } from '../../api/authSession';
import { openExternal } from '../../api/external';
import { copyText } from '../../utils/copyText';
import { SettingsSection } from './primitives';
import { Button } from '../../ui';

/**
 * Settings → OpenAPI — an embedded, interactive reference for VoiceStudio's own
 * local backend REST API (FastAPI, live spec at `<backend>/openapi.json`).
 *
 * The spec is fetched here (from the resolved backend base — same helper every
 * other API call uses, so it follows a remote-backend / LAN-share override) so
 * we own the loading and unreachable-backend states. The parsed spec is then
 * handed to Scalar's bundled React component as inline `content`. Scalar itself
 * is lazy-loaded (ScalarApiReference.jsx) — its ~heavy bundle only downloads
 * once the spec is in hand, and the fallback path never touches it. See that
 * wrapper for how it's kept CDN-free (local-first hard constraint).
 */

// Root-level FastAPI spec route (NOT under /api). Reused by apiFetch (attaches
// LAN PIN / API key) and rendered verbatim for the copy / open-raw affordances.
const SPEC_PATH = '/openapi.json';

const ScalarApiReference = lazy(() => import('./ScalarApiReference'));

/**
 * Fetch the spec from the backend base first — apiFetch is the path every other
 * call takes (remote-backend override, LAN PIN, API key). When that transport
 * fails and this page is itself served over http(s), try the SAME-ORIGIN copy:
 * the dev server proxies `/openapi.json` to the backend (vite.config.js), and
 * the packaged web build is served by the backend, so on those origins the
 * relative URL IS the backend. This is what keeps the reference working in an
 * embedded webview that walls off every origin but the page's own (the Claude
 * desktop browser pane, for one) — there the direct base is unreachable while
 * the same-origin route is fine. A file:// / app:// shell has no same-origin
 * spec; the fallback fails too and the original error surfaces as before.
 */
async function fetchSpec() {
  try {
    const res = await apiFetch(SPEC_PATH);
    return await res.json();
  } catch (err) {
    const proto = typeof window !== 'undefined' ? window.location?.protocol : '';
    if (!/^https?:$/.test(proto)) throw err;
    const base = new URL(API, window.location.href);
    const sameOrigin = base.origin === window.location.origin;
    // vite.config.js forwards to this one fixed local backend, not overrides.
    const knownDevProxy =
      import.meta.env.DEV &&
      ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname) &&
      base.protocol === 'http:' &&
      ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname) &&
      base.port === '3900';
    if (!sameOrigin && !knownDevProxy) throw err;
    const headers = new Headers();
    const session = getAdminSession(API);
    if (session) headers.set('Authorization', `Bearer ${session.token}`);
    try {
      const pin = sessionStorage.getItem('ov_pin');
      if (pin) headers.set('X-OmniVoice-Pin', pin);
    } catch {
      /* Cookie-authenticated requests also work without storage. */
    }
    let res;
    try {
      res = await fetch(SPEC_PATH, { cache: 'no-store', headers, credentials: 'include' });
    } catch {
      throw err;
    }
    if (!res.ok) throw err;
    return await res.json();
  }
}

function LoadingState({ label }) {
  return (
    <div
      data-testid="openapi-loading"
      className="flex items-center justify-center gap-[var(--space-2)] py-[var(--space-8)] text-[color:var(--chrome-fg-muted)] text-[length:var(--text-sm)]"
    >
      <RefreshCw size={14} className="motion-safe:animate-spin" aria-hidden="true" />
      {label}
    </div>
  );
}

export default function OpenApiPanel() {
  const { t } = useTranslation();
  const specUrl = `${API}${SPEC_PATH}`;

  // 'loading' → fetch in flight · 'ready' → spec parsed · 'error' → unreachable
  const [phase, setPhase] = useState('loading');
  const [spec, setSpec] = useState(null);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      setSpec(await fetchSpec());
      setPhase('ready');
    } catch {
      // apiFetch already retried transient transport failures and the
      // same-origin fallback was tried; a throw here means the backend
      // genuinely isn't serving the spec right now.
      setSpec(null);
      setPhase('error');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const copyUrl = useCallback(async () => {
    const ok = await copyText(specUrl);
    if (ok) {
      toast.success(t('openapi.copied', { defaultValue: 'Spec URL copied' }));
    } else {
      // copyText returns false when both clipboard paths fail (e.g. a
      // non-secure LAN-share context) — never leave the click unanswered.
      toast.error(
        t('openapi.copy_failed', {
          defaultValue: 'Copy failed — select and copy the URL above manually.',
        }),
      );
    }
  }, [specUrl, t]);

  const openRaw = useCallback(() => {
    openExternal(specUrl);
  }, [specUrl]);

  const loadingLabel = t('openapi.loading', { defaultValue: 'Loading API spec…' });

  return (
    <SettingsSection icon={Braces} title={t('openapi.title', { defaultValue: 'VoiceStudio API' })}>
      {/* Spec URL + copy / open-raw affordances — useful whether the embed
          loaded or not, so shown in every phase. */}
      <div className="mb-[var(--space-3)] flex flex-wrap items-center gap-[var(--space-2)]">
        <code className="min-w-0 max-w-full truncate rounded-[4px] bg-[color-mix(in_srgb,var(--chrome-fg)_8%,transparent)] px-[var(--space-2)] py-[2px] text-[length:var(--text-xs)] [font-family:var(--chrome-font-mono,monospace)] text-[color:var(--chrome-fg-muted)]">
          {specUrl}
        </code>
        <Button
          variant="subtle"
          size="sm"
          leading={<Copy size={13} aria-hidden="true" />}
          onClick={copyUrl}
          aria-label={t('openapi.copy_url_aria', { defaultValue: 'Copy the /openapi.json URL' })}
          data-testid="openapi-copy-url"
        >
          {t('openapi.copy_url', { defaultValue: 'Copy spec URL' })}
        </Button>
        <Button
          variant="subtle"
          size="sm"
          leading={<ExternalLink size={13} aria-hidden="true" />}
          onClick={openRaw}
          aria-label={t('openapi.open_raw_aria', {
            defaultValue: 'Open the raw OpenAPI JSON in your browser',
          })}
          data-testid="openapi-open-raw"
        >
          {t('openapi.open_raw', { defaultValue: 'Open raw spec' })}
        </Button>
      </div>

      {phase === 'loading' && <LoadingState label={loadingLabel} />}

      {phase === 'error' && (
        <div
          role="alert"
          data-testid="openapi-unreachable"
          className="flex flex-col items-start gap-[var(--space-3)] rounded-[var(--chrome-radius-pill)] border border-[color:color-mix(in_srgb,var(--chrome-fg)_10%,transparent)] px-[var(--space-4)] py-[var(--space-5)]"
        >
          <div className="flex items-center gap-[var(--space-2)] font-semibold text-[color:var(--chrome-fg)]">
            <AlertTriangle size={15} className="[color:#fabd2f]" aria-hidden="true" />
            {t('openapi.unreachable_title', { defaultValue: 'Backend spec unavailable' })}
          </div>
          <p className="m-0 text-[length:var(--text-sm)] leading-[1.6] text-[color:var(--chrome-fg-muted)]">
            {t('openapi.unreachable_body', {
              url: specUrl,
              defaultValue:
                "Couldn't reach the local backend's OpenAPI spec at {{url}}. Make sure the backend is running, then retry.",
            })}
          </p>
          <Button
            variant="subtle"
            size="sm"
            leading={<RefreshCw size={13} aria-hidden="true" />}
            onClick={load}
            data-testid="openapi-retry"
          >
            {t('openapi.retry', { defaultValue: 'Retry' })}
          </Button>
        </div>
      )}

      {phase === 'ready' && (
        <div
          data-testid="openapi-reference"
          className="overflow-hidden rounded-[var(--chrome-radius-pill)] border border-[color:color-mix(in_srgb,var(--chrome-fg)_10%,transparent)] [height:min(72vh,900px)] min-h-[420px]"
        >
          <Suspense fallback={<LoadingState label={loadingLabel} />}>
            <ScalarApiReference spec={spec} />
          </Suspense>
        </div>
      )}
    </SettingsSection>
  );
}
