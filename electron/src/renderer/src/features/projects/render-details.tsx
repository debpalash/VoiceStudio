import { useTranslation } from 'react-i18next';

export interface RenderSummary {
  engine?: string;
  voices?: { id: string; name: string }[];
  language?: string;
  format?: string;
  lines?: number;
  words?: number;
  speeds?: number[];
  options?: Record<string, unknown>;
  chapter_titles?: string[];
}

export interface RenderRecord {
  job_id: string;
  title?: string;
  output: string;
  type?: 'story' | 'audiobook';
  created_at?: number | string;
  duration_s?: number;
  chapters?: number;
  summary?: RenderSummary;
}

export function clock(seconds: number | undefined): string {
  const total = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

export function speedLabel(speeds: number[] | undefined): string {
  const values = (Array.isArray(speeds) ? speeds : []).filter((value) => Number.isFinite(value));
  if (!values.length) return '';
  const low = Math.min(...values);
  const high = Math.max(...values);
  return low === high ? `${low.toFixed(2)}×` : `${low.toFixed(2)}–${high.toFixed(2)}×`;
}

export const voiceLabel = (summary: RenderSummary | undefined) =>
  (Array.isArray(summary?.voices) ? summary.voices : [])
    .map((voice) => voice?.name || voice?.id)
    .filter(Boolean)
    .join(', ');

/** One language-neutral line that tells two renders apart in a list. */
export function renderRecipe(render: RenderRecord): string {
  return [
    voiceLabel(render.summary),
    speedLabel(render.summary?.speeds),
    render.summary?.engine,
    render.duration_s ? clock(render.duration_s) : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** "How it was made" for one finished render; older renders have no summary. */
export function RenderDetails({ render }: { render: RenderRecord }) {
  const { t } = useTranslation();
  const summary = render.summary;
  if (!summary)
    return <p className="text-xs text-muted-foreground">{t('projects.render_no_details')}</p>;
  const options = Object.entries(
    summary.options && typeof summary.options === 'object' ? summary.options : {},
  )
    .map(([key, value]) => `${key.replaceAll('_', ' ')} ${String(value)}`)
    .join(' · ');
  const rows: [string, string][] = [
    [t('projects.render_voice'), voiceLabel(summary)],
    [t('projects.render_speed'), speedLabel(summary.speeds)],
    [t('projects.render_engine'), summary.engine || ''],
    [t('projects.render_language'), summary.language || ''],
    [t('projects.render_format'), (summary.format || '').toUpperCase()],
    [
      t('projects.render_size_label'),
      t('projects.render_size', {
        chapters: render.chapters ?? summary.chapter_titles?.length ?? 0,
        lines: summary.lines ?? 0,
        words: summary.words ?? 0,
      }),
    ],
    [t('projects.render_settings'), options],
  ];
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
      {rows
        .filter(([, value]) => value)
        .map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words">{value}</dd>
          </div>
        ))}
    </dl>
  );
}
