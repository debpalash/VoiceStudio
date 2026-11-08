import { ChevronDownIcon, DownloadIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { getBridge } from './bridge';
import { Popover, PopoverContent, PopoverTrigger } from './popover';
import { Button, buttonVariants } from './ui/button';
import { saveExport } from '@/lib/export-history';
import { cn } from '@/lib/utils';

/**
 * Saves a backend audio file through the native save dialog. One click saves
 * `url` (WAV); with `mp3Url`, a format menu beside it also offers MP3.
 */
export function SaveAudioButton({
  url,
  mp3Url,
  suggestedName,
  disabled = false,
  iconOnly = false,
}: {
  url?: string;
  /** When set, a format menu beside the button also offers an MP3 export. */
  mp3Url?: string;
  suggestedName: string;
  disabled?: boolean;
  /** A compact secondary action: the label becomes the tooltip. */
  iconOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const bridge = getBridge();

  const save = async (target = url, name = suggestedName) => {
    if (!target || !bridge) return;
    setSaving(true);
    try {
      const saved = await saveExport(target, name);
      if (!saved) return;
      if (!saved.canceled && saved.path) {
        const path = saved.path;
        toast.success(t('clone.saved_to', { path }), {
          action: {
            label: t('clone.reveal'),
            onClick: () =>
              void bridge.files.revealPath(path).catch((error) => {
                const message = error instanceof Error ? error.message : String(error);
                toast.error(t('clone.download_failed', { message }));
              }),
          },
        });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      toast.error(t('clone.download_failed', { message }));
    } finally {
      setSaving(false);
    }
  };

  const off = disabled || saving || !url || !bridge;
  const button = (
    <Button
      variant="ghost"
      size={iconOnly ? 'icon-sm' : 'sm'}
      aria-label={iconOnly ? t('clone.download') : undefined}
      title={iconOnly ? t('clone.download') : undefined}
      onClick={() => void save()}
      disabled={off}
    >
      <DownloadIcon />
      {iconOnly ? null : t('clone.download')}
    </Button>
  );
  if (!mp3Url) return button;

  const choose = (target: string, name: string) => {
    setMenuOpen(false);
    void save(target, name);
  };
  const mp3Name = suggestedName.replace(/\.wav$/i, '') + '.mp3';

  return (
    <div className="flex items-center">
      {button}
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger
          aria-label={t('clone.download_format')}
          title={t('clone.download_format')}
          disabled={off}
          className={cn(buttonVariants({ variant: 'ghost', size: 'icon-sm' }), 'w-5')}
        >
          <ChevronDownIcon aria-hidden="true" className="size-3 opacity-70" />
        </PopoverTrigger>
        <PopoverContent align="end" className="flex min-w-40 flex-col p-1">
          <Button
            variant="ghost"
            size="sm"
            className="justify-start"
            onClick={() => choose(url!, suggestedName)}
          >
            {t('clone.download_wav')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="justify-start"
            onClick={() => choose(mp3Url, mp3Name)}
          >
            {t('clone.download_mp3')}
          </Button>
        </PopoverContent>
      </Popover>
    </div>
  );
}
