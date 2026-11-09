import type { RefObject } from 'react';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ListTreeIcon } from 'lucide-react';
import { parseChapterOutline } from '@shared/utils/audiobookScript';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const MIRROR_STYLES = [
  'font-family',
  'font-size',
  'font-style',
  'font-weight',
  'letter-spacing',
  'line-height',
  'overflow-wrap',
  'padding-bottom',
  'padding-left',
  'padding-right',
  'padding-top',
  'tab-size',
  'text-indent',
  'text-transform',
  'white-space',
  'word-break',
  'word-spacing',
];

/**
 * Displayed top of `offset` inside the textarea's content, counting soft-wrapped
 * lines: lay the preceding text out in a hidden mirror with the same width,
 * font and wrapping, then read where a marker after it lands.
 */
function textareaOffsetTop(textarea: HTMLTextAreaElement, offset: number) {
  const style = window.getComputedStyle(textarea);
  const mirror = document.createElement('div');
  mirror.style.whiteSpace = 'pre-wrap';
  mirror.style.overflowWrap = 'break-word';
  for (const name of MIRROR_STYLES) {
    const value = style.getPropertyValue(name);
    if (value) mirror.style.setProperty(name, value);
  }
  Object.assign(mirror.style, {
    position: 'absolute',
    visibility: 'hidden',
    top: '0',
    left: '-9999px',
    boxSizing: 'border-box',
    border: '0',
    width: `${textarea.clientWidth}px`,
    height: 'auto',
    overflow: 'hidden',
  });
  mirror.textContent = textarea.value.slice(0, offset);
  const marker = document.createElement('span');
  marker.dataset.caretMarker = '';
  marker.textContent = '\u200b';
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const top = marker.offsetTop;
  mirror.remove();
  return top;
}

export function scrollTextareaToOffset(textarea: HTMLTextAreaElement, start: number, end = start) {
  const length = textarea.value.length;
  const from = Math.max(0, Math.min(start, length));
  const to = Math.max(from, Math.min(end, length));
  textarea.focus();
  textarea.setSelectionRange(from, to);
  const top = textareaOffsetTop(textarea, from);
  textarea.scrollTop = Math.max(0, top - textarea.clientHeight / 3);
}

export function ChapterOutline({
  script,
  caret = 0,
  textareaRef,
  disabled,
  onJump,
}: {
  script: string;
  caret?: number;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  disabled?: boolean;
  onJump?: (start: number) => void;
}) {
  const { t } = useTranslation();
  const chapters = useMemo(() => parseChapterOutline(script), [script]);
  const activeIndex = chapters.reduce(
    (current, chapter) => (chapter.start <= caret ? chapter.index : current),
    -1,
  );
  const jump = (start: number, headingEnd: number) => {
    const input = textareaRef.current;
    if (!input) return;
    scrollTextareaToOffset(input, start, headingEnd);
    onJump?.(start);
  };

  return (
    <nav
      data-slot="audiobook-chapter-outline"
      aria-label={t('audiobook.outline')}
      className="flex max-h-36 min-w-0 flex-col gap-1 overflow-y-auto rounded-lg border border-border/60 bg-muted/15 p-1.5 lg:max-h-none lg:w-56 lg:shrink-0"
    >
      <p className="flex items-center gap-1.5 px-1.5 py-0.5 text-[11px] font-medium tracking-[0.01em] text-muted-foreground">
        <ListTreeIcon className="size-3.5" />
        {t('audiobook.outline')}
      </p>
      {chapters.length === 0 ? (
        <p className="px-1.5 py-1 text-xs leading-5 text-muted-foreground">
          {t('audiobook.outline_empty')}
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1 lg:flex-col lg:flex-nowrap">
          {chapters.map((chapter) => {
            const title = chapter.title || t('audiobook.chapter_n', { n: chapter.index + 1 });
            const active = chapter.index === activeIndex;
            return (
              <li key={`${chapter.index}:${chapter.start}`} className="min-w-0 lg:w-full">
                <Button
                  type="button"
                  size="sm"
                  variant={active ? 'secondary' : 'ghost'}
                  disabled={disabled}
                  aria-current={active ? 'true' : undefined}
                  aria-label={t('audiobook.jump_chapter', { title })}
                  title={title}
                  className={cn(
                    'h-auto min-h-8 w-full justify-start px-2 py-1 text-left whitespace-normal',
                    chapter.words === 0 && 'text-muted-foreground',
                  )}
                  onClick={() => jump(chapter.start, chapter.headingEnd)}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium">{title}</span>
                    <span className="block text-[10px] font-normal text-muted-foreground">
                      {t('audiobook.outline_words', { count: chapter.words })}
                    </span>
                  </span>
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
