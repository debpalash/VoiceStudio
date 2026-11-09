import { ChevronDownIcon, ChevronUpIcon, ReplaceIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { isImeComposing } from '@/lib/ime';
import { useState, type KeyboardEvent } from 'react';

export function SegmentFindBar({
  query,
  replacement,
  matchIndex,
  matchCount,
  canReplace,
  canReplaceAll,
  disabled,
  onQuery,
  onReplacement,
  onPrev,
  onNext,
  onReplace,
  onReplaceAll,
}: {
  query: string;
  replacement: string;
  matchIndex: number;
  matchCount: number;
  canReplace: boolean;
  canReplaceAll?: boolean;
  disabled?: boolean;
  onQuery: (value: string) => void;
  onReplacement: (value: string) => void;
  onPrev: () => void;
  onNext: () => void;
  onReplace: () => void;
  onReplaceAll: () => void;
}) {
  const { t } = useTranslation();
  const hasQuery = Boolean(query.trim());
  // Replace stays folded into one slim row until asked for (or already in use).
  const [replaceOpen, setReplaceOpen] = useState(false);
  const showReplace = replaceOpen || Boolean(replacement);
  const onFindKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (isImeComposing(event) || event.key !== 'Enter') return;
    event.preventDefault();
    if (event.shiftKey) onPrev();
    else onNext();
  };
  const onReplaceKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (isImeComposing(event) || event.key !== 'Enter') return;
    event.preventDefault();
    if (canReplace) onReplace();
  };
  return (
    <div data-slot="dub-find-bar" className="flex min-w-0 flex-wrap items-center gap-1.5 px-2 py-1">
      <Input
        value={query}
        disabled={disabled}
        aria-label={t('segment.search_placeholder')}
        placeholder={t('segment.search_placeholder')}
        className="h-7 w-40 min-w-32 bg-background/70"
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={onFindKey}
      />
      <span className="min-w-14 text-xs tabular-nums text-muted-foreground" aria-live="polite">
        {hasQuery
          ? matchCount
            ? t('segment.find_count', {
                current: matchIndex + 1,
                total: matchCount,
              })
            : t('segment.find_none')
          : null}
      </span>
      <Button
        size="icon-xs"
        variant="ghost"
        disabled={disabled || matchCount === 0}
        aria-label={t('segment.find_prev')}
        title={t('segment.find_prev')}
        onClick={onPrev}
      >
        <ChevronUpIcon />
      </Button>
      <Button
        size="icon-xs"
        variant="ghost"
        disabled={disabled || matchCount === 0}
        aria-label={t('segment.find_next')}
        title={t('segment.find_next')}
        onClick={onNext}
      >
        <ChevronDownIcon />
      </Button>
      <Button
        size="icon-xs"
        variant={showReplace ? 'secondary' : 'ghost'}
        disabled={disabled}
        aria-expanded={showReplace}
        aria-label={t('segment.replace_toggle')}
        title={t('segment.replace_toggle')}
        onClick={() => {
          if (showReplace) onReplacement('');
          setReplaceOpen(!showReplace);
        }}
      >
        <ReplaceIcon />
      </Button>
      {showReplace && (
        <>
          <Input
            value={replacement}
            disabled={disabled}
            aria-label={t('segment.replace_placeholder')}
            placeholder={t('segment.replace_placeholder')}
            className="h-7 w-36 min-w-28 bg-background/70"
            onChange={(event) => onReplacement(event.target.value)}
            onKeyDown={onReplaceKey}
          />
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled || !canReplace}
            aria-label={t('segment.replace')}
            onClick={onReplace}
          >
            <ReplaceIcon />
            <span className="hidden @5xl:inline">{t('segment.replace')}</span>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled || canReplaceAll === false || !hasQuery || matchCount === 0}
            aria-label={t('segment.replace_all')}
            onClick={onReplaceAll}
          >
            {t('segment.replace_all')}
          </Button>
        </>
      )}
    </div>
  );
}
