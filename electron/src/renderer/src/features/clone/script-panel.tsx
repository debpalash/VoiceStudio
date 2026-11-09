import { createPortal } from 'react-dom';
import { textareaCaret } from '@/lib/textarea-caret';
import { importScript, SCRIPT_ACCEPT } from '@/lib/import-script';
import {
  AlignLeftIcon,
  ChevronDownIcon,
  ClipboardPasteIcon,
  PlusIcon,
  Undo2Icon,
  FileUpIcon,
  SparklesIcon,
} from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useEngines } from '@/hooks/use-engines';
import { KNOWN_TAGS, engineShortName, expressionCaps } from '@/lib/expression';
import { setCloneSetting, useCloneSetting } from '@/lib/store/clone-settings';
import { cn } from '@/lib/utils';
import { SectionLabel } from './section-label';

export function ScriptPanel({
  voiceName,
  coachmark,
  onUserEdit,
}: { voiceName?: string; coachmark?: string; onUserEdit?: () => void } = {}) {
  const { t } = useTranslation();
  const text = useCloneSetting('text');
  const { activeTts } = useEngines();
  // Offer the engine's own reaction tags; any other engine would speak them as words.
  const engineTags = expressionCaps(activeTts).tags;
  const unsupportedTags = KNOWN_TAGS.filter((tag) => !engineTags.includes(tag));
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const pasteRef = useRef<HTMLDivElement>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [anchor, setAnchor] = useState({ left: 0, top: 0 });
  const [undo, setUndo] = useState<{ before: string; after: string } | null>(null);
  const [insertOpen, setInsertOpen] = useState(false);
  const [pasting, setPasting] = useState(false);
  const menuId = useId();

  useEffect(() => {
    if (!insertOpen && !pasteOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || !menuRef.current?.contains(target)) setInsertOpen(false);
      if (!(target instanceof Node) || !pasteRef.current?.contains(target)) setPasteOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        setInsertOpen(false);
        setPasteOpen(false);
        textareaRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [insertOpen, pasteOpen]);

  const edit = (value: string, replace = false) => {
    const field = textareaRef.current;
    if (!field) return;
    const before = field.value;
    const start = replace ? 0 : field.selectionStart;
    const end = replace ? before.length : field.selectionEnd;
    const after = before.slice(0, start) + value + before.slice(end);
    if (after === before) {
      setPasteOpen(false);
      return;
    }
    onUserEdit?.();
    field.focus();
    field.setSelectionRange(start, end);
    // Chromium's native editing transaction preserves Ctrl/Cmd+Z history.
    const native =
      typeof document.execCommand === 'function' &&
      document.execCommand('insertText', false, value);
    if (!native) {
      setCloneSetting('text', after);
      requestAnimationFrame(() => {
        field.focus();
        field.setSelectionRange(start + value.length, start + value.length);
      });
    } else setCloneSetting('text', field.value);
    if (replace) setUndo({ before, after });
    setInsertOpen(false);
    setPasteOpen(false);
  };

  const paste = async (replace = false) => {
    const before = textareaRef.current?.value;
    setPasting(true);
    setPasteOpen(false);
    try {
      const value = await navigator.clipboard.readText();
      // Do not overwrite edits made while a clipboard permission prompt was open.
      if (value && textareaRef.current?.value === before) edit(value, replace);
    } catch {
      toast.error(t('clone.paste_failed'));
      textareaRef.current?.focus();
    } finally {
      setPasting(false);
    }
  };

  const openInsert = (focusMenu = true) => {
    const field = textareaRef.current;
    if (!field) return;
    const point = textareaCaret(field);
    setAnchor({
      left: Math.max(8, Math.min(point.left, window.innerWidth - 368)),
      top: Math.max(8, Math.min(point.top + 6, window.innerHeight - 240)),
    });
    setPasteOpen(false);
    setInsertOpen(true);
    if (focusMenu)
      requestAnimationFrame(() =>
        menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true }),
      );
  };

  useEffect(() => {
    if (!insertOpen) return;
    const close = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Node) || !menuRef.current?.contains(target)) setInsertOpen(false);
    };
    window.addEventListener('resize', close);
    document.addEventListener('scroll', close, true);
    return () => {
      window.removeEventListener('resize', close);
      document.removeEventListener('scroll', close, true);
    };
  }, [insertOpen]);

  return (
    <section className="flex min-h-64 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <SectionLabel>
            <AlignLeftIcon aria-hidden="true" />
            {t('clone.text_label')}
          </SectionLabel>
          <div className="flex items-center gap-1">
            <input
              ref={importRef}
              type="file"
              className="sr-only"
              accept={SCRIPT_ACCEPT}
              aria-label={t('scriptEdit.import')}
              onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (!file) return;
                const before = textareaRef.current?.value;
                setPasting(true);
                try {
                  const imported = await importScript(file);
                  if (textareaRef.current?.value === before) edit(imported);
                } catch {
                  toast.error(t('scriptEdit.import_failed'));
                } finally {
                  setPasting(false);
                }
              }}
            />
            <Button
              variant="ghost"
              size="xs"
              className="font-normal text-muted-foreground hover:text-foreground"
              disabled={pasting}
              title="TXT, Markdown, DOC, DOCX, PDF, EPUB"
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => importRef.current?.click()}
            >
              <FileUpIcon />
              {t('scriptEdit.import')}
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className="font-normal text-muted-foreground hover:text-foreground"
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => void paste()}
              disabled={pasting}
            >
              <ClipboardPasteIcon data-icon="inline-start" />
              {t('clone.paste')}
            </Button>
            <div className="relative" ref={pasteRef}>
              <Button
                variant="ghost"
                size="icon-xs"
                disabled={pasting}
                aria-label={t('scriptEdit.paste_options')}
                aria-haspopup="menu"
                aria-expanded={pasteOpen}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => {
                  setPasteOpen(!pasteOpen);
                  setInsertOpen(false);
                }}
              >
                <ChevronDownIcon />
              </Button>
              {pasteOpen && (
                <div
                  role="menu"
                  aria-label={t('scriptEdit.paste_options')}
                  className="absolute right-0 top-full z-30 mt-2 min-w-44 rounded-lg border border-border bg-popover p-1 shadow-md"
                >
                  <button
                    autoFocus
                    type="button"
                    role="menuitem"
                    className="w-full rounded px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-muted outline-none"
                    onClick={() => void paste(true)}
                  >
                    {t('scriptEdit.replace')}
                  </button>
                </div>
              )}
            </div>
            {undo && undo.after === text && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  const field = textareaRef.current;
                  field?.focus();
                  if (typeof document.execCommand !== 'function' || !document.execCommand('undo'))
                    setCloneSetting('text', undo.before);
                  else if (field) setCloneSetting('text', field.value);
                  setUndo(null);
                }}
              >
                <Undo2Icon />
                {t('scriptEdit.undo')}
              </Button>
            )}
            <div>
              <Button
                variant="ghost"
                size="xs"
                className="font-normal text-muted-foreground hover:text-foreground"
                title={t('clone.insert_token')}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => (insertOpen ? setInsertOpen(false) : openInsert())}
                aria-expanded={insertOpen}
                aria-haspopup="menu"
                aria-controls={insertOpen ? menuId : undefined}
                aria-label={t('clone.insert_token')}
              >
                <PlusIcon data-icon="inline-start" />
                {t('clone.insert')}
                <ChevronDownIcon
                  className={cn('transition-transform', insertOpen && 'rotate-180')}
                />
              </Button>
              {insertOpen
                ? createPortal(
                    <div
                      ref={menuRef}
                      style={{ left: anchor.left, top: anchor.top }}
                      onKeyDown={(event) => {
                        const buttons = Array.from(
                          menuRef.current?.querySelectorAll<HTMLButtonElement>(
                            '[role="menuitem"]',
                          ) ?? [],
                        );
                        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
                        if (
                          [
                            'ArrowRight',
                            'ArrowDown',
                            'ArrowLeft',
                            'ArrowUp',
                            'Home',
                            'End',
                          ].includes(event.key)
                        ) {
                          event.preventDefault();
                          const next =
                            event.key === 'Home'
                              ? 0
                              : event.key === 'End'
                                ? buttons.length - 1
                                : (index +
                                    (event.key === 'ArrowRight' || event.key === 'ArrowDown'
                                      ? 1
                                      : -1) +
                                    buttons.length) %
                                  buttons.length;
                          buttons[next]?.focus();
                        }
                        if (event.key === 'Tab') setInsertOpen(false);
                      }}
                      id={menuId}
                      role="menu"
                      aria-label={t('clone.insert_token')}
                      className="fixed z-50 flex max-h-56 w-[min(360px,calc(100vw-16px))] flex-wrap gap-1 overflow-y-auto rounded-lg bg-popover p-2 text-popover-foreground shadow-md ring-1 ring-foreground/10 animate-in fade-in-0 zoom-in-95 motion-reduce:animate-none"
                    >
                      {[...engineTags, ...unsupportedTags].map((tag) => {
                        const supported = engineTags.includes(tag);
                        return (
                          <button
                            key={tag}
                            type="button"
                            role="menuitem"
                            aria-disabled={!supported || undefined}
                            aria-describedby={supported ? undefined : menuId + '-unsupported'}
                            className="rounded-full px-2 py-0.5 text-[length:var(--text-caption)] text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 aria-disabled:cursor-not-allowed aria-disabled:opacity-45 aria-disabled:hover:bg-transparent aria-disabled:hover:text-muted-foreground"
                            onClick={() => supported && edit(tag)}
                          >
                            {tag}
                          </button>
                        );
                      })}
                      {unsupportedTags.length > 0 && (
                        <p
                          id={menuId + '-unsupported'}
                          className="w-full px-2 pt-1 text-[length:var(--text-caption)] text-muted-foreground"
                        >
                          {t('expression.tags_unsupported', {
                            engine: engineShortName(activeTts) || t('engineSidebar.tts'),
                          })}
                        </p>
                      )}
                    </div>,
                    document.body,
                  )
                : null}
            </div>
          </div>
        </div>
        {coachmark ? (
          <div
            role="status"
            className="flex items-center gap-2 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 text-xs text-muted-foreground"
          >
            <SparklesIcon className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
            <span>{coachmark}</span>
          </div>
        ) : null}
        <Textarea
          data-clone-script
          ref={textareaRef}
          value={text}
          onChange={(event) => {
            setInsertOpen(false);
            onUserEdit?.();
            setCloneSetting('text', event.target.value);
          }}
          placeholder={
            voiceName
              ? t('cloneFlow.prompt_named', { name: voiceName })
              : t('clone.prompt_placeholder')
          }
          aria-label={t('clone.text_label')}
          className="min-h-32 flex-1 resize-none rounded-none border-0 bg-transparent px-0 py-3 leading-[var(--text-editor--line-height)] text-[length:var(--text-editor)] shadow-none ring-0 placeholder:text-muted-foreground focus-visible:ring-0 md:text-[length:var(--text-editor)] md:leading-[var(--text-editor--line-height)] dark:bg-transparent"
          onKeyDown={(event) => {
            if (event.altKey && event.key === '/') {
              event.preventDefault();
              openInsert();
            } else if (event.key !== 'Escape') setInsertOpen(false);
          }}
          spellCheck
        />
        <div className="flex justify-end">
          <span className="text-[length:var(--text-label)] text-muted-foreground tabular-nums">
            {t('clone.characters', { count: text.length })}
          </span>
        </div>
      </div>
    </section>
  );
}
