import { useState } from 'react';
import { AlignLeft, ClipboardPaste, Plus, ChevronDown } from 'lucide-react';
import DemoPresetGrid from '../DemoPresetGrid';
import { TAGS } from '../../utils/constants';

// `.tag-btn` (special-token chips in the Insert menu) migrated from index.css to
// Tailwind utilities (shadcn P4). Flat chrome pill, mono face — token utilities
// reference the same --chrome-* vars the old rule used, so the look is unchanged.
const TAG_BTN =
  'border border-transparent bg-transparent text-[var(--chrome-fg-muted)] px-[9px] py-[3px] rounded-[var(--chrome-radius-pill)] font-[var(--chrome-font-mono)] font-medium text-[0.66rem] whitespace-nowrap cursor-pointer transition-colors duration-[120ms] hover:bg-[var(--chrome-hover-bg)] hover:text-[var(--chrome-fg)] hover:border-transparent';

// Studio shell migrated from the `studio-*` classes + CloneDesignTab.css to
// utilities (fast shadcn). `.studio-panel` stays defined in index.css for the
// dub area; clone reproduces it inline so its bespoke restack (flat stack,
// overflow-visible insert popover) is self-contained.
const STUDIO_PANEL =
  'flex flex-col min-h-0 bg-[var(--chrome-bg)] border border-transparent rounded-none py-[10px] px-[12px] max-[800px]:px-[10px] max-[600px]:px-[6px] max-[600px]:py-[8px]';

export default function ScriptPanel({
  t,
  defineMethod,
  text,
  setText,
  activePersonality,
  demoPresets,
  applyDemoPreset,
  showDemoCoachmark,
  setShowDemoCoachmark,
  selectedProfile,
  DEMO_PROFILE_ID,
  textAreaRef,
  insertOpen,
  setInsertOpen,
  insertTag,
}) {
  const [pasting, setPasting] = useState(false);
  const [pasteFailed, setPasteFailed] = useState(false);
  const pasteText = async () => {
    const field = textAreaRef.current;
    setPasting(true);
    setPasteFailed(false);
    try {
      const value = await navigator.clipboard.readText();
      if (value) {
        // The store setter accepts a string, unlike React's functional setter.
        // Read the current field after the async permission prompt so edits made
        // while clipboard access was pending are retained.
        const currentField = textAreaRef.current;
        const current = currentField?.value ?? text;
        const start = currentField?.selectionStart ?? current.length;
        const end = currentField?.selectionEnd ?? current.length;
        setText(current.slice(0, start) + value + current.slice(end));
        setShowDemoCoachmark(false);
        requestAnimationFrame(() => {
          field?.focus();
          field?.setSelectionRange(start + value.length, start + value.length);
        });
      }
    } catch {
      setPasteFailed(true);
      field?.focus();
    } finally {
      setPasting(false);
    }
  };
  return (
    <div className="flex flex-col gap-[6px] flex-none min-h-0 relative z-[2]">
      {/* overflow-visible: the ⊕ Insert popover opens BELOW the textarea and
            must escape the panel's box instead of being clipped (#481). It
            used to open upward — but the script input sits at the very top of
            the clone modal, so the tag list (max-h 280px) climbed straight out
            of the viewport with no way to see or scroll it (owner-reported,
            screenshot showed the CMU chips clipped). Below always has room
            here: the panel is the topmost element in every mount. */}
      <div className={`${STUDIO_PANEL} relative z-[10] overflow-visible`}>
        <div className="label-row justify-between">
          <span className="inline-flex items-center gap-2">
            <AlignLeft className="label-icon" size={14} />
            {t('clone.text_label')}
          </span>
          <button
            type="button"
            disabled={pasting}
            onClick={pasteText}
            className="inline-flex min-h-9 items-center gap-2 rounded-md border-0 bg-transparent px-2 text-xs normal-case text-[var(--chrome-fg-muted)] cursor-pointer hover:bg-[var(--chrome-hover-bg)] disabled:opacity-50"
          >
            <ClipboardPaste size={14} />
            {t('clone.paste')}
          </button>
        </div>
        {pasteFailed && (
          <p role="alert" className="text-xs text-[var(--chrome-fg-muted)]">
            {t('clone.paste_failed')}
          </p>
        )}
        {/* Design-tab empty state: 7-card demo grid until the user
              interacts; then it steps aside for the standard form. */}
        {defineMethod === 'design' && !text && !activePersonality && demoPresets.length > 0 && (
          <DemoPresetGrid presets={demoPresets} onUse={applyDemoPreset} />
        )}
        {showDemoCoachmark && defineMethod === 'audio' && selectedProfile === DEMO_PROFILE_ID && (
          <div
            className="flex items-center gap-[8px] px-[10px] py-[6px] mb-[8px] rounded-[8px] bg-[rgba(243,165,182,0.08)] [border:1px_solid_rgba(243,165,182,0.25)] text-[11px] text-fg"
            role="note"
          >
            <span className="text-[14px] leading-none">💡</span>
            <span className="flex-1">{t('demo.clone_coachmark')}</span>
            <button
              type="button"
              className="border-0 bg-transparent text-[var(--color-fg-muted)] cursor-pointer text-[16px] leading-none px-1 rounded-[4px] hover:bg-white/[0.08] hover:text-[var(--color-fg)]"
              onClick={() => setShowDemoCoachmark(false)}
              aria-label="Dismiss coach mark"
            >
              ×
            </button>
          </div>
        )}
        <div className="relative flex-1 flex flex-col min-h-0">
          <textarea
            ref={textAreaRef}
            className="input-base studio-script-input flex-[0_1_auto] resize-y mb-[6px]"
            placeholder={
              defineMethod === 'audio'
                ? t('clone.prompt_placeholder')
                : t('clone.design_placeholder')
            }
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (showDemoCoachmark) setShowDemoCoachmark(false);
            }}
          />
          {/* Expression tokens live behind a popover — fourteen permanent
                chips were renting the page's best pixels for an occasional
                power feature (10x spec §1.4). */}
          <button
            type="button"
            className={`absolute right-[8px] bottom-[30px] inline-flex items-center gap-[4px] px-2 py-1 text-[0.66rem] bg-[var(--chrome-bg)] border rounded-[var(--chrome-radius-pill)] cursor-pointer transition-[color,border-color] duration-[var(--dur-fast)] focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px] ${
              insertOpen
                ? 'text-[var(--chrome-fg)] border-transparent'
                : 'text-[var(--chrome-fg-muted)] border-transparent hover:text-[var(--chrome-fg)] hover:border-transparent'
            }`}
            onClick={() => setInsertOpen((o) => !o)}
            aria-expanded={insertOpen}
            aria-label={t('clone.insert_token', { defaultValue: 'Insert expression token' })}
          >
            <Plus size={11} /> {t('clone.insert', { defaultValue: 'Insert' })}{' '}
            <ChevronDown size={10} />
          </button>
          {insertOpen && (
            <div className="fixed inset-0 z-[19]" onClick={() => setInsertOpen(false)} />
          )}
          {insertOpen && (
            <div
              className="absolute right-[8px] top-[calc(100%+6px)] z-20 flex flex-wrap gap-1 max-w-[min(360px,calc(100vw-16px))] max-h-[min(280px,calc(100vh-120px))] overflow-y-auto overscroll-contain p-2 bg-[var(--chrome-bg)] border border-transparent rounded-[10px] shadow-[0_8px_24px_rgba(0,0,0,0.45)]"
              role="menu"
            >
              {TAGS.map((tag) => (
                <button
                  key={tag}
                  className={TAG_BTN}
                  role="menuitem"
                  onClick={() => {
                    insertTag(tag);
                    setInsertOpen(false);
                  }}
                >
                  {tag}
                </button>
              ))}
              <button
                className={`${TAG_BTN} !border-transparent !text-[#b8bb26]`}
                role="menuitem"
                onClick={() => {
                  insertTag('[B EY1 S]');
                  setInsertOpen(false);
                }}
              >
                [CMU]
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
