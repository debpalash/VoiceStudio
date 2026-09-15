import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  FilmIcon,
  LoaderCircleIcon,
  PencilIcon,
  PlayIcon,
  RotateCcwIcon,
  XIcon,
} from 'lucide-react';
import type { MediaPlayerInstance } from '@/components/media-player';
import { VideoPlayer } from '@/components/video-player';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { apiJson, apiPath } from '@/lib/api/client';

interface DemoManifest {
  source: {
    code: string;
    label: string;
    video: string;
    script: string;
  };
  dubbed: Array<{
    code: string;
    label: string;
    video: string;
    script: string;
    dir?: 'ltr' | 'rtl';
  }>;
}

const DEMO_BASE = '/demo_audio/demo/dubbing';
const PLAYBACK_GROUP = 'dubbing-demo-comparison';

interface EditableDemoVideo {
  path: string;
  filename: string;
}

export function DubbingDemo({
  onDismiss,
  onTry,
  onEdit,
}: {
  onDismiss: () => void;
  onTry: () => void;
  onEdit: (sample: EditableDemoVideo) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [manifest, setManifest] = useState<DemoManifest | null>(null);
  const [failed, setFailed] = useState(false);
  const [language, setLanguage] = useState('es');
  const [synchronized, setSynchronized] = useState(true);
  const [scripts, setScripts] = useState<Record<string, string>>({});
  const [editingVideo, setEditingVideo] = useState(false);
  const sourcePlayer = useRef<MediaPlayerInstance>(null);
  const dubbedPlayer = useRef<MediaPlayerInstance>(null);
  const mirroring = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void apiJson<DemoManifest>(`${DEMO_BASE}/manifest.json`, { signal: controller.signal })
      .then(setManifest)
      .catch(() => !controller.signal.aborted && setFailed(true));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    void sourcePlayer.current?.pause().catch(() => {});
    void dubbedPlayer.current?.pause().catch(() => {});
  }, [language]);

  if (failed) return null;
  if (!manifest)
    return (
      <div className="flex h-24 items-center justify-center rounded-xl border border-border/50 bg-muted/20 text-xs text-muted-foreground">
        <LoaderCircleIcon className="mr-2 size-4 animate-spin motion-reduce:animate-none" />
        {t('demo.dubbing_loading')}
      </div>
    );

  const dubbed = manifest.dubbed.find((item) => item.code === language) || manifest.dubbed[0];
  if (!dubbed) return null;

  const synchronizePosition = (
    from: MediaPlayerInstance | null,
    to: MediaPlayerInstance | null,
  ) => {
    if (!synchronized || mirroring.current || !from || !to) return;
    mirroring.current = true;
    try {
      to.currentTime = from.currentTime;
    } finally {
      queueMicrotask(() => {
        mirroring.current = false;
      });
    }
  };

  const card = (
    channel: 'A' | 'B',
    code: string,
    label: string,
    tag: string,
    video: string,
    script: string,
    player: React.RefObject<MediaPlayerInstance | null>,
    peer: React.RefObject<MediaPlayerInstance | null>,
    direction?: 'ltr' | 'rtl',
    editableVideo?: EditableDemoVideo,
  ) => {
    const value = scripts[code] ?? script;
    const edited = value !== script;
    return (
      <article className="min-w-0 overflow-hidden rounded-2xl border border-border/55 bg-background/25 shadow-[inset_0_1px_0_color-mix(in_oklab,var(--foreground)_4%,transparent)]">
        <div className="flex items-center gap-2.5 px-3 py-2.5 text-xs font-medium">
          <span className="grid size-6 shrink-0 place-items-center rounded-md bg-primary/12 font-mono text-[10px] font-semibold text-primary">
            {channel}
          </span>
          <span>{label}</span>
          <span className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
            {tag}
          </span>
          <div className="ml-auto flex items-center gap-1">
            {edited && (
              <Button
                size="icon-xs"
                variant="ghost"
                className="text-muted-foreground"
                aria-label={`${t('preferences.reset')} ${label}`}
                title={t('preferences.reset')}
                onClick={() =>
                  setScripts((current) => {
                    const next = { ...current };
                    delete next[code];
                    return next;
                  })
                }
              >
                <RotateCcwIcon />
              </Button>
            )}
            {editableVideo && (
              <Button
                size="xs"
                variant="ghost"
                disabled={editingVideo}
                aria-label={`${t('clone.edit')} ${label}`}
                onClick={() => {
                  setEditingVideo(true);
                  void Promise.resolve(onEdit(editableVideo)).finally(() => setEditingVideo(false));
                }}
              >
                {editingVideo ? (
                  <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <PencilIcon />
                )}
                {t('clone.edit')}
              </Button>
            )}
          </div>
        </div>
        <div className="px-2.5">
          <VideoPlayer
            playerRef={player}
            controls="compact"
            load="eager"
            src={{ src: apiPath(`${DEMO_BASE}/${video}`), type: 'video/mp4' }}
            source={`${PLAYBACK_GROUP}-${tag}`}
            onPlay={() => synchronizePosition(player.current, peer.current)}
            onSeeked={() => synchronizePosition(player.current, peer.current)}
          />
        </div>
        <label className="sr-only" htmlFor={`dubbing-demo-script-${code}`}>
          {label} — {t('dub.transcript')}
        </label>
        <Textarea
          id={`dubbing-demo-script-${code}`}
          dir={direction}
          value={value}
          rows={3}
          spellCheck
          onChange={(event) => {
            const nextValue = event.currentTarget.value;
            setScripts((current) => ({ ...current, [code]: nextValue }));
          }}
          className="m-2.5 mt-3 h-24 min-h-20 max-h-40 w-[calc(100%-1.25rem)] resize-y rounded-xl border-border/45 bg-background/45 text-xs leading-5 text-muted-foreground [field-sizing:fixed] focus-visible:text-foreground"
        />
      </article>
    );
  };

  return (
    <section className="glass-panel @container/dubbing-demo w-full space-y-3 rounded-2xl border border-border/60 bg-card/35 p-4 text-left shadow-sm">
      <header className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3">
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <FilmIcon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium">{t('demo.dubbing_title')}</h3>
          <p className="text-xs text-muted-foreground">{t('demo.dubbing_picker')}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            aria-label={t('demo.dubbing_cta')}
            title={t('demo.dubbing_cta')}
            onClick={onTry}
          >
            <PlayIcon />
            <span className="hidden @min-[560px]:inline">{t('demo.dubbing_cta')}</span>
          </Button>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={t('demo.dubbing_dismiss')}
            onClick={onDismiss}
          >
            <XIcon />
          </Button>
        </div>
      </header>
      <div
        role="group"
        aria-label={t('demo.dubbing_picker')}
        className="flex flex-wrap items-center gap-1.5 rounded-xl border border-border/45 bg-background/30 p-1.5"
      >
        {manifest.dubbed.map((item) => (
          <Button
            key={item.code}
            size="xs"
            variant={item.code === dubbed.code ? 'secondary' : 'ghost'}
            aria-pressed={item.code === dubbed.code}
            onClick={() => setLanguage(item.code)}
          >
            {item.label}
          </Button>
        ))}
        <label className="ml-auto inline-flex items-center gap-2 px-1.5 text-xs text-muted-foreground">
          {t('demo.dubbing_sync')}
          <Switch checked={synchronized} onCheckedChange={setSynchronized} />
        </label>
      </div>
      <div className="grid gap-3 min-[1100px]:grid-cols-2">
        {card(
          'A',
          manifest.source.code,
          manifest.source.label,
          t('demo.original_tag'),
          manifest.source.video,
          manifest.source.script,
          sourcePlayer,
          dubbedPlayer,
        )}
        {card(
          'B',
          dubbed.code,
          dubbed.label,
          t('demo.dubbed_tag'),
          dubbed.video,
          dubbed.script,
          dubbedPlayer,
          sourcePlayer,
          dubbed.dir,
          {
            path: `${DEMO_BASE}/${dubbed.video}`,
            filename: dubbed.video,
          },
        )}
      </div>
    </section>
  );
}
