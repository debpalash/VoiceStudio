import { readTextFile } from '@shared/utils/readTextFile';
import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangleIcon, ClipboardPasteIcon, FileTextIcon, XIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { apiJson } from '@/lib/api/client';
import {
  buildPastePlan,
  detectPasteMode,
  type PasteTranslationCue,
  type PasteTranslationRow,
} from '@shared/utils/pasteTranslations';
import type { DubSegment } from './dub-session';

const PREVIEW_LIMIT = 120;

interface PasteTranslationProps {
  segments: DubSegment[];
  disabled?: boolean;
  onApply: (rows: PasteTranslationRow[]) => void;
  onClose: () => void;
}

export function PasteTranslation({ segments, disabled, onApply, onClose }: PasteTranslationProps) {
  const { t } = useTranslation();
  const fileInput = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [cues, setCues] = useState<PasteTranslationCue[] | null>(null);
  const [parsing, setParsing] = useState(false);
  const [parseFailed, setParseFailed] = useState(false);
  const [readFailed, setReadFailed] = useState(false);
  const mode = useMemo(() => detectPasteMode(text), [text]);

  useEffect(() => {
    if (mode !== 'timestamped' || !text.trim()) {
      setCues(null);
      setParsing(false);
      setParseFailed(false);
      return;
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setCues(null);
      setParsing(true);
      setParseFailed(false);
      void apiJson<{ segments: PasteTranslationCue[] }>('/dub/parse-subtitle-text', {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify({ text }),
      })
        .then((result) => setCues(result.segments || []))
        .catch(() => {
          if (!controller.signal.aborted) {
            setCues([]);
            setParseFailed(true);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setParsing(false);
        });
    }, 300);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [mode, text]);

  const plan = useMemo(() => {
    if (!text.trim() || (mode === 'timestamped' && cues === null)) return null;
    return buildPastePlan(text, segments, { mode, cues: cues || [] });
  }, [cues, mode, segments, text]);

  const readFile = (file?: File) => {
    if (!file) return;
    setReadFailed(false);
    void readTextFile(file)
      .then(setText)
      .catch(() => setReadFailed(true));
  };

  return (
    <section className="rounded-xl border border-primary/20 bg-primary/[0.035] shadow-sm">
      <div className="flex items-start gap-3 border-b border-border/50 px-4 py-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <ClipboardPasteIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-medium">{t('dub.paste_translation_title')}</h2>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
            {t('dub.paste_translation_desc')}
          </p>
        </div>
        <Button size="icon-xs" variant="ghost" aria-label={t('common.close')} onClick={onClose}>
          <XIcon />
        </Button>
      </div>
      <div className="space-y-3 p-4">
        <Textarea
          autoFocus
          rows={6}
          value={text}
          disabled={disabled}
          className="min-h-32 resize-y bg-background/50 leading-6"
          aria-label={t('dub.paste_translation_title')}
          placeholder={t('dub.paste_translation_placeholder')}
          onChange={(event) => setText(event.target.value)}
          onDrop={(event) => {
            const file = event.dataTransfer.files[0];
            if (!file) return;
            event.preventDefault();
            readFile(file);
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileInput}
            type="file"
            accept=".srt,.vtt,.txt,text/plain"
            className="sr-only"
            onChange={(event) => {
              readFile(event.target.files?.[0]);
              event.target.value = '';
            }}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() => fileInput.current?.click()}
          >
            <FileTextIcon />
            {t('dub.paste_translation_load_file')}
          </Button>
          {text.trim() && (
            <span className="rounded-full border border-border/60 bg-muted/30 px-2.5 py-1 text-xs text-muted-foreground">
              {t(`dub.paste_translation_mode_${mode}`)}
            </span>
          )}
          {plan && (
            <span className="ml-auto text-xs tabular-nums text-muted-foreground">
              {t('dub.paste_translation_counts', {
                segments: plan.rows.length,
                lines: plan.sourceCount,
                unmatched: plan.unmatchedCount,
              })}
            </span>
          )}
        </div>
        {(parseFailed || (plan && plan.matchedCount === 0)) && (
          <p role="alert" className="flex items-center gap-2 text-xs text-destructive">
            <AlertTriangleIcon className="size-3.5" />
            {t('dub.paste_translation_none_matched')}
          </p>
        )}
        {readFailed && (
          <p role="alert" className="flex items-center gap-2 text-xs text-destructive">
            <AlertTriangleIcon className="size-3.5" />
            {t('scriptEdit.import_failed')}
          </p>
        )}
        {plan && plan.unusedCount > 0 && (
          <p className="text-xs text-warning">
            {t('dub.paste_translation_unused', { count: plan.unusedCount })}
          </p>
        )}
        {plan && plan.rows.length > 0 && (
          <div className="max-h-72 overflow-y-auto rounded-lg border border-border/60 bg-background/30 [scrollbar-width:thin]">
            {plan.rows.slice(0, PREVIEW_LIMIT).map((row) => (
              <div
                key={row.id}
                className="grid grid-cols-[2rem_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b border-border/40 px-3 py-2 text-xs last:border-0"
              >
                <span className="font-mono tabular-nums text-muted-foreground">
                  {row.index + 1}
                </span>
                <span className="truncate text-muted-foreground">{row.before || '—'}</span>
                <span className={row.matched ? 'truncate' : 'text-destructive'}>
                  {row.matched ? row.after : t('dub.paste_translation_row_unmatched')}
                </span>
              </div>
            ))}
            {plan.rows.length > PREVIEW_LIMIT && (
              <p className="px-3 py-2 text-center text-xs text-muted-foreground">
                {t('dub.paste_translation_more_rows', {
                  count: plan.rows.length - PREVIEW_LIMIT,
                })}
              </p>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {t('dub.paste_translation_cancel')}
          </Button>
          <Button
            disabled={disabled || parsing || !plan?.matchedCount}
            aria-busy={parsing}
            onClick={() => {
              if (!plan?.matchedCount) return;
              onApply(plan.rows);
              onClose();
            }}
          >
            {t('dub.paste_translation_apply', { count: plan?.matchedCount || 0 })}
          </Button>
        </div>
      </div>
    </section>
  );
}
