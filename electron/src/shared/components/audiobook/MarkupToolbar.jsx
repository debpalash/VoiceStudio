import React, { useState } from 'react';
import {
  AudioLines,
  Bold,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  Pause,
  Smile,
  SpellCheck,
} from 'lucide-react';
import { TAGS } from '../../utils/constants';

// Compact chrome pill button — same visual language as the clone Insert menu.
const PILL =
  'inline-flex h-[26px] items-center justify-center gap-[4px] border border-transparent bg-[var(--chrome-bg)] text-[var(--chrome-fg-muted)] px-[7px] py-0 rounded-[var(--chrome-radius-pill)] [font-family:var(--chrome-font-mono)] text-[0.62rem] whitespace-nowrap cursor-pointer transition-colors duration-[120ms] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)] focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px]';
const TAG_BTN =
  'border border-transparent bg-transparent text-[var(--chrome-fg-muted)] px-[9px] py-[3px] rounded-[var(--chrome-radius-pill)] [font-family:var(--chrome-font-mono)] font-medium text-[0.66rem] whitespace-nowrap cursor-pointer transition-colors duration-[120ms] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)]';

/**
 * Insert markup at the script cursor (#1217). Buttons drop `[pause 500ms]`,
 * `[voice:NAME]`, and paired `[slow]…[/slow]` / `[fast]…[/fast]` /
 * `[emphasis]…[/emphasis]` / `[spell]…[/spell]` (wrapping the selection when
 * there is one), plus a reaction-tags menu from the shared TAGS list.
 *
 * Uses `setRangeText` where available so the native undo stack is preserved
 * (falls back to a controlled-value splice, e.g. under jsdom), matching the
 * clone ScriptPanel's cursor-insert approach — React's controlled value
 * tolerates it because we immediately sync state to the element's new value.
 */
export default function MarkupToolbar({ t, textareaRef, text, setText }) {
  const [reactionsOpen, setReactionsOpen] = useState(false);

  const focusCaret = (from, to) =>
    setTimeout(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(from, to);
    }, 0);

  // Splice `snippet` in place of [start,end); prefer setRangeText (undo-safe).
  const splice = (snippet, start, end) => {
    const el = textareaRef.current;
    if (!el) return;
    if (typeof el.setRangeText === 'function') {
      el.setRangeText(snippet, start, end, 'end');
      setText(el.value);
    } else {
      setText(text.slice(0, start) + snippet + text.slice(end));
    }
  };

  const insert = (snippet) => {
    const el = textareaRef.current;
    if (!el) return;
    const start = el.selectionStart ?? text.length;
    const end = el.selectionEnd ?? start;
    splice(snippet, start, end);
    focusCaret(start + snippet.length, start + snippet.length);
  };

  // Wrap the current selection (or drop an empty pair, caret between the tags).
  const wrap = (open, close) => {
    const el = textareaRef.current;
    if (!el) return;
    const start = el.selectionStart ?? text.length;
    const end = el.selectionEnd ?? start;
    const selected = (el.value ?? text).slice(start, end);
    splice(`${open}${selected}${close}`, start, end);
    const caret = selected
      ? start + open.length + selected.length + close.length
      : start + open.length;
    focusCaret(caret, caret);
  };

  const insertVoice = () => {
    const el = textareaRef.current;
    if (!el) return;
    const start = el.selectionStart ?? text.length;
    const end = el.selectionEnd ?? start;
    splice('[voice:NAME]', start, end);
    // Select the NAME placeholder so the user can type over it immediately.
    const nameStart = start + '[voice:'.length;
    focusCaret(nameStart, nameStart + 'NAME'.length);
  };

  return (
    <div
      className="relative flex flex-wrap items-center gap-[4px]"
      role="toolbar"
      aria-label={t('audiobook.markup_toolbar')}
    >
      <button
        type="button"
        className={PILL}
        onClick={() => insert('[pause 500ms]')}
        aria-label={t('audiobook.insert_pause')}
        title={t('audiobook.insert_pause')}
      >
        <Pause size={11} /> {t('audiobook.insert_pause')}
      </button>
      <button
        type="button"
        className={PILL}
        onClick={insertVoice}
        aria-label={t('audiobook.insert_voice')}
        title={t('audiobook.insert_voice')}
      >
        <AudioLines size={11} /> {t('audiobook.insert_voice')}
      </button>
      <button
        type="button"
        className={PILL}
        onClick={() => wrap('[slow]', '[/slow]')}
        aria-label={t('audiobook.insert_slow')}
        title={t('audiobook.insert_slow')}
      >
        <ChevronsLeft size={11} /> {t('audiobook.insert_slow')}
      </button>
      <button
        type="button"
        className={PILL}
        onClick={() => wrap('[fast]', '[/fast]')}
        aria-label={t('audiobook.insert_fast')}
        title={t('audiobook.insert_fast')}
      >
        <ChevronsRight size={11} /> {t('audiobook.insert_fast')}
      </button>
      <button
        type="button"
        className={PILL}
        onClick={() => wrap('[emphasis]', '[/emphasis]')}
        aria-label={t('audiobook.insert_emphasis')}
        title={t('audiobook.insert_emphasis')}
      >
        <Bold size={11} /> {t('audiobook.insert_emphasis')}
      </button>
      <button
        type="button"
        className={PILL}
        onClick={() => wrap('[spell]', '[/spell]')}
        aria-label={t('audiobook.insert_spell')}
        title={t('audiobook.insert_spell')}
      >
        <SpellCheck size={11} /> {t('audiobook.insert_spell')}
      </button>
      <button
        type="button"
        className={PILL}
        onClick={() => setReactionsOpen((o) => !o)}
        aria-expanded={reactionsOpen}
        aria-label={t('audiobook.insert_reactions')}
        title={t('audiobook.insert_reactions')}
      >
        <Smile size={11} /> {t('audiobook.insert_reactions')} <ChevronDown size={8} />
      </button>
      {reactionsOpen && (
        <>
          <button
            type="button"
            className="fixed inset-0 z-[19] cursor-default border-0 bg-transparent p-0"
            onClick={() => setReactionsOpen(false)}
            aria-label={t('common.close')}
          />
          <div
            className="absolute left-0 top-[calc(100%+6px)] z-20 flex flex-wrap gap-1 max-w-[min(360px,calc(100vw-16px))] max-h-[min(280px,calc(100vh-120px))] overflow-y-auto overscroll-contain p-2 bg-[var(--chrome-bg)] border border-transparent rounded-[10px] shadow-[0_8px_24px_rgba(0,0,0,0.45)]"
            role="menu"
          >
            {TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                className={TAG_BTN}
                role="menuitem"
                onClick={() => {
                  insert(tag);
                  setReactionsOpen(false);
                }}
              >
                {tag}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
