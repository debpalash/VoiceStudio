import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { ComputeVendorIcon, formatComputeRuntime } from '@/components/compute-vendor-icon';

const tones = {
  active: 'bg-primary/12 text-primary',
  recommended: 'border border-primary/25 text-primary',
  installed: 'bg-muted/70 text-muted-foreground',
  warning: 'bg-warning/10 text-warning-foreground',
  muted: 'border border-border/70 text-muted-foreground',
} as const;

export function StatusBadge({
  tone,
  title,
  children,
}: {
  tone: keyof typeof tones;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex h-5 shrink-0 items-center gap-1 rounded-full px-1.5 text-[11px] font-medium leading-none whitespace-nowrap',
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function ActiveBadge() {
  const { t } = useTranslation();
  return (
    <StatusBadge tone="active">
      <CheckIcon aria-hidden="true" className="size-3" />
      {t('modelSettings.selected')}
    </StatusBadge>
  );
}

export function RecommendedBadge() {
  const { t } = useTranslation();
  return (
    <StatusBadge tone="recommended" title={t('firstrun.chip_recommended_title')}>
      {t('firstrun.chip_recommended')}
    </StatusBadge>
  );
}

export function DeviceChip({ device, title }: { device?: string | null; title?: string | null }) {
  const { t } = useTranslation();
  if (!device) return null;
  return (
    <span
      title={title || undefined}
      className="inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md border border-border/60 bg-muted/35 px-2 text-[11px] font-medium tracking-wide text-muted-foreground"
    >
      <ComputeVendorIcon runtime={device} className="size-3.5 shrink-0" />
      {device === "unknown" ? t("common.unknown") : formatComputeRuntime(device)}
    </span>
  );
}
