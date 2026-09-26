import MarkupToolbar from './MarkupToolbar';
import StatsBar from './StatsBar';

/**
 * AudiobookScriptPanel — the Write tab: the chapter-delimited manuscript.
 *
 * Markdown `# H1` headings delimit chapters; inline `[voice:NAME]` and
 * `[pause …]` tags are honoured by the backend parser. Mirrors the clone
 * workspace's ScriptPanel: one labelled surface, toolbar docked on top, the
 * editor filling the remaining height.
 */
export default function AudiobookScriptPanel({
  t,
  text,
  setText,
  textareaRef,
  onScriptKeyDown,
  warningsDismissed,
  setWarningsDismissed,
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-[7px]">
      <div className="flex min-h-[18px] flex-wrap items-center justify-between gap-x-[12px] gap-y-1 px-[4px]">
        <span className="label-row mb-0">{t('audiobook.script')}</span>
        {text.trim() ? <StatsBar t={t} text={text} /> : null}
      </div>
      <div className="audiobook-tab__manuscript flex min-h-0 flex-1 flex-col overflow-hidden rounded-[14px]">
        <div className="border-b border-transparent px-[10px] py-[7px]">
          <MarkupToolbar t={t} textareaRef={textareaRef} text={text} setText={setText} />
        </div>
        <textarea
          ref={textareaRef}
          className="input-base"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (warningsDismissed) setWarningsDismissed(false);
          }}
          onKeyDown={onScriptKeyDown}
          placeholder={t('audiobook.script_placeholder')}
          aria-label={t('audiobook.script')}
        />
        {!text.trim() && (
          <p className="m-0 border-t border-transparent px-[14px] py-[9px] text-[var(--text-sm)] text-fg-muted">
            {t('audiobook.empty_hint')}
          </p>
        )}
      </div>
    </div>
  );
}
