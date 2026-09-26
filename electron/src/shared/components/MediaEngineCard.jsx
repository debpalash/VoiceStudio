/**
 * Media engine — invisible unless it needs help.
 *
 * The media engine (ffmpeg/ffprobe) is an internal dependency, not a system
 * requirement: when the backend's resolution chain finds nothing, preflight
 * already kicked a background download of the app's own pinned static build.
 * This renders NOTHING when the engine is ready (the ideal outcome), a quiet
 * one-line progress while acquiring, and an actionable card only on failure
 * (Retry / use a copy already on the machine). yt-dlp never appears here —
 * it's an importable module, not a user task.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader } from 'lucide-react';
import { apiJson, apiFetch } from '../api/client';
import { Button } from '../ui';

export default function MediaEngineCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState(null);
  const [detectError, setDetectError] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const st = await apiJson('/media-tools/status');
      setStatus(st);
      return st;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const acquiring = status?.ops?.acquire?.state === 'running';
  useEffect(() => {
    if (!acquiring) return undefined;
    const iv = setInterval(refresh, 1500);
    return () => clearInterval(iv);
  }, [acquiring, refresh]);

  const post = async (path, body) => {
    setBusy(true);
    setDetectError(null);
    try {
      const res = await apiFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          detail = (await res.json())?.detail || detail;
        } catch {
          /* non-JSON body */
        }
        throw new Error(detail);
      }
      return true;
    } catch (e) {
      setDetectError(e?.message || String(e));
      return false;
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const useSystemCopy = async () => {
    // ffprobe rides along: the resolver derives the sibling ffprobe from a
    // resolved ffmpeg, so pinning ffmpeg is enough in the common case.
    await post('/media-tools/ffmpeg/use-system');
  };

  const chooseFile = async () => {
    try {
      if ('__TAURI_INTERNALS__' in window) {
        const { invoke } = await import('@tauri-apps/api/core');
        const selection = await invoke('authorize_host_path', { kind: 'ffmpeg' });
        if (selection) {
          await post('/media-tools/ffmpeg/custom-path', {
            authorization: selection.authorization,
          });
          return;
        }
      }
    } catch (e) {
      setDetectError(e?.message || String(e));
    }
  };

  const isDesktop = '__TAURI_INTERNALS__' in window;

  if (!status || status.ready) return null; // the ideal outcome: nothing.

  const op = status.ops?.acquire || {};
  if (op.state === 'running' || op.state === 'idle') {
    // idle-and-not-ready = preflight is about to kick the download (or a
    // recheck is in flight) — show the quiet line, never flash the card.
    return (
      <div
        className="mt-3 flex items-center gap-2 text-xs text-fg-muted"
        data-testid="media-engine-progress"
      >
        <Loader className="animate-spin" size={12} aria-hidden="true" />
        {t('setup.media_engine_preparing', { defaultValue: 'Preparing media engine…' })}
        {op.state === 'running' && ` ${Math.round((op.progress || 0) * 100)}%`}
      </div>
    );
  }

  return (
    <div
      className="mt-3 flex flex-col gap-1.5 rounded-md border border-border px-3 py-2.5"
      data-testid="media-engine-card"
    >
      <span className="text-sm font-semibold">
        {t('setup.media_engine_failed_title', { defaultValue: 'Media engine download failed' })}
      </span>
      <span className="text-xs leading-snug text-fg-muted">
        {t('setup.media_engine_failed_desc', {
          defaultValue:
            "The app couldn't fetch its bundled audio/video engine (FFmpeg). Retry, or point it at a copy already on this computer.",
        })}
      </span>
      {(op.error || detectError) && (
        <span className="text-xs text-danger" role="alert" data-testid="media-engine-error">
          {detectError || op.error}
        </span>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <Button
          variant="subtle"
          size="sm"
          loading={busy}
          disabled={busy}
          onClick={() => post('/media-tools/acquire')}
          data-testid="media-engine-retry"
        >
          {t('setup.media_engine_retry', { defaultValue: 'Retry' })}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={useSystemCopy}
          data-testid="media-engine-use-system"
        >
          {t('setup.media_engine_use_system', { defaultValue: 'Use a system copy' })}
        </Button>
        {isDesktop && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={chooseFile}>
            {t('setup.media_engine_choose_file', { defaultValue: 'Choose file…' })}
          </Button>
        )}
      </div>
    </div>
  );
}
