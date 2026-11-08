import type { RefObject } from 'react';
import { Menu } from '@base-ui/react/menu';
import {
  AudioLinesIcon,
  BoldIcon,
  ChevronDownIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  HashIcon,
  HeartPulseIcon,
  PauseIcon,
  SmileIcon,
  SpellCheckIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button, buttonVariants } from '@/components/ui/button';
import { useEngines } from '@/hooks/use-engines';
import { KNOWN_TAGS, engineShortName, expressionCaps } from '@/lib/expression';
import { chapterHeadingSnippet } from '@shared/utils/audiobookScript';

export function AudiobookMarkupToolbar({
  textareaRef,
  text,
  disabled,
  setText,
}: {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  text: string;
  disabled: boolean;
  setText(value: string): void;
}) {
  const { t } = useTranslation();
  const { activeTts } = useEngines();
  // The engine's own reaction tags first; the rest stay visible but grayed (#3422).
  const caps = expressionCaps(activeTts);
  const engineTags = caps.tags;
  const engineName = engineShortName(activeTts) || t('engineSidebar.tts');
  // Offered on every engine so it can be found (#3422); grayed where ignored.
  const emotionSupported = caps.emotion !== 'none';
  const unsupportedTags = KNOWN_TAGS.filter((tag) => !engineTags.includes(tag));
  const focus = (start: number, end = start) =>
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(start, end);
    });
  const splice = (snippet: string, start: number, end: number) => {
    const input = textareaRef.current;
    if (!input) return;
    input.setRangeText(snippet, start, end, 'end');
    setText(input.value);
  };
  const insert = (snippet: string) => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    splice(snippet, start, end);
    focus(start + snippet.length);
  };
  const wrap = (open: string, close: string) => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    const selected = input.value.slice(start, end);
    splice(open + selected + close, start, end);
    focus(selected ? start + open.length + selected.length + close.length : start + open.length);
  };
  const insertChapter = () => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    const snippet = chapterHeadingSnippet(input.value, start, end);
    splice(snippet, start, end);
    focus(start + snippet.replace(/\n$/, '').length);
  };
  // [emotion:] at the caret (or around the selection, closed by a reset), with
  // the caret inside the tag ready for a free-text mood.
  const insertEmotion = () => {
    const input = textareaRef.current;
    if (!input || !emotionSupported) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    const selected = input.value.slice(start, end);
    splice(selected ? `[emotion:]${selected}[emotion:]` : '[emotion:]', start, end);
    focus(start + '[emotion:'.length);
  };
  const insertVoice = () => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    splice('[voice:NAME]', start, end);
    focus(start + 7, start + 11);
  };

  return (
    <div
      role="toolbar"
      aria-label={t('audiobook.markup_toolbar')}
      className="flex flex-wrap items-center gap-1 rounded-xl border border-border/50 bg-muted/15 p-1.5"
    >
      <Button size="xs" variant="ghost" disabled={disabled} onClick={insertChapter}>
        <HashIcon />
        {t('audiobook.insert_chapter')}
      </Button>
      <Button size="xs" variant="ghost" disabled={disabled} onClick={() => insert('[pause 0.5s]')}>
        <PauseIcon />
        {t('audiobook.insert_pause')}
      </Button>
      <Button size="xs" variant="ghost" disabled={disabled} onClick={insertVoice}>
        <AudioLinesIcon />
        {t('audiobook.insert_voice')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[slow]', '[/slow]')}
      >
        <ChevronsLeftIcon />
        {t('audiobook.insert_slow')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[fast]', '[/fast]')}
      >
        <ChevronsRightIcon />
        {t('audiobook.insert_fast')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[emphasis]', '[/emphasis]')}
      >
        <BoldIcon />
        {t('audiobook.insert_emphasis')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[spell]', '[/spell]')}
      >
        <SpellCheckIcon />
        {t('audiobook.insert_spell')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        aria-disabled={!emotionSupported || undefined}
        title={emotionSupported ? undefined : t('expression.unsupported', { engine: engineName })}
        className="aria-disabled:opacity-45"
        onClick={insertEmotion}
      >
        <HeartPulseIcon />
        {t('cloneEmotion.title')}
      </Button>
      <Menu.Root>
        <Menu.Trigger
          disabled={disabled}
          className={buttonVariants({ variant: 'ghost', size: 'xs' })}
        >
          <SmileIcon />
          {t('audiobook.insert_reactions')}
          <ChevronDownIcon />
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner sideOffset={6} align="start" className="z-50">
            <Menu.Popup className="flex max-h-72 max-w-sm flex-wrap gap-1 overflow-y-auto rounded-lg border border-border bg-popover p-2 text-popover-foreground shadow-lg outline-none">
              {[...engineTags, ...unsupportedTags].map((tag) => (
                <Menu.Item
                  key={tag}
                  disabled={!engineTags.includes(tag)}
                  onClick={() => insert(tag)}
                  className="cursor-default rounded-md px-2.5 py-1.5 text-xs outline-none data-disabled:opacity-45 data-highlighted:bg-accent"
                >
                  {tag}
                </Menu.Item>
              ))}
              {unsupportedTags.length > 0 && (
                <p className="w-full px-2.5 pt-1 text-xs text-muted-foreground">
                  {t('expression.tags_unsupported', { engine: engineName })}
                </p>
              )}
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>
    </div>
  );
}
