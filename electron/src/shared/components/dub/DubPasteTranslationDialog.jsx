import { useCallback, useEffect, useMemo, useState } from 'react';
import { ClipboardPaste, FileText, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Dialog, Button, Textarea, Badge } from '../../ui';
import { buildPastePlan, detectPasteMode } from '../../utils/pasteTranslations';
import { dubParseSubtitleText } from '../../api/dub';
import { readTextFile } from '../../utils/readTextFile';

/**
 * DubPasteTranslationDialog — paste a translation produced somewhere else
 * (ChatGPT, DeepL, a human translator) onto the segments that already exist.
 *
 * Nothing is applied until the user has seen the full before→after mapping:
 * a wrong-by-one paste over a 400-line transcript is expensive to notice
 * later and annoying to undo row by row. Unmatched rows are shown as such
 * rather than being quietly filled with the neighbouring line.
 *
 * `onApply(text, { mode, cues })` re-derives the identical plan through
 * `pasteTranslations`, so applying goes through the normal undo stack.
 */
const MODE_ICON = { timestamped: FileText, numbered: ClipboardPaste, plain: ClipboardPaste };

// A feature-length transcript runs to thousands of rows; mounting all of them
// in a 280px scroll box just to preview costs more than it informs. Show a
// window and say how many are hidden — the count banner above already carries
// the totals the decision actually rests on.
const PREVIEW_ROW_LIMIT = 200;

export default function DubPasteTranslationDialog({ open, segments = [], onApply, onClose }) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [cues, setCues] = useState(null);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState(null);

  useEffect(() => {
    if (open) {
      setText('');
      setCues(null);
      setParseError(null);
    }
  }, [open]);

  const mode = useMemo(() => detectPasteMode(text), [text]);

  // Timestamped pastes need the backend's lenient cue parser before a plan
  // can exist. Debounced so a long paste isn't re-parsed on every keystroke.
  useEffect(() => {
    if (!open || mode !== 'timestamped' || !text.trim()) {
      setCues(null);
      setParseError(null);
      setParsing(false);
      return undefined;
    }
    let cancelled = false;
    // Drop the previous parse the moment the text changes: keeping it would
    // let the preview — and an eager Apply during the debounce window — run
    // against cues from text the user has already edited away.
    setCues(null);
    setParseError(null);
    setParsing(true);
    const timer = setTimeout(() => {
      dubParseSubtitleText(text)
        .then((res) => {
          if (cancelled) return;
          setCues(res.segments || []);
          setParseError(null);
        })
        .catch((e) => {
          if (cancelled) return;
          setCues([]);
          setParseError(e?.message || String(e));
        })
        .finally(() => {
          if (!cancelled) setParsing(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, mode, text]);

  const plan = useMemo(() => {
    if (!text.trim()) return null;
    if (mode === 'timestamped' && cues === null) return null;
    return buildPastePlan(text, segments, { mode, cues: cues || [] });
  }, [text, segments, mode, cues]);

  const readFile = useCallback((file) => {
    if (!file) return;
    // As before, a file that cannot be read leaves the text as it was.
    readTextFile(file).then(setText, () => {});
  }, []);

  const apply = () => {
    onApply?.(text, { mode, cues: cues || [] });
    onClose?.();
  };

  if (!open) return null;

  const ModeIcon = MODE_ICON[mode] || ClipboardPaste;
  const canApply = !!plan && plan.matchedCount > 0;

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={
        <>
          <ClipboardPaste size={14} /> {t('dub.paste_translation_title')}
        </>
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('dub.paste_translation_cancel')}
          </Button>
          <Button variant="primary" onClick={apply} disabled={!canApply} loading={parsing}>
            {t('dub.paste_translation_apply', { count: plan?.matchedCount || 0 })}
          </Button>
        </>
      }
    >
      <p className="text-[length:var(--text-xs)] text-fg-muted mb-[var(--space-3)]">
        {t('dub.paste_translation_desc')}
      </p>

      <Textarea
        rows={7}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={t('dub.paste_translation_placeholder')}
        aria-label={t('dub.paste_translation_title')}
        autoFocus
        onDrop={(e) => {
          const file = e.dataTransfer?.files?.[0];
          if (file) {
            e.preventDefault();
            readFile(file);
          }
        }}
      />

      <div className="flex items-center gap-[var(--space-3)] mt-[var(--space-3)] flex-wrap">
        <label
          htmlFor="paste-translation-file"
          className="flex items-center gap-[6px] px-[10px] py-[4px] rounded-[6px] cursor-pointer text-[length:var(--text-xs)] text-fg-muted bg-[rgba(255,255,255,0.05)] [border:1px_solid_rgba(255,255,255,0.1)]"
        >
          <FileText size={11} /> {t('dub.paste_translation_load_file')}
          {/* `sr-only`, never `hidden`: a bare <label> isn't in the tab order,
              and `hidden` drops the input from both the tab order and the
              accessibility tree — together they leave keyboard-only users with
              no way to reach the file picker at all. Visually hidden but
              focusable keeps the pointer affordance and restores the keyboard
              path (focus the input, Enter/Space opens the picker). */}
          <input
            id="paste-translation-file"
            type="file"
            accept=".srt,.vtt,.txt,text/plain"
            className="sr-only"
            onChange={(e) => {
              readFile(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
        </label>
        {!!text.trim() && (
          <Badge tone="neutral" size="xs">
            <ModeIcon size={10} /> {t(`dub.paste_translation_mode_${mode}`)}
          </Badge>
        )}
      </div>

      {parseError && (
        <div
          role="alert"
          data-testid="paste-translation-error"
          className="flex items-center gap-[6px] mt-[var(--space-3)] text-[length:var(--text-xs)] text-[#fb4934]"
        >
          <AlertTriangle size={11} /> {parseError}
        </div>
      )}

      {plan && (
        <>
          <div
            className="mt-[var(--space-4)] px-[var(--space-3)] py-[6px] rounded-[var(--radius-md)] text-[length:var(--text-xs)] bg-[rgba(255,255,255,0.03)] [border:1px_solid_rgba(255,255,255,0.06)]"
            data-testid="paste-translation-summary"
          >
            {t('dub.paste_translation_counts', {
              segments: plan.rows.length,
              lines: plan.sourceCount,
              unmatched: plan.unmatchedCount,
            })}
            {plan.unusedCount > 0 && (
              <span className="text-[#fabd2f] ml-[var(--space-2)]">
                {t('dub.paste_translation_unused', { count: plan.unusedCount })}
              </span>
            )}
          </div>

          {plan.matchedCount === 0 && (
            <div
              role="alert"
              className="flex items-center gap-[6px] mt-[var(--space-2)] text-[length:var(--text-xs)] text-[#fb4934]"
            >
              <AlertTriangle size={11} /> {t('dub.paste_translation_none_matched')}
            </div>
          )}

          <div className="mt-[var(--space-3)] max-h-[280px] overflow-y-auto [border:1px_solid_var(--chrome-border)] rounded-[var(--radius-md)]">
            {plan.rows.slice(0, PREVIEW_ROW_LIMIT).map((row) => (
              <div
                key={row.id}
                data-testid="paste-translation-row"
                data-matched={row.matched ? 'true' : 'false'}
                className={`flex gap-[var(--space-3)] px-[var(--space-3)] py-[4px] text-[length:var(--text-xs)] [border-bottom:1px_solid_var(--chrome-border)] ${
                  row.matched ? '' : 'bg-[rgba(251,73,52,0.06)]'
                }`}
              >
                <span className="w-[28px] shrink-0 text-fg-muted font-[family-name:var(--chrome-font-mono)]">
                  {row.index + 1}
                </span>
                <span className="flex-1 min-w-0 text-fg-muted line-through decoration-[rgba(255,255,255,0.25)]">
                  {row.before || '—'}
                </span>
                <span className="flex-1 min-w-0 text-fg">
                  {row.matched ? (
                    row.after
                  ) : (
                    <em className="text-[#fb4934] not-italic">
                      {t('dub.paste_translation_row_unmatched')}
                    </em>
                  )}
                </span>
              </div>
            ))}
            {plan.rows.length > PREVIEW_ROW_LIMIT && (
              <div className="px-[var(--space-3)] py-[6px] text-[length:var(--text-xs)] text-fg-muted text-center">
                {t('dub.paste_translation_more_rows', {
                  count: plan.rows.length - PREVIEW_ROW_LIMIT,
                })}
              </div>
            )}
          </div>
        </>
      )}
    </Dialog>
  );
}
