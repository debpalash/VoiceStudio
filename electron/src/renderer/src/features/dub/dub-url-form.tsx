import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MAX_COOKIE_EXPORT_BYTES } from '@shared/utils/cookieExport';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { isDubUrl } from './dub-session';

/**
 * The link form. It owns its draft (URL, cookie export, captions switch), so a
 * new or removed source — which remounts it — always starts from empty.
 */
export function DubUrlForm({
  disabled,
  autoFocus,
  onSubmit,
  onCancel,
}: {
  disabled: boolean;
  autoFocus?: boolean;
  onSubmit: (url: string, cookieFile: File | undefined, fetchSubs: boolean) => void;
  /** Shown when the form replaces an existing source. */
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const [url, setUrl] = useState('');
  const [cookieFile, setCookieFile] = useState<File>();
  const [cookieError, setCookieError] = useState(false);
  const [fetchSubs, setFetchSubs] = useState(false);
  const clear = () => {
    setUrl('');
    setCookieFile(undefined);
    setCookieError(false);
  };
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (disabled || !isDubUrl(url)) return;
        onSubmit(url, cookieFile, fetchSubs);
        setCookieFile(undefined);
      }}
    >
      <div className="flex items-center gap-1.5">
        <div className="min-w-0 flex-1">
          <Input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-label={t('dub.paste_url')}
            placeholder={t('dub.paste_url')}
            autoFocus={autoFocus}
            disabled={disabled}
          />
        </div>
        {url && (
          <Button type="button" size="xs" variant="ghost" disabled={disabled} onClick={clear}>
            {t('common.clear')}
          </Button>
        )}
        {onCancel && (
          <Button type="button" size="xs" variant="ghost" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
        )}
      </div>
      {url && (
        <details className="space-y-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {t('dub.advanced')}
          </summary>
          <div className="flex items-center justify-between gap-2 text-xs">
            <span>{t('dub.pull_captions')}</span>
            <Switch
              aria-label={t('dub.pull_captions')}
              checked={fetchSubs}
              onCheckedChange={setFetchSubs}
              disabled={disabled}
            />
          </div>
          <p className="text-xs text-muted-foreground">{t('dub.youtube_auth')}</p>
          <Input
            type="file"
            accept=".txt,text/plain"
            aria-label={t('dub.youtube_cookie_file')}
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              setCookieError(Boolean(file && file.size > MAX_COOKIE_EXPORT_BYTES));
              setCookieFile(file && file.size <= MAX_COOKIE_EXPORT_BYTES ? file : undefined);
            }}
          />
          {cookieFile && (
            <div className="flex items-center gap-2">
              <span className="truncate text-xs">{cookieFile.name}</span>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                aria-label={t('dub.remove_cookie_file')}
                onClick={() => setCookieFile(undefined)}
              >
                {t('common.cancel')}
              </Button>
            </div>
          )}
          {cookieError && (
            <p role="alert" className="text-xs text-destructive">
              {t('dub.cookie_size_error')}
            </p>
          )}
        </details>
      )}
      {url && (
        <Button
          type="submit"
          variant="outline"
          className="w-full"
          disabled={disabled || !isDubUrl(url)}
        >
          {t('dub.ingest')}
        </Button>
      )}
    </form>
  );
}
