import { TrimAudio } from './trim-audio';
import { VoiceDestinations } from './voice-destinations';
import { placeLongformVoice } from './place-voice';
import type { Mode } from '@/features/longform/longform-session';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import {
  DownloadIcon,
  PackageIcon,
  PlayIcon,
  SearchIcon,
  Trash2Icon,
  UploadIcon,
  XIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { VideoPlayer } from '@/components/video-player';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiJson, apiPath, ApiError, describeError } from '@/lib/api/client';
import { queryKeys } from '@/lib/query';
import { selectCloneProfile } from '@/lib/store/reference';
import type { Profile } from '@/lib/api/types';
import { importsApi, type GalleryVoice, type YoutubeSearchResult } from './imports-api';
export function ImportsView() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const personaInput = useRef<HTMLInputElement>(null);
  const lock = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<YoutubeSearchResult[]>([]);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [trimming, setTrimming] = useState<GalleryVoice | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const voices = useQuery({
    queryKey: ['gallery-imports'],
    queryFn: ({ signal }) => importsApi.list(signal),
  });
  const run = async (action: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await action();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 413
          ? t('gallery.persona_too_large')
          : describeError(err),
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  const refresh = () => client.invalidateQueries({ queryKey: ['gallery-imports'] });
  const upload = (file: File, persona: boolean) =>
    run(async () => {
      if (persona) {
        const result = await importsApi.persona(file);
        await client.invalidateQueries({ queryKey: queryKeys.profiles });
        setMessage(
          t('gallery.persona_imported', {
            name: result.name,
            unverified: result.verified_own_voice ? '' : t('gallery.persona_unverified_suffix'),
          }),
        );
      } else await importsApi.upload(file);
      await refresh();
    });
  const search = () =>
    run(async () => {
      const value = query.trim();
      if (!value) return;
      if (/^https?:\/\//i.test(value)) {
        await importsApi.download({
          video_url: value,
          start_time: 0,
          duration: 15,
          character_name: t('gallery.imported_clip'),
          category: 'import',
          description: value,
        });
        await refresh();
        setQuery('');
        setResults([]);
      } else setResults((await importsApi.search(value)).results);
    });
  const useVoice = (voice: GalleryVoice, target?: Mode) =>
    run(async () => {
      const result = await importsApi.save(voice);
      const profiles = await apiJson<Profile[]>('/profiles');
      client.setQueryData(queryKeys.profiles, profiles);
      const profile = profiles.find((item) => item.id === result.profile_id);
      if (!profile) throw new Error('Profile missing');
      if (!mounted.current) return;
      if (target) {
        placeLongformVoice(profile, target);
        await navigate({
          to: target === 'stories' ? '/stories' : '/audiobook',
        });
        return;
      }
      selectCloneProfile(profile);
      await navigate({ to: '/clone' });
    });
  if (trimming)
    return (
      <TrimAudio
        src={'/gallery/voices/' + encodeURIComponent(trimming.id) + '/preview'}
        name={trimming.name + ' - ' + t('gallery.trim')}
        busy={busy}
        saveError={error}
        onCancel={() => setTrimming(null)}
        onSave={(file) =>
          void run(async () => {
            await importsApi.upload(file, trimming);
            await refresh();
            setTrimming(null);
          })
        }
      />
    );
  return (
    <section className="min-h-0 flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-5">
        <p className="text-sm leading-6 text-muted-foreground">{t('gallery.import_explainer')}</p>
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void search();
          }}
        >
          <Input
            className="min-w-48 flex-1"
            aria-label={t('gallery.import_placeholder')}
            placeholder={t('gallery.import_placeholder')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            disabled={busy}
          />
          <Button type="submit" disabled={busy || !query.trim()} aria-label={t('common.search')}>
            <SearchIcon />
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => fileInput.current?.click()}
          >
            <UploadIcon />
            {t('gallery.upload')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => personaInput.current?.click()}
          >
            <PackageIcon />
            {t('gallery.import_persona')}
          </Button>
        </form>
        <input
          ref={fileInput}
          type="file"
          accept="audio/*,video/*"
          hidden
          aria-label={t('gallery.upload')}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void upload(file, false);
          }}
        />
        <input
          ref={personaInput}
          type="file"
          accept=".ovsvoice,.omnivoice"
          hidden
          aria-label={t('gallery.import_persona')}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void upload(file, true);
          }}
        />
        {busy && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('common.loading')}
          </p>
        )}
        {(error || voices.isError) && (
          <PipelineFailure
            fallback={error || describeError(voices.error)}
            action={
              voices.isError ? (
                <Button size="xs" variant="ghost" onClick={() => void voices.refetch()}>
                  {t('common.retry')}
                </Button>
              ) : undefined
            }
            onDismiss={error ? () => setError('') : undefined}
          />
        )}
        {message && (
          <p role="status" className="text-sm">
            {message}
          </p>
        )}
        {results.length > 0 && (
          <div className="space-y-2">
            <h2 className="text-sm font-medium">
              {t('gallery.search_results', { count: results.length })}
            </h2>
            {results.map((result) => (
              <article
                key={result.video_id}
                className="space-y-3 rounded-lg border border-border/50 p-3"
              >
                <div className="flex items-center gap-3">
                  <span className="min-w-0 flex-1 text-sm">{result.title}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-pressed={previewing === result.video_id}
                    onClick={() =>
                      setPreviewing((current) =>
                        current === result.video_id ? null : result.video_id,
                      )
                    }
                  >
                    {previewing === result.video_id ? <XIcon /> : <PlayIcon />}
                    {t(previewing === result.video_id ? 'common.close' : 'gallery.preview')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await importsApi.download({
                          video_url:
                            'https://youtube.com/watch?v=' + encodeURIComponent(result.video_id),
                          start_time: 0,
                          duration: Math.min(Number.parseFloat(result.duration || '') || 15, 30),
                          character_name: result.title.slice(0, 40),
                          category: 'import',
                          description: result.title,
                        });
                        await refresh();
                        setPreviewing(null);
                        setResults([]);
                      })
                    }
                  >
                    <DownloadIcon />
                    {t('gallery.import')}
                  </Button>
                </div>
                {previewing === result.video_id && (
                  <VideoPlayer
                    src={`https://youtube.com/watch?v=${encodeURIComponent(result.video_id)}`}
                    source={`gallery-search:${result.video_id}`}
                  />
                )}
              </article>
            ))}
          </div>
        )}
        <h2 className="text-sm font-medium">{t('gallery.my_imports')}</h2>
        {voices.isPending ? (
          <p role="status">{t('common.loading')}</p>
        ) : voices.data?.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            {t('gallery.no_imports')}
          </p>
        ) : null}
        <div className="grid gap-3 lg:grid-cols-2">
          {voices.data?.map((voice) => (
            <article key={voice.id} className="space-y-3 rounded-xl border border-border/50 p-4">
              <div className="flex items-center gap-3">
                <AudioPreviewButton
                  src={apiPath('/gallery/voices/' + encodeURIComponent(voice.id) + '/preview')}
                  source={'import:' + voice.id}
                />
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-sm font-medium">{voice.name}</h3>
                  <p className="text-xs tabular-nums text-muted-foreground">
                    {voice.duration.toFixed(1)}s
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  disabled={busy}
                  aria-label={t('gallery.delete') + ': ' + voice.name}
                  onClick={() => setDeleting(voice.id)}
                >
                  <Trash2Icon />
                </Button>
              </div>
              {deleting === voice.id ? (
                <div className="space-y-2">
                  <p className="text-sm">{t('gallery.confirm_delete', { name: voice.name })}</p>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await importsApi.remove(voice.id);
                        setDeleting(null);
                        await refresh();
                      })
                    }
                  >
                    {t('gallery.delete')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setDeleting(null)}
                  >
                    {t('common.cancel')}
                  </Button>
                </div>
              ) : (
                <div className="flex items-center justify-between gap-2">
                  {' '}
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => void useVoice(voice)}
                  >
                    {t('gallery.use_voice')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setTrimming(voice)}
                  >
                    {t('gallery.trim')}
                  </Button>
                  <VoiceDestinations
                    disabled={busy}
                    onChoose={(target) => void useVoice(voice, target)}
                  />
                </div>
              )}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
