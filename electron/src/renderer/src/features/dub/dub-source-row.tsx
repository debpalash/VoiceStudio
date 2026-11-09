import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Menu } from '@base-ui/react/menu';
import {
  AudioLinesIcon,
  FilmIcon,
  FolderOpenIcon,
  LinkIcon,
  MoreHorizontalIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import { formatClock } from '@/components/waveform-player';
import { cn } from '@/lib/utils';
import { ConfirmDialog } from '../clone/confirm-dialog';
import { DUB_INPUT_ISSUE_KEYS, type DubInputIssue } from './dub-input-issue';

/** A URL source reads as its host; a file keeps its name. */
export function compactSourceLabel(source: string): string {
  if (!source) return '';
  try {
    return new URL(source).hostname.replace(/^www\./, '');
  } catch {
    return source;
  }
}

/** Locale-aware file size ("12.4 MB"), or '' when unknown. */
export function formatFileSize(bytes: number | undefined, locale: string): string {
  if (!bytes || !Number.isFinite(bytes) || bytes <= 0) return '';
  const mb = bytes / 1_000_000;
  const [value, unit] = mb >= 1000 ? [mb / 1000, 'gigabyte'] : [mb, 'megabyte'];
  try {
    return new Intl.NumberFormat(locale, {
      style: 'unit',
      unit,
      maximumFractionDigits: value < 10 ? 1 : 0,
    }).format(value);
  } catch {
    return `${value.toFixed(1)} ${unit === 'gigabyte' ? 'GB' : 'MB'}`;
  }
}

const ITEM =
  'flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-disabled:opacity-40 data-highlighted:bg-accent';

export function DubSourceRow({
  filename,
  inputType,
  duration,
  size,
  issue,
  disabled,
  removable,
  linkMode,
  confirmRemove,
  recovering,
  onChangeFile,
  onToggleLink,
  onRemove,
}: {
  filename: string;
  inputType: 'audio' | 'video';
  duration?: number;
  size?: number;
  issue?: DubInputIssue | null;
  /** A run or an interrupted job owns the source. */
  disabled: boolean;
  removable: boolean;
  linkMode: boolean;
  /** Removing would discard segments, edits or an interrupted run. */
  confirmRemove: boolean;
  recovering: boolean;
  onChangeFile: () => void;
  onToggleLink: () => void;
  onRemove: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const audio = inputType === 'audio';
  const removeLabel = t(audio ? 'dub.remove_audio' : 'dub.remove_video');
  const meta = [
    duration && duration > 0 ? formatClock(duration) : '',
    formatFileSize(size, i18n.language),
  ]
    .filter(Boolean)
    .join(' · ');
  const issueText = issue ? t(DUB_INPUT_ISSUE_KEYS[issue]) : '';
  const Icon = issue ? TriangleAlertIcon : audio ? AudioLinesIcon : FilmIcon;
  return (
    <div
      data-slot="dub-source-row"
      className={cn(
        'rounded-lg border px-2.5 py-2',
        issue ? 'border-warning/30 bg-warning/5' : 'border-transparent bg-background/35',
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <Icon
          aria-hidden="true"
          className={cn('size-4 shrink-0', issue ? 'text-warning' : 'text-primary')}
        />
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={filename}>
          {compactSourceLabel(filename)}
        </span>
        {meta && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{meta}</span>
        )}
        <Menu.Root>
          <Menu.Trigger
            aria-label={t('dub.file_actions')}
            title={t('dub.file_actions')}
            className={cn(buttonVariants({ variant: 'ghost', size: 'icon-xs' }), 'shrink-0')}
          >
            <MoreHorizontalIcon />
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner sideOffset={6} align="end" className="z-50">
              <Menu.Popup className="min-w-44 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg outline-none">
                <Menu.Item disabled={disabled} onClick={onChangeFile} className={ITEM}>
                  <FolderOpenIcon className="size-4" />
                  {t('dub.change_file')}
                </Menu.Item>
                <Menu.Item disabled={disabled} onClick={onToggleLink} className={ITEM}>
                  <LinkIcon className="size-4" />
                  {linkMode ? t('common.cancel') : t('dub.use_link')}
                </Menu.Item>
                <Menu.Item
                  disabled={!removable}
                  onClick={() => (confirmRemove ? setConfirmOpen(true) : onRemove())}
                  className={ITEM}
                >
                  <Trash2Icon className="size-4" />
                  {removeLabel}
                </Menu.Item>
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
      {issueText && (
        <p role="status" className="mt-1.5 pl-6 text-xs leading-5 text-foreground/90">
          {issueText}
        </p>
      )}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={removeLabel}
        description={t(
          recovering
            ? 'dub.remove_interrupted_confirm'
            : audio
              ? 'dub.remove_audio_confirm'
              : 'dub.remove_video_confirm',
        )}
        confirmLabel={removeLabel}
        onConfirm={onRemove}
      />
    </div>
  );
}
