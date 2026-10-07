import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDownIcon, DownloadIcon, LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { getBridge } from '@/components/bridge';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiPath, describeError, errorFromResponse } from '@/lib/api/client';
import { saveExport } from '@/lib/export-history';
import { dubExportRequest, type DubExportFormat } from './dub-export';
import { setDubExportPreferences, type DubSession } from './dub-session';

export function DubExportPanel({ session, disabled }: { session: DubSession; disabled: boolean }) {
  const { t } = useTranslation();
  const preferences = session.exportOptions || {};
  const format =
    session.inputType === 'audio' && preferences.format === 'mp4'
      ? 'wav'
      : preferences.format || (session.inputType === 'audio' ? 'wav' : 'mp4');
  const setFormat = (format: DubExportFormat) => setDubExportPreferences({ format });
  const trackChoice = preferences.track || '';
  const setTrack = (track: string) => setDubExportPreferences({ track });
  const excluded = preferences.excluded || [];
  const setExcluded = (excluded: string[]) => setDubExportPreferences({ excluded });
  const preserveBg = preferences.preserveBg ?? true;
  const setPreserveBg = (preserveBg: boolean) => setDubExportPreferences({ preserveBg });
  const burn = preferences.burn ?? false;
  const setBurn = (burn: boolean) => setDubExportPreferences({ burn });
  const dual = preferences.dual ?? false;
  const setDual = (dual: boolean) => setDubExportPreferences({ dual });
  const karaoke = preferences.karaoke ?? false;
  const setKaraoke = (karaoke: boolean) => setDubExportPreferences({ karaoke });
  const bitrate = preferences.bitrate || '192';
  const setBitrate = (bitrate: string) => setDubExportPreferences({ bitrate });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const tracks = ['original', ...session.tracks];
  const selected = tracks.filter((code) => !excluded.includes(code));
  const choices = format === 'mp4' ? selected : session.tracks;
  const track = choices.includes(trackChoice)
    ? trackChoice
    : choices.find((code) => code !== 'original') || choices[0] || '';
  const burnAllowed = session.generatedTiming !== 'stretch_video';
  const locked = disabled || saving;
  const subtitles = ['srt', 'vtt', 'ass'].includes(format);
  const ready =
    !!session.jobId &&
    !!track &&
    (subtitles ? session.segments.length > 0 : session.tracks.length > 0);
  const labels: Record<DubExportFormat, string> = {
    mp4: t('exportModal.mp4_h264'),
    wav: t('exportModal.wav_lossless'),
    mp3: t('exportModal.mp3_compressed'),
    srt: 'SRT',
    vtt: 'VTT',
    ass: t('exportModal.subs_style_karaoke'),
    stems: t('exportModal.pkg_stems_title'),
    clips: t('exportModal.pkg_clips_title'),
  };
  const save = async () => {
    if (!ready || locked || pending.current) return;
    pending.current = true;
    setSaving(true);
    setError(null);
    try {
      const request = dubExportRequest({
        jobId: session.jobId!,
        format,
        track,
        tracks: selected,
        preserveBg,
        burn: burn && burnAllowed,
        dual,
        karaoke,
        bitrate,
      });
      const url = apiPath(request.path);
      const bridge = getBridge();
      if (bridge) await saveExport(url, request.name);
      else {
        const response = await fetch(url);
        if (!response.ok) throw await errorFromResponse(response);
        const objectUrl = URL.createObjectURL(await response.blob());
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = request.name;
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
      }
    } catch (error) {
      setError(describeError(error));
    } finally {
      pending.current = false;
      setSaving(false);
    }
  };
  const toggle = (
    label: string,
    checked: boolean,
    change: (value: boolean) => void,
    unavailable = false,
  ) => (
    <div key={label} className="flex items-center justify-between gap-3 text-xs">
      <span>{label}</span>
      <Switch
        aria-label={label}
        checked={checked}
        onCheckedChange={change}
        disabled={locked || unavailable}
      />
    </div>
  );
  return (
    <details className="group rounded-xl border border-border/60 bg-muted/20 p-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium">
        <DownloadIcon className="size-4 text-muted-foreground" />
        {t('exportModal.export_options')}
        <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <fieldset disabled={locked} className="mt-3 space-y-3">
        <legend className="mb-2 text-xs text-muted-foreground">{t('exportModal.format')}</legend>
        <div className="flex flex-wrap gap-1">
          {(Object.keys(labels) as DubExportFormat[])
            .filter((value) => value !== 'mp4' || session.inputType === 'video')
            .map((value) => (
              <Button
                key={value}
                size="xs"
                variant={format === value ? 'secondary' : 'ghost'}
                aria-pressed={format === value}
                onClick={() => setFormat(value)}
              >
                {labels[value]}
              </Button>
            ))}
        </div>
        {format === 'mp4' && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t('exportModal.tracks')}</p>
            {tracks.map((code) =>
              toggle(
                code === 'original' ? t('exportModal.original') : code.toUpperCase(),
                selected.includes(code),
                (on) =>
                  setExcluded(on ? excluded.filter((item) => item !== code) : [...excluded, code]),
              ),
            )}
          </div>
        )}
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {t(format === 'mp4' ? 'exportModal.default_audio_track' : 'exportModal.languages')}
          </p>
          <div className="flex flex-wrap gap-1">
            {choices.map((code) => (
              <Button
                key={code}
                size="xs"
                aria-pressed={track === code}
                variant={track === code ? 'secondary' : 'ghost'}
                onClick={() => setTrack(code)}
              >
                {code === 'original' ? t('exportModal.original') : code.toUpperCase()}
              </Button>
            ))}
          </div>
        </div>
        {['mp4', 'wav', 'mp3'].includes(format) &&
          toggle(t('exportModal.bg_audio'), preserveBg, setPreserveBg)}
        {format === 'mp3' && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t('exportModal.bitrate')}</p>
            <div className="flex gap-1">
              {['128', '192', '256', '320'].map((value) => (
                <Button
                  key={value}
                  size="xs"
                  aria-pressed={bitrate === value}
                  variant={bitrate === value ? 'secondary' : 'ghost'}
                  onClick={() => setBitrate(value)}
                >
                  {value} kbps
                </Button>
              ))}
            </div>
          </div>
        )}
        {format === 'mp4' &&
          toggle(t('exportModal.hardsub'), burn && burnAllowed, setBurn, !burnAllowed)}
        {(['srt', 'vtt'].includes(format) || (format === 'mp4' && burn && burnAllowed)) &&
          toggle(t('exportModal.dual_subs'), dual, setDual)}
        {format === 'mp4' && burn && burnAllowed && (
          <>
            {toggle(t('exportModal.subs_style_karaoke'), karaoke && !dual, setKaraoke, dual)}
            {dual && (
              <p className="text-xs text-muted-foreground">{t('exportModal.karaoke_dual_note')}</p>
            )}
          </>
        )}
        {subtitles && <p className="text-xs text-muted-foreground">{t('exportModal.subs_note')}</p>}
        {format === 'mp4' && !burnAllowed && (
          <p className="text-xs text-muted-foreground">{t('exportModal.stretch_subs_note')}</p>
        )}
        {!ready && (
          <p className="text-xs text-muted-foreground">{t('exportModal.nothing_selected')}</p>
        )}
        <Button className="w-full" disabled={!ready || locked} onClick={() => void save()}>
          {saving ? <LoaderCircleIcon className="animate-spin" /> : <DownloadIcon />}
          {t(saving ? 'common.loading' : 'exportModal.export')}
        </Button>
      </fieldset>
      {error && (
        <PipelineFailure fallback={error} onDismiss={() => setError(null)} className="text-xs" />
      )}
    </details>
  );
}
