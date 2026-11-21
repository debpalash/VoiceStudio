import { AlertCircleIcon, AudioLinesIcon } from 'lucide-react';
import { getBridge } from '@/components/bridge';
import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { apiJson } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { SettingsSection, SettingsRow } from './settings-layout';
import { relaxWhenBackendBusy } from '@/lib/status-polling';
interface Tool {
  ok: boolean;
  version?: string;
  path?: string;
  origin?: string;
  overlay_version?: string;
}
interface Operation {
  state?: string;
  progress?: number;
  error?: string;
}
interface Status {
  ready: boolean;
  tools: Record<string, Tool>;
  ops: { acquire?: Operation; ytdlp_update?: Operation };
}

/** First-run media readiness stays invisible when healthy and actionable when it needs help. */
export function SetupMediaEngine() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const query = useQuery({
    queryKey: ['media-tools'],
    queryFn: ({ signal }) => apiJson<Status>('/media-tools/status', { signal }),
    refetchInterval: (state) => (state.state.data?.ops.acquire?.state === 'running' ? relaxWhenBackendBusy(1500) : false),
    retry: false,
  });
  const act = async (path: string, choose = false) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    try {
      const selected = choose ? await getBridge()?.files.authorizeMediaTool('ffmpeg') : undefined;
      if (choose && !selected) return;
      await apiJson('/media-tools/' + path, {
        method: 'POST',
        ...(selected ? { body: JSON.stringify({ authorization: selected.authorization }) } : {}),
      });
    } catch {
      // The status card stays visible and retryable; the operation snapshot
      // carries the backend's failure detail when acquisition itself fails.
    } finally {
      setBusy(false);
      pending.current = false;
      await client.invalidateQueries({ queryKey: ['media-tools'] });
    }
  };
  if (query.data?.ready) return null;
  const operation = query.data?.ops.acquire;
  const preparing =
    query.isPending || operation?.state === 'idle' || operation?.state === 'running';
  if (preparing) {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className="size-2 animate-pulse rounded-full bg-primary" aria-hidden="true" />
        {t('common.loading')} {t('settings.audio_tools')}
        {operation?.state === 'running' ? ` ${Math.round((operation.progress || 0) * 100)}%` : ''}
      </p>
    );
  }
  return (
    <SettingsSection icon={AlertCircleIcon} title={t('settings.audio_tools')}>
      <div className="flex flex-wrap items-center gap-2 p-4">
        <Button disabled={busy} onClick={() => void act('acquire')}>
          {t('settings.audio_tools_update_bundle')}
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => void act('ffmpeg/use-system')}>
          {t('settings.audio_tools_use_system')}
        </Button>
        {getBridge()?.files.authorizeMediaTool && (
          <Button
            variant="ghost"
            disabled={busy}
            onClick={() => void act('ffmpeg/custom-path', true)}
          >
            {t('settings.audio_tools_choose_file')}
          </Button>
        )}
      </div>
    </SettingsSection>
  );
}
export function MediaTools() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const query = useQuery({
    queryKey: ['media-tools'],
    queryFn: ({ signal }) => apiJson<Status>('/media-tools/status', { signal }),
    refetchInterval: () => relaxWhenBackendBusy(2000),
  });
  const act = async (path: string, tool?: 'ffmpeg' | 'ffprobe') => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(false);
    try {
      const selected = tool ? await getBridge()?.files.authorizeMediaTool(tool) : undefined;
      if (tool && !selected) return;
      await apiJson('/media-tools/' + path, {
        method: 'POST',
        ...(selected ? { body: JSON.stringify({ authorization: selected.authorization }) } : {}),
      });
      await client.invalidateQueries({ queryKey: ['media-tools'] });
    } catch {
      setError(true);
    } finally {
      setBusy(false);
      pending.current = false;
    }
  };
  const acquire = query.data?.ops.acquire;
  const update = query.data?.ops.ytdlp_update;
  const locked = busy || acquire?.state === 'running' || update?.state === 'running';
  const ytdlp = query.data?.tools.ytdlp;
  return (
    <SettingsSection icon={AudioLinesIcon} title={t('settings.audio_tools')}>
      {query.isError && (
        <Button variant="outline" onClick={() => void query.refetch()}>
          {t('backend.retry')}
        </Button>
      )}
      {query.isPending && (
        <p role="status" className="p-4">
          {t('common.loading')}
        </p>
      )}
      {(error || acquire?.state === 'error' || update?.state === 'error') && (
        <p role="alert" className="p-4 text-sm text-destructive">
          {t('common.error')}
        </p>
      )}
      {(['ffmpeg', 'ffprobe'] as const).map((tool) => {
        const info = query.data?.tools[tool];
        return (
          <SettingsRow
            key={tool}
            id={'media-' + tool}
            title={tool === 'ffmpeg' ? 'FFmpeg' : 'FFprobe'}
            description={
              info?.ok
                ? [info.version, info.path].filter(Boolean).join(' / ')
                : t(query.isPending ? 'common.loading' : 'modelSettings.unavailable')
            }
          >
            {info?.origin && (
              <span className="text-xs text-muted-foreground">
                {t('settings.audio_tools_origin_' + info.origin)}
              </span>
            )}
            {getBridge()?.files.authorizeMediaTool && (
              <Button
                size="sm"
                variant="outline"
                disabled={locked}
                onClick={() => void act(tool + '/custom-path', tool)}
              >
                {t('settings.audio_tools_choose_file')}
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={locked}
              onClick={() => void act(tool + '/use-system')}
            >
              {t('settings.audio_tools_use_system')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={locked}
              onClick={() => void act(tool + '/restore')}
            >
              {t('settings.audio_tools_restore')}
            </Button>
          </SettingsRow>
        );
      })}
      <div className="p-4">
        <Button variant="outline" disabled={locked} onClick={() => void act('acquire')}>
          {acquire?.state === 'running'
            ? t('common.loading') + ' ' + Math.round((acquire.progress || 0) * 100) + '%'
            : t('settings.audio_tools_update_bundle')}
        </Button>
        {acquire?.state === 'done' && (
          <p role="status" className="mt-2 text-xs">
            {t('modelSettings.available')}
          </p>
        )}
      </div>
      <SettingsRow id="media-ytdlp" title="yt-dlp" description={ytdlp?.version || '—'}>
        <Button
          size="sm"
          variant="outline"
          disabled={locked}
          onClick={() => void act('ytdlp/update')}
        >
          {t('settings.audio_tools_ytdlp_update')}
        </Button>
        {ytdlp?.overlay_version && (
          <Button
            size="sm"
            variant="ghost"
            disabled={locked}
            onClick={() => void act('ytdlp/restore')}
          >
            {t('settings.audio_tools_ytdlp_restore')}
          </Button>
        )}
      </SettingsRow>
      {update?.state === 'running' && (
        <p role="status" className="p-4 text-sm">
          {t('common.loading')}
        </p>
      )}
      {(update?.state === 'done' ||
        (ytdlp?.overlay_version && ytdlp.overlay_version !== ytdlp.version)) && (
        <p role="status" className="p-4 text-sm">
          {t('dub.install_restart', {
            engine: 'yt-dlp ' + (ytdlp?.overlay_version || ytdlp?.version || ''),
          })}
        </p>
      )}
    </SettingsSection>
  );
}
