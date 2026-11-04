import { useEffect, useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
/* @ts-expect-error shared JSX component has no declaration file */
import SearchableSelect from '@shared/components/SearchableSelect';
import type { RuntimeTorchPreference } from '../../../preload/index.d';

interface RuntimeTorchSelectProps {
  value: RuntimeTorchPreference;
  onChange: (value: RuntimeTorchPreference) => void;
  disabled?: boolean;
  pinned?: boolean;
  pending?: boolean;
  error?: boolean;
  notice?: string;
  autoFocus?: boolean;
}

export function RuntimeTorchSelect({
  value,
  onChange,
  disabled = false,
  pinned = false,
  pending = false,
  error = false,
  notice,
  autoFocus = false,
}: RuntimeTorchSelectProps) {
  const { t } = useTranslation();
  const identifier = useId();
  const container = useRef<HTMLDivElement>(null);
  const wasPending = useRef(false);
  const descriptionId = `${identifier}-description`;
  const noticeId = `${identifier}-notice`;
  const pinnedId = `${identifier}-pinned`;

  useEffect(() => {
    if (autoFocus) container.current?.querySelector('button')?.focus();
  }, [autoFocus]);

  useEffect(() => {
    const trigger = container.current?.querySelector('button');
    trigger?.setAttribute(
      'aria-describedby',
      [descriptionId, notice ? noticeId : '', pinned ? pinnedId : ''].filter(Boolean).join(' '),
    );
    trigger?.setAttribute('aria-invalid', String(error));
    if (wasPending.current && !pending && document.activeElement === document.body) {
      trigger?.focus();
    }
    wasPending.current = pending;
  }, [descriptionId, noticeId, pinnedId, pinned, pending, error, notice]);

  return (
    <div
      ref={container}
      role="group"
      aria-label={t('runtimeTorch.title')}
      aria-busy={pending}
      className="w-full space-y-2 text-left"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && event.defaultPrevented) event.stopPropagation();
      }}
    >
      <p className="text-sm font-medium">{t('runtimeTorch.title')}</p>
      <SearchableSelect
        value={value}
        options={(['auto', 'cpu', 'cuda', 'rocm'] as const).map((preference) => ({
          value: preference,
          label: t(`runtimeTorch.${preference}`),
        }))}
        onChange={(preference: RuntimeTorchPreference) => {
          if (!disabled && !pinned && !pending) onChange(preference);
        }}
        disabled={disabled || pinned || pending}
        buttonClassName="h-8 rounded-md border border-input bg-input/20 px-2 py-1.5 outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 aria-invalid:border-destructive dark:bg-input/30 dark:hover:bg-input/50"
        ariaLabel={t('runtimeTorch.title')}
        menuPortal
      />
      <p id={descriptionId} className="text-xs text-muted-foreground">
        {t(`runtimeTorch.${value}Description`)}
      </p>
      {pinned && (
        <p id={pinnedId} className="text-xs text-muted-foreground">
          {t('runtimeTorch.pinned')}
        </p>
      )}
      <p
        id={noticeId}
        role={error ? 'alert' : 'status'}
        aria-live={error ? 'assertive' : 'polite'}
        aria-atomic="true"
        className={error ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
      >
        {notice}
      </p>
    </div>
  );
}
