import { Fragment } from 'react';
import { useTranslation } from 'react-i18next';

/** The settings a finished render was produced with, as the backend recorded them. */
export interface RenderSummary {
  engine?: string;
  voices?: { id: string; name: string }[];
  language?: string;
  format?: string;
  lines?: number;
  words?: number;
  speeds?: number[];
  /** Engine options keyed by backend name; known keys map through OPTION_LABELS. */
  options?: Record<string, unknown>;
  chapter_titles?: string[];
}

/** One finished long-form render as listed by `/longform/jobs`. */
export interface RenderRecord {
  job_id: string;
  /** Path of the rendered audio file. */
  output: string;
  title?: string;
  type?: 'story' | 'audiobook';
  created_at?: number | string;
  duration_s?: number;
  chapters?: number;
  summary?: RenderSummary;
}

const OPTION_LABELS: Record<string, string> = {
  num_step: 'clone.steps',
  guidance_scale: 'clone.cfg',
  position_temperature: 'clone.pos_temp',
  class_temperature: 'clone.class_temp',
  postprocess_output: 'clone.postprocess',
  seed: 'audiobook.seed',
  emo_vector: 'audiobook.emotion_help',
  emo_text: 'audiobook.emotion_text',
  emo_alpha: 'audiobook.emotion_alpha',
  vary_repeats: 'audiobook.vary_repeats',
  line_gap_ms: 'audiobook.line_gap',
  paragraph_gap_ms: 'audiobook.paragraph_gap',
  trim_edges: 'audiobook.trim_edges',
};

/** Format seconds as m:ss, switching to h:mm:ss at one hour. */
export function clock(seconds?: number): string {
  const numeric = Number(seconds);
  const total = Math.max(0, Math.round(Number.isFinite(numeric) ? numeric : 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${minutes}:${String(remainder).padStart(2, '0')}`;
}

/** `1.00×` for one speed, `0.90–1.25×` for a range, nothing without usable values. */
export function speedLabel(speeds: unknown): string {
  if (!Array.isArray(speeds)) return '';
  const finite = speeds.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  if (!finite.length) return '';
  const low = Math.min(...finite).toFixed(2);
  const high = Math.max(...finite).toFixed(2);
  return low === high ? `${low}×` : `${low}–${high}×`;
}

/** Voice names (id when a name is blank) joined by commas. */
export function voiceLabel(summary?: RenderSummary): string {
  const voices: unknown = summary?.voices;
  if (!Array.isArray(voices)) return '';
  return voices
    .map((voice: { id?: string; name?: string } | null) =>
      voice && typeof voice === 'object' ? voice.name || voice.id || '' : '',
    )
    .filter(Boolean)
    .join(', ');
}

export function renderRecipe(render: RenderRecord): string {
  const parts: string[] = [];
  const append = (value: string) => {
    if (value) parts.push(value);
  };
  append(voiceLabel(render.summary));
  append(speedLabel(render.summary?.speeds));
  if (typeof render.summary?.engine === 'string') append(render.summary.engine);
  if (
    typeof render.duration_s === 'number' &&
    Number.isFinite(render.duration_s) &&
    render.duration_s !== 0
  ) {
    append(clock(render.duration_s));
  }
  return parts.join(' · ');
}

function RecordedRenderDetails({
  render,
  summary,
}: {
  render: RenderRecord;
  summary: RenderSummary;
}) {
  const { t } = useTranslation();
  const options =
    summary.options && typeof summary.options === 'object' && !Array.isArray(summary.options)
      ? Object.entries(summary.options)
          .filter(([key]) => Object.prototype.hasOwnProperty.call(OPTION_LABELS, key))
          .map(([key, value]) => {
            const shown =
              typeof value === 'boolean'
                ? t(value ? 'pronunciation.enabled' : 'supportPlans.disabled')
                : String(value);
            return `${t(OPTION_LABELS[key])} ${shown}`;
          })
          .join(' · ')
      : '';
  const chapterTitles = Array.isArray(summary.chapter_titles) ? summary.chapter_titles : [];
  const rows = [
    [t('projects.render_voice'), voiceLabel(summary)],
    [t('projects.render_speed'), speedLabel(summary.speeds)],
    [t('projects.render_engine'), typeof summary.engine === 'string' ? summary.engine : ''],
    [t('projects.render_language'), typeof summary.language === 'string' ? summary.language : ''],
    [
      t('projects.render_format'),
      typeof summary.format === 'string' ? summary.format.toUpperCase() : '',
    ],
    [
      t('projects.render_size_label'),
      t('projects.render_size', {
        chapters: render.chapters ?? chapterTitles.length,
        lines: typeof summary.lines === 'number' ? summary.lines : 0,
        words: typeof summary.words === 'number' ? summary.words : 0,
      }),
    ],
    [t('projects.render_settings'), options],
  ].filter((row): row is [string, string] => Boolean(row[1]));

  return (
    <dl className="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
      {rows.map(([term, detail]) => (
        <Fragment key={term}>
          <dt className="whitespace-nowrap text-muted-foreground">{term}</dt>
          <dd className="break-all">{detail}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export function RenderDetails({ render }: { render: RenderRecord }) {
  const { t } = useTranslation();
  if (render.summary == null) {
    return <p className="mt-1 text-xs text-muted-foreground">{t('projects.render_no_details')}</p>;
  }
  return <RecordedRenderDetails render={render} summary={render.summary} />;
}

/** Collapsible "how it was made" block for a Projects row. */
export function RenderDisclosure({ render }: { render: RenderRecord }) {
  const { t } = useTranslation();
  return (
    <details className="group mt-1.5">
      <summary className="w-fit cursor-pointer select-none text-xs text-muted-foreground underline-offset-2 hover:underline">
        {t('projects.render_details')}
      </summary>
      <RenderDetails render={render} />
    </details>
  );
}
