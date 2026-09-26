import React from 'react';
import { Loader, Play } from 'lucide-react';
import { Button } from '../../ui';

/**
 * The parsed chapter/span plan from "Preview plan", with a per-chapter audition
 * button. Extracted from AudiobookTab to keep that page under the line lint;
 * behaviour is unchanged.
 */
export default function PlanList({ t, plan, chapterPrev, onPreviewChapter, busy }) {
  return (
    <div className="audiobook-plan">
      <h3>{t('audiobook.plan_heading', { count: plan.chapter_count })}</h3>
      <ol style={{ paddingLeft: 18, margin: 0 }}>
        {plan.chapters.map((c, i) => {
          const prev = chapterPrev[i] || {};
          return (
            <li key={i} style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Button
                  variant="icon"
                  iconSize="sm"
                  onClick={() => onPreviewChapter(i)}
                  disabled={prev.loading || busy}
                  aria-label={t('audiobook.preview_chapter', { title: c.title })}
                >
                  {prev.loading ? <Loader size={12} className="spin" /> : <Play size={12} />}
                </Button>
                <strong>{c.title}</strong>{' '}
                <span className="muted">
                  {t('audiobook.chapter_meta', { spans: c.spans.length, chars: c.char_count })}
                </span>
              </div>
              {prev.url && (
                <audio controls src={prev.url} style={{ width: '100%', marginTop: 4 }} />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
