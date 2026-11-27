import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import {
  AlertCircleIcon,
  AlertTriangleIcon,
  BellIcon,
  ChevronRightIcon,
  InfoIcon,
  XIcon,
} from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/popover';
import { getBridge } from '@/components/bridge';
import { Button } from '@/components/ui/button';
import { apiFetch, apiJson } from '@/lib/api/client';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import { DownloadActivityList, useDownloadActivity } from './download-activity';
import { LongformActivityCard, longformActivityStatus } from '@/components/longform-activity-card';
import { useLongformActivity } from '@/lib/longform-activity';
import { TaskActivityList, useTaskActivity } from './task-activity';

interface SystemNotification {
  id: string;
  level: 'info' | 'warn' | 'error';
  title?: string;
  message?: string;
  action?: {
    type: 'navigate' | 'settings-tab' | 'link' | 'api' | string;
    target: string;
    label?: string;
  } | null;
  persistent?: boolean;
}

interface NotificationsResponse {
  notifications: SystemNotification[];
}

const DISMISSED_KEY = 'voicestudio.dismissed-system-notifications';

function loadDismissed(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? '[]');
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string')
      : [];
  } catch {
    return [];
  }
}

function notificationRoute(note: SystemNotification) {
  if (note.id === 'hf-token-missing') return '/settings/credentials' as const;
  if (note.id === 'ffmpeg-missing') return '/settings/media' as const;
  if (note.id === 'disk-low') return '/settings/storage' as const;
  if (note.id === 'gpu-arch-unsupported' || note.id === 'gpu-unavailable')
    return '/settings/performance' as const;
  if (note.id === 'crash-last-session' || note.id.startsWith('last-run-crash-'))
    return '/settings/logs' as const;
  if (note.action?.type === 'settings-tab') {
    if (note.action.target === 'audio-tools') return '/settings/media' as const;
    if (note.action.target === 'storage') return '/settings/storage' as const;
    if (note.action.target === 'network') return '/settings/network' as const;
    if (note.action.target === 'models') return '/settings/models' as const;
  }
  if (note.action?.type === 'navigate' && note.action.target === 'settings')
    return '/settings/general' as const;
  return null;
}

function levelStyle(level: SystemNotification['level']) {
  if (level === 'error') return 'border-destructive/45 bg-destructive/8 text-destructive';
  if (level === 'warn') return 'border-warning/45 bg-warning/8 text-warning-foreground';
  return 'border-primary/35 bg-primary/8 text-primary';
}

function LevelIcon({ level }: { level: SystemNotification['level'] }) {
  const Icon =
    level === 'error' ? AlertCircleIcon : level === 'warn' ? AlertTriangleIcon : InfoIcon;
  return <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />;
}

export function SystemNotifications({
  enabled,
  compact = false,
  titlebar = false,
}: {
  enabled: boolean;
  compact?: boolean;
  titlebar?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(loadDismissed);
  const [open, setOpen] = useState(false);
  const { tasks, checkingBatch } = useTaskActivity(enabled, open);
  const downloads = useDownloadActivity();
  // A Stories/Audiobook render keeps running when its page is left.
  const render = useLongformActivity();
  const downloadFailed = downloads.items.length > downloads.active;
  const query = useQuery({
    queryKey: ['system-notifications'],
    queryFn: ({ signal }) => apiJson<NotificationsResponse>('/system/notifications', { signal }),
    enabled,
    refetchInterval: enabled ? 30_000 : false,
  });
  const visible = useMemo(() => {
    const backend = (query.data?.notifications ?? []).filter(
      (note) => note.level === 'error' || !dismissed.includes(note.id),
    );
    return backend;
  }, [dismissed, query.data?.notifications]);

  const dismiss = (id: string) => {
    const next = [...dismissed.filter((item) => item !== id), id].slice(-50);
    setDismissed(next);
    try {
      localStorage.setItem(DISMISSED_KEY, JSON.stringify(next));
    } catch {
      // The current dismissal still applies when persistent storage is unavailable.
    }
  };

  const activate = async (note: SystemNotification) => {
    if (!note.action) return;
    if (note.id === 'crash-last-session') {
      void apiFetch('/system/crash/ack', { method: 'POST' }).catch(() => {});
    } else if (note.id.startsWith('last-run-crash-')) {
      void apiFetch('/system/last-run-crash/ack', { method: 'POST' }).catch(() => {});
    }
    const route = notificationRoute(note);
    if (route) {
      await navigate({ to: route });
      return;
    }
    if (note.action.type === 'link') {
      // The target is backend JSON, so it is untrusted input to a call that hands
      // a string to the OS. Validate the same way the gallery's author links are:
      // web and mail only. The main-process allowlist refuses the rest too; this
      // keeps the refusal in the hand that decided to act on the string.
      const target = note.action.target;
      let url: URL;
      try {
        url = new URL(target);
      } catch {
        toast.error(t('common.link_blocked'));
        return;
      }
      if (url.protocol !== 'https:' && url.protocol !== 'http:' && url.protocol !== 'mailto:') {
        toast.error(t('common.link_blocked'));
        return;
      }
      const bridge = getBridge();
      if (bridge) await bridge.files.openExternal(url.toString());
      else window.open(url.toString(), '_blank', 'noopener,noreferrer');
      return;
    }
    if (note.action.type === 'api' && note.action.target.startsWith('/')) {
      await apiFetch(note.action.target, { method: 'POST' });
      void query.refetch();
    }
  };

  const total = visible.length + downloads.items.length + (render ? 1 : 0) + tasks.length;
  const checking = query.isPending || checkingBatch;
  const triggerLabel =
    [
      render
        ? `${t(render.mode === 'stories' ? 'nav.stories' : 'audiobook.title')}: ${longformActivityStatus(render, t)}`
        : null,
      downloads.active ? t('modelInstall.activeCount', { count: downloads.active }) : null,
      tasks.length ? t('generationQueue.title') : null,
      ...visible.map((note) => note.title || note.message),
    ]
      .filter(Boolean)
      .join('. ') ||
    t(
      !enabled
        ? 'modelSettings.unavailable'
        : query.isError
          ? 'common.error'
          : checking
            ? 'preferences.loading'
            : 'logs.all_clear',
    );
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size={titlebar ? 'icon-sm' : 'icon-xs'}
            className={cn(
              'relative shrink-0 text-muted-foreground hover:text-foreground',
              titlebar && 'app-no-drag',
            )}
            aria-label={triggerLabel}
          />
        }
      >
        <BellIcon />
        {total > 0 && (
          <span
            className={cn(
              'absolute -top-0.5 -right-0.5 min-w-3 rounded-full px-0.5 text-center text-[8px] leading-3 font-semibold text-white',
              downloadFailed ||
                tasks.some((task) => task.failed) ||
                visible.some((note) => note.level === 'error')
                ? 'bg-destructive'
                : 'bg-primary',
            )}
            aria-hidden="true"
          >
            {total > 9 ? '9+' : total}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent
        side={titlebar ? 'bottom' : compact ? 'right' : 'top'}
        align={titlebar ? 'end' : 'start'}
        className="max-h-[min(28rem,calc(100vh-2rem))] w-[min(22rem,calc(100vw-2rem))] space-y-1 overflow-y-auto p-1.5"
      >
        <TaskActivityList tasks={tasks} />
        <LongformActivityCard />
        <DownloadActivityList activity={downloads} />
        {!enabled && total === 0 && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            {t('modelSettings.unavailable')}
          </p>
        )}
        {enabled && checking && total === 0 && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            {t('preferences.loading')}
          </p>
        )}
        {enabled && !checking && !query.isError && total === 0 && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            {t('logs.all_clear')}
          </p>
        )}
        {query.isError && (
          <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-xs text-destructive">
            <span>{t('common.error')}</span>
            <Button size="xs" variant="ghost" onClick={() => void query.refetch()}>
              {t('common.retry')}
            </Button>
          </div>
        )}
        {visible.map((note) => {
          const actionable = Boolean(note.action);
          const actionLabel =
            note.action?.label ??
            (note.id === 'disk-low' && note.action ? t('settings.storage') : null);
          return (
            <div
              key={note.id}
              className={cn(
                'group/notice flex items-start gap-2 rounded-lg border px-2.5 py-2 text-left',
                levelStyle(note.level),
              )}
            >
              <LevelIcon level={note.level} />
              <button
                type="button"
                disabled={!actionable}
                className="min-w-0 flex-1 text-left disabled:cursor-default"
                onClick={() => void activate(note)}
              >
                {note.title && (
                  <span className="block text-xs font-medium text-foreground">{note.title}</span>
                )}
                {note.message && (
                  <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">
                    {note.message}
                  </span>
                )}
                {actionLabel && (
                  <span className="mt-1 inline-flex items-center gap-0.5 text-[11px] font-medium">
                    {actionLabel}
                    <ChevronRightIcon className="size-3" aria-hidden="true" />
                  </span>
                )}
              </button>
              {note.level !== 'error' && !note.persistent && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="-mt-1 -mr-1 opacity-60 hover:opacity-100"
                  aria-label={note.title || note.message || note.id}
                  onClick={() => dismiss(note.id)}
                >
                  <XIcon />
                </Button>
              )}
            </div>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}
