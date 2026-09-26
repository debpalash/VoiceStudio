/**
 * Settings → Audio tools — the power-user surface for the media tools most
 * users never see (the wizard + backend provision them invisibly).
 *
 * One row per tool:
 *   • FFmpeg / FFprobe — version + origin badge (Bundled / System / Custom /
 *     App package) + path; actions: Use system copy (auto-detect),
 *     Choose file… (native picker in Tauri),
 *     Restore bundled (always-safe revert). The section header carries
 *     "Update bundled build" (one download covers both binaries).
 *   • yt-dlp — module version + Update (fetches the newest wheel into an
 *     update-surviving overlay; applies on restart) + Restore tested version.
 *
 * Absorbs the FFmpeg-path override that used to live in Settings → Network —
 * same backend store (prefs `env.FFMPEG_PATH`), one control surface.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { AudioLines, Film, ScanSearch, DownloadCloud } from 'lucide-react';
import { Button, Badge } from '../../ui';
import { SettingsSection, SettingRow } from './primitives';
import RestartBadge from './RestartBadge';

const ORIGIN_TONE = {
  bundled: 'success',
  sidecar: 'success',
  system: 'info',
  custom: 'warn',
};

function OriginBadge({ origin }) {
  const { t } = useTranslation();
  if (!origin) return null;
  const labels = {
    bundled: t('settings.audio_tools_origin_bundled', { defaultValue: 'Bundled' }),
    system: t('settings.audio_tools_origin_system', { defaultValue: 'System' }),
    custom: t('settings.audio_tools_origin_custom', { defaultValue: 'Custom' }),
    sidecar: t('settings.audio_tools_origin_sidecar', { defaultValue: 'App package' }),
  };
  return (
    <Badge tone={ORIGIN_TONE[origin] || 'neutral'} size="xs" data-testid={`origin-${origin}`}>
      {labels[origin] || origin}
    </Badge>
  );
}

function BinaryRow({ tool, info, onAction, busy }) {
  const { t } = useTranslation();
  const label = tool === 'ffmpeg' ? 'FFmpeg' : 'FFprobe';

  const chooseFile = () => onAction(`/media-tools/${tool}/custom-path`);

  return (
    <SettingRow
      align="start"
      stack
      icon={tool === 'ffmpeg' ? Film : ScanSearch}
      title={
        <>
          {label}
          <OriginBadge origin={info?.origin} />
          {!info?.ok && (
            <Badge tone="warn" size="xs">
              {t('settings.audio_tools_not_found', { defaultValue: 'Not available' })}
            </Badge>
          )}
        </>
      }
      note={
        info?.ok ? (
          <>
            {info.version ||
              t('settings.audio_tools_version_unknown', { defaultValue: 'version unknown' })}
            {' — '}
            <code className="font-mono">{info.path}</code>
          </>
        ) : (
          t(`settings.audio_tools_${tool}_desc`)
        )
      }
      control={
        <>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => onAction(`/media-tools/${tool}/use-system`)}
            aria-label={`${label}: ${t('settings.audio_tools_use_system')}`}
          >
            {t('settings.audio_tools_use_system', { defaultValue: 'Use system copy' })}
          </Button>
          {'__TAURI_INTERNALS__' in window && (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={chooseFile}
              aria-label={`${label}: ${t('settings.audio_tools_choose_file')}`}
            >
              {t('settings.audio_tools_choose_file', { defaultValue: 'Choose file…' })}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => onAction(`/media-tools/${tool}/restore`)}
            aria-label={`${label}: ${t('settings.audio_tools_restore')}`}
          >
            {t('settings.audio_tools_restore', { defaultValue: 'Restore bundled' })}
          </Button>
        </>
      }
    />
  );
}

export default function AudioToolsPanel() {
  const { t } = useTranslation();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const acquireWasRunning = useRef(false);
  const ytdlpWasRunning = useRef(false);

  const load = useCallback(async () => {
    try {
      const { apiJson } = await import('../../api/client');
      const st = await apiJson('/media-tools/status');
      setStatus(st);
      return st;
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while a background op runs; toast exactly once on the edge.
  const acquire = status?.ops?.acquire;
  const ytdlpOp = status?.ops?.ytdlp_update;
  useEffect(() => {
    if (acquire?.state === 'running') acquireWasRunning.current = true;
    else if (acquireWasRunning.current) {
      acquireWasRunning.current = false;
      if (acquire?.state === 'done') {
        toast.success(
          t('settings.audio_tools_bundle_done', { defaultValue: 'Bundled media engine ready.' }),
        );
      } else if (acquire?.state === 'error') {
        toast.error(
          t('settings.audio_tools_bundle_failed', {
            message: acquire.error,
            defaultValue: 'Bundled download failed: {{message}}',
          }),
        );
      }
    }
    if (ytdlpOp?.state === 'running') ytdlpWasRunning.current = true;
    else if (ytdlpWasRunning.current) {
      ytdlpWasRunning.current = false;
      if (ytdlpOp?.state === 'done') {
        toast.success(
          t('settings.audio_tools_ytdlp_updated', {
            version: ytdlpOp.version,
            defaultValue: 'yt-dlp {{version}} installed — restart the backend to apply.',
          }),
        );
      } else if (ytdlpOp?.state === 'error') {
        toast.error(
          t('settings.audio_tools_ytdlp_update_failed', {
            message: ytdlpOp.error,
            defaultValue: 'yt-dlp update failed: {{message}}',
          }),
        );
      }
    }
    if (acquire?.state !== 'running' && ytdlpOp?.state !== 'running') return undefined;
    const iv = setInterval(load, 1500);
    return () => clearInterval(iv);
  }, [acquire?.state, ytdlpOp?.state, load, t, acquire?.error, ytdlpOp?.error, ytdlpOp?.version]);

  const post = useCallback(
    async (path, body) => {
      setBusy(true);
      try {
        const { apiFetch } = await import('../../api/client');
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
            /* non-JSON error body */
          }
          throw new Error(detail);
        }
        return true;
      } catch (e) {
        toast.error(
          t('settings.audio_tools_path_failed', {
            message: e.message,
            defaultValue: "Couldn't set path: {{message}}",
          }),
        );
        return false;
      } finally {
        setBusy(false);
        load();
      }
    },
    [load, t],
  );

  const onToolAction = useCallback(
    async (path, body) => {
      let requestBody = body;
      let selectedPath = body?.path;
      if (path.endsWith('/custom-path')) {
        try {
          const { invoke } = await import('@tauri-apps/api/core');
          const tool = path.includes('/ffprobe/') ? 'ffprobe' : 'ffmpeg';
          const selection = await invoke('authorize_host_path', { kind: tool });
          if (!selection) return;
          requestBody = { authorization: selection.authorization };
          selectedPath = selection.path;
        } catch (e) {
          toast.error(
            t('settings.audio_tools_path_failed', {
              message: e.message || String(e),
              defaultValue: "Couldn't set path: {{message}}",
            }),
          );
          return;
        }
      }
      const ok = await post(path, requestBody);
      if (ok && (path.endsWith('/custom-path') || path.endsWith('/use-system'))) {
        toast.success(
          t('settings.audio_tools_path_set', {
            tool: path.includes('ffprobe') ? 'FFprobe' : 'FFmpeg',
            path:
              selectedPath || t('settings.audio_tools_origin_system', { defaultValue: 'System' }),
            defaultValue: '{{tool}} now uses {{path}}',
          }),
        );
      } else if (ok && path.endsWith('/restore')) {
        toast.success(
          t('settings.audio_tools_restored', {
            tool: path.includes('ffprobe') ? 'FFprobe' : 'FFmpeg',
            defaultValue: '{{tool}} restored to the app-managed build.',
          }),
        );
      }
    },
    [post, t],
  );

  const ytdlp = status?.tools?.ytdlp;
  const ytdlpNeedsRestart =
    ytdlpOp?.state === 'done' ||
    (ytdlp?.overlay_version && ytdlp.overlay_version !== ytdlp.version);

  return (
    <SettingsSection
      icon={AudioLines}
      title={t('settings.audio_tools', { defaultValue: 'Audio tools' })}
      description={t('settings.audio_tools_desc', {
        defaultValue:
          'The media engine (FFmpeg, FFprobe) and video downloader (yt-dlp) the app manages for you.',
      })}
      actions={
        <Button
          size="sm"
          variant="ghost"
          leading={<DownloadCloud size={12} />}
          loading={acquire?.state === 'running'}
          disabled={busy || acquire?.state === 'running'}
          onClick={() => post('/media-tools/acquire')}
          aria-label={t('settings.audio_tools_update_bundle', {
            defaultValue: 'Update bundled build',
          })}
        >
          {acquire?.state === 'running'
            ? t('settings.audio_tools_bundle_updating', {
                percent: Math.round((acquire.progress || 0) * 100),
                defaultValue: 'Downloading bundled build… {{percent}}%',
              })
            : t('settings.audio_tools_update_bundle', { defaultValue: 'Update bundled build' })}
        </Button>
      }
    >
      <BinaryRow tool="ffmpeg" info={status?.tools?.ffmpeg} onAction={onToolAction} busy={busy} />
      <BinaryRow tool="ffprobe" info={status?.tools?.ffprobe} onAction={onToolAction} busy={busy} />

      <SettingRow
        align="start"
        stack
        icon={DownloadCloud}
        title={
          <>
            {t('settings.audio_tools_ytdlp', { defaultValue: 'yt-dlp (video downloader)' })}
            {ytdlp?.origin && (
              <OriginBadge origin={ytdlp.origin === 'custom' ? 'custom' : 'bundled'} />
            )}
            {ytdlpNeedsRestart && <RestartBadge />}
          </>
        }
        note={
          <>
            {ytdlp?.version ||
              t('settings.audio_tools_version_unknown', { defaultValue: 'version unknown' })}
            {' — '}
            {t('settings.audio_tools_ytdlp_desc', {
              defaultValue:
                'Powers video/clip imports. Site support changes faster than app releases — update it here when imports start failing.',
            })}
          </>
        }
        hint={t('settings.audio_tools_manual_hint', {
          defaultValue:
            'Prefer your package manager? Install FFmpeg yourself (macOS: brew install ffmpeg · Debian/Ubuntu: sudo apt install ffmpeg · Windows: winget install ffmpeg) and press Use system copy. Nothing is ever installed system-wide by the app.',
        })}
        control={
          <>
            <Button
              size="sm"
              variant="subtle"
              loading={ytdlpOp?.state === 'running'}
              disabled={busy || ytdlpOp?.state === 'running'}
              onClick={() => post('/media-tools/ytdlp/update')}
              aria-label={`yt-dlp: ${t('settings.audio_tools_ytdlp_update', { defaultValue: 'Update' })}`}
            >
              {t('settings.audio_tools_ytdlp_update', { defaultValue: 'Update' })}
            </Button>
            {(ytdlp?.origin === 'custom' || ytdlp?.overlay_version) && (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy || ytdlpOp?.state === 'running'}
                onClick={async () => {
                  const ok = await post('/media-tools/ytdlp/restore');
                  if (ok) {
                    toast.success(
                      t('settings.audio_tools_ytdlp_restored', {
                        defaultValue: 'Tested yt-dlp restored — restart the backend to apply.',
                      }),
                    );
                  }
                }}
                aria-label={`yt-dlp: ${t('settings.audio_tools_ytdlp_restore', { defaultValue: 'Restore tested version' })}`}
                data-testid="ytdlp-restore"
              >
                {t('settings.audio_tools_ytdlp_restore', {
                  defaultValue: 'Restore tested version',
                })}
                {ytdlp?.baseline_version ? ` (${ytdlp.baseline_version})` : ''}
              </Button>
            )}
          </>
        }
      />
    </SettingsSection>
  );
}
