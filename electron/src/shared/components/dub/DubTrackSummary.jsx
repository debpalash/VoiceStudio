import { Check } from 'lucide-react';
import { Badge } from '../../ui';
import TrackManager from './TrackManager';

export default function DubTrackSummary({
  t,
  dubStep,
  dubTracks = [],
  incrementalPlan,
  exportTracks,
  setExportTracks,
  dubLangCode,
}) {
  if (!dubTracks.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="dub-translation-tracks">
      {dubStep === 'done' && (
        <Badge tone="success">
          <Check size={11} /> {t('dub.tracks_done', { tracks: dubTracks.join(', ') })}
        </Badge>
      )}
      {!!incrementalPlan?.stale?.length && (
        <Badge tone="warn">
          {t('dub.segments_changed', { count: incrementalPlan.stale.length })}
        </Badge>
      )}
      {incrementalPlan?.stale?.length === 0 && !!incrementalPlan?.fresh?.length && (
        <Badge tone="neutral">
          {t('dub.all_up_to_date', { count: incrementalPlan.fresh.length })}
        </Badge>
      )}
      <TrackManager
        t={t}
        tracks={[
          { code: 'original', label: t('dub.original_track'), kind: 'original' },
          ...dubTracks.map((code) => ({ code, label: code.toUpperCase(), kind: 'dub' })),
        ]}
        selection={exportTracks}
        setSelection={setExportTracks}
        primaryCode={dubLangCode}
      />
    </div>
  );
}
