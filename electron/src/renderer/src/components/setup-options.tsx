import { ChevronDownIcon, FolderOpenIcon } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ExistingPythonSettings } from '@/features/settings/existing-python-settings';
import { RemoteBackendSettings } from '@/features/settings/remote-backend-settings';
import i18n, { APP_LANGUAGE_ITEMS, APP_LANGUAGES, setAppLanguage, type AppLocale } from '@/i18n';
import { cn } from '@/lib/utils';
import type { BackendStatus, RuntimeRegion } from '../../../preload/index.d';
import { getBridge } from './bridge';
import { MODEL_HEADROOM_GIB, SetupHardware } from './setup-extras';

/** Where the runtime goes and whether it fits: one compact card above the install button. */
export function SetupLocation({
  status,
  disabled,
  showChange,
  onChange,
}: {
  status: BackendStatus;
  disabled: boolean;
  /** False when the primary action already is "Choose another folder". */
  showChange: boolean;
  onChange: () => void;
}) {
  const { t } = useTranslation();
  if (!status.runtimePath) return null;
  const { runtimeFreeGib: free, setupRequiredGib: needed } = status;
  const measured = free !== undefined && needed !== undefined;
  return (
    <section
      aria-label={t('firstrun.env_dir')}
      className="flex items-start gap-3 rounded-xl border border-border/50 bg-card/20 px-3.5 py-3"
    >
      <FolderOpenIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-medium">{t('firstrun.env_dir')}</span>
          <span className="text-xs text-muted-foreground">
            {t(status.runtimeStorage ? 'firstrun.env_dir_desc_scoped' : 'firstrun.env_dir_desc')}
          </span>
        </p>
        <p
          className="mt-0.5 truncate font-mono text-xs text-muted-foreground"
          title={status.runtimePath}
        >
          {status.runtimePath}
        </p>
        {measured ? (
          <p
            data-testid="setup-free-space"
            className={cn('mt-1 text-xs', free < needed ? 'text-warning' : 'text-muted-foreground')}
          >
            {t('firstrun.free_space', { free, needed })}
          </p>
        ) : null}
        {measured && free >= needed && free < needed + MODEL_HEADROOM_GIB ? (
          <p data-testid="setup-model-space" className="mt-0.5 text-xs text-warning">
            {t('setup.models_space')}
          </p>
        ) : null}
        <SetupHardware hardware={status.setupHardware} />
        {status.runtimeStorage && (
          <dl className="mt-1.5 space-y-0.5 text-xs text-muted-foreground">
            {(['data', 'models', 'temp'] as const).map((kind) => (
              <div key={kind} className="flex gap-2">
                <dt className="shrink-0">{t(`firstrun.storage_${kind}`)}</dt>
                <dd className="min-w-0 truncate font-mono" title={status.runtimeStorage?.[kind]}>
                  {status.runtimeStorage?.[kind]}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap justify-end gap-1">
        {status.runtimeCustom && (
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() => {
              void getBridge()
                ?.backend.useDefaultRuntimeLocation()
                .catch(() => {
                  /* The setup screen remains retryable if IPC is interrupted. */
                });
            }}
          >
            {t('firstrun.reset_default')}
          </Button>
        )}
        {showChange && (
          <Button size="sm" variant="outline" disabled={disabled} onClick={onChange}>
            {t('firstrun.change')}
          </Button>
        )}
      </div>
    </section>
  );
}

const REGIONS = ['auto', 'global', 'china', 'russia', 'restricted'] as const;

/**
 * Everything a first install rarely needs, behind the Options disclosure:
 * app language, download region, an existing Python interpreter and using a
 * VoiceStudio backend on another computer instead.
 */
export function SetupOptions({
  status,
  regionDisabled,
  showPython,
  showRemote,
}: {
  status: BackendStatus;
  regionDisabled: boolean;
  showPython: boolean;
  /** False when the remote editor is already on screen (Intel Macs). */
  showRemote: boolean;
}) {
  const { t } = useTranslation();
  const [choosingRegion, setChoosingRegion] = useState(false);
  const [connectionOpen, setConnectionOpen] = useState(false);
  // Mounted on first open and kept, so a typed address or a running test survives a toggle.
  const [connectionShown, setConnectionShown] = useState(false);
  const regionItems = REGIONS.map((region) => ({
    value: region,
    label: t(region === 'auto' ? 'bootstrap.auto_detect' : `bootstrap.region_${region}`),
  }));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-muted-foreground">
          <span>{t('settings.language')}</span>
          <Select
            items={APP_LANGUAGE_ITEMS}
            value={i18n.resolvedLanguage || i18n.language}
            onValueChange={(value) => void setAppLanguage(value as AppLocale)}
          >
            <SelectTrigger className="w-full bg-muted/20" aria-label={t('settings.language')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start">
              {APP_LANGUAGES.map(({ code, label }) => (
                <SelectItem key={code} value={code}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          <span>{t('firstrun.region_label')}</span>
          <Select
            items={regionItems}
            value={status.runtimeRegion || 'auto'}
            disabled={choosingRegion || regionDisabled}
            onValueChange={async (value) => {
              setChoosingRegion(true);
              try {
                await getBridge()?.backend.setRuntimeRegion(value as RuntimeRegion);
              } finally {
                setChoosingRegion(false);
              }
            }}
          >
            <SelectTrigger className="w-full bg-muted/20" aria-label={t('firstrun.region_label')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {regionItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>
      {showPython ? <ExistingPythonSettings /> : null}
      {showRemote ? (
        <Collapsible
          open={connectionOpen}
          onOpenChange={(open) => {
            if (open) setConnectionShown(true);
            setConnectionOpen(open);
          }}
        >
          <CollapsibleTrigger
            render={
              <Button
                variant="outline"
                size="sm"
                className="h-auto min-h-8 whitespace-normal py-1.5"
              />
            }
          >
            {t('setup.use_other_computer')}
            <ChevronDownIcon
              className={cn('size-4 transition-transform', connectionOpen && 'rotate-180')}
              aria-hidden="true"
            />
          </CollapsibleTrigger>
          <CollapsibleContent keepMounted className="pt-3">
            {connectionShown ? <RemoteBackendSettings /> : null}
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}
